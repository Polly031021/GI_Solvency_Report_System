"""浙商财险专属后处理。

2022 年实际资本表跨第 30-31 页，2023 年起表格整体后移。公司 PDF 的
S02 表格线稳定，这里按行次、项目、期末数、期初数的坐标重建，并在
“实际资本合计”处停止，避免把紧随其后的认可资产表并入。
"""

from __future__ import annotations

import io
import re
from typing import Any, Sequence

from . import register

_CAPITAL_HEADER = ["行次", "项目", "期末数", "期初数"]
_CODE_RE = re.compile(r"^\d+(?:\.\d+)*$")


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value if value is not None else ""))


def _cells(raw: Sequence[Any]) -> list[str]:
    values = [_compact(cell) for cell in raw]
    return [value for value in values if value]


def _header_index(rows: Sequence[Sequence[Any]]) -> int | None:
    for index, raw in enumerate(rows):
        cells = _cells(raw)
        text = "".join(cells)
        if (
            "行次" in text
            and ("项目" in text or "项项目目" in text)
            and ("期末数" in text or "本季度" in text or "本期数" in text)
            and ("期初数" in text or "上季度" in text or "上期数" in text)
        ):
            return index
    return None


def _extract_actual_capital(pdf_bytes: bytes, pages: Sequence[int]) -> list[list[str]]:
    import pdfplumber

    output: list[list[str]] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        started = False
        for page_number in pages:
            if page_number < 1 or page_number > len(pdf.pages):
                continue
            page = pdf.pages[page_number - 1]
            for table in page.find_tables():
                rows = table.extract() or []
                header_index = _header_index(rows)
                if header_index is not None:
                    started = True
                elif not started:
                    continue
                body = rows[header_index + 1:] if header_index is not None else rows
                for raw in body:
                    cells = _cells(raw)
                    if len(cells) < 4:
                        continue
                    code, label, values = cells[0], cells[1], cells[2:4]
                    if not _CODE_RE.fullmatch(code) or not label:
                        continue
                    output.append([code, label, values[0], values[1]])
                    if label == "实际资本合计":
                        return output
    return output


@register("zheshang")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    for table in tables:
        if str(getattr(table, "table_id", "") or "") != "ACTUAL_CAPITAL":
            continue
        pages = [
            int(page)
            for page in (getattr(table, "source_pages", None) or [])
            if str(page).isdigit() and int(page) > 0
        ]
        parsed = _extract_actual_capital(pdf_bytes, pages)
        if len(parsed) >= 10:
            rows = [list(_CAPITAL_HEADER), *parsed]
            table.rows = rows
            table.table_blocks = [{
                "page": pages[0] if pages else int(getattr(table, "page", 1) or 1),
                "rows": [list(row) for row in rows],
            }]
            table.strategy = "公司专属·实际资本表格坐标重建"
    return tables
