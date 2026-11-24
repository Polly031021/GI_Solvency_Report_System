"""比亚迪保险专属后处理：按公司表格坐标重建偿付能力主表。

2023 年表头缺少“行次”列，2024 年金额被拆成整数段与两位小数段两行
（如 ``4,882,754,972.`` + ``85``），2025 年表头再次变化。这里统一按
公司 PDF 的表格线和坐标读取，只去除单元格内换行，不改变占位符与金额。
"""

from __future__ import annotations

import io
import re
from typing import Any, Sequence

from . import register

_HEADER = ["指标名称", "本季度数", "上季度可比数", "基本情景下的下季度预测数"]
_STOP_PREFIXES = ("综合偿付能力充足率", "核心偿付能力充足率")
_NUM_RE = re.compile(r"^-?[\d,]+(?:\.\d*)?$")


def _is_amount(text: str) -> bool:
    return bool(_NUM_RE.fullmatch(text.replace(" ", "")))


def _is_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _join_fragments(fragments: list[tuple[float, str]]) -> str:
    fragments.sort(key=lambda item: item[0])
    out = ""
    for _y, text in fragments:
        piece = text.strip()
        if not piece:
            continue
        if not out:
            out = piece
        elif out.endswith((".", ",")):
            out += piece
        else:
            out += piece
    return out


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value if value is not None else ""))


def _grid_rows(pdf_bytes: bytes, pages: Sequence[int]) -> list[tuple[int, list[str]]]:
    """Read the company's ruled table and repair wrapped amount cells by coordinates."""
    import pdfplumber

    output: list[tuple[int, list[str]]] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page_number in pages:
            if page_number < 1 or page_number > len(pdf.pages):
                continue
            page = pdf.pages[page_number - 1]
            for table in page.find_tables():
                for raw in table.extract() or []:
                    cells = [_compact(cell) for cell in raw]
                    if len(cells) >= 5:
                        label, values = cells[1], cells[2:5]
                    elif len(cells) == 4:
                        label, values = cells[0], cells[1:4]
                    else:
                        continue
                    if not label or label in {"项目", "指标名称", "行次"}:
                        continue
                    if any("本季度" in value or "下季度" in value for value in values):
                        continue
                    output.append((page_number, [label, *values]))
    return output


def _rebuild_page(doc: Any, page_number: int) -> list[list[str]]:
    page = doc[page_number - 1]
    words = page.get_text("words")
    labels: list[tuple[float, str]] = []
    numbers: list[tuple[float, float, str]] = []
    for word in words:
        x0, y0, text = float(word[0]), float(word[1]), str(word[4])
        if _is_cjk(text) and 105 <= x0 <= 215:
            labels.append((y0, text))
        elif _is_amount(text) and x0 >= 230:
            numbers.append((y0, x0, text))
    rows: list[list[str]] = []
    for y_label, label in labels:
        if label.startswith(_STOP_PREFIXES):
            break
        window = [item for item in numbers if -9 <= (item[0] - y_label) <= 14]
        columns: list[list[tuple[float, str]]] = [[], [], []]
        for y_num, x0, text in window:
            index = 0 if x0 < 340 else (1 if x0 < 430 else 2)
            columns[index].append((y_num, text))
        cells = [_join_fragments(col) for col in columns]
        if any(cells):
            rows.append([label, *cells])
    return rows


@register("byd")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    import fitz

    for table in tables:
        if str(getattr(table, "table_id", "") or "") != "SOLVENCY_MAIN":
            continue
        pages = [
            int(page)
            for page in (getattr(table, "source_pages", None) or [])
            if str(page).isdigit() and int(page) > 0
        ]
        rebuilt = _grid_rows(pdf_bytes, pages)
        if rebuilt:
            blocks = [
                {
                    "page": page,
                    "rows": [
                        list(_HEADER),
                        *[list(row) for number, row in rebuilt if number == page],
                    ],
                    "unit": getattr(table, "unit", ""),
                }
                for page in pages
                if any(number == page for number, _ in rebuilt)
            ]
            table.rows = [
                list(row)
                for block in blocks
                for row in block["rows"]
            ]
            table.table_blocks = blocks
            table.source_pages = [block["page"] for block in blocks]
            table.page = table.source_pages[0]
            table.strategy = "公司专属·表格坐标重建"
            continue

        # Fallback for PDFs whose table border detection is unavailable.
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            rows: list[list[str]] = [list(_HEADER)]
            for page_number in pages:
                try:
                    rows.extend(_rebuild_page(doc, page_number))
                except Exception:  # pragma: no cover
                    continue
            if len(rows) > 1:
                table.rows = rows
                table.table_blocks = [{
                    "page": pages[0] if pages else int(getattr(table, "page", 1) or 1),
                    "rows": rows,
                }]
                table.strategy = "公司专属·文本坐标重建"
        finally:
            doc.close()
    return tables
