# -*- coding: utf-8 -*-
"""Regression tests for the company performance ranking trajectory chart."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.solvency_step7_charts import build_performance_pattern_trajectory  # noqa: E402


def _performance_frame() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    values = {
        "甲公司": {"SIGNED_PREMIUM": (1200.0, 1450.0), "NET_PROFIT": (120.0, 180.0)},
        "乙公司": {"SIGNED_PREMIUM": (1100.0, 1500.0), "NET_PROFIT": (150.0, 140.0)},
        "丙公司": {"SIGNED_PREMIUM": (900.0, 1000.0), "NET_PROFIT": (90.0, 110.0)},
    }
    for company, metric_values in values.items():
        for code, series in metric_values.items():
            for period, value in zip(("2024Q4", "2025Q4"), series):
                rows.append(
                    {
                        "公司": company,
                        "报告期": period,
                        "指标编码": code,
                        "期间口径": "本年累计数",
                        "数值": value,
                    }
                )
    return pd.DataFrame(rows)


def test_axis_titles_name_both_rankings() -> None:
    chart = build_performance_pattern_trajectory(
        _performance_frame(),
        "甲公司",
        ["2024Q4", "2025Q4"],
        x_title="签单保费排名",
        y_title="净利润排名",
    )
    spec_text = str(chart.to_dict())
    assert "签单保费排名" in spec_text
    assert "净利润排名" in spec_text


if __name__ == "__main__":
    test_axis_titles_name_both_rankings()
    print("全部通过")
