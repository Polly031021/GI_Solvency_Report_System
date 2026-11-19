"""珠峰财产保险专属提取后处理（2022–2025 版式）。

通用规则缺口：

1. ``MINIMUM_CAPITAL``：2024/2025 的 S05 主表完整地位于第 30/29 页，
   但 pdfplumber 只识别到表头后 3 行（量化风险、未考虑特征系数、寿险合计）；
   2022 年第 38 页只有“4 最低资本”单行，整页提取失败。这里按 PDF 文本行
   重建“行次/指标名称 + 期末数 + 期初数”表（不含行次列），四年统一。
2. ``ACTUAL_CAPITAL``：2024/2025 的 S02 表被 pdfplumber 把长标签拆成多行，
   2025 年末行还粘连成“5 实际资本合计 1 / 71,062,766.11 1”。这里按文本行
   重建“行次 + 项目 + 本季度 + 上季度”，并截到“5 实际资本合计”。
3. ``LIQUIDITY_MONITORING``：2023 年监测指标跨到第 22 页，通用规则只取到
   “九、应收款项占比”，缺应收保费/应收分保账款/期末总资产及“十、持有
   关联方资产占比”。这里按第 22 页文本补 6 行，已有“十、”时不重复补。
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from . import register

NUMBER_RE = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?$")
CODE_RE = re.compile(r"^\d+(?:\.\d+)*\*?$")
GROUP_RE = re.compile(r"^[一二三四五六七八九十]+、")
PLACEHOLDERS = {"--", "-", "—", "–", "－"}

_DETAIL_HEADER = ["行次", "项目", "本季度", "上季度"]
_MINIMUM_HEADER = ["指标名称", "期末数", "期初数"]
_TAIL_STOP = ("（三）", "(三)", "近三年平均")


def _is_number(token: str) -> bool:
    token = (token or "").strip()
    return bool(NUMBER_RE.match(token)) or token in PLACEHOLDERS


def _page_text(doc: Any, page_number: int) -> str:
    index = int(page_number) - 1
    if index < 0 or index >= doc.page_count:
        return ""
    return doc.load_page(index).get_text() or ""


def _page_lines(doc: Any, page_number: int) -> list[str]:
    lines = [line.strip() for line in _page_text(doc, page_number).splitlines() if line.strip()]
    # 页首是印刷页码（与物理页号不同），跳过，避免被当成行次/数值。
    if lines and lines[0].isdigit():
        lines = lines[1:]
    return lines


def _table_pages(table: Any) -> list[int]:
    pages = [
        int(page)
        for page in (getattr(table, "source_pages", None) or [])
        if str(page).strip().isdigit()
    ]
    if not pages:
        pages = [int(getattr(table, "page", 1) or 1)]
    return pages


def _sync(table: Any, rows: list[list[str]], page: int) -> None:
    """rows 与 table_blocks 必须同步，否则 STEP3 展开表块仍读旧值。"""
    table.rows = rows
    table.table_blocks = [
        {"page": int(page or 1), "rows": [list(row) for row in rows]}
    ]


def _parse_detail_rows(page_texts: Sequence[str]) -> list[list[str]]:
    """按“行次/项目/本季度/上季度”重建实际资本明细表。"""
    lines: list[str] = []
    for text in page_texts:
        page_lines = [line.strip() for line in text.splitlines() if line.strip()]
        if page_lines and page_lines[0].isdigit():
            page_lines = page_lines[1:]
        lines.extend(page_lines)

    try:
        start = lines.index("行次")
    except ValueError:
        return []
    cursor = start + 1
    header_words = {"项目", "本季度", "上季度", "期末数", "期初数"}
    while cursor < len(lines) and lines[cursor] not in header_words:
        cursor += 1
    while cursor < len(lines) and lines[cursor] in header_words:
        cursor += 1

    rows: list[list[str]] = []
    code = ""
    label = ""
    values: list[str] = []

    def flush() -> None:
        nonlocal code, label, values
        if code and label:
            cells = [code, label, *values[:2]]
            while len(cells) < 4:
                cells.append("")
            rows.append(cells)
        code, label, values = "", "", []

    for line in lines[cursor:]:
        if line.startswith(("（二）", "(二)", "（三）", "(三)", "十二、", "十三、")):
            break
        if CODE_RE.match(line):
            # 任何行次都是新行起点，即使上一行只有一列占位符。
            flush()
            code = line
            continue
        if _is_number(line):
            if code and label:
                values.append(line)
            continue
        if not code:
            continue
        if not label:
            label = line
        elif len(values) < 2:
            label += line
        else:
            flush()
            label = line
            code = ""
    flush()

    # 只保留到“5 实际资本合计”，避免把页尾“（二）认可资产”带进来。
    for index, row in enumerate(rows):
        if "实际资本合计" in row[1]:
            return rows[: index + 1]
    return rows


def _parse_minimum_rows(page_texts: Sequence[str]) -> list[list[str]]:
    """按文本行重建 S05 最低资本表（指标名称 + 期末数 + 期初数）。"""
    lines: list[str] = []
    for text in page_texts:
        page_lines = [line.strip() for line in text.splitlines() if line.strip()]
        if page_lines and page_lines[0].isdigit():
            page_lines = page_lines[1:]
        lines.extend(page_lines)

    try:
        start = lines.index("行次")
    except ValueError:
        return []
    cursor = start + 1
    header_words = {"项目", "期末数", "期初数", "本季度", "上季度"}
    while cursor < len(lines) and lines[cursor] not in header_words:
        cursor += 1
    while cursor < len(lines) and lines[cursor] in header_words:
        cursor += 1

    rows: list[list[str]] = []
    code = ""
    label = ""
    values: list[str] = []

    def flush() -> None:
        nonlocal code, label, values
        if code and label:
            cells = [label, *values[:2]]
            while len(cells) < 3:
                cells.append("")
            rows.append(cells)
        code, label, values = "", "", []

    for line in lines[cursor:]:
        if line.startswith(("（二）", "(二)", "（三）", "(三)", "十二、", "十三、")):
            break
        if CODE_RE.match(line):
            flush()
            code = line
            continue
        if _is_number(line):
            if code and label:
                values.append(line)
            continue
        if not code:
            continue
        if not label:
            label = line
        elif len(values) < 2:
            label += line
        else:
            flush()
            label = line
            code = ""
    flush()

    for index, row in enumerate(rows):
        if row[0] == "最低资本":
            return rows[: index + 1]
    return rows


def _append_liquidity_tail(table: Any, doc: Any, pages: Sequence[int]) -> None:
    """2023 年监测指标第 22 页续尾：补“九”子项和“十、持有关联方资产占比”。"""
    rows = [list(row) for row in (getattr(table, "rows", None) or [])]
    if not rows:
        return
    groups = [str(row[0]).strip() for row in rows if row and str(row[0]).strip()]
    if any(group.startswith("十、") for group in groups):
        return
    last_group = groups[-1] if groups else ""
    if not last_group.startswith("九、"):
        return

    page_number = max(pages) + 1 if pages else 1
    text = ""
    for candidate in range(page_number, page_number + 3):
        text = _page_text(doc, candidate)
        if "十、持有关联方资产占比" in text:
            page_number = candidate
            break
    else:
        return

    lines = _page_lines(doc, page_number)
    added: list[list[str]] = []
    group = last_group
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith(_TAIL_STOP):
            break
        if GROUP_RE.match(line):
            group = line
            cursor = index + 1
            if cursor < len(lines) and lines[cursor] == "指标值":
                cursor += 1
            values: list[str] = []
            while cursor < len(lines) and _is_number(lines[cursor]) and len(values) < 2:
                values.append(lines[cursor])
                cursor += 1
            if len(values) == 2:
                added.append([group, "指标值", *values])
                index = cursor
                continue
            index += 1
            continue
        label = line
        cursor = index + 1
        values = []
        while cursor < len(lines) and _is_number(lines[cursor]) and len(values) < 2:
            values.append(lines[cursor])
            cursor += 1
        if len(values) == 2:
            added.append(["", label, *values])
            index = cursor
            continue
        index += 1

    if added:
        _sync(table, [*rows, *added], pages[0] if pages else page_number)


@register("zhufeng")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """珠峰专属后处理入口。异常由调用方捕获，不在这里吞掉。"""
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for table in tables:
            table_id = str(getattr(table, "table_id", "") or "")
            pages = _table_pages(table)
            if table_id == "ACTUAL_CAPITAL":
                parsed = _parse_detail_rows([_page_text(doc, page) for page in pages])
                if len(parsed) >= 10:
                    _sync(table, [list(_DETAIL_HEADER), *parsed], pages[0])
            elif table_id == "MINIMUM_CAPITAL":
                parsed = _parse_minimum_rows([_page_text(doc, page) for page in pages])
                if len(parsed) >= 20:
                    _sync(table, [list(_MINIMUM_HEADER), *parsed], pages[0])
            elif table_id == "LIQUIDITY_MONITORING":
                _append_liquidity_tail(table, doc, pages)
    finally:
        doc.close()
    return tables
