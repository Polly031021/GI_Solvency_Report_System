# -*- coding: utf-8 -*-
"""Regression tests for the property-capital 100% stack chart."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.solvency_step7_charts import build_capital_amount_combo  # noqa: E402


def test_capital_amount_stack_excludes_tier_two_capital() -> None:
    rows = []
    for period, values in {
        "2024Q4": (60.0, 20.0, 15.0, 5.0),
        "2025Q4": (65.0, 18.0, 14.0, 3.0),
    }.items():
        for code, value in zip(
            (
                "CORE_T1_CAPITAL",
                "CORE_T2_CAPITAL",
                "ANC_T1_CAPITAL",
                "ANC_T2_CAPITAL",
            ),
            values,
        ):
            rows.append(
                {
                    "公司": "甲公司",
                    "报告期": period,
                    "指标编码": code,
                    "数值": value,
                    "单位": "亿元",
                }
            )
    chart = build_capital_amount_combo(
        pd.DataFrame(rows),
        ["2024Q4", "2025Q4"],
        panel_count=1,
        annual_labels=True,
    )
    spec_text = json.dumps(chart.to_dict(), ensure_ascii=False)
    assert "CORE_T1_CAPITAL" in spec_text
    assert "ANC_T1_CAPITAL" in spec_text
    assert "CORE_T2_CAPITAL" not in spec_text
    assert "ANC_T2_CAPITAL" not in spec_text
    assert '"field": "显示期"' in spec_text
    assert '"sort": ["2024", "2025"]' in spec_text


if __name__ == "__main__":
    test_capital_amount_stack_excludes_tier_two_capital()
    print("全部通过")
