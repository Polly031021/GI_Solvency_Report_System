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
    build_single_metric_trend_chart,
    capital_structure_industry_ratios,
    convert_multiple_units_to_percent,
    insurance_risk_liability_pressure_sentence,
    prepare_metric_trend,
)
from services.step7_excel_config import load_step7_config  # noqa: E402
from step7_solvency.rendering import prepare_annual_metric_frame  # noqa: E402
from step7_solvency.report import configured_chart_periods  # noqa: E402


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


def test_generic_metric_frame_uses_year_labels() -> None:
    frame = _capital_frame()
    frame = frame[frame["指标编码"].eq("COMBINED_SOLVENCY_RATIO")].copy()
    displayed, periods = prepare_annual_metric_frame(frame)
    assert periods == ["2023", "2024", "2025"]
    assert displayed["报告期"].tolist() == ["2023", "2024", "2025"]


def test_actual_ratio_marks_period_maximum_and_minimum() -> None:
    rows = []
    values = {
        "2024": {"甲公司": 16.0, "乙公司": 14.0, "丙公司": 15.0},
        "2025": {"甲公司": 13.0, "乙公司": 17.0, "丙公司": 15.5},
    }
    for period, company_values in values.items():
        for company, value in company_values.items():
            rows.append(
                {
                    "公司": company,
                    "报告期": period,
                    "指标编码": "ACTUAL_CAPITAL_TO_RECOGNIZED_ASSETS",
                    "指标名称": "实际资本/认可资产率",
                    "数值": value,
                    "单位": "%",
                    "数据类型": "百分比",
                }
            )
    metric, _, _, _ = prepare_metric_trend(
        pd.DataFrame(rows),
        "ACTUAL_CAPITAL_TO_RECOGNIZED_ASSETS",
        ["2024", "2025"],
        "",
    )
    for period in ("2024", "2025"):
        period_rows = metric[metric["报告期"].eq(period)]
        assert int(period_rows["是否全局最大"].sum()) == 1
        assert int(period_rows["是否全局最小"].sum()) == 1
    maxima = metric[metric["是否全局最大"]].set_index("报告期")["公司"].to_dict()
    minima = metric[metric["是否全局最小"]].set_index("报告期")["公司"].to_dict()
    assert maxima == {"2024": "甲公司", "2025": "乙公司"}
    assert minima == {"2024": "乙公司", "2025": "甲公司"}
    chart = build_single_metric_trend_chart(
        pd.DataFrame(rows),
        "ACTUAL_CAPITAL_TO_RECOGNIZED_ASSETS",
        ["2024", "2025"],
        {"甲公司": "#00338D", "乙公司": "#00B8F5", "丙公司": "#7213EA"},
    )
    spec_text = json.dumps(chart.to_dict(), ensure_ascii=False)
    assert '"labelAngle": 0' in spec_text


def test_core_metric_year_window_uses_one_annual_observation() -> None:
    periods = [
        "2022Q4",
        "2023Q2",
        "2023Q4",
        "2024Q1",
        "2024Q4",
        "2025Q2",
        "2026Q1",
    ]
    selected = configured_chart_periods(
        periods,
        {"annual_labels": True, "year_start": 2023, "year_end": 2025},
    )
    assert selected == ["2023Q4", "2024Q4", "2025Q2"]


def test_years_limit_keeps_latest_five_annual_observations() -> None:
    periods = [f"{year}Q4" for year in range(2018, 2026)]
    selected = configured_chart_periods(
        periods,
        {"annual_labels": True, "years": 5},
    )
    assert selected == ["2021Q4", "2022Q4", "2023Q4", "2024Q4", "2025Q4"]


def test_non_life_risk_liability_ratio_uses_percent() -> None:
    rows = [
        {
            "公司": "甲公司",
            "报告期": "2024Q4",
            "指标编码": "NON_LIFE_INSURANCE_RISK_TO_LIABILITIES",
            "指标名称": "保险风险/认可负债率",
            "数值": 0.0262,
            "单位": "倍",
            "数据类型": "比率",
        },
        {
            "公司": "甲公司",
            "报告期": "2025Q4",
            "指标编码": "NON_LIFE_INSURANCE_RISK_TO_LIABILITIES",
            "指标名称": "保险风险/认可负债率",
            "数值": 0.0250,
            "单位": "倍",
            "数据类型": "比率",
        },
    ]
    frame = pd.DataFrame(rows[:1])
    converted = convert_multiple_units_to_percent(frame)
    assert abs(float(converted.iloc[0]["数值"]) - 2.62) < 1e-9
    assert converted.iloc[0]["单位"] == "%"
    chart = build_single_metric_trend_chart(
        pd.DataFrame(rows),
        "NON_LIFE_INSURANCE_RISK_TO_LIABILITIES",
        ["2024Q4", "2025Q4"],
        {"甲公司": "#00338D"},
    )
    spec_text = json.dumps(chart.to_dict(), ensure_ascii=False)
    assert '"labelAngle": 0' in spec_text


def test_non_life_risk_liability_marks_period_extremes() -> None:
    rows = []
    values = {
        "2024Q4": {"甲公司": 0.0280, "乙公司": 0.0163, "丙公司": 0.0230},
        "2025Q4": {"甲公司": 0.0240, "乙公司": 0.0210, "丙公司": 0.0260},
    }
    for period, company_values in values.items():
        for company, value in company_values.items():
            rows.append(
                {
                    "公司": company,
                    "报告期": period,
                    "指标编码": "NON_LIFE_INSURANCE_RISK_TO_LIABILITIES",
                    "指标名称": "保险风险/认可负债率",
                    "数值": value,
                    "单位": "倍",
                    "数据类型": "比率",
                }
            )
    metric, _, _, _ = prepare_metric_trend(
        pd.DataFrame(rows),
        "NON_LIFE_INSURANCE_RISK_TO_LIABILITIES",
        ["2024Q4", "2025Q4"],
        "",
    )
    maxima = metric[metric["是否全局最大"]].set_index("报告期")["公司"].to_dict()
    minima = metric[metric["是否全局最小"]].set_index("报告期")["公司"].to_dict()
    assert maxima == {"2024Q4": "甲公司", "2025Q4": "丙公司"}
    assert minima == {"2024Q4": "乙公司", "2025Q4": "乙公司"}


def test_investment_return_uses_horizontal_year_labels() -> None:
    rows = []
    for year in range(2021, 2026):
        rows.append(
            {
                "公司": "甲公司",
                "报告期": f"{year}Q4",
                "指标编码": "INVESTMENT_RETURN",
                "指标名称": "投资收益率",
                "数值": 3.0 + (year - 2021) * 0.1,
                "单位": "%",
                "数据类型": "百分比",
            }
        )
    chart = build_single_metric_trend_chart(
        pd.DataFrame(rows),
        "INVESTMENT_RETURN",
        [f"{year}Q4" for year in range(2021, 2026)],
        {"甲公司": "#00338D"},
    )
    spec_text = json.dumps(chart.to_dict(), ensure_ascii=False)
    assert '"labelAngle": 0' in spec_text


def test_investment_return_marks_period_extremes() -> None:
    rows = []
    values = {
        "2024Q4": {"甲公司": 3.2, "乙公司": 2.8, "丙公司": 3.0},
        "2025Q4": {"甲公司": 3.1, "乙公司": 3.5, "丙公司": 2.9},
    }
    for period, company_values in values.items():
        for company, value in company_values.items():
            rows.append(
                {
                    "公司": company,
                    "报告期": period,
                    "指标编码": "INVESTMENT_RETURN",
                    "指标名称": "投资收益率",
                    "数值": value,
                    "单位": "%",
                    "数据类型": "百分比",
                }
            )
    metric, _, _, _ = prepare_metric_trend(
        pd.DataFrame(rows),
        "INVESTMENT_RETURN",
        ["2024Q4", "2025Q4"],
        "",
    )
    maxima = metric[metric["是否全局最大"]].set_index("报告期")["公司"].to_dict()
    minima = metric[metric["是否全局最小"]].set_index("报告期")["公司"].to_dict()
    assert maxima == {"2024Q4": "甲公司", "2025Q4": "乙公司"}
    assert minima == {"2024Q4": "乙公司", "2025Q4": "丙公司"}


def test_template_configures_five_year_investment_return_trend() -> None:
    modules = load_step7_config(
        ROOT / "templates" / "Step7-偿付能力公司报告展示模块.xlsx"
    )
    module = next(
        item
        for item in modules
        if item.level_one == "经营与偿付能力联动分析"
        and item.level_two == "投资利润"
    )
    assert module.chart_name == "投资收益率"
    assert module.chart_kind == "single_metric_trend"
    assert module.metric_codes == ("INVESTMENT_RETURN",)
    assert module.chart_config == {"years": 5, "annual_labels": True}


def test_industry_capital_structure_ratios_use_latest_period_totals() -> None:
    rows = []
    values = {
        ("甲公司", "2025Q4"): {
            "ACTUAL_CAPITAL": 100.0,
            "CORE_T1_CAPITAL": 70.0,
            "CORE_T2_CAPITAL": 17.0,
            "ANC_T1_CAPITAL": 10.0,
            "ANC_T2_CAPITAL": 3.0,
        },
        ("乙公司", "2025Q4"): {
            "ACTUAL_CAPITAL": 200.0,
            "CORE_T1_CAPITAL": 150.0,
            "CORE_T2_CAPITAL": 24.0,
            "ANC_T1_CAPITAL": 20.0,
            "ANC_T2_CAPITAL": 6.0,
        },
        ("甲公司", "2024Q4"): {
            "ACTUAL_CAPITAL": 100.0,
            "CORE_T1_CAPITAL": 50.0,
            "CORE_T2_CAPITAL": 10.0,
            "ANC_T1_CAPITAL": 30.0,
            "ANC_T2_CAPITAL": 10.0,
        },
    }
    for (company, period), metric_values in values.items():
        for code, value in metric_values.items():
            rows.append(
                {
                    "公司": company,
                    "报告期": period,
                    "指标编码": code,
                    "期间口径": "本季度末数",
                    "数值": value,
                    "单位": "亿元",
                }
            )
    ratios = capital_structure_industry_ratios(
        pd.DataFrame(rows),
        ("CORE_T1_CAPITAL", "CORE_T2_CAPITAL"),
        ("ANC_T1_CAPITAL", "ANC_T2_CAPITAL"),
        ["2024Q4", "2025Q4"],
    )
    assert ratios is not None
    core_ratio, ancillary_ratio, period = ratios
    assert abs(core_ratio - 87.0) < 1e-9
    assert abs(ancillary_ratio - 13.0) < 1e-9
    assert period == "2025Q4"


def test_insurance_risk_pressure_sentence_is_group_driven() -> None:
    rows = []
    values = {
        "甲公司": ("外资系", 1.5),
        "乙公司": ("外资系", 1.6),
        "丙公司": ("大型公司", 2.4),
        "丁公司": ("中型公司", 2.5),
        "戊公司": ("农业保险", 2.1),
    }
    for company, (group, value) in values.items():
        rows.append(
            {
                "公司": company,
                "报告期": "2025",
                "期间口径": "本季度末数",
                "同业分类": group,
                "指标编码": "NON_LIFE_INSURANCE_RISK_TO_LIABILITIES",
                "数值": value,
                "单位": "%",
            }
        )
    sentence = insurance_risk_liability_pressure_sentence(
        pd.DataFrame(rows),
        "NON_LIFE_INSURANCE_RISK_TO_LIABILITIES",
        ["2025"],
    )
    assert "外资系公司的非寿险保险风险压力因子平均值" in sentence
    assert "明显低于其他类型公司的平均水平" in sentence
    assert "控制业务风险方面的严格程度" in sentence


if __name__ == "__main__":
    test_annual_period_order_prefers_q4()
    test_capital_combo_axis_uses_year_labels()
    test_generic_metric_frame_uses_year_labels()
    test_actual_ratio_marks_period_maximum_and_minimum()
    test_core_metric_year_window_uses_one_annual_observation()
    test_non_life_risk_liability_ratio_uses_percent()
    test_non_life_risk_liability_marks_period_extremes()
    test_years_limit_keeps_latest_five_annual_observations()
    test_investment_return_uses_horizontal_year_labels()
    test_investment_return_marks_period_extremes()
    test_template_configures_five_year_investment_return_trend()
    test_industry_capital_structure_ratios_use_latest_period_totals()
    test_insurance_risk_pressure_sentence_is_group_driven()
    print("全部通过")
