# -*- coding: utf-8 -*-
"""行业整体保费结构的纯逻辑回归：Q4 全年累计、车险拆分与五年窗口。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.solvency_step7_charts import (  # noqa: E402
    HISTORICAL_INDUSTRY_PREMIUM,
    HISTORICAL_SOURCE,
    UPLOAD_SOURCE,
    build_industry_premium_structure_chart,
    prepare_industry_premium_annual,
)


def _complete_rows(*, end_year: int = 2025) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for year in range(2022, end_year + 1):
        for company_index, company in enumerate(("甲公司", "乙公司", "丙公司")):
            base = 10_000.0 * (company_index + 1) * (year - 2021)
            for quarter in range(1, 5):
                # Q1-Q3 values are deliberately cumulative stepping stones;
                # the chart must pick the Q4 annual-cumulative value.
                total = base * quarter
                motor = total * 0.6
                for code, value in (
                    ("SIGNED_PREMIUM", total),
                    ("SIGNED_PREMIUM_MOTOR", motor),
                ):
                    rows.append(
                        {
                            "公司": company,
                            "报告期": f"{year}Q{quarter}",
                            "指标编码": code,
                            "期间口径": "本年累计数",
                            "数值": value,
                            "单位": "万元",
                        }
                    )
    return pd.DataFrame(rows)


def test_industry_premium_uses_q4_annual_cumulative() -> None:
    frame = _complete_rows()
    annual, detail, companies = prepare_industry_premium_annual(
        frame,
        years=5,
        include_historical=False,
    )
    assert len(companies) == 3
    assert annual["年度"].tolist() == ["2022", "2023", "2024", "2025"]
    assert annual["完整"].all()
    # Three companies at Q4 2022: 40k + 80k + 120k.
    assert annual.iloc[0]["合计"] == 240_000.0
    assert annual.iloc[0]["车险"] == 144_000.0
    assert annual.iloc[0]["非车险"] == 96_000.0
    assert annual.iloc[-1]["合计"] == 960_000.0
    assert set(detail["状态"].astype(str)) <= {"", "nan"}


def test_industry_premium_chart_renders_complete_2022_2025() -> None:
    annual, _, _ = prepare_industry_premium_annual(
        _complete_rows(),
        years=5,
        include_historical=False,
    )
    chart = build_industry_premium_structure_chart(annual, unit_mode="亿元")
    spec = chart.to_dict()
    assert len(spec["layer"]) == 6
    assert spec["resolve"]["scale"]["color"] == "independent"
    assert spec["resolve"]["scale"]["y"] == "independent"
    assert spec["layer"][0]["encoding"]["color"]["scale"]["range"] == [
        "#1E49E2",
        "#00B8F5",
        "#D9E2EC",
    ]
    # A 0% reference rule, a base-year marker and the YoY growth line are
    # added on the secondary axis.
    assert sum(layer["mark"]["type"] == "rule" for layer in spec["layer"]) == 1
    growth_line = next(
        layer for layer in spec["layer"] if layer["mark"]["type"] == "line"
    )
    assert growth_line["encoding"]["y"]["field"] == "增长率"
    assert growth_line["encoding"]["y"]["axis"]["orient"] == "right"
    # 右侧只显示百分比刻度：增长率已按百分数存储，用 labelExpr 补上 %，
    # 避免金额轴或原始数值刻度出现在同一侧造成叠加。
    assert growth_line["encoding"]["y"]["axis"]["format"] == ".0f"
    assert growth_line["encoding"]["y"]["axis"]["labelExpr"] == "datum.label + '%'"
    assert spec["layer"][0]["encoding"]["y"]["axis"]["orient"] == "left"
    assert spec["layer"][1]["encoding"]["y"]["axis"] is None
    assert growth_line["encoding"]["color"]["scale"]["range"] == [
        "#7213EA",
        "#FD349C",
        "#00338D",
    ]
    assert spec["layer"][0]["encoding"]["x"]["scale"]["paddingInner"] == 0.2
    assert spec["layer"][0]["mark"]["size"] == 32
    assert spec["width"] == 600
    assert spec["config"]["axis"]["labelColor"] == "#475569"
    assert spec["config"]["axis"]["gridColor"] == "#E5E7EB"
    x_sort = spec["layer"][0]["encoding"]["x"]["sort"]
    assert x_sort == ["2022", "2023", "2024", "2025"]


def test_industry_premium_handles_2022_2026_window() -> None:
    annual, _, _ = prepare_industry_premium_annual(
        _complete_rows(end_year=2026),
        years=5,
        include_historical=False,
    )
    assert annual["年度"].tolist() == ["2022", "2023", "2024", "2025", "2026"]
    assert annual["完整"].all()
    chart = build_industry_premium_structure_chart(annual, unit_mode="亿元")
    assert chart.to_dict()["layer"][0]["encoding"]["x"]["sort"] == [
        "2022",
        "2023",
        "2024",
        "2025",
        "2026",
    ]


def test_industry_premium_still_renders_partial_year() -> None:
    frame = _complete_rows()
    frame = frame[
        ~(
            frame["报告期"].str.startswith("2023")
            & frame["指标编码"].eq("SIGNED_PREMIUM_MOTOR")
            & frame["公司"].eq("丙公司")
        )
    ]
    annual, _, _ = prepare_industry_premium_annual(
        frame,
        years=5,
        include_historical=False,
    )
    assert not annual.loc[annual["年度"].eq("2023"), "完整"].iloc[0]
    chart = build_industry_premium_structure_chart(annual, unit_mode="亿元")
    spec = chart.to_dict()
    assert len(spec["layer"]) == 6
    assert spec["layer"][0]["encoding"]["x"]["sort"] == ["2022", "2023", "2024", "2025"]
    assert spec["layer"][0]["encoding"]["color"]["scale"]["domain"] == [
        "车险",
        "非车险",
        "未拆分",
    ]


def test_historical_baseline_fills_missing_years_and_upload_wins() -> None:
    annual, _, _ = prepare_industry_premium_annual(
        _complete_rows(end_year=2024),
        years=None,
        include_historical=True,
    )
    assert annual["年度"].tolist() == [
        "2017",
        "2018",
        "2019",
        "2020",
        "2021",
        "2022",
        "2023",
        "2024",
    ]
    assert annual["来源"].tolist() == [
        HISTORICAL_SOURCE,
        HISTORICAL_SOURCE,
        HISTORICAL_SOURCE,
        HISTORICAL_SOURCE,
        HISTORICAL_SOURCE,
        UPLOAD_SOURCE,
        UPLOAD_SOURCE,
        UPLOAD_SOURCE,
    ]

    historical_2021 = annual[annual["年度"].eq("2021")].iloc[0]
    assert historical_2021["合计"] == 13_676.0 * 10_000.0
    assert historical_2021["车险"] == 13_676.0 * 10_000.0 * 0.57
    assert historical_2021["非车险"] == 13_676.0 * 10_000.0 * 0.43
    assert historical_2021["完整"]

    # The uploaded 2024 aggregate is 120k + 240k + 360k, not the fixed 16,907 亿.
    uploaded_2024 = annual[annual["年度"].eq("2024")].iloc[0]
    assert uploaded_2024["来源"] == UPLOAD_SOURCE
    assert uploaded_2024["合计"] == 720_000.0
    assert [row[0] for row in HISTORICAL_INDUSTRY_PREMIUM] == list(range(2017, 2025))


def test_display_range_switches_between_five_ten_and_all_years() -> None:
    frame = _complete_rows(end_year=2025)
    five, _, _ = prepare_industry_premium_annual(frame, years=5)
    ten, _, _ = prepare_industry_premium_annual(frame, years=10)
    every, _, _ = prepare_industry_premium_annual(frame, years=None)
    assert five["年度"].tolist() == ["2021", "2022", "2023", "2024", "2025"]
    assert ten["年度"].tolist() == every["年度"].tolist()
    assert every["年度"].tolist() == [
        "2017",
        "2018",
        "2019",
        "2020",
        "2021",
        "2022",
        "2023",
        "2024",
        "2025",
    ]
    chart = build_industry_premium_structure_chart(every, unit_mode="亿元")
    assert chart.to_dict()["width"] == 882


if __name__ == "__main__":
    test_industry_premium_uses_q4_annual_cumulative()
    test_industry_premium_chart_renders_complete_2022_2025()
    test_industry_premium_handles_2022_2026_window()
    test_industry_premium_still_renders_partial_year()
    test_historical_baseline_fills_missing_years_and_upload_wins()
    test_display_range_switches_between_five_ten_and_all_years()
    print("PASS 行业保费结构：固定历史、上传覆盖、Q4 累计与 5/10/全部窗口")
