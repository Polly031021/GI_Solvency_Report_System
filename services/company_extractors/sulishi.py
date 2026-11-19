"""苏黎世专属提取后处理：修复经营指标表头。

2022-2025 年“主要经营指标（财险）”表的首行是分组标题
``主要经营指标 (万元) | -- | --``，不是列表头；通用提取/标准化会把
该行误判为表头，导致保险业务收入、净利润、综合成本率等指标的
期间口径全部变成 ``--`` 并在去重时丢弃。

本模块只重写 ``table.rows`` 与 ``table.table_blocks``：

* 去掉首行分组标题；
* 在首行补标准三列表头 ``指标名称 / 本季度数 / 本年累计数``；
* 实际资本表若缺表头，同样补 ``指标名称 / 本季度数 / 上季度可比数``。

数据值完全沿用通用提取结果，不按年份/页码写死，重复执行结果一致。
"""

from __future__ import annotations

from typing import Any, Sequence

from . import register

_OPER_HEADER = ["指标名称", "本季度数", "本年累计数"]
_ACT_HEADER = ["指标名称", "本季度数", "上季度可比数"]


def _cell(value: Any) -> str:
    return str(value or "").strip()


def _row_text(row: Sequence[Any]) -> str:
    return "".join(_cell(cell) for cell in row)


def _sync(table: Any, rows: list[list[str]], page: int) -> None:
    table.rows = [list(row) for row in rows]
    table.table_blocks = [
        {"page": int(page), "rows": [list(row) for row in rows]}
    ]
    table.source_pages = [int(page)]


def _fix_operating(table: Any) -> None:
    rows = [list(row) for row in (getattr(table, "rows", None) or [])]
    if not rows:
        return
    # 分组标题只可能出现在数据首行；保留真正以“指标名称”开头的表头。
    if "主要经营指标" in _row_text(rows[0]):
        rows = rows[1:]
    if not rows:
        return
    first_text = _row_text(rows[0])
    if "指标名称" not in first_text:
        rows = [_OPER_HEADER, *rows]
    page = int(getattr(table, "page", 1) or 1)
    _sync(table, rows, page)
    table.strategy = "专属提取·苏黎世·经营指标表头补全"


def _fix_actual_capital(table: Any) -> None:
    rows = [list(row) for row in (getattr(table, "rows", None) or [])]
    if not rows:
        return
    if "指标名称" in _row_text(rows[0]):
        return
    # 只保留从“实际资本”开始的实际资本明细行，避免把页标题并入。
    start = next(
        (index for index, row in enumerate(rows) if "实际资本" in _row_text(row)),
        0,
    )
    rows = rows[start:]
    if not rows:
        return
    rows = [_ACT_HEADER, *rows]
    page = int(getattr(table, "page", 1) or 1)
    _sync(table, rows, page)
    table.strategy = "专属提取·苏黎世·实际资本表头补全"


@register("sulishi")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """修复苏黎世经营指标/实际资本表的表头结构。"""
    for table in tables:
        table_id = str(getattr(table, "table_id", "") or "")
        if table_id in {"NON_LIFE_OPERATING_METRICS", "OPERATING_METRICS"}:
            _fix_operating(table)
        elif table_id == "ACTUAL_CAPITAL":
            _fix_actual_capital(table)
    return tables
