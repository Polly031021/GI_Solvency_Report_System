"""久隆保险专属提取后处理。

通用规则在久隆四份季报上能定位到全部目标表，但有三处残留问题：

1. 跨页表（2022 经营表第 12/13 页、2022 实际资本表第 20/21 页、
   2023-2025 最低资本表）由通用合并逻辑在行中间插入
   ``【第N页表格分块】`` 占位行，会落成空标签的 RAW_ 未映射记录；
2. 2022 经营表首页块（第 12 页）只有 3 列（指标名称/本季度数/本年累计数），
   续页块（第 13 页）多出“行次”列变 4 列；2022 实际资本表与 2023-2025
   最低资本表的续页/附加资本行同样只有部分行带行次列。列宽不一致会让
   标签落到不同列位，同名指标在不同行按不同列解析；
3. 经营表表头（指标名称/本季度数/本年累计数）只在首页块出现，续页无表头。

这里不重算任何数值，只做三件事：删占位行、按表头列宽对齐数据行（行次列
在表头缺列时丢弃，缺列补空）、把整理后的行同步回 ``table_blocks``。
行数、数值与单位保持通用结果，避免与 PDF 出现任何一位数字的差异。
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from . import register

_BLOCK_MARK_RE = re.compile(r"^【第\d+页表格分块】$")
_ROW_NO_RE = re.compile(r"^\d+(?:\.\d+)*$")
_VALUE_RE = re.compile(
    r"^(?:-{1,2}|—|–|－|[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?)$"
)

_HEADER_LABELS = {"指标名称", "项目", "行次", "监测指标", "行次/项目"}


def _text(value: Any) -> str:
    return "" if value is None else str(value).replace("\r", " ").replace("\n", " ").strip()


def _row_is_block_marker(row: Sequence[Any]) -> bool:
    cells = [_text(cell) for cell in row if _text(cell)]
    return len(cells) == 1 and bool(_BLOCK_MARK_RE.match(cells[0]))


def _is_row_number(value: Any) -> bool:
    return bool(_ROW_NO_RE.match(_text(value)))


def _header_width(rows: Sequence[Sequence[Any]]) -> int:
    """取第一行（表头）宽度；表头缺失时回退到出现频次最高的宽度。"""
    for row in rows:
        cells = [_text(cell) for cell in row]
        if any(cell in _HEADER_LABELS for cell in cells):
            return len(row)
    widths = [len(row) for row in rows if row]
    if not widths:
        return 0
    return max(set(widths), key=widths.count)


def _align_rows(rows: Sequence[Sequence[Any]], width: int) -> list[list[str]]:
    """按表头列宽对齐：宽出的“行次”列丢弃，不足的尾部补空。"""
    aligned: list[list[str]] = []
    for row in rows:
        cells = [_text(cell) for cell in row]
        if _row_is_block_marker(cells):
            continue
        if width and len(cells) > width:
            # 多出的列只可能是行首“行次”；仅当首列是纯序号时才丢。
            while len(cells) > width and _is_row_number(cells[0]):
                cells = cells[1:]
            if len(cells) > width:
                cells = cells[:width]
        if width and len(cells) < width:
            cells = cells + [""] * (width - len(cells))
        aligned.append(cells)
    return aligned


def _rebuild_table(table: Any) -> None:
    rows = getattr(table, "rows", None)
    if not isinstance(rows, list) or not rows:
        return
    width = _header_width(rows)
    blocks = getattr(table, "table_blocks", None)
    if isinstance(blocks, list) and blocks:
        new_blocks: list[dict[str, Any]] = []
        merged: list[list[str]] = []
        for block in blocks:
            if not isinstance(block, dict):
                new_blocks.append(block)
                continue
            block_rows = block.get("rows") or []
            aligned = _align_rows(block_rows, width)
            new_blocks.append({**block, "rows": aligned})
            merged.extend(aligned)
        table.rows = merged
        table.table_blocks = new_blocks
    else:
        table.rows = _align_rows(rows, width)


def _rebuild_2022_main(table: Any, pdf_bytes: bytes) -> bool:
    pages = [
        int(page)
        for page in (getattr(table, "source_pages", None) or [])
        if str(page).isdigit()
    ]
    if pages != [11]:
        return False
    import fitz

    with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
        text = pdf.load_page(10).get_text() or ""
    lines = [_text(line) for line in text.splitlines() if _text(line)]
    try:
        start = lines.index("下季度预测数") + 1
    except ValueError:
        return False

    rows: list[list[str]] = []
    row_no = ""
    label: list[str] = []
    values: list[str] = []

    def flush() -> None:
        nonlocal row_no, label, values
        if row_no and label:
            rows.append(
                [
                    f"{row_no} {''.join(label)}".strip(),
                    values[0] if values else "",
                    values[1] if len(values) > 1 else "",
                    values[2] if len(values) > 2 else "",
                ]
            )
        row_no = ""
        label = []
        values = []

    for line in lines[start:]:
        if line.startswith("（二）流动性风险监管指标"):
            break
        if _ROW_NO_RE.fullmatch(line) and not line.startswith("0."):
            flush()
            row_no = line
            continue
        if row_no and _VALUE_RE.fullmatch(line):
            values.append(line)
            if len(values) >= 3:
                flush()
            continue
        if row_no:
            if values:
                values = []
            label.append(line)
    flush()
    if len(rows) < 14:
        return False
    header = ["指标名称", "本季度末数", "上季度末数", "下季度末预测数"]
    table.rows = [header, *rows]
    table.table_blocks = [
        {"page": 11, "rows": [list(row) for row in table.rows]}
    ]
    table.unit = "元"
    table.unit_source = "元"
    return True


@register("jiulong")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """久隆保险专属后处理入口。"""
    for table in tables:
        table_id = str(getattr(table, "table_id", "") or "")
        if table_id == "SOLVENCY_MAIN":
            _rebuild_2022_main(table, pdf_bytes)
        if table_id in {
            "NON_LIFE_OPERATING_METRICS",
            "ACTUAL_CAPITAL",
            "MINIMUM_CAPITAL",
        }:
            _rebuild_table(table)
    return tables

# Explicit company extraction hooks.
def locate_company_tables(pdf_bytes, table_configs=()):
    from ._company_hooks import locate_for_company
    return locate_for_company("jiulong", pdf_bytes, table_configs)


def extract_company_tables(pdf_bytes, table_configs):
    from ._company_hooks import extract_for_company
    return extract_for_company(
        "jiulong",
        pdf_bytes,
        table_configs,
        mode="document",
    )
