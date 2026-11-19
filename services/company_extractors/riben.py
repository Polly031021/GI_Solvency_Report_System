"""日本财产 提取后处理（占位）。

通用规则对该公司的表结构与列对齐已足够，本模块用于登记公司级
extractor，便于后续有针对性的版式修补；当前不做任何改动。
"""

from __future__ import annotations

from typing import Any, Sequence

from . import register


@register("riben")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    return tables
