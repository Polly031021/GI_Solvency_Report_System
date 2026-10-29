# -*- coding: utf-8 -*-
"""Capability scores use the original pools and do not change with display filters."""

from __future__ import annotations

import pandas as pd

from step7_solvency.plan_inputs import capability_scores
from step7_solvency.specs import CAPABILITY_INDUSTRY_BENCHMARK_GROUPS


def _row(
    company: str,
    code: str,
    value: float,
    category: str,
) -> dict[str, object]:
    return {
        "公司": company,
        "指标编码": code,
        "报告期": "2025Q4",
        "期间口径": "本季度末数" if code != "COMBINED_RATIO" else "本年累计数",
        "数值": value,
        "单位": "%" if code == "COMBINED_RATIO" else "万元",
        "同业分类": category,
    }


def test_original_scoring_is_stable_when_display_filter_changes() -> None:
    baseline = pd.DataFrame(
        [
            _row("甲公司", "TOTAL_ASSETS", 70.0, "前三大"),
            _row("乙公司", "TOTAL_ASSETS", 90.0, "前三大"),
            _row("丙公司", "TOTAL_ASSETS", 50.0, "大型公司"),
            _row("丁公司", "TOTAL_ASSETS", 80.0, "大型公司"),
            _row("甲公司", "COMBINED_RATIO", 95.0, "前三大"),
            _row("乙公司", "COMBINED_RATIO", 105.0, "前三大"),
            _row("丙公司", "COMBINED_RATIO", 80.0, "大型公司"),
            _row("丁公司", "COMBINED_RATIO", 100.0, "大型公司"),
        ]
    )
    selected = baseline[
        baseline["公司"].isin(["甲公司", "丙公司"])
    ].copy()
    axes = [
        {
            "code": "TOTAL_ASSETS",
            "label": "资产",
            "period": "本季度末数",
        },
        {
            "code": "COMBINED_RATIO",
            "label": "承保",
            "period": "本年累计数",
            "lower_is_better": True,
        },
    ]

    full_scores, _, _ = capability_scores(
        baseline,
        axes,
        ["2025Q4"],
        baseline_frame=baseline,
        industry_benchmark_groups=CAPABILITY_INDUSTRY_BENCHMARK_GROUPS,
    )
    selected_scores, _, _ = capability_scores(
        selected,
        axes,
        ["2025Q4"],
        baseline_frame=baseline,
        industry_benchmark_groups=CAPABILITY_INDUSTRY_BENCHMARK_GROUPS,
    )
    full_scores = full_scores.set_index("公司")
    selected_scores = selected_scores.set_index("公司")

    # Filtering display companies must return exactly the scores already
    # calculated in the full-company universe.
    for company in ("甲公司", "丙公司"):
        assert (
            selected_scores.loc[company, "资产_得分"]
            == full_scores.loc[company, "资产_得分"]
        )
        assert (
            selected_scores.loc[company, "承保_得分"]
            == full_scores.loc[company, "承保_得分"]
        )

    # Top 3 keeps the original full-industry ranking pool.
    assert abs(float(selected_scores.loc["甲公司", "资产_得分"]) - (30 + 70 / 3)) < 1e-9
    # Large companies keep their same-category pool.
    assert selected_scores.loc["丙公司", "资产_得分"] == 30.0


if __name__ == "__main__":
    test_original_scoring_is_stable_when_display_filter_changes()
