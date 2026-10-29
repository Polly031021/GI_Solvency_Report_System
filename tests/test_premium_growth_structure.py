# -*- coding: utf-8 -*-
"""Regression tests for the five-year premium-growth structure chart."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.solvency_step7_charts import (  # noqa: E402
    build_premium_growth_structure_chart,
    group_premium_growth_panels,
    prepare_premium_growth_panel,
    premium_growth_axis_domain,
)


def _premium_frame() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    totals = {
        "甲公司": [1000.0, 1120.0, 1080.0, 1260.0, 1450.0, 1600.0],
        "乙公司": [800.0, 760.0, 850.0, 910.0, 1010.0, 980.0],
    }
    motor_ratios = {
        "甲公司": [0.62, 0.60, 0.58, 0.55, 0.52, 0.49],
        "乙公司": [0.45, 0.46, 0.48, 0.50, 0.51, 0.53],
    }
    peer_groups = {"甲公司": "前十大", "乙公司": "外资系"}
    for company, values in totals.items():
        for index, (year, total) in enumerate(zip(range(2020, 2026), values)):
            period = f"{year}Q4"
            rows.extend(
                [
                    {
                        "公司": company,
                        "报告期": period,
                        "指标编码": "SIGNED_PREMIUM",
                        "期间口径": "本年累计数",
                        "数值": total,
                        "同业分类": peer_groups[company],
                    },
                    {
                        "公司": company,
                        "报告期": period,
                        "指标编码": "SIGNED_PREMIUM_MOTOR",
                        "期间口径": "本年累计数",
                        "数值": total * motor_ratios[company][index],
                        "同业分类": peer_groups[company],
                    },
                ]
            )
    return pd.DataFrame(rows)


def test_prepares_five_bars_and_total_premium_growth() -> None:
    frame = _premium_frame()
    panel = prepare_premium_growth_panel(frame, "甲公司")

    assert panel.years == ("2021", "2022", "2023", "2024", "2025")
    assert panel.company_group == "前十大"
    assert set(panel.bar_frame["年度"]) == set(panel.years)
    shares = panel.bar_frame.groupby("年度")["占比"].sum()
    assert all(abs(float(value) - 100.0) < 1e-9 for value in shares)

    growth = panel.line_frame.set_index("年度")["同比增速"]
    assert "2020" not in growth.index
    assert abs(float(growth.loc["2021"]) - 12.0) < 1e-9
    assert abs(float(growth.loc["2022"]) - ((1080.0 - 1120.0) / 1120.0 * 100.0)) < 1e-9
    assert growth.index.tolist() == list(panel.years)


def test_missing_motor_split_uses_unallocated_segment() -> None:
    frame = _premium_frame()
    frame = frame[
        ~(
            frame["公司"].eq("甲公司")
            & frame["指标编码"].eq("SIGNED_PREMIUM_MOTOR")
            & frame["报告期"].eq("2025Q4")
        )
    ]
    panel = prepare_premium_growth_panel(frame, "甲公司")
    latest = panel.bar_frame[panel.bar_frame["年度"].eq("2025")]
    assert latest["类别"].tolist() == ["未拆分"]
    assert float(latest.iloc[0]["占比"]) == 100.0


def test_chart_uses_independent_left_and_right_axes() -> None:
    frame = _premium_frame()
    frame = pd.concat(
        [
            frame,
            pd.DataFrame(
                [
                    {
                        "公司": company,
                        "报告期": f"{year}Q4",
                        "指标编码": "COMBINED_SOLVENCY_RATIO",
                        "期间口径": "本季度末数",
                        "数值": 180.0 + year - 2021,
                        "同业分类": "前十大",
                    }
                    for company in ("甲公司", "乙公司")
                    for year in range(2021, 2026)
                ]
            ),
        ],
        ignore_index=True,
    )
    panels = [prepare_premium_growth_panel(frame, company) for company in ("甲公司", "乙公司")]
    assert not panels[0].solvency_frame.empty
    chart = build_premium_growth_structure_chart(
        panels[0],
        growth_domain=premium_growth_axis_domain(panels),
        unit_label="亿元",
    )
    spec = chart.to_dict()
    assert spec["resolve"]["scale"]["y"] == "independent"
    spec_text = str(spec)
    assert "'orient': 'right'" in spec_text
    assert "总保费同比增速" in spec_text
    assert "'title': '年份'" in spec_text
    assert "'width': 310" in spec_text
    assert "综合偿付能力充足率" not in spec_text


def test_groups_follow_company_category_order() -> None:
    frame = _premium_frame()
    panels = [
        prepare_premium_growth_panel(frame, company)
        for company in ("乙公司", "甲公司")
    ]
    grouped = group_premium_growth_panels(panels)
    assert [name for name, _ in grouped] == ["前十大", "外资系"]
    assert [panel.company for panel in grouped[0][1]] == ["甲公司"]
    assert [panel.company for panel in grouped[1][1]] == ["乙公司"]


def test_group_falls_back_to_company_type() -> None:
    frame = _premium_frame().drop(columns=["同业分类"])
    frame["公司类型"] = "中型公司"
    panel = prepare_premium_growth_panel(frame, "甲公司")
    assert panel.company_group == "中型公司"


if __name__ == "__main__":
    test_prepares_five_bars_and_total_premium_growth()
    test_missing_motor_split_uses_unallocated_segment()
    test_chart_uses_independent_left_and_right_axes()
    test_groups_follow_company_category_order()
    test_group_falls_back_to_company_type()
    print("全部通过")
