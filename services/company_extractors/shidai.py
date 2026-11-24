"""史带财险专属提取后处理。"""

from __future__ import annotations

import re
from typing import Any, Sequence

from . import register

_VALUE_RE = re.compile(
    r"^(?:-{1,2}|—|–|－|[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?)$"
)


def _rebuild_2022_main(table: Any, pdf_bytes: bytes) -> None:
    pages = [
        int(page)
        for page in (getattr(table, "source_pages", None) or [])
        if str(page).isdigit()
    ]
    if pages != [10]:
        return
    import fitz

    with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
        text = pdf.load_page(9).get_text() or ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    try:
        start = lines.index("上季度末数") + 1
    except ValueError:
        return

    rows: list[list[str]] = []
    label = ""
    values: list[str] = []
    for line in lines[start:]:
        if _VALUE_RE.fullmatch(line) and label:
            values.append(line)
            if len(values) >= 2:
                rows.append([label, values[0], values[1]])
                label = ""
                values = []
            continue
        if values:
            break
        if any(term in line for term in ("偿付能力", "单位：")):
            label = line
            continue
        break
    if len(rows) != 4:
        return
    header = ["项目", "本季度末数", "上季度末数"]
    table.rows = [header, *rows]
    table.table_blocks = [
        {"page": 10, "rows": [list(row) for row in table.rows]}
    ]
    table.unit = "元"
    table.unit_source = "元"


@register("shidai")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    for table in tables:
        if str(getattr(table, "table_id", "") or "") == "SOLVENCY_MAIN":
            _rebuild_2022_main(table, pdf_bytes)
    return tables
