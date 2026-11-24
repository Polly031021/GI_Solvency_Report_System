"""东海航运专属提取后处理：重建实际资本、最低资本与近三年收益率表。

东海航运 2022–2025 四期偿付能力报告的版式稳定，但通用规则在三处
会丢行或串表：

1. 实际资本表（S02）跨两页，第二页无表头，且标签可能跨页续表；
   通用页内解析只取到续页片段，前页主表缺失。
2. 最低资本表（S05）跨两页，第二页无表头；2025 年第 39 页没有
   “最低资本”之外的终止锚点，定位器在“最低资本”值行提前截断，
   漏掉“附加资本/最低资本”合计。
3. 近三年（综合）投资收益率在 2024 年分列第 18/19 页，通用提取
   把第 18 页整张经营表当作该表。

专属逻辑只重写 ``table.rows`` 与 ``table.table_blocks``，数据全部从
PDF 文本重新解析，按标题定位，不写死年份和页码；重复执行结果一致。
"""

from __future__ import annotations

import re
from typing import Any, Sequence

import fitz

from . import register

_CODE_RE = re.compile(r"^\d+(?:\.\d+){0,2}\*?$")
# 金额固定两位小数；"-"/"—" 表示空值，<不适用> 表示不适用。
_VALUE_RE = re.compile(
    r"^(?:-?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}%?|-|—|–|<不适用>)$"
)
_PERCENT_RE = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%$")
_CODE_LABEL_RE = re.compile(r"^(\d+(?:\.\d+){0,2}\*?)\s+(\S.*)$")
_PAGE_NO_RE = re.compile(r"^\d{1,3}$")

_NUMBERED_HEADER = ["行次", "项目", "期末数", "期初数"]
_THREE_YEAR_HEADER = ["指标名称", "本年累计数"]
_HEADER_LINES = {
    "行次",
    "项目",
    "期末数",
    "期初数",
    "本季度末",
    "上季度末",
    "单位：元",
}
_THREE_YEAR_ITEMS = ("近三年平均投资收益率", "近三年平均综合投资收益率")


def _clean(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _page_lines(doc: Any, page_no: int) -> list[str]:
    """取一页文本行，去掉页首页码与空行。"""
    raw = doc[page_no - 1].get_text("text").splitlines()
    lines = [line.strip() for line in raw if line.strip()]
    if lines and _PAGE_NO_RE.fullmatch(lines[0]):
        lines = lines[1:]
    return lines


def _find_title_page(doc: Any, title: str) -> int | None:
    """按精确标题定位表格起始页；标题在整份报告中唯一。"""
    for page_no in range(1, doc.page_count + 1):
        if any(line.strip() == title for line in doc[page_no - 1].get_text("text").splitlines()):
            return page_no
    return None


def _parse_numbered(doc: Any, start: int, max_pages: int = 2) -> list[tuple[int, list[str]]]:
    """连续解析“行次/项目/期末数/期初数”版式。

    值判断必须在行次判断之前：``0.00`` 既是合法金额也能匹配行次正则，
    先判值可避免把零值当行次、导致该行金额丢失。
    """
    parsed: list[tuple[int, list[str]]] = []
    state: dict[str, Any] = {"code": None, "label": [], "values": [], "page": start}

    def flush() -> None:
        code = state["code"]
        if code is None:
            return
        label = _clean("".join(state["label"]))
        if label:
            values = list(state["values"])[:2]
            values += [""] * (2 - len(values))
            parsed.append(
                (int(state["page"]), [str(code), label, values[0], values[1]])
            )
        state["code"] = None
        state["label"] = []
        state["values"] = []

    for page_no in range(start, min(doc.page_count, start + max_pages - 1) + 1):
        for line in _page_lines(doc, page_no):
            text = line.strip()
            if not text:
                continue
            if text in _HEADER_LINES or text.startswith("单位："):
                continue
            has_row = state["code"] is not None
            if has_row and len(state["values"]) < 2 and _VALUE_RE.fullmatch(text):
                state["values"].append(text)
                state["page"] = page_no
                continue
            if _CODE_RE.fullmatch(text):
                flush()
                state["code"] = text
                state["page"] = page_no
                continue
            combined = _CODE_LABEL_RE.fullmatch(text)
            if combined:
                flush()
                state["code"] = combined.group(1)
                state["label"] = [combined.group(2)]
                state["page"] = page_no
                continue
            if has_row and not state["values"]:
                state["label"].append(text)
    flush()
    return parsed


def _rebuild_numbered(table: Any, doc: Any, title: str, strategy: str) -> None:
    start = _find_title_page(doc, title)
    if start is None:
        return
    parsed = _parse_numbered(doc, start)
    # 正常年份 30 行以上；解析失败时保留通用结果，避免把表清空。
    if len(parsed) < 10:
        return

    blocks: list[dict[str, Any]] = []
    current_page: int | None = None
    for page_no, row in parsed:
        if page_no != current_page:
            rows: list[list[str]] = []
            if not blocks:
                rows.append(list(_NUMBERED_HEADER))
            blocks.append({"page": page_no, "rows": rows})
            current_page = page_no
        blocks[-1]["rows"].append(list(row))

    table.rows = [list(_NUMBERED_HEADER)] + [list(row) for _, row in parsed]
    table.table_blocks = blocks
    table.unit = "元"
    table.strategy = strategy


def _parse_three_year(doc: Any, start: int) -> list[list[str]]:
    """从起始页及下一页读取两条近三年平均收益率。"""
    found: dict[str, str] = {}
    for page_no in range(start, min(doc.page_count, start + 1) + 1):
        lines = _page_lines(doc, page_no)
        for index, line in enumerate(lines):
            label = line.strip()
            if label not in _THREE_YEAR_ITEMS or label in found:
                continue
            for candidate in lines[index + 1 : index + 3]:
                value = candidate.strip()
                if _PERCENT_RE.fullmatch(value):
                    found[label] = value
                    break
    if len(found) < 2:
        return []
    return [[label, found[label]] for label in _THREE_YEAR_ITEMS]


def _rebuild_three_year(table: Any, doc: Any) -> None:
    start = _find_title_page(doc, "近三年平均投资收益率")
    if start is None:
        return
    rows = _parse_three_year(doc, start)
    if not rows:
        return
    blocks: list[dict[str, Any]] = []
    for page_no in range(start, min(doc.page_count, start + 1) + 1):
        page_rows = [
            list(row)
            for row in rows
            if any(
                row[0] == line.strip()
                for line in doc[page_no - 1].get_text("text").splitlines()
            )
        ]
        if page_rows:
            if not blocks:
                page_rows = [list(_THREE_YEAR_HEADER)] + page_rows
            blocks.append({"page": page_no, "rows": page_rows})
    table.rows = [list(_THREE_YEAR_HEADER)] + [list(row) for row in rows]
    table.table_blocks = blocks
    table.unit = "%"
    table.strategy = "专属提取·东海·近三年投资收益率"


def _repair_main_header(table: Any) -> None:
    """2023 年主表首列表头丢失“行次”，补齐以免数值列整体错位。"""
    rows = getattr(table, "rows", None) or []
    if not rows:
        return
    header = rows[0]
    if len(header) >= 5 and not _clean(header[0]) and _clean(header[1]) == "项目":
        header[0] = "行次"
    blocks = getattr(table, "table_blocks", None) or []
    if blocks:
        first = blocks[0].get("rows") if isinstance(blocks[0], dict) else None
        if first and len(first[0]) >= 5 and not _clean(first[0][0]) and _clean(first[0][1]) == "项目":
            first[0][0] = "行次"


@register("donghai")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """东海航运专属后处理入口。异常由调用方捕获，不在这里吞掉。"""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for table in tables:
            table_id = str(getattr(table, "table_id", "") or "")
            if table_id == "SOLVENCY_MAIN":
                _repair_main_header(table)
            elif table_id == "ACTUAL_CAPITAL":
                _rebuild_numbered(
                    table, doc, "实际资本表", "专属提取·东海·实际资本表"
                )
            elif table_id == "MINIMUM_CAPITAL":
                _rebuild_numbered(
                    table, doc, "最低资本表", "专属提取·东海·最低资本表"
                )
            elif table_id == "THREE_YEAR_INVESTMENT_RETURN":
                _rebuild_three_year(table, doc)
    finally:
        doc.close()
    return tables

# Explicit company extraction hooks.
def locate_company_tables(pdf_bytes, table_configs=()):
    from ._company_hooks import locate_for_company
    return locate_for_company("donghai", pdf_bytes, table_configs)


def extract_company_tables(pdf_bytes, table_configs):
    from ._company_hooks import extract_for_company
    return extract_for_company(
        "donghai",
        pdf_bytes,
        table_configs,
        mode="legacy",
    )
