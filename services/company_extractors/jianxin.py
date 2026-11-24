"""建信财险专属提取后处理（2022–2025 版式）。

通用规则在四份季报上只剩两处缺口（均已用公司 YAML 修正到定位层）：

1. ``近三年（综合）投资收益率``：表格是横向两列（指标名称 / 近三年平均投资
   收益率 / 近三年平均综合投资收益率），pdfplumber 只识别到两行表头，真正的
   “指标数据 4.65% 4.11%”一行没有表格线，需按文本行重建。2022 年报告没有
   这一小节，属于披露缺失，不重建、保持空表。
2. ``流动性风险监测指标``：监管指标表（LCR1–3/回溯偏差率/净现金流）与监测
   指标表同页相邻、且监测指标跨页；pdfplumber 把两页拼成 59 行，还混入近三年
   表和整张经营表。这里按文本行只取“流动性风险监管指标”到“主要经营指标”
   之间的内容，并把 LCR 原生长标签还原为指标库名称
   ``LCR{1,2,3}未来{3,12}个月``（原标签是“基本情景下公司整体流动性覆盖率…”，
   与指标库别名顺序相反）。

经营表与 S05 最低资本表已由 YAML 的 start_item/end_item 修正，无需重建；
本模块只改 ``rows`` 与 ``table_blocks``，幂等，异常由调用方兜底。
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from . import register

# 金额/百分比/占位符。2025 年起金额为四舍五入整数，同样能识别。
_NUMBER_RE = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?%?|-?\d+(?:\.\d+)?%?")
_PAGE_NO_RE = re.compile(r"^\d{1,3}$")
_PLACEHOLDERS = {"--", "-", "—", "–", "－", "不适用", "未披露"}

_OPERATING_HEADER = ["指标名称", "本季度数", "本年度累计数"]
_LIQUIDITY_HEADER = ["指标名称", "本季度末数", "上季度末数"]
_THREE_YEAR_HEADER = ["指标名称", "近三年平均"]

# “（十一）效益类指标 / （十二）规模类指标”是分组标题，不是指标行。
_GROUP_TITLE_RE = re.compile(r"^（[一二三四五六七八九十百]+）(?:效益|规模)类指标$")
_LCR_NO_RE = re.compile(r"LCR\s*([123])", re.IGNORECASE)
_LCR_WINDOW_RE = re.compile(r"未来\s*(\d+)\s*个月")

_OPERATING_STOP_TERMS = ("偿付能力状况表", "S01", "风险管理能力", "（四）近三年")
_LIQUIDITY_STOP_TERMS = (
    "（四）",
    "近三年",
    "主要经营指标",
    "偿付能力状况表",
    "S01",
    "实际资本表",
)


def _clean(value: Any) -> str:
    return str(value or "").replace("\u3000", " ").strip()


def _is_number_token(token: str) -> bool:
    token = _clean(token)
    if not token:
        return False
    if token in _PLACEHOLDERS:
        return True
    return bool(_NUMBER_RE.fullmatch(token))


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
        lines.extend(_clean(line) for line in text.splitlines() if _clean(line))
    return lines


def _pages_with(texts: Sequence[str], term: str) -> list[int]:
    return [index + 1 for index, text in enumerate(texts) if term in text]


def _sync(table: Any, rows: list[list[str]], page: int) -> None:
    """rows 与 table_blocks 必须同步，否则 STEP3 展开表块仍读旧值。"""
    table.rows = rows
    table.table_blocks = [
        {"page": int(page or 1), "rows": [list(row) for row in rows]}
    ]
    table.page = int(page or 1)
    table.whole_page_block = True


def _iter_rows(
    lines: Sequence[str],
    *,
    skip_terms: Sequence[str] = (),
    exact_skip: Sequence[str] = (),
    stop_terms: Sequence[str] = (),
) -> list[list[str]]:
    """按 fitz 的“单元格逐行输出”版式重建表格行。

    fitz 把每个单元格单独放一行：``标签行`` + 0~2 个数值行。本报告的经营表、
    流动性表标签都不换行，因此不做“下一行并回标签”的合并（流动性表的
    “流动性覆盖率”分节行没有数值，靠 ``exact_skip`` 排除）。
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
            or line in exact_skip
            or any(term in line for term in skip_terms)
        ):
            index += 1
            continue
        label = line
        values: list[str] = []
        cursor = index + 1
        while cursor < len(lines) and len(values) < 2:
            if not _is_number_token(lines[cursor]):
                break
            values.append(lines[cursor])
            cursor += 1
        if values:
            rows.append([label, *values])
        index = cursor if values else index + 1
    return rows


def _parse_operating(lines: Sequence[str]) -> list[list[str]]:
    """重建整张经营指标表（标签 + 本季度数 + 本年度累计数）。"""
    start = 0
    for position, line in enumerate(lines):
        if "主要经营指标" in line:
            start = position + 1
            break
    rows = _iter_rows(
        lines[start:],
        skip_terms=("指标名称", "本季度数", "本年度累计数", "公司名称", "单位：", "单位:"),
        exact_skip=("（五）主要经营指标", "（四）主要经营指标"),
        stop_terms=_OPERATING_STOP_TERMS,
    )
    return [row for row in rows if not _GROUP_TITLE_RE.match(row[0])]


def _parse_three_year(lines: Sequence[str]) -> list[list[str]]:
    """只保留“近三年平均（综合）投资收益率”两行。

    fitz 版式：``指标名称`` / 两个指标标签 / ``指标数据`` / 两个百分比。
    """
    labels = [
        _clean(line)
        for line in lines
        if line.startswith("近三年平均") and "投资收益率" in line
    ]
    if not labels:
        return []
    values: list[str] = []
    for position, line in enumerate(lines):
        if _clean(line) != "指标数据":
            continue
        cursor = position + 1
        while cursor < len(lines) and len(values) < len(labels):
            if not _is_number_token(lines[cursor]):
                break
            values.append(_clean(lines[cursor]))
            cursor += 1
        break
    if len(values) != len(labels):
        return []
    return [[label, value] for label, value in zip(labels, values)]


def _parse_liquidity(lines: Sequence[str]) -> list[list[str]]:
    """重建流动性监管 + 监测两张表（同页相邻、监测指标跨页）。

    分节行“流动性覆盖率”没有数值，必须跳过；“LCR1（…）—未来3 个月”这类
    长标签还原为指标库的 ``LCR1未来3个月``，否则只能落到未映射。
    """
    start = 0
    for position, line in enumerate(lines):
        if "流动性风险监管指标" in line:
            start = position + 1
            break
    rows = _iter_rows(
        lines[start:],
        skip_terms=(
            "指标名称",
            "本季度末数",
            "上季度末数",
            "公司名称",
            "单位：",
            "单位:",
            "流动性风险监管指标",
            "流动性风险监测指标",
        ),
        exact_skip=("流动性覆盖率",),
        stop_terms=_LIQUIDITY_STOP_TERMS,
    )
    output: list[list[str]] = []
    for label, *values in rows:
        lcr_no = _LCR_NO_RE.search(label)
        window = _LCR_WINDOW_RE.search(label)
        if lcr_no and window:
            label = f"LCR{lcr_no.group(1)}未来{window.group(1)}个月"
        output.append([label, *values[:2]])
    return output


def _liquidity_pages(texts: Sequence[str]) -> list[int]:
    """从“流动性风险监管指标”所在页开始，取到含“主要经营指标”的页为止。"""
    start = None
    for index, text in enumerate(texts):
        if "流动性风险监管指标" in text:
            start = index
            break
    if start is None:
        return []
    pages = [start + 1]
    cursor = start + 1
    while cursor < len(texts):
        pages.append(cursor + 1)
        if any(
            term in texts[cursor]
            for term in ("主要经营指标", "实际资本表", "认可资产表", "最低资本表")
        ):
            break
        cursor += 1
    return pages


@register("jianxin")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """建信财险专属后处理入口；异常由调用方捕获。"""
    texts = _doc_texts(pdf_bytes)

    for table in tables:
        table_id = str(getattr(table, "table_id", "") or "")
        if table_id == "NON_LIFE_OPERATING_METRICS":
            pages = _pages_with(texts, "主要经营指标")
            if not pages:
                continue
            page = pages[0]
            parsed = _parse_operating(_page_lines(texts, [page]))
            if len(parsed) >= 10:
                _sync(table, [_OPERATING_HEADER, *parsed], page)

        elif table_id == "THREE_YEAR_INVESTMENT_RETURN":
            pages = _pages_with(texts, "近三年平均投资收益率")
            if not pages:
                # 2022 年报告没有近三年投资收益率小节，保持空表。
                continue
            page = pages[0]
            parsed = _parse_three_year(_page_lines(texts, [page]))
            if parsed:
                _sync(table, [_THREE_YEAR_HEADER, *parsed], page)

        elif table_id == "LIQUIDITY_MONITORING":
            pages = _liquidity_pages(texts)
            if not pages:
                continue
            parsed = _parse_liquidity(_page_lines(texts, pages))
            if len(parsed) >= 8:
                _sync(table, [_LIQUIDITY_HEADER, *parsed], pages[0])

    return tables

# Explicit company extraction hooks.
def locate_company_tables(pdf_bytes, table_configs=()):
    from ._company_hooks import locate_for_company
    return locate_for_company("jianxin", pdf_bytes, table_configs)


def extract_company_tables(pdf_bytes, table_configs):
    from ._company_hooks import extract_for_company
    return extract_for_company(
        "jianxin",
        pdf_bytes,
        table_configs,
        mode="document",
    )
