"""三星产险专属提取后处理（2022-2025 版式）。

三星产险报告的表格文字层是“每个单元格一行”的竖排结构（fitz 文本顺序为
标签行后紧跟若干单值行），pdfplumber 的网格识别会：

1. “偿付能力充足率指标”把整行 3 个数值挤进一个单元格，其余行只剩最后一列；
2. “主要经营指标”只识别到前 12 行，综合费用率/赔付率/签单保费系列全部丢失；
3. “实际资本明细表”“最低资本表”跨页续页列错位、行截断；
4. “近三年（综合）投资收益率”两张表只有 2 行，未进入规则提取结果；
5. “流动性风险监测指标”与 2.2 回溯表混排，期间/数值错配。

本后处理器直接在 PDF 文本层按页重建这 6 张表的行，保留原始项目名称与
数值口径，再交给通用标准化流程（指标匹配、期间归一、单位换算）处理。

行结构（均带表头，便于 Step3 识别期间）：

* SOLVENCY_MAIN            [指标名称, 本季度数, 上季度可比数, 下季度预测数]
* NON_LIFE_OPERATING_METRICS [指标名称, 本季度数, 本年度累计数]
* ACTUAL_CAPITAL           [行次, 项目, 期末数, 期初数]
* MINIMUM_CAPITAL          [行次, 项目, 期末数, 期初数]
* THREE_YEAR_INVESTMENT_RETURN [项目, 本季度]（行标签含“近三年平均”）
* LIQUIDITY_MONITORING     [指标名称, 本季度数, 上季度数]
  2022 年无 2.4 监测指标表，使用 2.2 回溯表（1./2.1/2.2/2.3 行）；
  2023-2025 年合并 2.2 回溯表与 2.4 监测指标表（一、经营活动净现金流
  为全年累计口径，与 2.1 本季度口径冲突，保留 2.1 值）。
"""

from __future__ import annotations

import re
from typing import Iterable, Sequence

from . import register

try:  # 与仓库其它模块一致：优先 pymupdf，兼容旧 fitz 包名
    import pymupdf  # type: ignore
except ImportError:  # pragma: no cover
    import fitz as pymupdf  # type: ignore

_NUMBER_RE = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?$")
_ROW_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)*\*?$")
_MISSING = {"-", "--", "—", "–", "－", "/"}

_MAIN_HEADER = ["指标名称", "本季度数", "上季度可比数", "下季度预测数"]
_OPERATING_HEADER = ["指标名称", "本季度数", "本年度累计数"]
_RENAMED_LABELS = {
    # 2.4/3.8 与通用递延所得税资产/3.4 在原文同名，补上所属资本层级以唯一命中词典。
    "2.4": "核心二级资本超限额应扣除部分",
    "3.4": "附属一级资本-递延所得税资产（由经营性亏损引起的递延所得税资产除外）",
    "3.8": "附属一级资本超限额应扣除部分",
}

_CAPITAL_HEADER = ["行次", "项目", "期末数", "期初数"]
_THREE_YEAR_HEADER = ["项目", "本季度"]
_LIQUIDITY_HEADER = ["指标名称", "本季度数", "上季度数"]

# 经营指标里的分组标题行：没有独立指标含义，不能算指标行。
_GROUP_LABEL_RE = re.compile(r"^(?:主要经营指标|（[一二三四五六七八九十]+）(?:效益|规模|品质)类指标)$")


def _page_lines(document, page_index: int) -> list[str]:
    text = document.load_page(page_index).get_text() or ""
    return [line.strip() for line in text.splitlines() if line.strip()]


def _find_pages(document, *needles: str) -> list[int]:
    """返回同时包含全部关键字的物理页（1 基）。"""
    hits: list[int] = []
    for index in range(len(document)):
        text = document.load_page(index).get_text() or ""
        if all(needle in text for needle in needles):
            hits.append(index + 1)
    return hits


def _value_tokens(line: str) -> list[str] | None:
    """整行只由数值/占位符构成时返回 tokens，否则返回 None。"""
    stripped = line.strip()
    if not stripped:
        return None
    if stripped in _MISSING:
        return [stripped]
    parts = stripped.split()
    if parts and all(_NUMBER_RE.match(part) for part in parts):
        return parts
    return None


def _is_code_line(lines: Sequence[str], index: int) -> bool:
    """行次单元格：纯编号行，且下一非空行是中文项目名称。"""
    line = lines[index].strip()
    if not _ROW_NUMBER_RE.match(line):
        return False
    for follow in lines[index + 1 : index + 4]:
        if _NUMBER_RE.match(follow) or follow in _MISSING:
            continue
        return bool(re.search(r"[\u4e00-\u9fff]", follow))
    return False


def _skip_header_cells(lines: Sequence[str], start: int, header: Iterable[str]) -> int:
    cells = set(header)
    index = start
    while index < len(lines) and lines[index].strip() in cells:
        index += 1
    return index


def _parse_main(document) -> tuple[list[list[str]], int]:
    pages = _find_pages(document, "指标名称", "本季度数", "上季度可比数")
    if not pages:
        return [], 0
    page = pages[0]
    lines = _page_lines(document, page - 1)
    try:
        start = lines.index("指标名称")
    except ValueError:  # pragma: no cover - 防御
        return [], page
    index = _skip_header_cells(lines, start, _MAIN_HEADER)
    rows: list[list[str]] = [list(_MAIN_HEADER)]
    current: list[str] | None = None
    for line in lines[index:]:
        tokens = _value_tokens(line)
        if tokens is not None:
            if current is not None:
                current.extend(tokens)
            continue
        if line.startswith(("（二）", "（三）", "五、", "六、")):
            break
        current = [line]
        rows.append(current)
    rows = [row for row in rows if len(row) > 1]
    return rows, page


def _parse_operating(document) -> tuple[list[list[str]], int]:
    pages = _find_pages(document, "指标名称", "本季度数", "本年度累计数")
    if not pages:
        return [], 0
    # 可能有“偿付能力有充足率指标”以外同名表头，取含（四）主要经营指标的页。
    page = next((p for p in pages if "主要经营指标" in "\n".join(_page_lines(document, p - 1))), pages[0])
    lines = _page_lines(document, page - 1)
    try:
        start = lines.index("指标名称")
    except ValueError:  # pragma: no cover
        return [], page
    index = _skip_header_cells(lines, start, _OPERATING_HEADER)
    rows: list[list[str]] = [list(_OPERATING_HEADER)]
    current: list[str] | None = None
    skip_row = False
    for line in lines[index:]:
        tokens = _value_tokens(line)
        if tokens is not None:
            if current is not None and not skip_row:
                current.extend(tokens)
            continue
        if line.startswith("五、"):
            break
        if _GROUP_LABEL_RE.match(line):
            skip_row = True
            current = None
            continue
        skip_row = False
        current = [line]
        rows.append(current)
    rows = [row for row in rows if len(row) > 1]
    return rows, page


def _parse_capital_page(document, page: int) -> list[list[str]]:
    lines = _page_lines(document, page - 1)
    if "行次" in lines:
        index = _skip_header_cells(lines, lines.index("行次") + 1, _CAPITAL_HEADER)
    else:
        # 续页不重复表头时从首个“行次单元格”开始。
        index = next((i for i in range(len(lines)) if _is_code_line(lines, i)), len(lines))
    rows: list[list[str]] = []
    current: list[str] | None = None
    while index < len(lines):
        line = lines[index]
        if re.match(r"^（[一二三四五六七八九十]{1,3}）", line) or re.match(r"^[一二三四五六七八九十]{1,3}、", line):
            break  # 下一张表/下一章节标题
        if _is_code_line(lines, index):
            if current is not None:
                rows.append(current)
            current = [line, ""]
            index += 1
            continue
        tokens = _value_tokens(line)
        if current is not None:
            if tokens is not None:
                current.extend(tokens)
            else:
                current[1] += line
        index += 1
    if current is not None:
        rows.append(current)
    for row in rows:
        if len(row) > 1 and row[0].strip() in _RENAMED_LABELS:
            row[1] = _RENAMED_LABELS[row[0].strip()]
    return rows


def _trim_capital_rows(rows: list[list[str]], terminal_code: str, terminal_label: str) -> list[list[str]]:
    """保留到“合计/最低资本”行（含），其后若同页还有下一张表则丢弃。"""
    trimmed: list[list[str]] = []
    for row in rows:
        trimmed.append(row)
        code = row[0].strip()
        label = row[1].strip() if len(row) > 1 else ""
        if code == terminal_code and terminal_label in label:
            break
    return trimmed


def _parse_actual_capital(document) -> tuple[list[list[str]], list[tuple[int, list[list[str]]]]]:
    pages = _find_pages(document, "实际资本明细表")
    if not pages:
        return [], []
    blocks: list[tuple[int, list[list[str]]]] = []
    first_page = pages[0]
    index = first_page
    while index <= len(document):
        lines = _page_lines(document, index - 1)
        data = _trim_capital_rows(_parse_capital_page(document, index), "5", "实际资本合计")
        if not data:
            break
        blocks.append((index, data))
        page_text = "\n".join(lines)
        if "（七）" in page_text or "5" == data[-1][0].strip():
            break
        index += 1
        if index > first_page + 1:  # 实际资本表最多跨 2 页
            break
    all_rows: list[list[str]] = [list(_CAPITAL_HEADER)]
    for _page, data in blocks:
        all_rows.extend(data)
    return all_rows, blocks


def _parse_minimum_capital(document) -> tuple[list[list[str]], int]:
    pages = [p for p in _find_pages(document, "最低资本表") if "行次" in "\n".join(_page_lines(document, p - 1))]
    if not pages:
        return [], 0
    page = pages[0]
    rows = _trim_capital_rows(_parse_capital_page(document, page), "4", "最低资本")
    return [list(_CAPITAL_HEADER), *rows], page


def _parse_three_year(document) -> tuple[list[list[str]], int]:
    pages = _find_pages(document, "近三年平均投资收益率")
    if not pages:
        return [], 0
    page = pages[0]
    lines = _page_lines(document, page - 1)
    rows: list[list[str]] = [list(_THREE_YEAR_HEADER)]
    try:
        start = lines.index("项目")
    except ValueError:  # pragma: no cover
        return [], page
    current: list[str] | None = None
    for line in lines[start + 1 :]:
        tokens = _value_tokens(line)
        if tokens is not None:
            if current is not None and len(current) == 1:
                current.extend(tokens)
            continue
        if line == "本季度":
            continue
        current = [line]
        rows.append(current)
    rows = [row for row in rows if len(row) > 1]
    return rows, page


def _parse_backtrace(document) -> tuple[list[list[str]], int]:
    pages = _find_pages(document, "经营活动净现金流回溯不利偏差率")
    if not pages:
        return [], 0
    page = pages[0]
    lines = _page_lines(document, page - 1)
    rows: list[list[str]] = []
    current: list[str] | None = None
    allowed_prefixes = ("1.", "2.1")
    for line in lines:
        if line.startswith("2.3 净现金流汇总表") or line.startswith("2.4"):
            break
        tokens = _value_tokens(line)
        if tokens is not None:
            if current is not None:
                current.extend(tokens)
            continue
        if not line.startswith(allowed_prefixes):
            current = None
            continue
        current = [line]
        rows.append(current)
    rows = [row for row in rows if len(row) > 1]
    return rows, page


def _parse_monitor(document) -> tuple[list[list[str]], int]:
    pages = _find_pages(document, "指标值", "监测指标")
    if not pages:
        return [], 0
    page = pages[0]
    lines = _page_lines(document, page - 1)
    rows: list[list[str]] = []
    current: list[str] | None = None
    for line in lines:
        if re.match(r"^[一二三四五六七八九十]+、", line):
            if current is not None:
                rows.append(current)
            current = [line]
            continue
        if current is None:
            continue
        if line == "指标值":
            continue
        tokens = _value_tokens(line)
        if tokens is not None:
            if len(current) == 1:
                current.extend([tokens[0], "-"])
            continue
        if len(current) == 1:  # 长标签换行
            current[0] += line
    if current is not None:
        rows.append(current)
    # 一、经营活动净现金流为全年累计值，与本表 2.2 回溯表的本季度口径冲突，
    # 统一采用回溯表 2.1 的季度值，避免同一指标同期间出现两个不同数值。
    rows = [row for row in rows if not row[0].startswith("一、")]
    return [row for row in rows if len(row) > 1], page


def _set_rows(table, rows: list[list[str]], pages: Sequence[int], unit: str = "") -> None:
    table.rows = rows
    page = int(pages[0]) if pages else int(getattr(table, "page", 1) or 1)
    table.page = page
    table.source_pages = [int(p) for p in pages]
    table.table_blocks = []
    table.whole_page_block = False
    if unit:
        table.unit = unit
        table.block_unit = unit


@register("sanxing")
def postprocess(tables, *, pdf_bytes):
    if not pdf_bytes:
        return tables
    document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    liquidity = None
    try:
        for table in tables:
            table_id = str(getattr(table, "table_id", "") or "")
            try:
                if table_id == "SOLVENCY_MAIN":
                    rows, page = _parse_main(document)
                    if rows:
                        _set_rows(table, rows, [page], "元")
                elif table_id == "NON_LIFE_OPERATING_METRICS":
                    rows, page = _parse_operating(document)
                    if rows:
                        _set_rows(table, rows, [page], "元")
                elif table_id == "ACTUAL_CAPITAL":
                    rows, blocks = _parse_actual_capital(document)
                    if rows:
                        if len(blocks) > 1:
                            table.rows = rows
                            table.page = blocks[0][0]
                            table.source_pages = [page for page, _ in blocks]
                            table.table_blocks = [
                                {"page": page, "rows": [list(_CAPITAL_HEADER), *data]}
                                for page, data in blocks
                            ]
                            table.whole_page_block = True
                            table.unit = "元"
                            table.block_unit = "元"
                        else:
                            _set_rows(table, rows, [block_page for block_page, _ in blocks], "元")
                elif table_id == "MINIMUM_CAPITAL":
                    rows, page = _parse_minimum_capital(document)
                    if rows:
                        _set_rows(table, rows, [page], "元")
                elif table_id == "THREE_YEAR_INVESTMENT_RETURN":
                    rows, page = _parse_three_year(document)
                    if rows:
                        _set_rows(table, rows, [page], "%")
                elif table_id == "LIQUIDITY_MONITORING":
                    if liquidity is None:
                        back_rows, back_page = _parse_backtrace(document)
                        monitor_rows, monitor_page = _parse_monitor(document)
                        liquidity = {
                            "rows": [list(_LIQUIDITY_HEADER), *back_rows, *monitor_rows],
                            "back": back_page,
                            "monitor": monitor_page,
                            "kept": False,
                        }
                    page = int(getattr(table, "page", 0) or 0)
                    pages_set = {page} | set(getattr(table, "source_pages", []) or [])
                    keep = False
                    if liquidity["monitor"] and liquidity["monitor"] in pages_set:
                        keep = True
                    elif liquidity["back"] and liquidity["back"] in pages_set and not liquidity["kept"]:
                        keep = True
                        liquidity["kept"] = True
                    pages = [p for p in (liquidity["back"], liquidity["monitor"]) if p]
                    if keep and len(liquidity["rows"]) > 1:
                        _set_rows(table, liquidity["rows"], pages)
                    else:
                        # 同页的覆盖率明细/净现金流汇总等表与监测表不同源，清空避免重复记录。
                        table.rows = []
                        table.table_blocks = []
            except Exception:  # 单表失败不影响其它表，保留通用提取结果
                continue
    finally:
        document.close()
    return tables

# Explicit company extraction hooks.
def locate_company_tables(pdf_bytes, table_configs=()):
    from ._company_hooks import locate_for_company
    return locate_for_company("sanxing", pdf_bytes, table_configs)


def extract_company_tables(pdf_bytes, table_configs):
    from ._company_hooks import extract_for_company
    return extract_for_company(
        "sanxing",
        pdf_bytes,
        table_configs,
        mode="legacy",
    )
