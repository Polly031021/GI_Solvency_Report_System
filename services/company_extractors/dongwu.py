"""东吴财险专属后处理：重建偿付能力主表。

东吴财险 2025 主表表头“行次”竖排成两个单元格，表头列数与数据列数不一致，
标准化把“本季度末”整体右移一列。这里按词坐标重建五列主表。
"""

from __future__ import annotations

from typing import Any, Sequence

from . import register
from ._main_table import rebuild_main_rows


@register("dongwu")
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
