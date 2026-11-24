"""中路财险专属提取后处理。

2023 年实际资本表跨第 29-30 页，续页没有表头，通用网格只保留了首页前
11 行。这里按文本层的“行次 -> 项目 -> 期末数/期初数”顺序重建整表，不重算
数值，也不把主表汇总行当成 S02 明细。
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from . import register

_ROW_NO_RE = re.compile(r"^\d+(?:\.\d+)*(?:\*)?$")
_VALUE_RE = re.compile(
    r"^(?:-{1,2}|—|–|－|[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?)$"
)
_STOP_RE = re.compile(r"^(?:十一、|S05|最低资本表)")


def _clean(value: Any) -> str:
    return str(value or "").replace("\u3000", " ").strip()


def _page_texts(pdf_bytes: bytes) -> list[str]:
    import fitz

    with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
        return [page.get_text() or "" for page in pdf]


def _capital_rows(texts: Sequence[str], pages: Sequence[int]) -> list[list[str]]:
    lines = [
        _clean(line)
        for page in pages
        if 0 < page <= len(texts)
        for line in texts[page - 1].splitlines()
        if _clean(line)
    ]
    try:
        start = lines.index("期初数") + 1
    except ValueError:
        return []

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
                ]
            )
        row_no = ""
        label = []
        values = []

    for line in lines[start:]:
        if _STOP_RE.match(line):
            break
        if re.fullmatch(r"—\d+—", line):
            continue
        if (
            _ROW_NO_RE.fullmatch(line)
            and not line.startswith("0.")
        ):
            flush()
            row_no = line
            continue
        if row_no and _VALUE_RE.fullmatch(line):
            values.append(line)
            if len(values) >= 2:
                flush()
            continue
        if row_no:
            if values:
                values = []
            label.append(line)
    flush()
    return rows


def _sync_table(table: Any, rows: list[list[str]], unit: str) -> None:
    table.rows = [list(row) for row in rows]
    first_page = int(
        (getattr(table, "source_pages", None) or [getattr(table, "page", 1)])[0]
        or 1
    )
    table.table_blocks = [
        {"page": first_page, "rows": [list(row) for row in rows]}
    ]
    if unit:
        table.unit = unit
        table.unit_source = unit


@register("zhonglu")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    texts: list[str] | None = None
    for table in tables:
        if str(getattr(table, "table_id", "") or "") != "ACTUAL_CAPITAL":
            continue
        pages = [
            int(page)
            for page in (getattr(table, "source_pages", None) or [])
            if str(page).isdigit()
        ]
        if pages != [29, 30] or len(getattr(table, "rows", []) or []) >= 30:
            continue
        if texts is None:
            texts = _page_texts(pdf_bytes)
        body = _capital_rows(texts, pages)
        if len(body) < 29:
            continue
        header = ["指标名称", "期末数", "期初数"]
        unit = (
            "元"
            if any("单位：元" in texts[page - 1] for page in pages)
            else str(getattr(table, "unit", "") or "")
        )
        _sync_table(table, [header, *body], unit)
    return tables
