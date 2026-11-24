"""中国渔业互助保险社专属提取后处理（2023-2025 版式）。

通用规则的三个缺口：

1. ``SOLVENCY_MAIN``：pdfplumber 把表头拆成 ``['', '', '下季', '度预测数']``
   （2025 为 ``'下'/'季度预测数'``），标准化认不出三个期间，三列数值全部
   落到“本季度末数”并产生 RAW 标签重复行。这里只修表头，行数据保持原样。
2. ``NON_LIFE_OPERATING_METRICS``：经营指标表没有线框，pdfplumber 只识别
   同页上方 LCR 尾部的小表；2023 年“综合成本率 -1509.02%”还会被拆成
   ``-15`` 与 ``09.02%`` 两行。这里按 PDF 文本从“（三）经营指标”重建到
   “（四）近三年投资收益率指标”，跨页按块保留。
3. ``LIQUIDITY_MONITORING``：``2. 监测指标`` 同样没有线框，通用提取抓到
   的是 LCR 尾部。这里按文本重建 10 行指标。

最低资本表由 YAML 的 ``start_item/end_item: []`` 清区域后已完整（35 行），
实际资本表/近三年收益率表通用提取正确，本模块不干预。

只改 ``table.rows`` / ``table.table_blocks`` / ``table.source_pages``，
不写死年份和页码，重复执行结果一致。
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from . import register

_DASHES = {"-", "—", "–", "--", "－", "−"}
_NUMBER_RE = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?$")
_PAGE_NO_RE = re.compile(r"^\d{1,3}$")
_SPACE_RE = re.compile(r"[\s\u3000]+")

_OP_HEADER = ["指标名称", "本季度数", "本年度累计数"]
_LIQ_HEADER = ["指标名称", "本季度数", "上季度数"]
_SOLVENCY_HEADER = ["项目", "本季度末数", "上季度末数", "下季度末预测数"]

_OP_GROUPS = {"主要经营指标", "效益类指标", "规模类指标"}
_OP_SKIP = {
    "指标名称",
    "本季度数",
    "上季度数",
    "本年度累计数",
    "本年累计数",
    "单位：元",
    "单位:元",
    "单位：万元",
    "单位:万元",
}
_LIQ_SKIP = {
    "指标名称",
    "本季度数",
    "上季度数",
    "单位：元",
    "单位:元",
    "单位：%",
    "单位:%",
}
_OP_END_PREFIXES = ("（四）", "(四)")
_LIQ_END_PREFIXES = ("（三）", "(三)")


def _compact(value: Any) -> str:
    return _SPACE_RE.sub("", str(value or "")).strip()


def _clean(value: Any) -> str:
    return str(value or "").replace("\u3000", " ").strip()


def _is_number(text: str) -> bool:
    return bool(_NUMBER_RE.fullmatch(text))


def _is_value(text: str) -> bool:
    """指标值：数字、百分数、破折号占位或“不适用”。

    2024 年“手续费及佣金占比”的累计值在文本层粘连成 ``0.00%-``，
    先剥掉尾部占位符再判断，否则整行会被当成只有 1 个数值而跳过。
    """
    if text in _DASHES or text == "不适用":
        return True
    if text.endswith("-") and _is_number(text[:-1]):
        return True
    return _is_number(text)


def _normalize_value(text: str) -> str:
    """去掉 PDF 尾部粘连的占位符，如 2024 年的 ``0.00%-``。"""
    cleaned = _clean(text)
    if cleaned.endswith("-") and _is_number(cleaned[:-1]):
        cleaned = cleaned[:-1]
    return cleaned


def _page_lines(doc: Any, page_no: int) -> list[str]:
    """取一页文本行：去空行、去页首页码。"""
    raw = [line.strip() for line in doc[page_no - 1].get_text("text").splitlines()]
    lines = [line for line in raw if line]
    if lines and _PAGE_NO_RE.fullmatch(lines[0]):
        lines = lines[1:]
    return lines


def _sync(
    table: Any,
    flat_rows: list[list[str]],
    blocks: list[dict[str, Any]],
) -> None:
    table.rows = [list(row) for row in flat_rows]
    table.table_blocks = [
        {"page": int(block["page"]), "rows": [list(row) for row in block["rows"]]}
        for block in blocks
        if block.get("rows")
    ]
    pages = sorted({int(block["page"]) for block in blocks if block.get("rows")})
    if pages:
        table.source_pages = pages
        table.page = pages[0]


# ---------------------------------------------------------------- 偿付能力主表


def _fix_solvency_header(table: Any) -> None:
    rows = [list(row) for row in (getattr(table, "rows", None) or [])]
    if not rows:
        return
    header = [_compact(cell) for cell in rows[0]]
    has_periods = (
        any("本季度" in cell for cell in header)
        and any("上季度" in cell for cell in header)
        and any("预测" in cell for cell in header)
    )
    if has_periods:
        return
    width = max(len(row) for row in rows)
    fixed = list(_SOLVENCY_HEADER[:width])
    fixed.extend([""] * (width - len(fixed)))
    rows[0] = fixed
    table.rows = rows
    for block in list(getattr(table, "table_blocks", None) or []):
        if not isinstance(block, dict) or not block.get("rows"):
            continue
        block_rows = [list(row) for row in block["rows"]]
        block_rows[0] = list(fixed)
        block["rows"] = block_rows


# ---------------------------------------------------------------- 经营指标表


def _parse_operating_lines(lines: Sequence[str]) -> list[list[str]]:
    rows: list[list[str]] = []
    index = 0
    total = len(lines)
    while index < total:
        text = _compact(lines[index])
        if text.startswith(_OP_END_PREFIXES):
            break
        if not text or text in _OP_SKIP or text in _OP_GROUPS or text.startswith(_LIQ_END_PREFIXES):
            index += 1
            continue
        if _is_value(text):
            # 页码、孤立的占位符或上一分组残值。
            index += 1
            continue
        values: list[str] = []
        cursor = index + 1
        while cursor < total and len(values) < 2 and _is_value(_compact(lines[cursor])):
            values.append(_normalize_value(lines[cursor]))
            cursor += 1
        if len(values) == 2:
            rows.append([_clean(lines[index]), values[0], values[1]])
            index = cursor
        else:
            index += 1
    return rows


def _operating_rows_for_page(lines: Sequence[str], first: bool) -> list[list[str]]:
    start = 0
    if first:
        for index, line in enumerate(lines):
            text = _compact(line)
            if text.startswith("（三）") or text.startswith("(三)"):
                if "经营指标" in text:
                    start = index + 1
                    break
    return _parse_operating_lines(lines[start:])


def _rebuild_operating(table: Any, doc: Any) -> None:
    pages = sorted({int(page) for page in (getattr(table, "source_pages", None) or [])})
    pages = [page for page in pages if 1 <= page <= doc.page_count]
    if not pages:
        return
    blocks: list[dict[str, Any]] = []
    flat: list[list[str]] = []
    for order, page_no in enumerate(pages):
        rows = _operating_rows_for_page(_page_lines(doc, page_no), first=order == 0)
        if not rows:
            continue
        blocks.append({"page": page_no, "rows": [list(_OP_HEADER), *[list(row) for row in rows]]})
        if not flat:
            flat.append(list(_OP_HEADER))
        flat.extend(list(row) for row in rows)
    if len(flat) <= 1:
        return
    _sync(table, flat, blocks)
    table.strategy = "专属提取·中国渔业互助·经营指标文本重建"


# ------------------------------------------------------------ 流动性监测指标


def _parse_monitoring_lines(lines: Sequence[str]) -> list[list[str]]:
    rows: list[list[str]] = []
    index = 0
    total = len(lines)
    while index < total:
        text = _compact(lines[index])
        if text.startswith(_LIQ_END_PREFIXES):
            break
        if not text or text in _LIQ_SKIP:
            index += 1
            continue
        if _is_value(text):
            index += 1
            continue
        values: list[str] = []
        cursor = index + 1
        while cursor < total and len(values) < 2 and _is_value(_compact(lines[cursor])):
            values.append(_normalize_value(lines[cursor]))
            cursor += 1
        if len(values) == 2:
            rows.append([_clean(lines[index]), values[0], values[1]])
            index = cursor
        else:
            index += 1
    return rows


def _rebuild_monitoring(table: Any, doc: Any) -> None:
    pages = sorted({int(page) for page in (getattr(table, "source_pages", None) or [])})
    pages = [page for page in pages if 1 <= page <= doc.page_count]
    if not pages:
        return
    blocks: list[dict[str, Any]] = []
    flat: list[list[str]] = []
    for page_no in pages:
        lines = _page_lines(doc, page_no)
        start = 0
        for index, line in enumerate(lines):
            text = _compact(line)
            if re.match(r"^2[.．、]?监测指标", text):
                start = index + 1
                break
        rows = _parse_monitoring_lines(lines[start:])
        if not rows:
            continue
        blocks.append({"page": page_no, "rows": [list(_LIQ_HEADER), *[list(row) for row in rows]]})
        if not flat:
            flat.append(list(_LIQ_HEADER))
        flat.extend(list(row) for row in rows)
    if len(flat) <= 1:
        return
    _sync(table, flat, blocks)
    table.strategy = "专属提取·中国渔业互助·流动性监测指标文本重建"


@register("yuye")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """中国渔业互助专属后处理入口。异常由调用方捕获，不在这里吞掉。"""
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for table in tables:
            table_id = str(getattr(table, "table_id", "") or "")
            if table_id == "SOLVENCY_MAIN":
                _fix_solvency_header(table)
            elif table_id in {"NON_LIFE_OPERATING_METRICS", "OPERATING_METRICS"}:
                _rebuild_operating(table, doc)
            elif table_id == "LIQUIDITY_MONITORING":
                _rebuild_monitoring(table, doc)
    finally:
        doc.close()
    return tables

# Explicit company extraction hooks.
def locate_company_tables(pdf_bytes, table_configs=()):
    from ._company_hooks import locate_for_company
    return locate_for_company("yuye", pdf_bytes, table_configs)


def extract_company_tables(pdf_bytes, table_configs):
    from ._company_hooks import extract_for_company
    return extract_for_company(
        "yuye",
        pdf_bytes,
        table_configs,
        mode="document",
    )
