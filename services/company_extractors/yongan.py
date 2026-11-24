# -*- coding: utf-8 -*-
"""永安财险（永安财产保险股份有限公司）专属提取后处理。

永安 2022-2025 四份季报的版式高度一致，通用规则在以下五张表上失配：

1. ``NON_LIFE_OPERATING_METRICS``：整页表格被页内分组标题
   （“四、主要经营指标”“（十一）效益类指标”等）顶掉表头，只识别到
   前 13 行；本提取器按正文重建整表（30/29 行）。
2. ``ACTUAL_CAPITAL``（S02）：报表跨两页，通用只取首页；本提取器把
   两页行次拼接后按“行次 → 多行标签 → 期末数 → 期初数”重建，
   保留行次列，供标准化阶段消解“减：超限额应扣除的部分”等同名行。
3. ``MINIMUM_CAPITAL``（S05）：同样跨两页，且第二页 3.x/4 行缺失；
   重建后 2022 年 33 行、2023-2025 年 34 行（多“量化风险最低资本
   （未考虑特征系数前）”）。
4. ``LIQUIDITY_MONITORING``：2023-2025 与“（四）近三年（综合）投资
   收益率”同页，整页模式只取到近三年两行；按小节标题裁切重建 10 行。
5. ``THREE_YEAR_INVESTMENT_RETURN``：非线框表，正文两行百分比；按
   正文正则重建。2022 年报告确无此表，不补建。

约定：只改 ``table.rows`` / ``table.table_blocks`` / ``table.unit`` /
``table.source_pages``，不改 table_id，不进入标准化，不吞异常。
"""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any, Sequence

from . import register

ROW_NO_RE = re.compile(r"^\d+(?:\.\d+)*(?:\*)?$")
VALUE_RE = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?$")
PLACEHOLDERS = {"-", "--", "—", "–", "－"}
TOP_HEADING_RE = re.compile(r"^[一二三四五六七八九十百]+、")
GROUP_HEADING_RE = re.compile(
    r"^[（(][一二三四五六七八九十百]+[）)].*(?:类指标|指标)$"
)
SHEET_TITLE_RE = re.compile(r"^S0\d-")

_OPER_HEADER = ["指标名称", "本季度数", "本年度累计数"]
_LEDGER_HEADER = ["行次", "项目", "期末数", "期初数"]
_LIQ_HEADER = ["指标名称", "本季度数", "上季度数"]
_THREE_HEADER = ["项目", "数值"]


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def _clean(value: Any) -> str:
    return str(value or "").replace("\u3000", " ").strip()


def _is_value(text: str) -> bool:
    if not text:
        return False
    return text in PLACEHOLDERS or bool(VALUE_RE.fullmatch(text.replace(" ", "")))


def _is_placeholder(text: str) -> bool:
    return text in PLACEHOLDERS


def _is_top_heading(text: str) -> bool:
    return bool(text) and bool(TOP_HEADING_RE.match(text))


def _is_group_heading(text: str) -> bool:
    return bool(text) and bool(GROUP_HEADING_RE.match(text))


def _join_label(parts: Sequence[str]) -> str:
    text = "".join(part for part in parts if part)
    return re.sub(r"\s+", " ", text).strip()


def _table_pages(table: Any) -> list[int]:
    pages: list[int] = []
    for value in list(getattr(table, "source_pages", None) or []) + [
        getattr(table, "page", None)
    ]:
        try:
            page = int(value)
        except (TypeError, ValueError):
            continue
        if page > 0 and page not in pages:
            pages.append(page)
    return pages


def _find_page(
    pages_lines: Sequence[Sequence[str]], terms: Sequence[str]
) -> int | None:
    for index, lines in enumerate(pages_lines, start=1):
        joined = "\n".join(lines)
        if all(term in joined for term in terms):
            return index
    return None


def _sync(
    table: Any,
    header: Sequence[str],
    rows: Sequence[tuple[int, list[str]]],
    unit: str,
) -> None:
    """rows 与 table_blocks 同步；按物理页切块，块内重复表头。"""
    grouped: "OrderedDict[int, list[list[str]]]" = OrderedDict()
    for page, row in rows:
        grouped.setdefault(int(page), []).append(list(row))
    if not grouped:
        return
    table.rows = [list(header), *[list(cells) for _, cells in rows]]
    table.table_blocks = [
        {"page": page, "rows": [list(header), *page_rows]}
        for page, page_rows in grouped.items()
    ]
    table.source_pages = list(grouped)
    table.page = next(iter(grouped))
    table.unit = unit
    table.unit_source = unit


# --------------------------------------------------------------------------- #
# S02/S05 行次表：行次 →（可多行）标签 → 两个单元格
# --------------------------------------------------------------------------- #
def _valid_row_start(lines: Sequence[str], index: int) -> bool:
    text = lines[index]
    if not ROW_NO_RE.fullmatch(text):
        return False
    label_index = index + 1
    if label_index >= len(lines):
        return False
    label = lines[label_index]
    if (
        not label
        or ROW_NO_RE.fullmatch(label)
        or _is_value(label)
        or _is_top_heading(label)
    ):
        return False
    # 2025 年 S02 首页把“S02-实际资本表 / 公司名称 / 单位 / 表头 / 首行标签”
    # 识别成候选行次时，标签会拼成一整串；这类标题噪声不是数据行。
    if (
        SHEET_TITLE_RE.search(label)
        or "公司名称" in label
        or "单位：" in label
        or label in {"行次", "项目", "期末数", "期初数"}
    ):
        return False
    cells = 0
    cursor = label_index + 1
    while cursor < len(lines) and cursor <= label_index + 12:
        current = lines[cursor]
        if current == "" or _is_value(current):
            cells += 1
        elif ROW_NO_RE.fullmatch(current):
            break
        cursor += 1
        if cells >= 2:
            break
    return cells >= 2


def _find_row_start(lines: Sequence[str], start: int = 0) -> int | None:
    for index in range(max(0, start), len(lines)):
        if _valid_row_start(lines, index):
            return index
    return None


def _parse_ledger(
    lines: Sequence[str],
    page_of: Sequence[int],
    start: int,
    terminal_label: str,
) -> list[tuple[int, list[str]]]:
    rows: list[tuple[int, list[str]]] = []
    index = start
    total = len(lines)
    while index < total:
        if not _valid_row_start(lines, index):
            index += 1
            continue
        page = int(page_of[index])
        row_no = lines[index]
        label_parts: list[str] = []
        cells: list[str] = []
        index += 1
        while index < total and len(cells) < 2:
            current = lines[index]
            if current == "" or _is_value(current):
                cells.append("" if _is_placeholder(current) else current)
                index += 1
            else:
                label_parts.append(current)
                index += 1
        if len(cells) < 2:
            break
        label = _join_label(label_parts)
        rows.append((page, [row_no, label, cells[0], cells[1]]))
        if label == terminal_label:
            break
    return rows


def _continuation_pages(
    pages_lines: Sequence[Sequence[str]], start: int, terminal_label: str
) -> list[int]:
    pages = [start]
    if any(line == terminal_label for line in pages_lines[start - 1]):
        return pages
    for page in range(start + 1, min(len(pages_lines), start + 3) + 1):
        lines = pages_lines[page - 1]
        if any(SHEET_TITLE_RE.match(line) for line in lines) and not any(
            line == terminal_label for line in lines
        ):
            break
        pages.append(page)
        if any(line == terminal_label for line in lines):
            break
    return pages


def _rebuild_ledger(
    pages_lines: Sequence[Sequence[str]],
    preferred: Sequence[int],
    *,
    title_term: str,
    marker_term: str,
    terminal_label: str,
) -> list[tuple[int, list[str]]]:
    start = _find_page(pages_lines, (title_term, marker_term))
    if start is None and preferred:
        start = preferred[0]
    if start is None:
        return []
    span = _continuation_pages(pages_lines, start, terminal_label)
    flat: list[str] = []
    page_of: list[int] = []
    for page in span:
        for line in pages_lines[page - 1]:
            flat.append(line)
            page_of.append(page)
    # 从表头“期初数”之后开始找数据行：避免把首页页码/报表标题当成行次。
    scan_from = 0
    for index, line in enumerate(flat):
        if "期初数" in line:
            scan_from = index + 1
            break
    row_start = _find_row_start(flat, scan_from)
    if row_start is None:
        row_start = _find_row_start(flat)
    if row_start is None:
        return []
    return _parse_ledger(flat, page_of, row_start, terminal_label)


# --------------------------------------------------------------------------- #
# 经营指标：无行次，标签（可多行）+ 两个单元格
# --------------------------------------------------------------------------- #
def _rebuild_operating(
    pages_lines: Sequence[Sequence[str]], preferred: Sequence[int]
) -> list[tuple[int, list[str]]]:
    page = _find_page(
        pages_lines, ("主要经营指标", "保险业务收入", "本年度累计数")
    )
    if page is None and preferred:
        page = preferred[0]
    if page is None:
        return []
    lines = pages_lines[page - 1]
    header_end = None
    for index, line in enumerate(lines):
        if line == "本年度累计数":
            header_end = index + 1
            break
    if header_end is None:
        return []

    rows: list[tuple[int, list[str]]] = []
    index = header_end
    total = len(lines)
    while index < total:
        current = lines[index]
        if not current:
            index += 1
            continue
        if _is_placeholder(current) or _is_top_heading(current):
            index += 1
            continue
        if _is_group_heading(current):
            index += 1
            continue
        label_parts: list[str] = []
        while index < total and not _is_value(lines[index]):
            current = lines[index]
            if not current:
                index += 1
                continue
            if _is_group_heading(current) or _is_top_heading(current):
                break
            label_parts.append(current)
            index += 1
        if index >= total or not _is_value(lines[index]):
            continue
        cells: list[str] = []
        while index < total and len(cells) < 2 and (
            lines[index] == "" or _is_value(lines[index])
        ):
            current = lines[index]
            cells.append("" if _is_placeholder(current) else current)
            index += 1
        if len(cells) < 2 or not label_parts:
            continue
        label = _join_label(label_parts)
        if label.endswith("类指标") or label in PLACEHOLDERS:
            continue
        rows.append((page, [label, cells[0], cells[1]]))
    return rows


# --------------------------------------------------------------------------- #
# 流动性风险监测指标
# --------------------------------------------------------------------------- #
def _rebuild_liquidity(
    pages_lines: Sequence[Sequence[str]], preferred: Sequence[int]
) -> list[tuple[int, list[str]]]:
    page = _find_page(
        pages_lines, ("流动性风险监测指标", "经营活动净现金流")
    )
    if page is None and preferred:
        page = preferred[0]
    if page is None:
        return []
    lines = pages_lines[page - 1]
    header_end = None
    for index, line in enumerate(lines):
        if line == "上季度数":
            header_end = index + 1
            break
    if header_end is None:
        return []

    rows: list[tuple[int, list[str]]] = []
    index = header_end
    total = len(lines)
    while index < total:
        current = lines[index]
        if not current:
            index += 1
            continue
        if current.startswith("（四）") or _is_top_heading(current):
            break
        label_parts: list[str] = []
        while index < total and not _is_value(lines[index]):
            current = lines[index]
            if not current:
                index += 1
                continue
            if current.startswith("（四）") or _is_top_heading(current):
                break
            label_parts.append(current)
            index += 1
        if index >= total or not _is_value(lines[index]):
            break
        cells: list[str] = []
        while index < total and len(cells) < 2 and (
            lines[index] == "" or _is_value(lines[index])
        ):
            current = lines[index]
            cells.append("" if _is_placeholder(current) else current)
            index += 1
        if len(cells) < 2 or not label_parts:
            break
        rows.append((page, [_join_label(label_parts), cells[0], cells[1]]))
    return rows


# --------------------------------------------------------------------------- #
# 近三年（综合）投资收益率
# --------------------------------------------------------------------------- #
_THREE_YEAR_RE = re.compile(
    r"近三年平均(?P<kind>综合)?投资收益率"
    r"\s*[为:：]?\s*"
    r"(?P<value>-?\d+(?:\.\d+)?%)"
)


def _rebuild_three_year(
    pages_lines: Sequence[Sequence[str]],
) -> list[tuple[int, list[str]]]:
    rows: list[tuple[int, list[str]]] = []
    seen: set[str] = set()
    for page, lines in enumerate(pages_lines, start=1):
        text = "\n".join(lines)
        for match in _THREE_YEAR_RE.finditer(text):
            label = "近三年平均" + (
                "综合" if match.group("kind") else ""
            ) + "投资收益率"
            if label in seen:
                continue
            seen.add(label)
            rows.append((page, [label, match.group("value")]))
    return rows


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
@register("yongan")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """永安专属后处理入口；异常交给调用方记录，不在这里吞掉。"""
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        pages_lines = [
            [_clean(line) for line in (doc.load_page(index).get_text() or "").splitlines()]
            for index in range(len(doc))
        ]
        for table in tables:
            table_id = str(getattr(table, "table_id", "") or "")
            preferred = _table_pages(table)
            if table_id in {"NON_LIFE_OPERATING_METRICS", "OPERATING_METRICS"}:
                rows = _rebuild_operating(pages_lines, preferred)
                if len(rows) >= 25:
                    _sync(table, _OPER_HEADER, rows, "元")
            elif table_id == "ACTUAL_CAPITAL":
                rows = _rebuild_ledger(
                    pages_lines,
                    preferred,
                    title_term="S02-实际资本表",
                    marker_term="核心一级资本",
                    terminal_label="实际资本合计",
                )
                if len(rows) >= 20:
                    _sync(table, _LEDGER_HEADER, rows, "元")
            elif table_id == "MINIMUM_CAPITAL":
                rows = _rebuild_ledger(
                    pages_lines,
                    preferred,
                    title_term="S05-最低资本表",
                    marker_term="量化风险最低资本",
                    terminal_label="最低资本",
                )
                if len(rows) >= 20:
                    _sync(table, _LEDGER_HEADER, rows, "元")
            elif table_id == "LIQUIDITY_MONITORING":
                rows = _rebuild_liquidity(pages_lines, preferred)
                if len(rows) >= 8:
                    _sync(table, _LIQ_HEADER, rows, "元")
            elif table_id == "THREE_YEAR_INVESTMENT_RETURN":
                rows = _rebuild_three_year(pages_lines)
                if len(rows) >= 1:
                    _sync(table, _THREE_HEADER, rows, "%")
            # SOLVENCY_MAIN 通用结果已正确（4 列 / 15 行 / 万元），不重建。
    finally:
        doc.close()
    return tables


from ._document import make_standard_document_hooks

locate_company_tables, extract_company_tables = make_standard_document_hooks(
    "永安财险", postprocess
)

# Explicit company extraction hooks.
def locate_company_tables(pdf_bytes, table_configs=()):
    from ._company_hooks import locate_for_company
    return locate_for_company("yongan", pdf_bytes, table_configs)


def extract_company_tables(pdf_bytes, table_configs):
    from ._company_hooks import extract_for_company
    return extract_for_company(
        "yongan",
        pdf_bytes,
        table_configs,
        mode="document",
    )
