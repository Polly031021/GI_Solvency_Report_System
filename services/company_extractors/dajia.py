"""大家财险专属提取后处理。

大家 2023-2025 三份报告的版式稳定，通用规则已能定位并提取全部目标表
（清空首尾项目、整页线框识别后经营表 33 行、最低资本表 35 行）。剩下
两个纯“标签/单位”问题：

1. ``流动性风险监测指标`` 的指标名列带 ``一、``~``十、`` 章节号与
   ``指标值`` 后缀（``一、经营活动净现金流指标值``），导致现金流、
   融资杠杆比例等指标无法命中关键词字典，全部落成 RAW_ 未映射；
2. ``近三年投资收益率`` 与流动性监测同页且整页帧单位是“元”，但内容
   全部是百分比——若不修正单位，``3.44%`` 会被当作金额参与元/万元
   换算。

这里只做标签清洗与单位修正，不重建行、不改数值。章节号/后缀清洗后，
``经营活动净现金流`` 等四个流动性指标可按精确别名命中。
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from . import register

_SECTION_RE = re.compile(r"^\s*[一二三四五六七八九十]{1,3}\s*[、.．]\s*")
_SUFFIX_RE = re.compile(r"\s*指标值\s*$")
_SPACE_RE = re.compile(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])")
_HEADER_LABELS = {"监测指标", "指标名称", "项目"}


def _clean_label(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r", " ").replace("\n", " ").strip()
    text = _SECTION_RE.sub("", text)
    text = _SUFFIX_RE.sub("", text)
    text = _SPACE_RE.sub("", text)
    return text.strip()


def _clean_liquidity(table: Any) -> None:
    rows = getattr(table, "rows", None)
    if not isinstance(rows, list):
        return
    for row in rows:
        if not row:
            continue
        label = str(row[0] or "").strip()
        if label in _HEADER_LABELS:
            continue
        row[0] = _clean_label(row[0])


def _fix_three_year_unit(table: Any) -> None:
    rows = getattr(table, "rows", None) or []
    values = [
        str(row[-1]).strip()
        for row in rows
        if row and str(row[-1] or "").strip().endswith("%")
    ]
    if values:
        table.unit = "%"
        table.unit_source = "%"


@register("dajia")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """大家财险专属后处理入口。"""
    for table in tables:
        table_id = str(getattr(table, "table_id", "") or "")
        if table_id == "LIQUIDITY_MONITORING":
            _clean_liquidity(table)
        elif table_id == "THREE_YEAR_INVESTMENT_RETURN":
            _fix_three_year_unit(table)
    return tables
