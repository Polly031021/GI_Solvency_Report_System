"""华安财险专属提取后处理：重建最低资本表、流动性监测表与近三年收益率表。

华安财险 2022–2025 年偿付能力报告版式稳定：偿付能力主表、主要经营指标、
实际资本表已由公司级 YAML 规则（start_item/end_item 裁剪）完整提取，无需
本模块干预。通用逻辑仍会带偏三张表：

1. 最低资本表（2022 p40 / 2023 p39 / 2024 p39 / 2025 p37）：表头只有
   “行次/期末数/期初数”，PDF 文本层逐单元格一行，个别标签（如
   “……境外固定收益类资产价格风险”+“最低资本”）折行。2022 年定位器先命
   中第 33 页管理层分析“与上季度末预测对比”小表（含“最低资本（万元）”
   行），且该年没有产出表对象。
2. 流动性风险监测指标（2022 p20-21 / 2023 p20-21 / 2024 p21-22 /
   2025 p16-17）：2022/2023/2025 与监管指标同页，2024 合并成
   “类别/序号/项目”一张表（类别列竖排成碎行、脚注标记单独成行）；
   通用定位统一取成偿付能力主表。
3. 近三年（综合）投资收益率：2023 起以两行标签 + 两个百分数出现，
   且与主要经营指标同页，通用提取把经营表当成了该表；2022 年报告确无
   此节，不做补建。

专属逻辑只重写 ``table.rows`` / ``table.table_blocks`` / ``table.source_pages``
（必要时用 FlexibleTable 补建缺失表对象），数据全部从 PDF 文本按标题与
行标签重新解析，不写死年份和页码，重复执行结果一致。
"""

from __future__ import annotations

import re
from typing import Any, Sequence

import fitz

from ..batch_extraction import FlexibleTable
from . import register

_NUMBER_RE = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?$")
_PAGE_NO_RE = re.compile(r"^\d{1,3}$")
_PERCENT_RE = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%$")
_SPACE_RE = re.compile(r"[\s\u3000]+")
_SERIAL_RE = re.compile(r"^\d{1,2}(?![\d.%])")
_DASHES = {"-", "—", "–", "--", "－", "−"}

_MIN_HEADER = ["指标名称", "期末数", "期初数"]
_THREE_YEAR_HEADER = ["指标名称", "本年累计"]

# 最低资本表行次：1 / 1.1 / 1.2.1 / 1* 等
_CAPITAL_CODE_RE = re.compile(r"^\d+(?:\.\d+)*(?:\*)?$")
# 只认章节标题（“（二）非寿险业务保费风险……”“（三）巨灾风险最低资本”），
# 不能用“巨灾风险最低资本”等词做停止词——它同时是表内行标签的前缀
_CAPITAL_STOP_TERMS = ("十二、", "最低资本表（续）")

# 流动性风险监测指标（10 项，标签按 PDF 原文；靠“标签 + （单位）”精确匹配，
# 避免把监管表里的“经营活动净现金流回溯不利偏差率RDR（%）”误认成监测指标）
_MONITOR_LABELS = (
    "经营活动净现金流",
    "百元保费经营活动净现金流",
    "特定业务现金流支出占比",
    "规模保费同比增速",
    "现金及流动性管理工具占比",
    "季均融资杠杆比例",
    "AA级（含）以下境内固定收益类资产占比",
    "持股比例大于5%的上市股票投资占比",
    "应收款项占比",
    "持有关联方资产占比",
)

_MONITOR_HEADINGS = (
    "（三）流动性风险监测指标",
    "(三)流动性风险监测指标",
    "（二）流动性风险监管指标和流动性风险监测指标",
    "(二)流动性风险监管指标和流动性风险监测指标",
)


def _page_lines(doc: Any, page: int) -> list[str]:
    """取一页文本行：去空行、去页首页码。"""
    raw = [line.strip() for line in doc[page - 1].get_text("text").splitlines()]
    lines = [line for line in raw if line]
    cleaned: list[str] = []
    for line in lines:
        if not cleaned and _PAGE_NO_RE.fullmatch(line):
            continue
        cleaned.append(line)
    return cleaned


def _compact(value: Any) -> str:
    return _SPACE_RE.sub("", str(value or "")).strip()


def _is_value(value: Any) -> bool:
    text = str(value or "").strip()
    if text in _DASHES:
        return True
    return bool(_NUMBER_RE.fullmatch(text))


def _find_page(doc: Any, required: Sequence[str]) -> int | None:
    for page in range(1, doc.page_count + 1):
        text = _compact(doc[page - 1].get_text("text"))
        if all(term in text for term in required):
            return page
    return None


def _sync(table: Any, rows: list[list[str]], blocks: list[dict[str, Any]]) -> None:
    table.rows = [list(row) for row in rows]
    table.table_blocks = [
        {"page": int(block["page"]), "rows": [list(row) for row in block["rows"]]}
        for block in blocks
    ]
    pages = [int(block["page"]) for block in blocks if block.get("rows")]
    if pages:
        table.source_pages = sorted(set(pages))
        table.page = pages[0]


# ---------------------------------------------------------------- 最低资本表


def _minimum_page(doc: Any) -> int | None:
    for page in range(1, doc.page_count + 1):
        text = _compact(doc[page - 1].get_text("text"))
        if "行次" in text and "期末数" in text and "偿付能力最低资本" in text:
            return page
    return None


def _is_capital_stop(line: str) -> bool:
    text = _compact(line)
    if text.startswith(("（二）", "(二)", "（三）", "(三)")):
        return True
    return any(term in text for term in _CAPITAL_STOP_TERMS)


def _parse_capital_rows(lines: Sequence[str]) -> list[list[str]]:
    rows: list[list[str]] = []
    index = 0
    total = len(lines)
    while index < total:
        line = lines[index]
        if _is_capital_stop(line):
            break
        if not _CAPITAL_CODE_RE.fullmatch(_compact(line)):
            index += 1
            continue
        index += 1
        parts: list[str] = []
        while index < total:
            current = lines[index]
            if _is_capital_stop(current):
                break
            if _is_value(current) or _CAPITAL_CODE_RE.fullmatch(_compact(current)):
                break
            parts.append(current)
            index += 1
        values: list[str] = []
        while index < total and len(values) < 2 and _is_value(lines[index]):
            values.append(lines[index].strip())
            index += 1
        label = _compact("".join(parts))
        if label and len(values) == 2:
            rows.append([label, values[0], values[1]])
    return rows


def _capital_rows(doc: Any) -> tuple[int | None, list[list[str]]]:
    page = _minimum_page(doc)
    if page is None:
        return None, []
    rows = _parse_capital_rows(_page_lines(doc, page))
    labels = {row[0] for row in rows}
    if "偿付能力最低资本" not in labels or len(rows) < 10:
        return page, []
    return page, [_MIN_HEADER, *rows]


def _rebuild_minimum(doc: Any, table: Any) -> None:
    page, rows = _capital_rows(doc)
    if page is None or not rows:
        return
    _sync(table, rows, [{"page": page, "rows": rows}])
    table.unit = "万元"
    table.strategy = "专属提取·华安·最低资本表文本重建"


def _append_minimum(doc: Any, tables: list[Any]) -> None:
    if any(str(getattr(t, "table_id", "") or "") == "MINIMUM_CAPITAL" for t in tables):
        return
    page, rows = _capital_rows(doc)
    if page is None or not rows:
        return
    tables.append(
        FlexibleTable(
            table_id="MINIMUM_CAPITAL",
            table_name="最低资本表",
            page=page,
            source_pages=[page],
            rows=rows,
            table_blocks=[{"page": page, "rows": [list(row) for row in rows]}],
            table_config={
                "table_id": "MINIMUM_CAPITAL",
                "table_name": "最低资本表",
                "canonical_headers": list(_MIN_HEADER),
            },
            unit="万元",
            strategy="专属提取·华安·补建最低资本表",
            quality_score=1.0,
            evidence="定位器命中管理层分析页且未产出表对象，由专属提取器从“（一）最低资本表”文本补建",
        )
    )


# ---------------------------------------------------------------- 流动性监测表


def _monitor_label(line: str) -> str | None:
    text = _compact(line)
    text = _SERIAL_RE.sub("", text)
    for label in _MONITOR_LABELS:
        if not text.startswith(label):
            continue
        rest = text[len(label) :]
        if not rest or rest[0] in "（(":
            return label
    return None


def _monitor_header(lines: Sequence[str], start: int = 0) -> list[str]:
    # 只看监测小节内部：2024 起监管指标与监测指标并排同页，整页搜索会误取
    # 偿付能力主表表头的“上季度可比数”。
    window = [_compact(line) for line in lines[start : start + 10]]
    label_col = "指标名称" if "指标名称" in window else "项目"
    first = "本季度数" if "本季度数" in window else "本季度末数"
    second = ""
    for candidate in ("上季度数", "上季度可比数", "上季度末数"):
        if candidate in window:
            second = candidate
            break
    return [label_col, first, second or "上季度数"]


def _monitor_section_start(lines: Sequence[str]) -> int | None:
    compacted = [_compact(line) for line in lines]
    for idx, text in enumerate(compacted):
        if any(text.startswith(heading) for heading in _MONITOR_HEADINGS):
            return idx
    return None


def _monitor_stop(lines: Sequence[str], idx: int) -> bool:
    text = _compact(lines[idx])
    if "主要经营指标" in text or "近三年" in text:
        return True
    return text.startswith(("（四）", "(四)", "（五）", "(五)"))


def _parse_monitor_page(lines: Sequence[str], start: int = 0) -> list[list[str]]:
    rows: list[list[str]] = []
    index = start
    total = len(lines)
    while index < total:
        if _monitor_stop(lines, index):
            break
        label = _monitor_label(lines[index])
        if label is None:
            index += 1
            continue
        values: list[str] = []
        cursor = index + 1
        while cursor < total and len(values) < 4 and _is_value(lines[cursor]):
            values.append(lines[cursor].strip())
            cursor += 1
        if len(values) >= 3 and _SERIAL_RE.match(values[0]) and len(values[0]) == 1:
            values = values[1:]  # 脚注标记（如“经营活动净现金流（万元）”后的“2”）
        if len(values) >= 2:
            rows.append([label, values[0], values[1]])
        index = cursor if cursor > index else index + 1
    return rows


def _liquidity_pages(doc: Any) -> list[int]:
    for page in range(1, doc.page_count + 1):
        lines = _page_lines(doc, page)
        if _monitor_section_start(lines) is None:
            continue
        pages = [page]
        next_page = page + 1
        if page < doc.page_count:
            next_lines = _page_lines(doc, next_page)
            head = [_compact(line) for line in next_lines[:10]]
            if any(any(text.startswith(_compact(label)) for label in _MONITOR_LABELS) for text in head):
                pages.append(next_page)
        return pages
    return []


def _rebuild_liquidity(doc: Any, table: Any) -> None:
    pages = _liquidity_pages(doc)
    if not pages:
        return
    header: list[str] | None = None
    blocks: list[dict[str, Any]] = []
    total_rows: list[list[str]] = []
    for order, page in enumerate(pages):
        lines = _page_lines(doc, page)
        start = _monitor_section_start(lines)
        if start is None:
            start = 0
        if header is None:
            header = _monitor_header(lines, start)
        rows = _parse_monitor_page(lines, start)
        if not rows:
            continue
        block_rows = [list(header)] + rows if order == 0 else rows
        blocks.append({"page": page, "rows": block_rows})
        total_rows.extend(rows)
    if len(total_rows) < 5 or header is None:
        return
    _sync(table, [header, *total_rows], blocks)
    table.strategy = "专属提取·华安·流动性监测指标文本重建"


# ------------------------------------------------------------ 近三年投资收益率


def _three_year_page(doc: Any) -> int | None:
    for page in range(1, doc.page_count + 1):
        text = _compact(doc[page - 1].get_text("text"))
        if "近三年平均投资收益率" in text and "近三年平均综合投资收益率" in text:
            return page
    return None


def _parse_three_year(lines: Sequence[str]) -> list[list[str]] | None:
    start = next(
        (
            i
            for i, line in enumerate(lines)
            if "近三年（综合）投资收益率" in line
            or "近三年(综合)投资收益率" in line
            or _compact(line).startswith("近三年平均投资收益率")
        ),
        None,
    )
    if start is None:
        return None
    body = list(lines[start:])
    positions: list[tuple[str, int]] = []
    for label in ("近三年平均投资收益率", "近三年平均综合投资收益率"):
        index = next(
            (i for i, line in enumerate(body) if _compact(line).startswith(label)),
            None,
        )
        if index is None:
            return None
        positions.append((label, index))
    positions.sort(key=lambda item: item[1])
    numbered: list[tuple[int, str]] = []
    for index, line in enumerate(body):
        text = _compact(line)
        if _PERCENT_RE.fullmatch(text):
            numbered.append((index, text))
    # 标签与数值分行：两个百分数依次出现在“近三年平均投资收益率”之后
    values = [value for index, value in numbered if index > positions[0][1]]
    if len(values) < 2:
        return None
    records = [
        [f"{positions[0][0]}（%）", values[0]],
        [f"{positions[1][0]}（%）", values[1]],
    ]
    return [list(_THREE_YEAR_HEADER), *records]


def _rebuild_three_year(doc: Any, table: Any) -> None:
    page = _three_year_page(doc)
    if page is None:
        return
    rows = _parse_three_year(_page_lines(doc, page))
    if not rows:
        return
    _sync(table, rows, [{"page": page, "rows": rows}])
    table.unit = "%"
    table.strategy = "专属提取·华安·近三年收益率文本重建"


def _append_three_year(doc: Any, tables: list[Any]) -> None:
    if any(
        str(getattr(t, "table_id", "") or "") == "THREE_YEAR_INVESTMENT_RETURN"
        for t in tables
    ):
        return
    page = _three_year_page(doc)
    if page is None:
        return  # 2022 年报告确无此节
    rows = _parse_three_year(_page_lines(doc, page))
    if not rows:
        return
    tables.append(
        FlexibleTable(
            table_id="THREE_YEAR_INVESTMENT_RETURN",
            table_name="近三年（综合）投资收益率",
            page=page,
            source_pages=[page],
            rows=rows,
            table_blocks=[{"page": page, "rows": [list(row) for row in rows]}],
            table_config={
                "table_id": "THREE_YEAR_INVESTMENT_RETURN",
                "table_name": "近三年（综合）投资收益率",
                "canonical_headers": list(_THREE_YEAR_HEADER),
            },
            unit="%",
            strategy="专属提取·华安·补建近三年收益率",
            quality_score=1.0,
            evidence="定位器命中同页经营表，由专属提取器从“近三年（综合）投资收益率”文本补建",
        )
    )


@register("huaan")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """华安财险专属后处理入口；异常由调用方捕获。"""
    if not tables or not pdf_bytes:
        return tables
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        updated = list(tables)
        for table in updated:
            table_id = str(getattr(table, "table_id", "") or "")
            if table_id == "MINIMUM_CAPITAL":
                _rebuild_minimum(doc, table)
            elif table_id == "LIQUIDITY_MONITORING":
                _rebuild_liquidity(doc, table)
            elif table_id == "THREE_YEAR_INVESTMENT_RETURN":
                _rebuild_three_year(doc, table)
        _append_minimum(doc, updated)
        _append_three_year(doc, updated)
    return updated
