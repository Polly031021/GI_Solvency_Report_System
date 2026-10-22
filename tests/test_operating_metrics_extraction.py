# -*- coding: utf-8 -*-
"""主要经营指标：一页多表时按目标表区域裁切并保留整张表的尾部。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.solvency_table_boundaries import (  # noqa: E402
    boundary_variants,
    enforce_output_boundaries,
)
from services.solvency_table_extractor import _slice_target_rows  # noqa: E402

CONFIG = {
    "start_item_terms": ["保险业务收入"],
    "end_item_terms": ["综合成本率", "综合赔付率", "费用率"],
    "boundary_end_mode": "page_tail",
    "stop_terms": ["近三年平均投资收益率", "近三年（综合）投资收益率"],
    "content_terms": ["保险业务收入", "综合成本率"],
    "continuation_terms": ["签单保费", "规模类指标"],
}

ROWS = [
    ["（四）主要经营指标", "", "", ""],
    ["行次", "指标名称（单位：万元）", "本季度数", "本年累计数"],
    ["1", "保险业务收入", "40,754.54", "142,818.97"],
    ["10", "综合投资收益率（%）", "0.74%", "2.69%"],
    ["11", "效益类指标", "", ""],
    ["11.1", "综合成本率（%）", "-", "96.81%"],
    ["11.2", "综合赔付率（%）", "-", "60.56%"],
    ["11.3", "综合费用率（%）", "-", "36.24%"],
    ["12", "规模类指标", "", ""],
]


def test_boundary_variants_expose_page_tail_mode() -> None:
    variants = boundary_variants("NON_LIFE_OPERATING_METRICS", CONFIG)
    assert variants
    assert variants[0]["end_mode"] == "page_tail"
    assert "近三年平均投资收益率" in variants[0]["stop_terms"]


def test_boundary_keeps_rows_after_first_end_item() -> None:
    bounded, note = enforce_output_boundaries(
        "NON_LIFE_OPERATING_METRICS",
        ROWS,
        require_complete=False,
        table_config=CONFIG,
    )
    labels = [row[1] for row in bounded if len(row) > 1]
    assert "综合成本率（%）" in labels
    assert "综合赔付率（%）" in labels
    assert "综合费用率（%）" in labels
    assert "规模类指标" in labels
    assert "page_tail" not in note  # human-readable note only
    assert "已按首尾项目边界截取" in note


def test_slice_target_rows_keeps_page_tail() -> None:
    sliced = _slice_target_rows("NON_LIFE_OPERATING_METRICS", ROWS, CONFIG)
    labels = [row[1] for row in sliced if len(row) > 1]
    assert "保险业务收入" in labels
    assert "综合赔付率（%）" in labels
    assert "规模类指标" in labels
    assert "主要经营指标" not in labels[0]


if __name__ == "__main__":
    test_boundary_variants_expose_page_tail_mode()
    test_boundary_keeps_rows_after_first_end_item()
    test_slice_target_rows_keeps_page_tail()
    print("PASS 主要经营指标：页尾边界与目标区域裁切")
