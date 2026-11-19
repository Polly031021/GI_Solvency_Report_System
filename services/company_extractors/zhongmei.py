"""中煤财险专属后处理：重建偿付能力主表。

中煤财险 2022 主表表头被拆成“指标名称 本季 / 度末数 上 / 季度末数”，
最后一列（下季度预测数）表头缺失，通用识别把预测列当作认可资产。
这里按词坐标重建五列主表。
"""

from __future__ import annotations

from typing import Any, Sequence

from . import register
from ._main_table import rebuild_main_rows


@register("zhongmei")
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
