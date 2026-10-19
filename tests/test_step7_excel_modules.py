# -*- coding: utf-8 -*-
"""STEP7 Excel 配置驱动的回归测试。

生产环境的 STEP7 图表清单来自 Excel 注释表（Step7Module 列表），
与内置 COMPANY_NAVIGATION 兜底路径并不完全相同——例如"结构变化组合图"
"业绩模式分列图""能力五边形""承保效益散点图"只有 Excel 才会启用。

本测试为每一种图表类型各构造一个模块，确认这些路径都能正常渲染，
避免出现"内置路径通过、Excel 路径崩溃"的情况。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from streamlit.testing.v1 import AppTest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_workflow_smoke import synthetic_integrated_data  # noqa: E402

from services.solvency_navigation import (  # noqa: E402
    CREDIT_RISK_ASSET_SCATTER,
    INSURANCE_RISK_LIABILITY_SCATTER,
    MARKET_RISK_ASSET_SCATTER,
)
from services.solvency_step7_chart_plans import (  # noqa: E402
    CAPABILITY_RADAR,
    CAPITAL_AMOUNT_COMBO,
    CAPITAL_EFFICIENCY_BUBBLE,
    CAPITAL_RATIO_COMBO,
    CAPITAL_STRUCTURE_COMBO,
    COMPANY_BAR_TREND,
    COMPANY_PERIOD_BAR,
    COMPONENT_STACK,
    EFFECT_DIVERGING,
    FINANCING_TABLE,
    INDUSTRY_PREMIUM_STRUCTURE,
    INDUSTRY_MARKET_SHARE,
    KEY_METRICS_TABLE,
    MARKET_CREDIT_MATRIX,
    PENDING_DEFINITION,
    PERFORMANCE_PATTERN_GRID,
    RISK_RATIO_SCATTER,
    SINGLE_METRIC_TREND,
    SOLVENCY_MATRIX,
    SOLVENCY_RATIO_COMBO,
    STRUCTURE_CHANGE_CHART,
    TREND_WITH_COMPANY_BARS,
    UNDERWRITING_EFFICIENCY_SCATTER,
)
from services.step7_excel_config import Step7Module  # noqa: E402

QUANT_CODES = (
    "INSURANCE_RISK_CAPITAL",
    "NON_LIFE_INSURANCE_RISK_CAPITAL",
    "MARKET_RISK_CAPITAL",
    "CREDIT_RISK_CAPITAL",
    "QUANT_RISK_DIVERSIFICATION_EFFECT",
    "CONTRACT_LOSS_ABSORPTION_EFFECT",
)
MARKET_CODES = (
    "INTEREST_RATE_RISK_CAPITAL",
    "EQUITY_RISK_CAPITAL",
    "REAL_ESTATE_RISK_CAPITAL",
    "MARKET_RISK_DIVERSIFICATION_EFFECT",
    "MARKET_RISK_CAPITAL",
)
POLICY_SURPLUS_CODES = (
    "POLICY_SURPLUS_CORE_T1",
    "POLICY_SURPLUS_CORE_T2",
    "POLICY_SURPLUS_ANC_T1",
    "POLICY_SURPLUS_ANC_T2",
)
CAPITAL_TIER_CODES = (
    "CORE_T1_CAPITAL",
    "CORE_T2_CAPITAL",
    "ANC_T1_CAPITAL",
    "ANC_T2_CAPITAL",
)

# (图表名称, 图表类型, 指标编码, 图表配置)
MODULE_SPECS = (
    ("单指标趋势", SINGLE_METRIC_TREND, ("CORE_SOLVENCY_RATIO",), {}),
    ("公司柱状图", COMPANY_BAR_TREND, ("COMBINED_SOLVENCY_RATIO",), {}),
    ("趋势加公司柱", TREND_WITH_COMPANY_BARS, ("COMBINED_SOLVENCY_RATIO",), {}),
    ("公司分期柱", COMPANY_PERIOD_BAR, ("CORE_SOLVENCY_RATIO",), {}),
    ("核心及综合充足率", SOLVENCY_RATIO_COMBO, ("CORE_SOLVENCY_RATIO", "COMBINED_SOLVENCY_RATIO"), {}),
    ("综合充足率变化", CAPITAL_RATIO_COMBO, ("ACTUAL_CAPITAL", "MINIMUM_CAPITAL", "COMBINED_SOLVENCY_RATIO"), {}),
    ("核心充足率变化", CAPITAL_RATIO_COMBO, ("CORE_T1_CAPITAL", "MINIMUM_CAPITAL", "CORE_SOLVENCY_RATIO"), {}),
    ("核心资本占比", CAPITAL_STRUCTURE_COMBO, CAPITAL_TIER_CODES, {}),
    ("资本规模与结构", CAPITAL_AMOUNT_COMBO, CAPITAL_TIER_CODES, {}),
    ("资本使用效率与核心资本占比气泡图", CAPITAL_EFFICIENCY_BUBBLE, ("ACTUAL_CAPITAL", "REGISTERED_CAPITAL", "CORE_T1_CAPITAL", "RECOGNIZED_ASSETS"), {}),
    ("量化风险最低资本构成", COMPONENT_STACK, QUANT_CODES, {}),
    ("各类市场风险占比", COMPONENT_STACK, MARKET_CODES, {}),
    ("计入各级资本的保单未来盈余构成占比", COMPONENT_STACK, POLICY_SURPLUS_CODES, {}),
    ("效应发散", EFFECT_DIVERGING, ("QUANT_RISK_DIVERSIFICATION_EFFECT",), {}),
    ("偿付能力矩阵", SOLVENCY_MATRIX, ("CORE_SOLVENCY_RATIO", "COMBINED_SOLVENCY_RATIO"), {}),
    ("市场信用矩阵", MARKET_CREDIT_MATRIX, ("MARKET_RISK_TO_QUANT_CAPITAL", "CREDIT_RISK_TO_QUANT_CAPITAL"), {}),
    (MARKET_RISK_ASSET_SCATTER, RISK_RATIO_SCATTER, ("INTEREST_RATE_RISK_TO_ASSETS", "EQUITY_RISK_TO_ASSETS", "RECOGNIZED_ASSETS"), {}),
    (CREDIT_RISK_ASSET_SCATTER, RISK_RATIO_SCATTER, ("SPREAD_RISK_TO_ASSETS", "COUNTERPARTY_RISK_TO_ASSETS", "RECOGNIZED_ASSETS"), {}),
    (INSURANCE_RISK_LIABILITY_SCATTER, RISK_RATIO_SCATTER, ("NON_LIFE_INSURANCE_RISK_TO_LIABILITIES", "LIFE_INSURANCE_RISK_TO_LIABILITIES", "RECOGNIZED_LIABILITIES"), {}),
    ("自定义风险散点", RISK_RATIO_SCATTER, ("MARKET_RISK_TO_QUANT_CAPITAL", "CREDIT_RISK_TO_QUANT_CAPITAL"), {}),
    ("关键偿付数据概览", KEY_METRICS_TABLE, (), {}),
    ("增资发债信息统计", FINANCING_TABLE, (), {}),
    ("核心资本明细占比-待定", PENDING_DEFINITION, (), {}),
    ("能力五边形", CAPABILITY_RADAR, ("PREMIUM_GROWTH", "ROE", "TOTAL_ASSETS", "ACTUAL_CAPITAL", "COMBINED_SOLVENCY_RATIO"), {}),
    ("承保效益", UNDERWRITING_EFFICIENCY_SCATTER, (), {}),
    ("承保规模", STRUCTURE_CHANGE_CHART, ("SIGNED_PREMIUM", "SIGNED_PREMIUM_MOTOR"), {}),
    ("经营效益", STRUCTURE_CHANGE_CHART, ("NET_PROFIT", "SIGNED_PREMIUM"), {}),
    ("资本规模", STRUCTURE_CHANGE_CHART, ("CORE_T1_CAPITAL", "ANC_T1_CAPITAL", "ACTUAL_CAPITAL"), {}),
    ("公司业绩展示", PERFORMANCE_PATTERN_GRID, ("SIGNED_PREMIUM", "NET_PROFIT"), {}),
    (
        "行业整体保费规模与结构",
        INDUSTRY_PREMIUM_STRUCTURE,
        ("SIGNED_PREMIUM", "SIGNED_PREMIUM_MOTOR"),
        {"years": 5},
    ),
    (
        "行业市场份额",
        INDUSTRY_MARKET_SHARE,
        ("SIGNED_PREMIUM", "NET_PROFIT", "COMBINED_RATIO"),
        {"min_coverage": 0.8},
    ),
)


def build_modules() -> list[Step7Module]:
    """按 MODULE_SPECS 构造 Excel 模块列表（覆盖每一种图表类型）。"""
    return [
        Step7Module(
            module_id=f"T{index:02d}",
            level_one="回归测试",
            level_two="全部",
            chart_name=chart_name,
            chart_kind=chart_kind,
            metric_codes=codes,
            chart_config=config,
            requires_all=False,
        )
        for index, (chart_name, chart_kind, codes, config) in enumerate(MODULE_SPECS, start=1)
    ]


def _app_with_modules() -> AppTest:
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=900)
    at.session_state["logged_in"] = True
    at.session_state["user_role"] = "项目组成员"
    at.session_state["active_profile_id"] = "NON_LIFE_SOLVENCY"
    data = synthetic_integrated_data()
    at.session_state["integrated_data"] = data
    at.session_state["standard_data"] = data
    at.session_state["step7_modules"] = build_modules()
    at.session_state["company_nav_level_one"] = "回归测试"
    at.session_state["company_nav_level_two"] = "全部"
    at.session_state["workflow_main_tab"] = "STEP7 公司报告"
    at.session_state["s7_company_types"] = ["全部"]
    return at


def test_excel_driven_step7_charts() -> None:
    """Excel 配置的每一种图表类型都必须能渲染。"""
    at = _app_with_modules()
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    print(f"PASS Excel 驱动 STEP7：{len(MODULE_SPECS)} 种图表类型全部渲染成功")


def test_industry_premium_template_module() -> None:
    """模板中的“行业概览 > 规模与发展”必须实际渲染行业保费结构图。"""
    template_path = ROOT / "templates" / "Step7-偿付能力公司报告展示模块.xlsx"
    if not template_path.exists():
        print("SKIP 行业概览模板测试：未找到 Step7-偿付能力公司报告展示模块.xlsx")
        return
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=900)
    at.session_state["logged_in"] = True
    at.session_state["user_role"] = "项目组成员"
    at.session_state["active_profile_id"] = "NON_LIFE_SOLVENCY"
    data = synthetic_integrated_data()
    at.session_state["integrated_data"] = data
    at.session_state["standard_data"] = data
    at.session_state["step7_notes_bytes_NON_LIFE_SOLVENCY"] = template_path.read_bytes()
    at.session_state["workflow_main_tab"] = "STEP7 公司报告"
    at.session_state["company_nav_level_one"] = "行业概览"
    at.session_state["company_nav_level_two"] = "规模与发展"
    at.session_state["s7_company_types"] = ["全部"]
    at.session_state["s7_unit_mode"] = "亿元"
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert len(at.metric) >= 4, "行业保费结构页的四个指标卡未渲染"

    # Selecting “全部” must render the full industry pool, not keep the
    # previously selected single chart.
    at.session_state["company_nav_level_two"] = "全部"
    at.session_state["company_nav_chart"] = "承保效益"
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert len(at.metric) >= 4, "选择“全部”后行业保费结构图未恢复渲染"

    at.session_state["company_nav_level_two"] = "市场份额"
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    markdown_text = "\n".join(item.value for item in at.markdown)
    assert "公司类别市场份额" in markdown_text
    assert "公司类别金额对比" in markdown_text
    print("PASS 行业概览：保费结构、全部切换与市场份额均正常")


def test_default_step7_config_without_upload() -> None:
    """Step7 must load the bundled template without a sidebar upload."""
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=900)
    at.session_state["logged_in"] = True
    at.session_state["user_role"] = "项目组成员"
    at.session_state["active_profile_id"] = "NON_LIFE_SOLVENCY"
    data = synthetic_integrated_data()
    at.session_state["integrated_data"] = data
    at.session_state["standard_data"] = data
    at.session_state["workflow_main_tab"] = "STEP7 公司报告"
    at.session_state["company_nav_level_one"] = "行业概览"
    at.session_state["company_nav_level_two"] = "规模与发展"
    at.session_state["s7_company_types"] = ["全部"]
    at.session_state["s7_unit_mode"] = "亿元"
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert len(at.metric) >= 4, "默认 STEP7 配置未自动加载"
    print("PASS STEP7 默认配置自动加载")


if __name__ == "__main__":
    test_excel_driven_step7_charts()
    test_industry_premium_template_module()
    test_default_step7_config_without_upload()
    print("全部通过")
