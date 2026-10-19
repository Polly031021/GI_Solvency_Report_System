from __future__ import annotations

from dataclasses import dataclass

from .solvency_navigation import (
    COMPANY_NAVIGATION,
)


SINGLE_METRIC_TREND = "single_metric_trend"
TREND_WITH_COMPANY_BARS = "trend_with_company_bars"
COMPANY_BAR_TREND = "company_bar_trend"
COMPANY_PERIOD_BAR = "company_period_bar"
SOLVENCY_RATIO_COMBO = "solvency_ratio_combo"
CAPITAL_AMOUNT_COMBO = "capital_amount_combo"
CAPITAL_RATIO_COMBO = "capital_ratio_combo"
CAPITAL_STRUCTURE_COMBO = "capital_structure_combo"
CAPITAL_EFFICIENCY_BUBBLE = "capital_efficiency_bubble"
COMPONENT_STACK = "component_stack"
EFFECT_DIVERGING = "effect_diverging"
SOLVENCY_MATRIX = "solvency_matrix"
MARKET_CREDIT_MATRIX = "market_credit_matrix"
RISK_RATIO_SCATTER = "risk_ratio_scatter"
KEY_METRICS_TABLE = "key_metrics_table"
FINANCING_TABLE = "financing_table"
PENDING_DEFINITION = "pending_definition"
CAPABILITY_RADAR = "capability_radar"          # ★ 新增
UNDERWRITING_EFFICIENCY_SCATTER = "underwriting_efficiency_scatter"  # 承保效益五年年度散点图
STRUCTURE_CHANGE_CHART = "structure_change_chart"  # ★ 新增：结构变化组合图
PERFORMANCE_PATTERN_GRID = "performance_pattern_grid"  # ★ 新增：业绩模式分列网格
INDUSTRY_PREMIUM_STRUCTURE = "industry_premium_structure"  # ★ 新增：行业整体保费规模与结构
INDUSTRY_MARKET_SHARE = "industry_market_share"  # ★ 新增：行业市场份额


@dataclass(frozen=True)
class ChartPlan:
    kind: str
    description: str


SPECIAL_CHART_PLANS: dict[str, ChartPlan] = {
    "关键偿付数据概览": ChartPlan(
        KEY_METRICS_TABLE,
        "最新报告期与去年同期关键偿付数据对照表",
    ),
    "核心及综合充足率": ChartPlan(
        SOLVENCY_RATIO_COMBO,
        "按公司和报告期展示综合偿付能力充足率柱形与核心偿付能力充足率折线",
    ),
    "综合充足率变化": ChartPlan(
        CAPITAL_RATIO_COMBO,
        "实际资本与最低资本堆叠柱组合综合偿付能力充足率折线",
    ),
    "核心充足率变化": ChartPlan(
        CAPITAL_RATIO_COMBO,
        "核心资本与最低资本堆叠柱组合核心偿付能力充足率折线",
    ),
    "核心资本占比": ChartPlan(
        CAPITAL_STRUCTURE_COMBO,
        "核心资本与附属资本堆叠柱组合核心资本占实际资本比例折线",
    ),
    "资本使用效率与核心资本占比气泡图": ChartPlan(
        CAPITAL_EFFICIENCY_BUBBLE,
        "最新报告期实际资本/注册资本、核心资本/注册资本与认可资产规模气泡图",
    ),
    "核心资本/注册资本": ChartPlan(
        SINGLE_METRIC_TREND,
        "多公司跨期折线图",
    ),
    "计入核心资本的保单未来盈余/核心资本的比例": ChartPlan(
        SINGLE_METRIC_TREND,
        "多公司跨期折线图",
    ),
    "资本规模与结构": ChartPlan(
        CAPITAL_AMOUNT_COMBO,
        "核心一级、核心二级、附属一级和附属二级资本堆叠图",
    ),
    "核心资本明细占比-待定": ChartPlan(
        PENDING_DEFINITION,
        "Excel 标记为待定，尚无组成指标定义",
    ),
    "附属资本明细占比-待定": ChartPlan(
        PENDING_DEFINITION,
        "Excel 标记为待定，尚无组成指标定义",
    ),
    "计入各级资本的保单未来盈余构成占比": ChartPlan(
        COMPONENT_STACK,
        "计入各级资本的保单未来盈余构成占比堆叠图",
    ),
    "量化风险最低资本构成": ChartPlan(
        COMPONENT_STACK,
        "量化风险最低资本正负向构成堆叠图",
    ),
    "各类保险风险（寿）占比": ChartPlan(
        COMPONENT_STACK,
        "寿险保险风险最低资本构成堆叠图",
    ),
    "各类市场风险占比": ChartPlan(
        COMPONENT_STACK,
        "市场风险最低资本构成堆叠图",
    ),
    "各类信用风险占比": ChartPlan(
        COMPONENT_STACK,
        "信用风险最低资本构成堆叠图",
    ),
    "增资发债信息统计": ChartPlan(
        FINANCING_TABLE,
        "重大融资信息表",
    ),
    # ★ 新增：能力五边形雷达图
    "能力五边形": ChartPlan(
        CAPABILITY_RADAR,
        "五维能力雷达图（保费增速 / ROE / 总资产 / 实际资本 / 偿付能力）",
    ),
    # 承保效益五年年度散点图 + 盈亏公司数趋势
    "承保效益": ChartPlan(
        UNDERWRITING_EFFICIENCY_SCATTER,
        "近五年逐年展示综合成本率与保费收入排名的散点图，"
        "以 100% 虚线标示盈亏平衡，并汇总盈利/亏损公司数趋势",
    ),
    # ★ 新增：规模与发展分类下的三张图
    "承保规模": ChartPlan(
        STRUCTURE_CHANGE_CHART,
        "车险与非车险保费占比及同比增速",
    ),
    "经营效益": ChartPlan(
        STRUCTURE_CHANGE_CHART,
        "净利润与总资产占比及同比增速",
    ),
    "资本规模": ChartPlan(
        STRUCTURE_CHANGE_CHART,
        "核心资本与附属资本占比及同比增速",
    ),
    "公司业绩展示": ChartPlan(
        PERFORMANCE_PATTERN_GRID,
        "按业绩模式（逐年提升/稳健经营/V形反转/大起大落/持续恶化）分列展示",
    ),
    "行业整体保费规模与结构": ChartPlan(
        INDUSTRY_PREMIUM_STRUCTURE,
        "近五年行业保费合计堆叠柱（车险/非车险）叠加车险、非车险及合计趋势线",
    ),
    "行业市场份额": ChartPlan(
        INDUSTRY_MARKET_SHARE,
        "最新年度按公司类别的保费、净利润、承保利润市场份额与金额对比",
    ),
}


CHART_PLAN_REGISTRY: dict[str, ChartPlan] = {
    entry.chart_name: SPECIAL_CHART_PLANS.get(
        entry.chart_name,
        ChartPlan(SINGLE_METRIC_TREND, "跨期趋势图"),
    )
    for entry in COMPANY_NAVIGATION
}


def chart_plan_for(chart_name: str) -> ChartPlan:
    # ★ 先查显式注册的特殊图表（能力五边形、综合充足率变化等都在这）
    name = str(chart_name)
    if name in SPECIAL_CHART_PLANS:
        return SPECIAL_CHART_PLANS[name]
    return CHART_PLAN_REGISTRY.get(
        name,
        ChartPlan(SINGLE_METRIC_TREND, "跨期趋势图"),
    )
