"""紫金财产专属提取后处理。

通用规则在紫金四份季报上能定位到全部目标表，但有三个残留问题：

1. ``流动性风险监测指标`` 的指标名列自带章节号与“指标值”后缀，
   如 ``一、经营活动净现金流指标值（万元）``；关键词字典里没有这些
   修饰，导致现金流/保费增速/流动资产占比等指标全部落成 RAW_ 未映射；
2. ``实际资本表`` 2022 年跨 38/39 两页，第 39 页没有表头，通用合并
   会在行中间插入 ``【第39页表格分块】`` 占位行；
3. 指标名被 PDF 换行拆开（``百元保费经营活动净现金 流指标值``），
   中间混入空格。

这里不重建表格，只做标签清洗与占位行清理；行数、数值与单位保持
通用结果，避免与 PDF 出现任何一位数字的偏差。清洗后可映射的指标
（经营活动净现金流、规模保费同比增速、现金及流动性管理工具占比、
季均融资杠杆比例）按“指标值”同义后缀规则命中；字典里没有编码的
监测项（现金流入/流出、特定业务现金流支出等）继续保持未映射。
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from . import register

_SECTION_RE = re.compile(r"^\s*[一二三四五六七八九十]{1,3}\s*[、.．]\s*")
_SUFFIX_RE = re.compile(r"\s*指标值\s*")
_BLOCK_MARK_RE = re.compile(r"^【第\d+页表格分块】$")
_SPACE_RE = re.compile(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])")


def _clean_label(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r", " ").replace("\n", " ").strip()
    text = _SECTION_RE.sub("", text)
    text = _SUFFIX_RE.sub("", text)
    text = _SPACE_RE.sub("", text)
    return text.strip()


def _row_is_block_marker(row: Sequence[Any]) -> bool:
    cells = [str(cell).strip() for cell in row if str(cell or "").strip()]
    return len(cells) == 1 and bool(_BLOCK_MARK_RE.match(cells[0]))


def _clean_liquidity_labels(table: Any) -> None:
    rows = getattr(table, "rows", None)
    if not isinstance(rows, list):
        return
    for row in rows:
        if not row or _row_is_block_marker(row):
            continue
        if str(row[0] or "").strip() in {"监测指标", "指标名称", "项目"}:
            continue
        row[0] = _clean_label(row[0])


def _drop_block_markers(table: Any) -> None:
    rows = getattr(table, "rows", None)
    if not isinstance(rows, list):
        return
    kept = [row for row in rows if not _row_is_block_marker(row)]
    if len(kept) != len(rows):
        table.rows = kept


@register("zijin")
def _fix_three_year_unit(table: Any) -> None:
    rows = getattr(table, "rows", None) or []
    if any(str(row[-1]).strip().endswith("%") for row in rows if row):
        table.unit = "%"
        table.unit_source = "%"


@register("zijin")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """紫金财产专属后处理入口。"""
    for table in tables:
        table_id = str(getattr(table, "table_id", "") or "")
        if table_id == "LIQUIDITY_MONITORING":
            _clean_liquidity_labels(table)
            _drop_block_markers(table)
        elif table_id == "ACTUAL_CAPITAL":
            _drop_block_markers(table)
        elif table_id == "THREE_YEAR_INVESTMENT_RETURN":
            _fix_three_year_unit(table)
    return tables
