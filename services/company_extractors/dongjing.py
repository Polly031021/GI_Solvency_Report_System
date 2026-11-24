"""东京海上（东京海上日动火灾保险（中国））专属提取后处理（2022-2025）。

PDF 文本层是“每个单元格一行”的竖排结构，pdfplumber 网格识别会：

1. 2022/2024 偿付能力充足率主表跨页（2022 第 9/10 页、2024 第 9/10 页），
   续页无表头，通用提取把金额并进标签或只留零散行；
2. 主要经营指标 2023/2024 只识别前 13 行，效益类/规模类指标全部丢失；
3. 最低资本表只切出表头后 3-4 行，最低资本合计缺失；
4. 2022 流动性表与 LCR 表同页混排（28 行），期间口径错配。

本后处理器按页从文本层重建 4 张表，保留原文项目名称与数值，再交给
通用标准化流程（指标匹配、期间归一、单位换算）。

行结构（均带表头，便于 Step3 识别期间）：

* SOLVENCY_MAIN                [指标名称, 本季度数, 上季度数, 下季度预测数]
* NON_LIFE_OPERATING_METRICS   [指标名称, 本季度数, 本年度累计数]
* MINIMUM_CAPITAL              [行次, 项目, 期末数, 期初数]
* LIQUIDITY_MONITORING         [指标名称, 本季度数, 上季度数]

实际资本明细表（2022-2025 均为 31 行、单页完整）与近三年投资收益率表由
通用提取直接得到，不在本模块重建，仅补齐收益率表的单位。
"""

from __future__ import annotations

import re
from typing import Sequence

from . import register

try:  # 与仓库其它模块一致：优先 pymupdf，兼容旧 fitz 包名
    import pymupdf  # type: ignore
except ImportError:  # pragma: no cover
    import fitz as pymupdf  # type: ignore

_NUMBER_RE = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?$")
_ROW_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)*\*?$")
_MISSING = {"-", "--", "—", "–", "－", "/"}
_SECTION_RE = re.compile(r"^(?:（[一二三四五六七八九十]{1,3}）|[一二三四五六七八九十]{1,3}、)")
_LIQUIDITY_ROW_RE = re.compile(r"^[一二三四五六七八九十]{1,3}、")

_MAIN_HEADER = ["指标名称", "本季度数", "上季度数", "下季度预测数"]
_OPERATING_HEADER = ["指标名称", "本季度数", "本年度累计数"]
_CAPITAL_HEADER = ["行次", "项目", "期末数", "期初数"]
_LIQUIDITY_HEADER = ["指标名称", "本季度数", "上季度数"]


def _page_lines(document, page_index: int) -> list[str]:
    text = document.load_page(page_index).get_text() or ""
    return [line.strip() for line in text.splitlines() if line.strip()]


def _find_pages(document, *needles: str) -> list[int]:
    hits: list[int] = []
    for index in range(len(document)):
        text = document.load_page(index).get_text() or ""
        if all(needle in text for needle in needles):
            hits.append(index + 1)
    return hits


def _value_tokens(line: str) -> list[str] | None:
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
    """行次单元格：纯编号行，且随后能遇到中文项目名称。"""
    line = lines[index].strip()
    if not _ROW_NUMBER_RE.match(line):
        return False
    for follow in lines[index + 1 : index + 4]:
        if _NUMBER_RE.match(follow) or follow in _MISSING:
            continue
        return bool(re.search(r"[\u4e00-\u9fff]", follow))
    return False


def _strip_page_number(lines: list[str], page: int) -> list[str]:
    if lines and lines[0] == str(page):
        return lines[1:]
    return lines


def _trim(rows: list[list[str]], code: str, label: str) -> list[list[str]]:
    trimmed: list[list[str]] = []
    for row in rows:
        trimmed.append(row)
        if row[0].strip() == code and label in (row[1] if len(row) > 1 else ""):
            break
    return trimmed


def _parse_main(document) -> tuple[list[list[str]], int]:
    pages = _find_pages(document, "偿付能力充足率指标", "本季度数")
    if not pages:
        return [], 0
    first = pages[0]
    rows: list[list[str]] = []
    current: list[str] | None = None
    joined: list[list[str]] = [list(_MAIN_HEADER)]
    page = first
    while page <= len(document) and page <= first + 2:
        lines = _strip_page_number(_page_lines(document, page - 1), page)
        index = lines.index("行次") + 1 if "行次" in lines else 0
        for i in range(index, len(lines)):
            line = lines[i]
            if _SECTION_RE.match(line) and rows:
                break
            if line in _MAIN_HEADER:
                continue
            if _is_code_line(lines, i):
                current = [line, ""]
                rows.append(current)
                continue
            if current is None:
                continue
            tokens = _value_tokens(line)
            if tokens is not None:
                current.extend(tokens)
            else:
                current[1] += line
        if any(len(row) > 1 and "综合偿付能力充足率" in row[1] for row in rows):
            break
        page += 1
    joined.extend([row[1], *row[2:]] for row in rows if len(row) > 2)
    return joined, first


def _parse_operating(document) -> tuple[list[list[str]], int]:
    pages = _find_pages(document, "主要经营指标", "本年累计数")
    if not pages:
        return [], 0
    first = pages[0]
    rows: list[list[str]] = []
    current: list[str] | None = None
    page = first
    while page <= len(document) and page <= first + 2:
        lines = _strip_page_number(_page_lines(document, page - 1), page)
        # 2022/2025 第 10-11 页先有 LCR 表（表头同为“指标名称”），必须按
        # “指标名称 + 本季度数 + 本年累计数”三连定位经营表自身表头。
        index = 0
        for i, line in enumerate(lines):
            if line == "指标名称" and "本季度数" in lines[i + 1 : i + 4] and "本年累计数" in lines[i + 1 : i + 4]:
                index = i + 1
                break
        for i in range(index, len(lines)):
            line = lines[i]
            if _is_code_line(lines, i):
                continue
            # 经营表行标签本身带“（一）…（十二）”序号，只有“五、”这类
            # 顶级章节标题才是下界；“效益类/规模类指标”是分组行，不是指标。
            if re.match(r"^[一二三四五六七八九十]{1,3}、", line) and rows:
                break
            if re.match(r"^（[一二三四五六七八九十]{1,3}）(?:效益|规模|品质)类指标$", line):
                current = None
                continue
            if line in ("指标名称", "本季度数", "本年累计数", "行次", "四、主要经营指标"):
                current = None
                continue
            tokens = _value_tokens(line)
            if tokens is not None:
                if current is not None:
                    current.extend(tokens)
                continue
            current = [line]
            rows.append(current)
        if any(len(row) > 1 and "其他渠道签单保费" in row[0] for row in rows):
            break
        page += 1
    joined = [list(_OPERATING_HEADER), *[row for row in rows if len(row) > 1]]
    return joined, first


def _parse_minimum(document) -> tuple[list[list[str]], int]:
    pages = [
        p
        for p in _find_pages(document, "最低资本表")
        if "行次" in _page_lines(document, p - 1)
    ]
    if not pages:
        return [], 0
    page = pages[0]
    lines = _strip_page_number(_page_lines(document, page - 1), page)
    index = lines.index("行次") + 1
    while index < len(lines) and lines[index] in _CAPITAL_HEADER:
        index += 1
    rows: list[list[str]] = []
    current: list[str] | None = None
    for i in range(index, len(lines)):
        line = lines[i]
        if _SECTION_RE.match(line) and rows:
            break
        if _is_code_line(lines, i):
            if current is not None:
                rows.append(current)
            current = [line, ""]
            continue
        if current is None:
            continue
        tokens = _value_tokens(line)
        if tokens is not None:
            current.extend(tokens)
        else:
            current[1] += line
    if current is not None:
        rows.append(current)
    rows = _trim(rows, "4", "最低资本")
    return [list(_CAPITAL_HEADER), *[row for row in rows if len(row) > 2]], page


def _parse_liquidity(document) -> tuple[list[list[str]], int]:
    for page in range(1, len(document) + 1):
        lines = _strip_page_number(_page_lines(document, page - 1), page)
        if "监测指标" not in lines or "本季度" not in lines or "上季度" not in lines:
            continue
        index = lines.index("监测指标") + 1
        while index < len(lines) and lines[index] in ("本季度", "上季度", "本季度数", "上季度数"):
            index += 1
        rows: list[list[str]] = []
        current: list[str] | None = None
        for line in lines[index:]:
            if _SECTION_RE.match(line) and not _LIQUIDITY_ROW_RE.match(line):
                break
            tokens = _value_tokens(line)
            if tokens is not None:
                if current is not None:
                    current.extend(tokens)
                continue
            if _LIQUIDITY_ROW_RE.match(line):
                current = [line]
                rows.append(current)
            elif current is None:
                continue
            else:
                current[0] += line
        rows = [row for row in rows if len(row) > 1]
        if rows:
            return [list(_LIQUIDITY_HEADER), *rows], page
    return [], 0


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


@register("dongjing")
def postprocess(tables, *, pdf_bytes):
    if not pdf_bytes:
        return tables
    document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        for table in tables:
            table_id = str(getattr(table, "table_id", "") or "")
            try:
                if table_id == "SOLVENCY_MAIN":
                    rows, page = _parse_main(document)
                    if len(rows) > 1:
                        _set_rows(table, rows, [page], "元")
                elif table_id == "NON_LIFE_OPERATING_METRICS":
                    rows, page = _parse_operating(document)
                    if len(rows) > 1:
                        _set_rows(table, rows, [page], "元")
                elif table_id == "MINIMUM_CAPITAL":
                    rows, page = _parse_minimum(document)
                    if len(rows) > 1:
                        _set_rows(table, rows, [page], "元")
                elif table_id == "LIQUIDITY_MONITORING":
                    rows, page = _parse_liquidity(document)
                    if len(rows) > 1:
                        _set_rows(table, rows, [page], "元")
                elif table_id == "THREE_YEAR_INVESTMENT_RETURN":
                    if getattr(table, "rows", None):
                        table.unit = "%"
                        table.block_unit = "%"
            except Exception:  # 单表失败不影响其它表，保留通用提取结果
                continue
    finally:
        document.close()
    return tables

# Explicit company extraction hooks.
def locate_company_tables(pdf_bytes, table_configs=()):
    from ._company_hooks import locate_for_company
    return locate_for_company("dongjing", pdf_bytes, table_configs)


def extract_company_tables(pdf_bytes, table_configs):
    from ._company_hooks import extract_for_company
    return extract_for_company(
        "dongjing",
        pdf_bytes,
        table_configs,
        mode="legacy",
    )
