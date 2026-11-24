"""北部湾财产专属提取后处理（2022–2025 版式）。

通用规则无法覆盖的缺口（四份年报同构）：

1. ``主要经营指标``：pdfplumber 只识别到“1.综合成本率”为止，效益类/规模类
   （费用率、赔付率、签单保费、前五大险种、渠道保费等）没有表格线，
   整段缺失；这里按 PDF 文本行重建“指标名称 + 本季度数 + 本年度累计数”。
2. ``流动性风险监管指标`` 与 ``流动性风险监测指标`` 同页相邻，pdfplumber
   把两张表合并，并把右列金额/百分比按列宽劈开（``-6,76 | 9.09``）；
   这里按文本行重建，并把 ``其中：LCR1/2/3`` 还原为标准名称
   ``LCR{1,2,3}未来{3,12}个月``（原标签是“基本情景下公司整体流动性覆盖率”，
   与指标库顺序相反，子串匹配失败）。
3. ``近三年（综合）投资收益率``：2024 年只有 2 行，却把第 14 页整页流动性表
   与第 15 页整页经营表一起当作该表内容（59 行）。标准化阶段对表名含
   “近三年”的表强制期间口径“近三年平均”，于是经营指标被记成
   “近三年平均/万元”（金额单位还是元）。这里只保留两行收益率。
4. ``S05 最低资本表`` 末尾紧接“保险风险各子类风险及巨灾风险最低资本表”，
   需在“4 最低资本”后截断；跨页还会插入 ``【第N页表格分块】`` 伪行。
5. 2022 年偿付能力主表无表头（直接从“认可资产（万元）”开始），2023/2024
   又把“上季度数/下季度预测数”挤成一格；四份报告统一写回标准 4 列表头。
6. 2025 年流动性监管表在上一页（第 12 页，与主表同页），定位器只锚到监测页，
   这里把前一页补进 ``source_pages``；同页经营表带“单位：元”，会把万元值
   再除 10000，重建后把流动性表单位固定为万元。
7. 2025 年经营表同页后接 S01 与“风险管理能力”段落，遇到这些标记即停止。

约定：只改 ``rows`` 与 ``table_blocks``，保持幂等；任何异常都不能阻断通用流程。
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from . import register

# 金额/百分比/占位符。允许“106,17.5”这类千分位，也允许纯小数。
_NUMBER_RE = re.compile(
    r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?%?|-?\d+(?:\.\d+)?%?"
)
_PAGE_NO_RE = re.compile(r"^\d{1,3}$")
_GROUP_TITLE_RE = re.compile(
    r"^(?:[一二三四五六七八九十百]+、|（[一二三四五六七八九十百]+）)\s*"
    r"(?:主要)?(?:经营|效益|规模|品质)类?指标$"
)
# “流动性覆盖率-未来 3 个月”这类分节行（数值列是占位符），记录后续 LCR 属哪一档。
_LCR_WINDOW_RE = re.compile(r"流动性覆盖率\s*[-－—]?\s*未来\s*(\d+)\s*个月")
_LCR_TAG_RE = re.compile(r"^[\s其中：:]*(LCR\s*[123])", re.IGNORECASE)
_SKIP_LABEL_TERMS = ("指标名称", "公司名称", "单位：", "单位:", "项目")
_PLACEHOLDERS = {"--", "-", "—", "–", "－", "不适用", "未披露"}

_OPERATING_HEADER = ["指标名称", "本季度数", "本年度累计数"]
_LIQUIDITY_HEADER = ["指标名称", "本季度数", "上季度数"]
_THREE_YEAR_HEADER = ["指标名称", "近三年平均"]
_SOLVENCY_MAIN_HEADER = ["指标名称", "本季度数", "上季度数", "下季度预测数"]


def _clean(value: Any) -> str:
    return str(value or "").replace("\u3000", " ").strip()


def _is_number_token(token: str) -> bool:
    token = token.strip()
    if not token:
        return False
    if token in _PLACEHOLDERS:
        return True
    return bool(_NUMBER_RE.fullmatch(token))


def _split_values(line: str) -> tuple[str, list[str]]:
    """把一行拆成“标签 + 行末连续数值/占位符”。

    标签本身可能以编号开头（``1.综合成本率``、``（一）保险业务收入``），
    所以只从行末往前吃 token，遇到第一个非数值即停。
    """
    tokens = _clean(line).split()
    values: list[str] = []
    while tokens and _is_number_token(tokens[-1]):
        values.insert(0, tokens.pop())
    return " ".join(tokens).strip(), values


def _doc_texts(pdf_bytes: bytes) -> list[str]:
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return [doc.load_page(index).get_text() or "" for index in range(len(doc))]
    finally:
        doc.close()


def _page_lines(texts: Sequence[str], pages: Sequence[int]) -> list[str]:
    lines: list[str] = []
    for page in pages:
        try:
            text = texts[int(page) - 1]
        except (IndexError, TypeError, ValueError):
            continue
        lines.extend(
            _clean(line) for line in text.splitlines() if _clean(line)
        )
    return lines


def _sync(table: Any, rows: list[list[str]], page: int) -> None:
    """rows 与 table_blocks 必须同步，否则 STEP3 展开表块仍读旧值。"""
    table.rows = rows
    table.table_blocks = [
        {"page": int(page or 1), "rows": [list(row) for row in rows]}
    ]


def _drop_markers(rows: Sequence[Sequence[str]]) -> list[list[str]]:
    output: list[list[str]] = []
    for row in rows:
        cells = [_clean(cell) for cell in row]
        if any("表格分块" in cell for cell in cells):
            continue
        output.append(cells)
    return output


def _ensure_main_header(rows: Sequence[Sequence[str]]) -> list[list[str]]:
    """统一偿付能力主表的表头。

    2022 年完全没有表头；2023/2024 年的表头被挤成
    “指标名称 | 本季度数 | 上季度数 下季度预测数 | 空”，导致“上季度末数”
    整列取不到值。四份报告的数据都是 3 个期间列，这里直接写回标准表头。
    """
    cleaned = _drop_markers(rows)
    if not cleaned:
        return cleaned
    first = cleaned[0][0] if cleaned[0] else ""
    if first.startswith(("指标名称", "项目")):
        cleaned[0] = list(_SOLVENCY_MAIN_HEADER)
    else:
        cleaned.insert(0, list(_SOLVENCY_MAIN_HEADER))
    return cleaned


def _row_label(cells: Sequence[str]) -> str:
    """S05 行有“行次/项目”两列；取项目列作为标签。"""
    if len(cells) > 1 and cells[0].isdigit():
        return cells[1].replace(" ", "")
    return (cells[0] if cells else "").replace(" ", "")


_SUBTABLE_START_LABELS = {"保费风险最低资本", "准备金风险最低资本"}


def _trim_minimum(rows: Sequence[Sequence[str]]) -> list[list[str]]:
    """在“4 最低资本”后截断，并去掉紧随其后的保险风险子表。

    跨页时子表可能单独成块（块内没有合计行），因此再以
    “保费风险最低资本/准备金风险最低资本”作为兜底停止点。
    """
    output: list[list[str]] = []
    for row in rows:
        cells = [_clean(cell) for cell in row]
        if any("表格分块" in cell for cell in cells):
            continue
        label = _row_label(cells)
        if label in _SUBTABLE_START_LABELS:
            break
        output.append(cells)
        if label in {"4最低资本", "最低资本"}:
            break
    return output


def _iter_rows(
    lines: Sequence[str],
    *,
    skip_terms: Sequence[str],
    stop_terms: Sequence[str] = (),
) -> list[list[str]]:
    """按 fitz 的“单元格逐行输出”版式重建表格行。

    fitz 把每个单元格单独放一行，因此结构固定为：
    ``标签行`` + ``数值行``（多数 2 个，效益类只有 1 个）。
    标签偶尔被换行拆成两行（如“签单”/“保费”），此时把第二行并回标签。
    """
    rows: list[list[str]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if stop_terms and any(term in line for term in stop_terms):
            break
        if (
            _PAGE_NO_RE.match(line)
            or _is_number_token(line)
            or any(term in line for term in skip_terms)
        ):
            index += 1
            continue
        label = line
        values: list[str] = []
        cursor = index + 1
        while cursor < len(lines):
            following = lines[cursor]
            if _is_number_token(following):
                values.append(following)
                cursor += 1
                if len(values) >= 2:
                    break
                continue
            if not values:
                label = f"{label}{following}"
                cursor += 1
                continue
            break
        if label:
            rows.append([label, *values[:2]])
        index = cursor
    return rows


def _parse_operating(lines: Sequence[str]) -> list[list[str]]:
    """重建整张经营指标表（标签 + 本季度数 + 本年度累计数）。"""
    # 2025 年经营表与流动性监测表同在第 13 页，必须从“主要经营指标”
    # 标题之后的第一张表头开始，否则会把流动性表当成经营表。
    heading = 0
    for position, line in enumerate(lines):
        if "主要经营指标" in line:
            heading = position
            break
    start = heading
    for position in range(heading, len(lines)):
        if lines[position].startswith("指标名称"):
            start = position + 1
            break
    rows = _iter_rows(
        lines[start:],
        skip_terms=("指标名称", "本季度数", "本年度累计数", "公司名称", "单位：", "单位:"),
        # 2025 年第 14 页经营表后面直接接 S01 偿付能力状况表和风险管理
        # 能力段落，越界会把这些行当成经营指标。
        stop_terms=("偿付能力状况表", "S01", "风险管理能力"),
    )
    return [row for row in rows if not _GROUP_TITLE_RE.match(row[0])]


def _parse_three_year(lines: Sequence[str]) -> list[list[str]]:
    """只保留“近三年平均（综合）投资收益率”两行。

    fitz 版式：标签一行、百分比一行；2024 年两行分别落在第 14 页尾和第 15 页首。
    """
    rows: list[list[str]] = []
    seen: set[str] = set()
    for index, line in enumerate(lines):
        if not line.startswith("近三年平均") or "投资收益率" not in line:
            continue
        label, values = _split_values(line)
        if not values and index + 1 < len(lines):
            follow_label, follow_values = _split_values(lines[index + 1])
            if follow_values and not follow_label:
                values = follow_values
        if not values or label in seen:
            continue
        seen.add(label)
        rows.append([label, values[-1]])
    return rows


def _parse_liquidity(lines: Sequence[str]) -> list[list[str]]:
    """重建流动性监管 + 监测两张表（同页相邻，fitz 逐单元格输出）。

    分节行“流动性覆盖率-未来3 个月/12 个月”决定后续 LCR 标签的期间，
    这里把“其中：LCR1（基本情景下…）”还原成指标库的
    “LCR1未来3个月”等名称，否则子串匹配失败会落到未映射。
    """
    rows: list[list[str]] = []
    start = 0
    for position, line in enumerate(lines):
        if "流动性风险监管指标" in line:
            start = position + 1
            break
    header = _iter_rows(
        lines[start:],
        skip_terms=(
            "指标名称",
            "本季度数",
            "上季度数",
            "公司名称",
            "单位：",
            "单位:",
            "流动性风险监管指标",
            "流动性风险监测指标",
            "表（",
        ),
        stop_terms=("近三年", "主要经营指标"),
    )
    window = ""
    for label, *values in header:
        window_match = _LCR_WINDOW_RE.search(label)
        if window_match:
            window = window_match.group(1)
            continue
        tag = _LCR_TAG_RE.match(label)
        if tag:
            number = re.sub(r"\D", "", tag.group(1))
            if window:
                label = f"LCR{number}未来{window}个月"
            else:
                label = f"LCR{number}"
        rows.append([label, *values[:2]])
    return rows


@register("beibuwan")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """北部湾财产专属后处理入口；异常由调用方捕获。"""
    texts = _doc_texts(pdf_bytes)
    for table in tables:
        table_id = str(getattr(table, "table_id", "") or "")
        pages = [
            int(page)
            for page in (getattr(table, "source_pages", None) or [])
            if str(page).strip().isdigit()
        ] or [int(getattr(table, "page", 1) or 1)]
        first_page = pages[0]

        if table_id == "SOLVENCY_MAIN":
            rows = _ensure_main_header(list(table.rows or []))
            table.rows = rows
            for block in list(getattr(table, "table_blocks", None) or []):
                if isinstance(block, dict) and block.get("rows"):
                    block["rows"] = _ensure_main_header(block["rows"])

        elif table_id == "NON_LIFE_OPERATING_METRICS":
            parsed = _parse_operating(_page_lines(texts, pages))
            if parsed:
                _sync(table, [_OPERATING_HEADER, *parsed], first_page)

        elif table_id == "LIQUIDITY_MONITORING":
            # 2025 年“监管指标”在上一页（第 12 页）、“监测指标”在第 13 页，
            # 定位器只锚到监测页；这里把上一页补进来，四份报告统一处理。
            joined = "\n".join(
                texts[page - 1] for page in pages if 0 < page <= len(texts)
            )
            if "流动性风险监管指标" not in joined and first_page > 1:
                pages = [first_page - 1, *pages]
                first_page = pages[0]
            parsed = _parse_liquidity(_page_lines(texts, pages))
            if parsed:
                _sync(table, [_LIQUIDITY_HEADER, *parsed], first_page)
                # 流动性表金额单位是万元；2025 年页面能嗅探到“单位：元”
                # （来自同页经营表），会把这些万元值再除 10000。
                table.unit = "万元"
                table.block_unit = ""

        elif table_id == "THREE_YEAR_INVESTMENT_RETURN":
            parsed = _parse_three_year(_page_lines(texts, pages))
            if parsed:
                _sync(table, [_THREE_YEAR_HEADER, *parsed], first_page)

        elif table_id == "MINIMUM_CAPITAL":
            table.rows = _trim_minimum(list(table.rows or []))
            for block in list(getattr(table, "table_blocks", None) or []):
                if isinstance(block, dict) and block.get("rows"):
                    block["rows"] = _trim_minimum(block["rows"])

        elif table_id == "ACTUAL_CAPITAL":
            rows = _drop_markers(list(table.rows or []))
            table.rows = rows
            for block in list(getattr(table, "table_blocks", None) or []):
                if isinstance(block, dict) and block.get("rows"):
                    block["rows"] = _drop_markers(block["rows"])
    return tables

# Explicit company extraction hooks.
def locate_company_tables(pdf_bytes, table_configs=()):
    from ._company_hooks import locate_for_company
    return locate_for_company("beibuwan", pdf_bytes, table_configs)


def extract_company_tables(pdf_bytes, table_configs):
    from ._company_hooks import extract_for_company
    return extract_for_company(
        "beibuwan",
        pdf_bytes,
        table_configs,
        mode="document",
    )
