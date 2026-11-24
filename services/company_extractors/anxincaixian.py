"""安心财险 提取后处理（占位）。

通用规则对该公司的表结构与列对齐已足够，本模块用于登记公司级
extractor，便于后续有针对性的版式修补；当前不做任何改动。
"""

from __future__ import annotations

from typing import Any, Sequence

from . import register


@register("anxincaixian")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    return tables

# Explicit company extraction hooks.
def locate_company_tables(pdf_bytes, table_configs=()):
    from ._company_hooks import locate_for_company
    return locate_for_company("anxincaixian", pdf_bytes, table_configs)


def extract_company_tables(pdf_bytes, table_configs):
    from ._company_hooks import extract_for_company
    return extract_for_company(
        "anxincaixian",
        pdf_bytes,
        table_configs,
        mode="document",
    )
