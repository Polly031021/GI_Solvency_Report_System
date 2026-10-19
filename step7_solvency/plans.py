"""STEP7 组合图调度：按图表类型分派到 plan_renderers 中的具体实现。"""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
from services.step7_excel_config import Step7Module
from services.solvency_step7_chart_plans import (
    COMPANY_LINE_TREND,
    COMPANY_PERIOD_BAR,
    CAPITAL_AMOUNT_COMBO,
    CAPITAL_EFFICIENCY_BUBBLE,
    CAPITAL_RATIO_COMBO,
    CAPITAL_STRUCTURE_COMBO,
    CAPABILITY_RADAR,
    INDUSTRY_PREMIUM_STRUCTURE,
    INDUSTRY_MARKET_SHARE,
    UNDERWRITING_EFFICIENCY_SCATTER,
    STRUCTURE_CHANGE_CHART,
    COMPONENT_STACK,
    EFFECT_DIVERGING,
    FINANCING_TABLE,
    KEY_METRICS_TABLE,
    MARKET_CREDIT_MATRIX,
    PENDING_DEFINITION,
    RISK_RATIO_SCATTER,
    SOLVENCY_MATRIX,
    SOLVENCY_RATIO_COMBO,
    chart_plan_for,
    PERFORMANCE_PATTERN_GRID,
)
from .rendering import convert_unit
from .selection import company_color_map
from .specs import COMPONENT_STACK_SPECS, RISK_RATIO_SCATTER_SPECS

from .plan_inputs import chart_input_metric_codes
from .plan_renderers import (
    render_plan_capability_radar,
    render_plan_company_line_trend,
    render_plan_industry_premium_structure,
    render_plan_industry_market_share,
    render_plan_capital_amount_combo,
    render_plan_capital_efficiency_bubble,
    render_plan_capital_ratio_combo,
    render_plan_capital_structure_combo,
    render_plan_company_period_bar,
    render_plan_component_stack,
    render_plan_effect_diverging,
    render_plan_financing_table,
    render_plan_key_metrics_table,
    render_plan_market_credit_matrix,
    render_plan_pending_definition,
    render_plan_performance_pattern_grid,
    render_plan_risk_ratio_scatter,
    render_plan_solvency_matrix,
    render_plan_solvency_ratio_combo,
    render_plan_structure_change_chart,
    render_plan_underwriting_efficiency_scatter,
)


def render_combination_analysis(
    frame: pd.DataFrame,
    chart_name: str,
    *,
    periods: list[str],
    unit_mode: str,
    highlight_company: str,
    key_prefix: str,
    financing_data: pd.DataFrame | None = None,
    module_override: Step7Module | None = None,  # ★ 新增
    industry_frame: pd.DataFrame | None = None,
) -> None:
    """Render an approved multi-metric plan selected by chart name.

    如果传入了 module_override，则用它携带的 chart_kind / metric_codes /
    chart_config 覆盖 COMPANY_NAVIGATION 里的默认配置。
    """
    if module_override is not None and module_override.chart_kind:
        plan = SimpleNamespace(kind=module_override.chart_kind)
    else:
        plan = chart_plan_for(chart_name)

    # ---- 应用 Excel chart_config 覆盖硬编码 spec ----
    if module_override is not None and module_override.chart_config:
        cfg = module_override.chart_config
        if plan.kind == COMPONENT_STACK and "components" in cfg:
            COMPONENT_STACK_SPECS[chart_name] = tuple(
                (c["code"], c["label"], c["color"]) for c in cfg["components"]
            )
        if plan.kind == RISK_RATIO_SCATTER and "scatter" in cfg:
            s = cfg["scatter"]
            RISK_RATIO_SCATTER_SPECS[chart_name] = (
                s["x_code"],
                s["y_code"],
                s["bubble_size_code"],
                s.get("denominator_label", "认可资产"),
            )

    if plan.kind == STRUCTURE_CHANGE_CHART:
        render_plan_structure_change_chart(chart_name, frame)
        return

    # ★ 行业整体保费结构：使用上传数据中的全部公司，不受 Step7 公司/类型筛选影响
    if plan.kind == INDUSTRY_PREMIUM_STRUCTURE:
        render_plan_industry_premium_structure(
            industry_frame if industry_frame is not None else frame,
            unit_mode=unit_mode,
            key_prefix=key_prefix,
            module_override=module_override,
        )
        return

    if plan.kind == INDUSTRY_MARKET_SHARE:
        render_plan_industry_market_share(
            industry_frame if industry_frame is not None else frame,
            unit_mode=unit_mode,
            key_prefix=key_prefix,
            module_override=module_override,
        )
        return

    # ★ 新增：公司业绩展示（收入排名—利润排名，按模式分列）
    if plan.kind == PERFORMANCE_PATTERN_GRID:
        render_plan_performance_pattern_grid(frame, key_prefix, module_override)
        return

    if plan.kind == KEY_METRICS_TABLE:
        render_plan_key_metrics_table(frame, highlight_company)
        return
    if plan.kind == FINANCING_TABLE:
        render_plan_financing_table(financing_data)
        return
    if plan.kind == PENDING_DEFINITION:
        render_plan_pending_definition(chart_name)
        return
    required_codes = chart_input_metric_codes(
        chart_name,
        plan.kind,
        metric_codes_override=(module_override.metric_codes if module_override else None),
        chart_config=(module_override.chart_config if module_override else None),
    )
    chart_frame = (
        frame[frame["指标编码"].fillna("").astype(str).isin(required_codes)].copy()
        if required_codes
        else frame
    )
    # ★ 二次保险：主流程 show_step_7_solvency 已经在入口处清洗过公司名，
    #   这里再做一次同样的归一化，防止其他调用方绕过主流程导致名字不一致。
    chart_frame["公司"] = (
        chart_frame["公司"]
        .astype(str)
        .str.replace(r"[（(]\s*偿付能力\s*[)）]\s*$", "", regex=True)
        .str.strip()
    )
    converted = convert_unit(chart_frame, unit_mode)
    company_colors = company_color_map(converted["公司"], highlight_company)
    company_panel_count = converted["公司"].nunique()
    dense_company_panels = company_panel_count >= 10
    if plan.kind == SOLVENCY_RATIO_COMBO:
        render_plan_solvency_ratio_combo(
            company_panel_count,
            converted,
            dense_company_panels,
            highlight_company,
            key_prefix,
            periods,
        )
    elif plan.kind == COMPANY_PERIOD_BAR:
        render_plan_company_period_bar(
            chart_name,
            company_panel_count,
            converted,
            dense_company_panels,
            highlight_company,
            key_prefix,
            module_override,
            periods,
        )

    elif plan.kind == CAPITAL_RATIO_COMBO:
        render_plan_capital_ratio_combo(
            chart_name,
            company_panel_count,
            converted,
            dense_company_panels,
            highlight_company,
            key_prefix,
            module_override,
            periods,
        )
    elif plan.kind == CAPITAL_STRUCTURE_COMBO:
        render_plan_capital_structure_combo(
            company_panel_count,
            converted,
            dense_company_panels,
            highlight_company,
            key_prefix,
            module_override,
            periods,
        )
    elif plan.kind == COMPONENT_STACK:
        render_plan_component_stack(
            chart_name,
            company_panel_count,
            converted,
            dense_company_panels,
            highlight_company,
            key_prefix,
            periods,
        )

    elif plan.kind == CAPITAL_AMOUNT_COMBO:
        render_plan_capital_amount_combo(
            company_panel_count,
            converted,
            dense_company_panels,
            highlight_company,
            key_prefix,
            periods,
        )
    elif plan.kind == EFFECT_DIVERGING:
        render_plan_effect_diverging(chart_name, converted, key_prefix, module_override, periods)
    elif plan.kind == CAPITAL_EFFICIENCY_BUBBLE:
        render_plan_capital_efficiency_bubble(
            company_colors, converted, highlight_company, key_prefix, periods
        )
    elif plan.kind == SOLVENCY_MATRIX:
        render_plan_solvency_matrix(
            company_colors, converted, highlight_company, key_prefix, periods
        )
    elif plan.kind == MARKET_CREDIT_MATRIX:
        render_plan_market_credit_matrix(
            company_colors, converted, highlight_company, key_prefix, periods
        )
    elif plan.kind == RISK_RATIO_SCATTER:
        # 坐标轴口径来自注册表（Excel 可在"图表配置(JSON)"的 scatter 字段覆盖）。
        # 未注册的自定义图表名回退到"市场风险/认可资产"口径，避免 KeyError。
        render_plan_risk_ratio_scatter(
            chart_name, company_colors, converted, highlight_company, key_prefix, periods
        )

    # ★ 新增：能力五边形雷达图
    elif plan.kind == CAPABILITY_RADAR:
        render_plan_capability_radar(
            chart_frame, highlight_company, key_prefix, module_override, periods
        )
        return

    # ★ 新增：每家公司一个跨期折线小图（核心指标速览）
    elif plan.kind == COMPANY_LINE_TREND:
        render_plan_company_line_trend(
            chart_frame, highlight_company, key_prefix, periods
        )
        return

    # ★ 新增：承保效益散点图
    elif plan.kind == UNDERWRITING_EFFICIENCY_SCATTER:
        render_plan_underwriting_efficiency_scatter(
            frame,
            key_prefix,
            module_override,
            industry_frame=industry_frame,
        )
        return
