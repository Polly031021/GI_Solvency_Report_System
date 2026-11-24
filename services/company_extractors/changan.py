"""长安保险（长安责任保险股份有限公司）专属提取后处理。

2022 年完整 S01 在第 9 页，S02 明细跨第 25-26 页，两张表均明确为元。
第 13 页只是审计前后对比的 8 行万元摘要，不能作为 S01 提取源。

当前 YAML 已显式声明 S01/S02 单位为元。这里的量级判断仅作为单位缺失时的兜底，
保留对旧版规则的兼容；已识别单位不会被覆盖。
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from . import register

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
_YUAN_FLOOR = 1e7  # 元表的最低量级门槛：>1000 万元


def _text(value: Any) -> str:
    return "" if value is None else str(value).replace(",", "").replace("，", "").strip()


def _max_abs(rows: Sequence[Sequence[Any]]) -> float:
    peak = 0.0
    for row in rows or ():
        for cell in row:
            number = _NUMBER_RE.fullmatch(re.sub(r"\s", "", _text(cell)))
            if not number:
                continue
            try:
                peak = max(peak, abs(float(number.group())))
            except ValueError:
                continue
    return peak


def _ensure_capital_unit(table: Any) -> None:
    unit = str(getattr(table, "unit", "") or "").strip()
    if unit and unit != "未识别":
        return
    peak = _max_abs(getattr(table, "rows", []) or [])
    if not peak:
        return
    if peak >= _YUAN_FLOOR:
        table.unit = "元"
    else:
        table.unit = "万元"
    table.unit_source = table.unit


def _fix_operating_unit(table: Any) -> None:
    """经营指标本体（保险业务收入 762,638,002.33 等）实为元。"""
    unit = str(getattr(table, "unit", "") or "").strip()
    peak = _max_abs(getattr(table, "rows", []) or [])
    if peak >= _YUAN_FLOOR and unit != "元":
        table.unit = "元"
        table.unit_source = "元"


def _ensure_operating_header(table: Any) -> None:
    """经营表区域从「四、主要经营指标」起裁，表头行丢失。

    缺表头时两列金额都会被默认成「本季度末数」，同指标两份候选打分相同，
    冲突消解后整条丢弃（填报汇总显示 INSURANCE_REVENUE 记录数 0）。
    这里按 PDF 原文补回三列表头，使 第2列→本季度末数、第3列→本年累计数。
    """
    rows = list(getattr(table, "rows", []) or [])
    if not rows:
        return
    first = [_text(cell) for cell in rows[0]]
    if first and first[0] in {"指标名称", "项目", "行次"}:
        return
    if len(first) < 3:
        return
    header = ["指标名称", "本季度数", "本年度累计数"]
    table.rows = [header, *rows]
    # STEP3 优先读 table_blocks，必须同步，否则 rows 上的补表头会被重建覆盖。
    blocks = getattr(table, "table_blocks", None)
    if isinstance(blocks, list):
        for block in blocks:
            if not isinstance(block, dict):
                continue
            block_rows = block.get("rows")
            if not isinstance(block_rows, list) or not block_rows:
                continue
            first_cells = [_text(cell) for cell in block_rows[0]]
            if first_cells and first_cells[0] in {"指标名称", "项目", "行次"}:
                continue
            block["rows"] = [header, *block_rows]
            break


@register("changan")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """长安保险专属后处理入口：单位兜底与经营表表头修复。"""
    for table in tables:
        table_id = str(getattr(table, "table_id", "") or "")
        if table_id in {"SOLVENCY_MAIN", "ACTUAL_CAPITAL"}:
            _ensure_capital_unit(table)
        elif table_id == "NON_LIFE_OPERATING_METRICS":
            _fix_operating_unit(table)
            _ensure_operating_header(table)
    return tables
