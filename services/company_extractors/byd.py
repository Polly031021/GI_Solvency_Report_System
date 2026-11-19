"""比亚迪保险专属后处理：重建偿付能力主表。

比亚迪保险 2024 主表金额被拆成整数段与两位小数段两行（如
``4,882,754,972.`` + ``85``），通用识别把碎段错位分配给下一行。
这里按词坐标把同一行的三段金额拼回。
"""

from __future__ import annotations

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

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for table in tables:
            if str(getattr(table, "table_id", "") or "") != "SOLVENCY_MAIN":
                continue
            pages = [int(p) for p in (getattr(table, "source_pages", None) or []) if str(p).isdigit()]
            rows: list[list[str]] = [_HEADER]
            for page_number in pages:
                try:
                    rows.extend(_rebuild_page(doc, page_number))
                except Exception:  # pragma: no cover
                    continue
            if len(rows) > 1:
                table.rows = rows
                blocks = getattr(table, "table_blocks", None) or []
                if blocks:
                    try:
                        blocks[0]["rows"] = rows
                    except Exception:  # pragma: no cover
                        pass
        return tables
    finally:
        doc.close()
