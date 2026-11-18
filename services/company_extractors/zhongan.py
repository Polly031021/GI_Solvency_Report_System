"""众安产险专属提取后处理。

已覆盖的通用规则缺口：

1. 实际资本表（S02）与最低资本表（S05）把单位写在表格上方的正文里
   （“（以下单位均为人民币万元）”），既不在表格首行，也不在任一单元格内。
   通用单位嗅探 ``detect_table_unit()`` / ``sniff_unit_from_text()`` 都要求
   “单位”后面紧跟冒号或直接跟“万元”，识别不了“以下单位均为人民币万元”，
   于是表级单位落到“未识别”，STEP3 按默认单位“元”把这两张表的金额除以
   10000（2024 年最低资本 32 条明细全部缩小一万倍，如 741,189.83 万元被写成
   74.118983 万元）。这里在提取结束时补回表级单位。
2. 只改单位元数据，不动行数据与数值；其余版式差异由 YAML 的整页 frames
   路径处理。
"""

from __future__ import annotations

from typing import Any, Sequence

from . import register

# 众安正文表格由 Word 导出：单位要么写在表上方正文（实际资本/最低资本页
# “（以下单位均为人民币万元）”），要么整节不写单位（2022 年“四、主要指标”
# 三张表）。金额列一律以万元列示，故这几张表的表级单位兜底为万元；
# 每股收益、车险车均保费等“元”口径行由 STEP3 的指标级单位覆盖。
_WANYUAN_TABLES = (
    "SOLVENCY_MAIN",
    "NON_LIFE_OPERATING_METRICS",
    "ACTUAL_CAPITAL",
    "MINIMUM_CAPITAL",
    "LIQUIDITY_MONITORING",
)

_UNKNOWN_UNITS = {"", "未识别", "none", "null", "-"}


@register("zhongan")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """补齐实际资本/最低资本表的表级单位；异常由调用方兜底。"""
    del pdf_bytes  # 单位写在页标题里，无需再解析 PDF
    for table in tables:
        table_id = str(getattr(table, "table_id", "") or "")
        if table_id not in _WANYUAN_TABLES:
            continue
        unit = str(getattr(table, "unit", "") or "").strip()
        if unit.lower() in _UNKNOWN_UNITS:
            table.unit = "万元"
            table.unit_source = "万元"
    return tables
