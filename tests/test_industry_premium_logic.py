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
    build_industry_premium_mix_chart,
    build_industry_premium_structure_chart,
    build_industry_premium_total_chart,
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


def test_industry_premium_total_chart_has_bars_and_growth_line() -> None:
    annual, _, _ = prepare_industry_premium_annual(
        _complete_rows(),
        years=5,
        include_historical=False,
    )
    spec = build_industry_premium_total_chart(annual, unit_mode="亿元").to_dict()
    assert spec["width"] == "container"
    assert spec["resolve"]["scale"]["y"] == "independent"
    assert [layer["mark"]["type"] for layer in spec["layer"]] == [
        "bar",
        "text",
        "rule",
        "point",
        "text",
        "line",
    ]
    bar_layer = spec["layer"][0]
    assert bar_layer["encoding"]["y"]["field"] == "合计"
    assert bar_layer["encoding"]["y"]["axis"]["orient"] == "left"
    assert bar_layer["mark"]["size"] == 28
    assert bar_layer["encoding"]["x"]["scale"]["paddingInner"] == 0.12
    assert bar_layer["encoding"]["x"]["sort"] == ["2022", "2023", "2024", "2025"]
    assert bar_layer["encoding"]["x"]["title"] == "年份"
    assert bar_layer["encoding"]["x"]["axis"]["values"] == [
        "2022",
        "2023",
        "2024",
        "2025",
    ]
    assert bar_layer["encoding"]["x"]["axis"]["domain"] is True
    assert "color" not in bar_layer["encoding"]
    growth_line = next(
        layer for layer in spec["layer"] if layer["mark"]["type"] == "line"
    )
    assert growth_line["encoding"]["y"]["field"] == "增长率"
    assert growth_line["encoding"]["y"]["axis"]["orient"] == "right"
    assert growth_line["encoding"]["y"]["axis"]["format"] == ".0f"
    assert growth_line["encoding"]["y"]["axis"]["labelExpr"] == "datum.label + '%'"
    assert spec["config"]["axis"]["labelColor"] == "#475569"
    assert spec["config"]["axis"]["gridColor"] == "#E5E7EB"
    vega_spec = build_industry_premium_total_chart(
        annual, unit_mode="亿元"
    ).to_dict(format="vega")
    year_axis = next(
        axis
        for axis in vega_spec["axes"]
        if (
            axis.get("scale") == "x"
            or str(axis.get("scale", "")).startswith("layer_")
            and axis.get("orient") == "bottom"
        )
    )
    assert year_axis["title"] == "年份"
    assert year_axis["values"] == ["2022", "2023", "2024", "2025"]


def test_industry_premium_mix_chart_uses_100_percent_stack() -> None:
    annual, _, _ = prepare_industry_premium_annual(
        _complete_rows(),
        years=5,
        include_historical=False,
    )
    spec = build_industry_premium_mix_chart(annual, unit_mode="亿元").to_dict()
    assert spec["width"] == "container"
    assert spec["resolve"]["scale"]["y"] == "independent"
    assert spec["resolve"]["scale"]["color"] == "independent"
    assert [layer["mark"]["type"] for layer in spec["layer"]] == [
        "bar",
        "text",
        "rule",
        "point",
        "text",
        "line",
    ]
    share_bar = spec["layer"][0]
    assert share_bar["encoding"]["y"]["field"] == "占比"
    assert share_bar["encoding"]["y"]["axis"]["format"] == ".0%"
    assert share_bar["encoding"]["y"]["axis"]["orient"] == "left"
    assert share_bar["encoding"]["color"]["scale"]["range"] == [
        "#1E49E2",
        "#00B8F5",
    ]
    # Share labels sit inside the stacked segments.
    share_label = spec["layer"][1]
    assert share_label["mark"]["type"] == "text"
    assert share_label["encoding"]["y"]["field"] == "标签位置"
    growth_line = next(
        layer for layer in spec["layer"] if layer["mark"]["type"] == "line"
    )
    assert growth_line["encoding"]["y"]["field"] == "增长率"
    assert growth_line["encoding"]["y"]["axis"]["orient"] == "right"
    assert growth_line["encoding"]["y"]["axis"]["labelExpr"] == "datum.label + '%'"
    assert growth_line["encoding"]["color"]["scale"]["range"] == [
        "#7213EA",
        "#FD349C",
    ]
    assert share_bar["encoding"]["x"]["sort"] == ["2022", "2023", "2024", "2025"]
    assert share_bar["encoding"]["x"]["title"] == "年份"
    assert share_bar["encoding"]["x"]["axis"]["values"] == [
        "2022",
        "2023",
        "2024",
        "2025",
    ]
    vega_spec = build_industry_premium_mix_chart(
        annual, unit_mode="亿元"
    ).to_dict(format="vega")
    assert any(
        axis.get("orient") == "bottom" and axis.get("title") == "年份"
        for axis in vega_spec["axes"]
    )


def test_combined_structure_chart_places_two_panels_side_by_side() -> None:
    annual, _, _ = prepare_industry_premium_annual(
        _complete_rows(),
        years=5,
        include_historical=False,
    )
    spec = build_industry_premium_structure_chart(annual, unit_mode="亿元").to_dict()
    assert len(spec["hconcat"]) == 2
    assert spec["hconcat"][0]["layer"][0]["encoding"]["y"]["field"] == "合计"
    assert spec["hconcat"][1]["layer"][0]["encoding"]["y"]["field"] == "占比"


def test_industry_premium_handles_2022_2026_window() -> None:
    annual, _, _ = prepare_industry_premium_annual(
        _complete_rows(end_year=2026),
        years=5,
        include_historical=False,
    )
    assert annual["年度"].tolist() == ["2022", "2023", "2024", "2025", "2026"]
    assert annual["完整"].all()
    total_spec = build_industry_premium_total_chart(annual, unit_mode="亿元").to_dict()
    mix_spec = build_industry_premium_mix_chart(annual, unit_mode="亿元").to_dict()
    expected = ["2022", "2023", "2024", "2025", "2026"]
    assert total_spec["layer"][0]["encoding"]["x"]["sort"] == expected
    assert mix_spec["layer"][0]["encoding"]["x"]["sort"] == expected


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
    spec = build_industry_premium_mix_chart(annual, unit_mode="亿元").to_dict()
    assert len(spec["layer"]) == 6
    assert spec["layer"][0]["encoding"]["x"]["sort"] == ["2022", "2023", "2024", "2025"]
    assert spec["layer"][0]["encoding"]["color"]["scale"]["domain"] == [
        "车险",
        "非车险",
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


def _scale_error_rows() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for year, total, motor in (
        (2022, 500_000.0, 300_000.0),
        (2023, 550_000.0, 330_000.0),
        (2024, 600_000.0, 360_000.0),
    ):
        for code, value in (("SIGNED_PREMIUM", total), ("SIGNED_PREMIUM_MOTOR", motor)):
            rows.append(
                {
                    "公司": "甲公司",
                    "报告期": f"{year}Q4",
                    "指标编码": code,
                    "期间口径": "本年累计数",
                    "数值": value,
                    "单位": "万元",
                }
            )
    # 2025 annual total was delivered in 元 but labelled 万元 (10,000x too large).
    rows.append(
        {
            "公司": "甲公司",
            "报告期": "2025Q4",
            "指标编码": "SIGNED_PREMIUM",
            "期间口径": "本年累计数",
            "数值": 6_050_000_000.0,
            "单位": "万元",
        }
    )
    rows.append(
        {
            "公司": "甲公司",
            "报告期": "2025Q4",
            "指标编码": "SIGNED_PREMIUM",
            "期间口径": "本季度数",
            "数值": 150_000.0,
            "单位": "万元",
        }
    )
    # 乙公司 2025 annual total was delivered in 亿元 but labelled 万元
    # (10,000x too small); the Q4 quarter value and its own history anchor it.
    for year, total in ((2023, 700_000.0), (2024, 800_000.0)):
        rows.append(
            {
                "公司": "乙公司",
                "报告期": f"{year}Q4",
                "指标编码": "SIGNED_PREMIUM",
                "期间口径": "本年累计数",
                "数值": total,
                "单位": "万元",
            }
        )
    rows.append(
        {
            "公司": "乙公司",
            "报告期": "2025Q4",
            "指标编码": "SIGNED_PREMIUM",
            "期间口径": "本年累计数",
            "数值": 80.0,
            "单位": "万元",
        }
    )
    rows.append(
        {
            "公司": "乙公司",
            "报告期": "2025Q4",
            "指标编码": "SIGNED_PREMIUM",
            "期间口径": "本季度数",
            "数值": 200_000.0,
            "单位": "万元",
        }
    )
    return pd.DataFrame(rows)


def test_reported_unit_scale_values_are_aggregated_as_is() -> None:
    annual, detail, _ = prepare_industry_premium_annual(
        _scale_error_rows(),
        years=None,
        include_historical=False,
    )
    latest = annual[annual["年度"].eq("2025")].iloc[0]
    # No automatic scale repair: the source numeric column is summed exactly.
    # 甲 6,050,000,000 + 乙 80.
    assert latest["合计"] == 6_050_000_080.0
    assert latest["异常数"] == 0
    assert set(
        detail.loc[detail["年度"].eq("2025"), "状态"].astype(str)
    ) == {"缺车险拆分"}


def test_company_disclosure_suffix_keeps_total_and_motor_split_together() -> None:
    rows = [
        {
            "公司": "丙公司（偿付能力）",
            "报告期": "2025Q4",
            "指标编码": "SIGNED_PREMIUM",
            "期间口径": "本年累计数",
            "数值": 900_000.0,
            "单位": "万元",
        },
        {
            "公司": "丙公司",
            "报告期": "2025Q4",
            "指标编码": "SIGNED_PREMIUM_MOTOR",
            "期间口径": "本年累计数",
            "数值": 500_000.0,
            "单位": "万元",
        },
    ]
    annual, detail, _ = prepare_industry_premium_annual(
        pd.DataFrame(rows),
        years=None,
        include_historical=False,
    )
    latest = annual[annual["年度"].eq("2025")].iloc[0]
    assert latest["合计"] == 900_000.0
    assert latest["车险"] == 500_000.0
    assert latest["非车险"] == 400_000.0
    assert detail.iloc[0]["公司"] == "丙公司"
    assert detail.iloc[0]["状态"] == ""


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
    total_spec = build_industry_premium_total_chart(every, unit_mode="亿元").to_dict()
    mix_spec = build_industry_premium_mix_chart(every, unit_mode="亿元").to_dict()
    expected = [str(year) for year in range(2017, 2026)]
    assert total_spec["width"] == "container"
    assert mix_spec["width"] == "container"
    assert total_spec["layer"][0]["encoding"]["x"]["sort"] == expected
    assert mix_spec["layer"][0]["encoding"]["x"]["sort"] == expected


def test_source_review_flag_does_not_change_raw_value() -> None:
    rows = [
        {
            "公司": "利宝保险",
            "报告期": "2025Q4",
            "指标编码": "SIGNED_PREMIUM",
            "期间口径": "本年累计数",
            "数值": 33.4421,
            "单位": "万元",
            "数据校验状态": "待复核",
            "校验说明": "疑似单位/量级异常（待复核_信号分歧），未自动修正，请人工核对",
        },
        {
            "公司": "利宝保险",
            "报告期": "2025Q4",
            "指标编码": "SIGNED_PREMIUM_MOTOR",
            "期间口径": "本年累计数",
            "数值": 175_466.85,
            "单位": "万元",
        },
        {
            "公司": "己公司",
            "报告期": "2025Q4",
            "指标编码": "SIGNED_PREMIUM",
            "期间口径": "本年累计数",
            "数值": 500_000.0,
            "单位": "万元",
        },
        {
            "公司": "己公司",
            "报告期": "2025Q4",
            "指标编码": "SIGNED_PREMIUM_MOTOR",
            "期间口径": "本年累计数",
            "数值": 300_000.0,
            "单位": "万元",
        },
    ]
    annual, detail, _ = prepare_industry_premium_annual(
        pd.DataFrame(rows),
        years=None,
        include_historical=False,
    )
    latest = annual[annual["年度"].eq("2025")].iloc[0]
    # The validation status is ignored for calculation: the reported numeric
    # value is summed as-is, so the industry total stays the raw sum.
    assert latest["合计"] == 500_033.4421
    assert latest["异常数"] == 1
    flagged = detail[detail["公司"].eq("利宝保险")].iloc[0]
    assert flagged["合计"] == 33.4421
    assert flagged["状态"] == "异常（车险大于合计）"


if __name__ == "__main__":
    test_industry_premium_uses_q4_annual_cumulative()
    test_industry_premium_total_chart_has_bars_and_growth_line()
    test_industry_premium_mix_chart_uses_100_percent_stack()
    test_combined_structure_chart_places_two_panels_side_by_side()
    test_industry_premium_handles_2022_2026_window()
    test_industry_premium_still_renders_partial_year()
    test_historical_baseline_fills_missing_years_and_upload_wins()
    test_reported_unit_scale_values_are_aggregated_as_is()
    test_company_disclosure_suffix_keeps_total_and_motor_split_together()
    test_source_review_flag_does_not_change_raw_value()
    test_display_range_switches_between_five_ten_and_all_years()
    print("PASS 行业保费结构：固定历史、上传覆盖、Q4 累计与 5/10/全部窗口")
