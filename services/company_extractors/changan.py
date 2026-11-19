"""长安保险（长安责任保险股份有限公司）专属提取后处理。

通用规则在 2022 年报（唯一一份）上能定位到全部目标表，残留问题只有单位嗅探：

* 第 13 页 S01「偿付能力充足率指标」与第 24 页 S02「实际资本明细表」都没有
  「单位：万元」字样（模板版式），通用嗅探得到「未识别」，STEP3 按默认单位
  「元」再除 10000，导致认可资产 710,681.65 万元 被写成 71.07 万元。
* 交叉校验证明这两张表确实是万元：S01 实际资本 = 认可资产 - 认可负债
  （710,681.65 - 715,732.02 = -5,050.37）；且 S01 最低资本 54,993.83 万元
  = S05 最低资本 549,938,259.25 元 / 10000；正文亦写明「总资产 72.7 亿元」。
* S05 最低资本表本身带「元」表头，已正确识别，勿再改。

因此仅对 SOLVENCY_MAIN / ACTUAL_CAPITAL 补单位：未识别时按万元标注；若表中出现
≥ 1000 万量级的元值（本司不存在，留作跨版本保险）则判定为元。幂等：已识别不改。
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


def _fix_wan_yuan_unit(table: Any) -> None:
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
    """第 12 页同页出现「单位：元」和「单位：万元」两行，通用嗅探取到了后者。

    经营指标本体（保险业务收入 762,638,002.33、净利润 -332,947,845.62）实为元，
    正文亦写「签单保费 36.2 亿元」。量级 ≥ 1000 万即可判定为元。
    """
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
    """长安保险专属后处理入口：补 S01/S02 的万元单位、纠正经营表单位与表头。"""
    for table in tables:
        table_id = str(getattr(table, "table_id", "") or "")
        if table_id in {"SOLVENCY_MAIN", "ACTUAL_CAPITAL"}:
            _fix_wan_yuan_unit(table)
        elif table_id == "NON_LIFE_OPERATING_METRICS":
            _fix_operating_unit(table)
            _ensure_operating_header(table)
    return tables
