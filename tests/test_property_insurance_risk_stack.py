# -*- coding: utf-8 -*-
"""Regression tests for the non-life insurance-risk capital stack."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.step7_excel_config import load_step7_config  # noqa: E402
from services.solvency_step7_chart_plans import SINGLE_METRIC_TREND  # noqa: E402
from services.solvency_step7_charts import build_component_stack_chart  # noqa: E402
from services.solvency_step7_charts import insurance_risk_industry_sentence  # noqa: E402
from services.solvency_step7_charts import quant_risk_industry_sentence  # noqa: E402
from step7_solvency.specs import COMPONENT_STACK_SPECS  # noqa: E402


QUANT_RISK_CODES = (
    "NON_LIFE_INSURANCE_RISK_CAPITAL",
    "MARKET_RISK_CAPITAL",
    "CREDIT_RISK_CAPITAL",
    "QUANT_RISK_DIVERSIFICATION_EFFECT",
)
INSURANCE_RISK_CODES = (
    "NON_LIFE_PREMIUM_RESERVE_RISK_CAPITAL",
    "NON_LIFE_CATASTROPHE_RISK_CAPITAL",
    "NON_LIFE_INSURANCE_RISK_DIVERSIFICATION_EFFECT",
)


def test_property_risk_specs_use_four_non_life_categories() -> None:
    specs = COMPONENT_STACK_SPECS["量化风险最低资本构成"]
    assert tuple(code for code, _, _ in specs) == QUANT_RISK_CODES
    assert tuple(label for _, label, _ in specs) == (
        "非寿险业务保险风险",
        "市场风险",
        "信用风险",
        "风险分散",
    )


def test_bundled_template_uses_property_risk_categories() -> None:
    modules = load_step7_config(
        ROOT / "templates" / "Step7-偿付能力公司报告展示模块.xlsx"
    )
    selected = [
        module
        for module in modules
        if module.chart_name == "量化风险最低资本构成"
    ]
    assert selected
    for module in selected:
        assert module.metric_codes == QUANT_RISK_CODES
        labels = tuple(
            component["label"]
            for component in module.chart_config["components"]
        )
        assert labels == ("非寿险业务保险风险", "市场风险", "信用风险", "风险分散")

    insurance = next(
        module
        for module in modules
        if module.chart_name == "保险风险构成"
        and module.level_two == "最低资本变动"
    )
    assert insurance.metric_codes == INSURANCE_RISK_CODES
    insurance_labels = tuple(
        component["label"] for component in insurance.chart_config["components"]
    )
    assert insurance_labels == ("保费及准备金风险", "巨灾风险", "风险分散")

    insurance_ratio = next(
        module
        for module in modules
        if module.chart_name == "保险风险/认可负债率"
        and module.level_two == "最低资本变动"
    )
    assert insurance_ratio.chart_kind == SINGLE_METRIC_TREND
    assert insurance_ratio.metric_codes == (
        "NON_LIFE_INSURANCE_RISK_TO_LIABILITIES",
    )
    target_ids = {
        "capital_amount_combo",
        "quant_risk_stack",
        "non_life_insurance_risk_component",
        "non_life_insurance_risk_to_liabilities",
    }
    configured = [
        module
        for module in modules
        if module.level_one == "偿付能力水平深度拆解"
        and module.module_id in target_ids
    ]
    assert len(configured) == 4
    for module in configured:
        assert module.chart_config["annual_labels"] is True
        assert module.chart_config["year_start"] == 2023
        assert module.chart_config["year_end"] == 2025


def test_property_risk_chart_renders_all_categories() -> None:
    values = (40.0, 35.0, 30.0, -12.0)
    rows = []
    for code, value in zip(QUANT_RISK_CODES, values):
        rows.append(
            {
                "公司": "甲公司",
                "报告期": "2025Q4",
                "指标编码": code,
                "数值": value,
                "单位": "亿元",
            }
        )
    rows.append(
        {
            "公司": "甲公司",
            "报告期": "2025Q4",
            "指标编码": "QUANT_RISK_CAPITAL",
            "数值": 100.0,
            "单位": "亿元",
        }
    )
    chart = build_component_stack_chart(
        pd.DataFrame(rows),
        COMPONENT_STACK_SPECS["量化风险最低资本构成"],
        ["2025Q4"],
        "量化风险最低资本构成",
        label_denominator_code="QUANT_RISK_CAPITAL",
        negative_component_codes=("QUANT_RISK_DIVERSIFICATION_EFFECT",),
        plot_proportions=True,
        annual_labels=True,
    )
    spec_text = json.dumps(chart.to_dict(), ensure_ascii=False)
    for label in ("非寿险业务保险风险", "市场风险", "信用风险", "风险分散"):
        assert label in spec_text
    assert '"field": "显示期"' in spec_text
    assert '"sort": ["2025"]' in spec_text


def test_insurance_risk_specs_use_non_life_subcategories() -> None:
    specs = COMPONENT_STACK_SPECS["保险风险构成"]
    assert tuple(code for code, _, _ in specs) == INSURANCE_RISK_CODES
    assert tuple(label for _, label, _ in specs) == (
        "保费及准备金风险",
        "巨灾风险",
        "风险分散",
    )


def test_quant_risk_summary_is_data_driven() -> None:
    rows = []
    values = {
        "甲公司": {
            "NON_LIFE_INSURANCE_RISK_CAPITAL": 60.0,
            "MARKET_RISK_CAPITAL": 15.0,
            "CREDIT_RISK_CAPITAL": 17.0,
            "QUANT_RISK_DIVERSIFICATION_EFFECT": -8.0,
            "QUANT_RISK_CAPITAL": 100.0,
        },
        "乙公司": {
            "NON_LIFE_INSURANCE_RISK_CAPITAL": 70.0,
            "MARKET_RISK_CAPITAL": 12.0,
            "CREDIT_RISK_CAPITAL": 16.0,
            "QUANT_RISK_DIVERSIFICATION_EFFECT": -6.0,
            "QUANT_RISK_CAPITAL": 100.0,
        },
    }
    for company, company_values in values.items():
        for code, value in company_values.items():
            rows.append(
                {
                    "公司": company,
                    "报告期": "2025Q4",
                    "指标编码": code,
                    "期间口径": "本季度末数",
                    "数值": value,
                    "单位": "亿元",
                }
            )
    sentence = quant_risk_industry_sentence(
        pd.DataFrame(rows),
        COMPONENT_STACK_SPECS["量化风险最低资本构成"],
        ["2025Q4"],
        negative_component_codes=("QUANT_RISK_DIVERSIFICATION_EFFECT",),
    )
    assert sentence == (
        "财产险公司2025年度的最低资本以保险风险为主（平均占比65.0%）；"
        "信用风险平均占比16.5%，略高于市场风险的13.5%。"
    )


def test_insurance_risk_summary_is_data_driven() -> None:
    rows = []
    company_values = {
        "甲公司": ("中型公司", 95.0, 6.0),
        "乙公司": ("小型公司", 92.0, 5.0),
        "丙公司": ("农业保险", 94.0, 30.0),
        "丁公司": ("大型公司", 80.0, 7.0),
        "戊公司": ("前十大", 78.0, 6.0),
        "己公司": ("外资系", 70.0, 5.0),
    }
    for company, (group, premium, catastrophe) in company_values.items():
        for code, value in (
            ("NON_LIFE_PREMIUM_RESERVE_RISK_CAPITAL", premium),
            ("NON_LIFE_CATASTROPHE_RISK_CAPITAL", catastrophe),
            ("NON_LIFE_INSURANCE_RISK_CAPITAL", 100.0),
        ):
            rows.append(
                {
                    "公司": company,
                    "报告期": "2025Q4",
                    "期间口径": "本季度末数",
                    "同业分类": group,
                    "指标编码": code,
                    "数值": value,
                    "单位": "亿元",
                }
            )
    sentence = insurance_risk_industry_sentence(
        pd.DataFrame(rows),
        ["2025Q4"],
    )
    assert "保费及准备金风险最低资本平均占比较高的主要是" in sentence
    assert "农业保险" in sentence
    assert "公司间分布较分散" in sentence
    assert "巨灾风险最低资本平均占比相对比较集中" in sentence
    assert "少数公司表现突出" in sentence
    assert "其中丙公司最高（30.0%）" in sentence


if __name__ == "__main__":
    test_property_risk_specs_use_four_non_life_categories()
    test_bundled_template_uses_property_risk_categories()
    test_property_risk_chart_renders_all_categories()
    test_insurance_risk_specs_use_non_life_subcategories()
    test_quant_risk_summary_is_data_driven()
    test_insurance_risk_summary_is_data_driven()
    print("全部通过")
