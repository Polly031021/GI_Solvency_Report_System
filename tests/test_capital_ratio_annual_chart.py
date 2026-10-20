# -*- coding: utf-8 -*-
"""Regression tests for annual capital-ratio combination charts."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.solvency_step7_charts import (  # noqa: E402
    annual_period_order,
    build_capital_ratio_combo_chart,
)


def _capital_frame() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    values = {
        "2023Q2": (120.0, 80.0, 150.0),
        "2023Q4": (130.0, 90.0, 160.0),
        "2024Q1": (140.0, 95.0, 170.0),
        "2024Q4": (155.0, 100.0, 185.0),
        "2025Q2": (165.0, 110.0, 205.0),
    }
    for period, (actual, minimum, ratio) in values.items():
        for code, value in (
            ("ACTUAL_CAPITAL", actual),
            ("MINIMUM_CAPITAL", minimum),
            ("COMBINED_SOLVENCY_RATIO", ratio),
        ):
            rows.append(
                {
                    "公司": "甲公司",
                    "报告期": period,
                    "指标编码": code,
                    "期间口径": "本季度末数",
                    "数值": value,
                    "单位": "%" if code == "COMBINED_SOLVENCY_RATIO" else "亿元",
                }
            )
    return pd.DataFrame(rows)


def test_annual_period_order_prefers_q4() -> None:
    periods = ["2023Q2", "2023Q4", "2024Q1", "2024Q4", "2025Q2"]
    assert annual_period_order(periods) == ["2023Q4", "2024Q4", "2025Q2"]


def test_capital_combo_axis_uses_year_labels() -> None:
    frame = _capital_frame()
    periods = annual_period_order(frame["报告期"].astype(str).unique().tolist())
    chart = build_capital_ratio_combo_chart(
        frame,
        {"实际资本": ("ACTUAL_CAPITAL",), "最低资本": ("MINIMUM_CAPITAL",)},
        periods,
        "综合充足率变化",
        ratio_code="COMBINED_SOLVENCY_RATIO",
        ratio_label="综合偿付能力充足率（%）",
        annual_labels=True,
    )
    spec_text = json.dumps(chart.to_dict(), ensure_ascii=False)
    assert '"field": "显示期"' in spec_text
    assert '"sort": ["2023", "2024", "2025"]' in spec_text


if __name__ == "__main__":
    test_annual_period_order_prefers_q4()
    test_capital_combo_axis_uses_year_labels()
    print("全部通过")
