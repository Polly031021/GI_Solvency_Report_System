"""融通财险专属后处理：重建偿付能力主表。

融通财险 2025 主表带独立“行次”列，文本层把行次、标签与金额切成多段，
通用识别会把本季度数与上季度数粘成一格（如 ``73,408.74538,167.77``），
标准化后本季度数被截断。这里按词坐标重建五列主表。
"""

from __future__ import annotations

from typing import Any, Sequence

from . import register
from ._main_table import rebuild_main_rows


@register("rongtong")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for table in tables:
            if str(getattr(table, "table_id", "") or "") != "SOLVENCY_MAIN":
                continue
            pages = [int(p) for p in (getattr(table, "source_pages", None) or []) if str(p).isdigit()]
            if not pages:
                continue
            rows = rebuild_main_rows(doc, pages)
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
