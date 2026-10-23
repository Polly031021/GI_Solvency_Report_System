"""STEP7 各类组合图的具体渲染实现，由 plans.render_combination_analysis 调度。"""

from __future__ import annotations

from copy import deepcopy
from html import escape

import pandas as pd
import streamlit as st
from dashboard_components import render_key_solvency_overview, render_major_financing
from services.report_style import (
    COBALT_BLUE,
    PACIFIC_BLUE,
    PINK,
    PURPLE,
)
from services.solvency_navigation import (
    KPMG_CAPITAL_COMBO_COLORS,
    MARKET_RISK_ASSET_SCATTER,
    metric_codes_for_chart,
)
from services.solvency_step7_charts import (
    CAPITAL_STRUCTURE_COLORS,
    HISTORICAL_SOURCE,
    INDUSTRY_PREMIUM_WINDOWS,
    UPLOAD_SOURCE,
    annual_period_order,
    build_capability_radar_chart,
    build_company_line_trend_chart,
    build_industry_premium_mix_chart,
    build_industry_premium_total_chart,
    build_market_share_amount_bars,
    build_market_share_pies,
    market_share_category_colors,
    build_structure_change_chart,
    build_premium_growth_structure_chart,
    group_premium_growth_panels,
    prepare_premium_growth_panel,
    premium_growth_axis_domain,
    build_capital_amount_combo,
    build_capital_ratio_combo_chart,
    capital_ratio_amount_axis_domain,
    build_company_period_bar_chart,
    build_component_stack_chart,
    build_effect_diverging_chart,
    build_matrix_chart,
    build_solvency_ratio_combo_chart,
    component_stack_axis_domain,
    component_stack_proportion_axis_domain,
    metric_bar_axis_domain,
    report_period_color_map,
    solvency_ratio_axis_domain,
    PERFORMANCE_PATTERN_ORDER,
    classify_performance_pattern,
    build_performance_pattern_trajectory,
    prepare_industry_premium_annual,
    prepare_industry_solvency_overview,
    prepare_market_share_data,
    TOP10_CATEGORY,
    TOP3_CATEGORY,
    TOP4_10_CATEGORY,
)
from .fragments import (
    render_capital_efficiency_bubble_fragment,
    render_risk_ratio_scatter_fragment,
)
from services.report_i18n import (
    category_name,
    chart_name as chart_display_name,
    get_language,
    is_english,
    localize_plotly,
    t,
    translate_dataframe,
    translate_text,
    unit_name,
)
from .rendering import (
    chart_without_internal_title,
    convert_unit,
    render_altair_chart,
    render_chart_legend,
    render_company_chart_grid,
)
from .selection import ordered_companies
from .specs import (
    COMPONENT_STACK_DENOMINATOR_CODES,
    COMPONENT_STACK_SPECS,
    QUANT_RISK_STACK_RATIO_CODES,
    RISK_RATIO_SCATTER_SPECS,
    RISK_SCATTER_DISPLAY_WIDTH,
    RISK_SCATTER_DUAL_VIEW_CHARTS,
    DEFAULT_CAPABILITY_AXES,
)
from .underwriting import render_underwriting_efficiency_scatter
from .insight_cards import render_insight_card

from .plan_inputs import capability_scores


def render_plan_structure_change_chart(chart_name, frame):
    companies = ordered_companies(frame)
    if not companies:
        st.warning(t("当前没有公司数据。", "No company data."))
        return

    if chart_name == "承保规模":
        category_map = {"SIGNED_PREMIUM_MOTOR": "车险", "SIGNED_PREMIUM": "非车险"}
        total_code = "SIGNED_PREMIUM"
        line_color_map = {"车险": "#E36C0A", "非车险": "#9B3FB5"}
    elif chart_name == "经营效益":
        category_map = {"NET_PROFIT": "净利润率"}
        total_code = "SIGNED_PREMIUM"
        line_color_map = {"净利润率": "#E36C0A"}
    elif chart_name == "资本规模":
        category_map = {"CORE_T1_CAPITAL": "核心资本", "ANC_T1_CAPITAL": "附属资本"}
        total_code = "ACTUAL_CAPITAL"
        line_color_map = {"核心资本": "#E36C0A", "附属资本": "#9B3FB5"}
    else:
        return

    periods = sorted(frame["报告期"].dropna().unique().tolist())
    columns_per_row = 2

    for i in range(0, len(companies), columns_per_row):
        batch = companies[i : i + columns_per_row]
        cols = st.columns(len(batch), gap="large")
        for col, company in zip(cols, batch):
            with col:
                try:
                    chart = build_structure_change_chart(
                        frame,
                        category_map=category_map,
                        total_code=total_code,
                        period_order=periods,
                        company_name=company,
                        line_color_map=line_color_map,
                        show_legend=True,
                    )
                    render_altair_chart(chart, use_container_width=True)
                except Exception as e:
                    # ★ 详细报错 + 打印堆栈，便于排查
                    import traceback

                    st.error(
                        t(
                            f"❌ {company} 图表渲染失败：{e}",
                            f"❌ {company} chart failed: {e}",
                        )
                    )
                    with st.expander(t("查看详细错误", "View detailed error"), expanded=False):
                        st.code(traceback.format_exc())


def render_plan_premium_growth_structure(
    chart_name,
    frame,
    *,
    unit_mode: str,
    module_override,
):
    """Render per-company 100% premium structure with total-premium growth."""
    companies = ordered_companies(frame)
    if not companies:
        st.warning(t("当前没有公司数据。", "No company data."))
        return

    cfg = module_override.chart_config if module_override else {}
    try:
        years = max(1, min(int(cfg.get("years", 5)), 10))
    except (TypeError, ValueError):
        years = 5
    total_code = str(cfg.get("total_code", "SIGNED_PREMIUM")).strip()
    motor_code = str(cfg.get("motor_code", "SIGNED_PREMIUM_MOTOR")).strip()
    annual_scope = str(cfg.get("annual_scope", "本年累计数")).strip()
    group_column = str(cfg.get("group_column", "同业分类")).strip() or "同业分类"
    converted = convert_unit(frame, unit_mode)

    panels = [
        prepare_premium_growth_panel(
            converted,
            company,
            years=years,
            total_code=total_code,
            motor_code=motor_code,
            annual_scope=annual_scope,
            group_column=group_column,
        )
        for company in companies
    ]
    panels = [panel for panel in panels if not panel.bar_frame.empty]
    if not panels:
        st.warning(
            t(
                f"没有可用于绘图的年度累计保费数据。请检查 {total_code}、"
                f"{motor_code} 及期间口径“{annual_scope or '本年累计数'}”。",
                f"No annual premium data is available for charting. Check {total_code}, "
                f"{motor_code} and the period basis “{annual_scope or 'Year-to-date'}”.",
            )
        )
        return

    growth_domain = premium_growth_axis_domain(panels)
    has_growth = any(not panel.line_frame.empty for panel in panels)
    st.caption(
        t(
            f"公司按“{group_column}”分组，每家公司展示最近 {years} 个年度："
            "左轴为车险/非车险的 100% 结构占比，"
            "右轴折线为总保费同比增速；非车险按“总签单保费 - 车险”计算。"
            "首年若缺少上一年度基数，则不显示折线点。",
            f"Companies grouped by “{category_name(group_column)}”; each company shows the latest "
            f"{years} years. The left axis is the 100% motor/non-motor mix and the right-axis "
            "line is total premium growth. Non-motor premium is total signed premium less motor. "
            "No growth point is shown for the first year without a prior-year base.",
        )
    )
    legend_items = [
        ("#00338D", t("车险", "Motor"), "square"),
        ("#00B8F5", t("非车险", "Non-motor"), "square"),
        ("#E36C0A", t("总保费同比增速", "Total premium growth"), "line"),
    ]
    if any(panel.bar_frame["类别"].astype(str).eq("未拆分").any() for panel in panels):
        legend_items.append(("#D9E2EC", t("未拆分", "Unallocated"), "square"))
    render_chart_legend(legend_items)
    if not has_growth:
        st.caption(
            t(
                "当前各公司都没有可比的连续年度保费基数，因此图内暂不显示同比增速折线。",
                "No company has a comparable consecutive-year premium base, so growth lines are not shown.",
            )
        )

    columns_per_row = 2
    for group_index, (group_name, group_panels) in enumerate(
        group_premium_growth_panels(panels)
    ):
        st.markdown(
            f"<div style='margin:18px 0 8px;font-size:14px;font-weight:700;"
            f"color:#00338D;border-left:4px solid #00338D;padding-left:8px;'>"
            f"{category_name(group_name)}"
            f"{t('（', ' (')}{len(group_panels)}"
            f"{t(' 家）', ' companies)')}</div>",
            unsafe_allow_html=True,
        )
        for index in range(0, len(group_panels), columns_per_row):
            batch = group_panels[index : index + columns_per_row]
            cols = st.columns(len(batch), gap="large")
            for column, panel in zip(cols, batch):
                with column:
                    try:
                        chart = build_premium_growth_structure_chart(
                            panel,
                            growth_domain=growth_domain,
                            unit_label=unit_mode,
                        )
                        render_altair_chart(
                            chart,
                            width="stretch",
                            key=(
                                f"premium_growth_{group_index}_{index}_"
                                f"{panel.company}"
                            ),
                        )
                    except Exception as exc:
                        st.error(
                            t(
                                f"❌ {panel.company} 保费增速图渲染失败：{exc}",
                                f"❌ {panel.company} premium growth chart failed: {exc}",
                            )
                        )


def render_plan_performance_pattern_grid(frame, key_prefix, module_override):
    companies = ordered_companies(frame)
    if not companies:
        st.warning(t("当前没有公司数据。", "No company data."))
        return

    cfg = module_override.chart_config if module_override else {}
    revenue_code = str(cfg.get("revenue_code", "SIGNED_PREMIUM")).strip()
    profit_code = str(cfg.get("profit_code", "NET_PROFIT")).strip()
    pattern_overrides: dict = cfg.get("pattern_overrides") or {}

    periods_list = sorted(frame["报告期"].dropna().astype(str).unique().tolist())

    def _metric_label(code: str, fallback: str) -> str:
        known = {
            "SIGNED_PREMIUM": t("签单保费", "Signed premium"),
            "NET_PROFIT": t("净利润", "Net profit"),
        }
        if code in known:
            return known[code]
        names = (
            frame.loc[frame["指标编码"].astype(str).eq(code), "指标名称"]
            .dropna()
            .astype(str)
            .str.strip()
        )
        names = names[names.ne("")]
        if not names.empty:
            return str(names.iloc[0])
        return fallback

    revenue_label = _metric_label(revenue_code, t("收入", "Revenue"))
    profit_label = _metric_label(profit_code, t("利润", "Profit"))
    revenue_rank_title = (
        f"{revenue_label}{t('排名', ' rank')}" if not revenue_label.endswith(" rank") else revenue_label
    )
    profit_rank_title = (
        f"{profit_label}{t('排名', ' rank')}" if not profit_label.endswith(" rank") else profit_label
    )

    # ---------- 1. 计算每家公司、每个报告期的收入/利润排名 ----------
    _df = frame.copy()
    _df["数值"] = pd.to_numeric(_df["数值"], errors="coerce")
    _df["报告期"] = _df["报告期"].astype(str)
    _df["公司"] = _df["公司"].astype(str).str.strip()

    all_companies = list(
        dict.fromkeys(c for c in _df["公司"].dropna().astype(str) if c and c != "nan")
    )
    n_companies = len(all_companies)
    if n_companies == 0:
        st.warning(t("无公司数据。", "No company data."))
        return

    rank_rows: list[dict] = []
    for period in periods_list:
        period_df = _df[_df["报告期"].eq(period)]
        revenue = (
            period_df[period_df["指标编码"].astype(str).eq(revenue_code)][["公司", "数值"]]
            .dropna(subset=["数值"])
            .drop_duplicates("公司", keep="last")
        )
        profit = (
            period_df[period_df["指标编码"].astype(str).eq(profit_code)][["公司", "数值"]]
            .dropna(subset=["数值"])
            .drop_duplicates("公司", keep="last")
        )
        if revenue.empty or profit.empty:
            continue
        revenue["收入排名"] = revenue["数值"].rank(ascending=False, method="min")
        profit["利润排名"] = profit["数值"].rank(ascending=False, method="min")
        merged = revenue[["公司", "收入排名"]].merge(
            profit[["公司", "利润排名"]], on="公司", how="inner"
        )
        merged["报告期"] = period
        rank_rows.append(merged)

    if not rank_rows:
        st.warning(
            t(
                f"缺少 {revenue_code} 或 {profit_code} 数据，"
                "无法计算排名。请检查 STEP5/STEP6 集成数据。",
                f"Missing {revenue_code} or {profit_code} data, so ranks cannot be calculated. "
                "Check the integrated data in STEP5/STEP6.",
            )
        )
        return

    rank_df = pd.concat(rank_rows, ignore_index=True)

    # ---------- 2. 对每家公司做模式分类（人工覆盖优先） ----------
    pattern_map: dict[str, str] = {}
    for company in all_companies:
        if company in pattern_overrides:
            pattern_map[company] = str(pattern_overrides[company]).strip()
            continue
        series = rank_df[rank_df["公司"].eq(company)].sort_values("报告期")["利润排名"]
        if series.empty:
            continue
        pattern_map[company] = classify_performance_pattern(series)

    # ---------- 3. 按模式分组 ----------
    grouped: dict[str, list[str]] = {p: [] for p in PERFORMANCE_PATTERN_ORDER}
    for company in all_companies:
        pattern = pattern_map.get(company, "其他")
        if pattern not in grouped:
            grouped[pattern] = []
        grouped[pattern].append(company)

    active_patterns = [p for p in PERFORMANCE_PATTERN_ORDER if grouped.get(p)]
    if not active_patterns:
        st.warning(
            t(
                "没有公司能被归类到任何业绩模式。",
                "No company could be classified into a performance pattern.",
            )
        )
        return

    # ---------- 4. 展示说明 ----------
    st.caption(
        t(
            f"横轴为「{revenue_rank_title}」，纵轴为「{profit_rank_title}」；"
            "数值越小表示排名越靠前，第 1 名位于右上角。"
            "每家小图的坐标轴按该公司自身排名范围自适应缩放，便于看出排名波动；"
            "刻度数字仍为真实名次。公司按利润排名年度轨迹归类为："
            "逐年提升 / 稳健经营 / V形反转 / 大起大落 / 持续恶化。",
            f"The x-axis is “{revenue_rank_title}” and the y-axis is “{profit_rank_title}”. "
            "Lower values indicate a better rank, with rank 1 in the top-right corner. "
            "Each panel is scaled to the company’s own rank range; tick labels remain actual ranks. "
            "Companies are grouped by their annual profit-rank trajectory: "
            "Consistent improvement / Steady performance / V-shaped recovery / Highly volatile / Continuous deterioration.",
        )
    )
    # ★ 新增：数据诊断展开区，帮用户排查"某年缺失"的具体原因
    with st.expander(
        t(
            "🔍 数据诊断：查看每家公司的收入/利润数据覆盖情况",
            "Data diagnostics: revenue/profit coverage by company",
        ),
        expanded=False,
    ):
        st.caption(
            t(
                f"下表显示每家公司每个报告期是否有「{revenue_code}」和「{profit_code}」数据。"
                "❌ 表示该期该指标缺失（未披露或未集成）。"
                "若发现大量 ❌，请到 STEP5/STEP6 检查原始数据是否完整。",
                f"The table shows whether each company has “{revenue_code}” and “{profit_code}” "
                "data in each period. ❌ means the metric is missing (not disclosed or not integrated). "
                "If many ❌ appear, check the source data in STEP5/STEP6.",
            )
        )
        _diag_rows = []
        for _c in all_companies:
            _c_df = _df[_df["公司"].eq(_c)]
            for _p in periods_list:
                _p_df = _c_df[_c_df["报告期"].eq(_p)]
                _has_revenue = (
                    not _p_df[_p_df["指标编码"].astype(str).eq(revenue_code)]
                    .dropna(subset=["数值"])
                    .empty
                )
                _has_profit = (
                    not _p_df[_p_df["指标编码"].astype(str).eq(profit_code)]
                    .dropna(subset=["数值"])
                    .empty
                )
                _diag_rows.append(
                    {
                        "公司": _c,
                        "报告期": _p,
                        t(f"有{revenue_code}", f"Has {revenue_code}"): (
                            "✅" if _has_revenue else "❌"
                        ),
                        t(f"有{profit_code}", f"Has {profit_code}"): (
                            "✅" if _has_profit else "❌"
                        ),
                    }
                )
        st.dataframe(
            translate_dataframe(pd.DataFrame(_diag_rows)),
            width="stretch",
            hide_index=True,
        )

    # ---------- 5. 按模式纵向分组，组内公司横向网格排列 ----------
    try:
        grid_cols = int(cfg.get("grid_cols", 4) or 4)
    except (TypeError, ValueError):
        grid_cols = 4
    grid_cols = max(3, min(5, grid_cols))
    for _pattern in active_patterns:
        _companies = grouped[_pattern]
        # 模式标题横幅（整行）
        st.markdown(
            f"<div style='background:#D94330; color:#FFFFFF; "
            f"text-align:center; padding:6px 0; border-radius:3px; "
            f"font-weight:700; font-size:13px; margin:14px 0 10px;'>"
            f"{escape(category_name(_pattern))}"
            f"{t('（', ' (')}{len(_companies)}{t(' 家）', ' companies)')}</div>",
            unsafe_allow_html=True,
        )
        # 该组公司按 grid_cols 横向分批，每批一行
        for _start in range(0, len(_companies), grid_cols):
            _batch = _companies[_start : _start + grid_cols]
            _cols = st.columns(grid_cols, gap="small")
            for _slot, _company in enumerate(_batch):
                with _cols[_slot]:
                    st.markdown(
                        f"<div style='text-align:center; "
                        f"font-size:12px; font-weight:700; color:#C8102E; "
                        f"margin:6px 0 2px; white-space:nowrap; "
                        f"overflow:hidden; text-overflow:ellipsis;' "
                        f"title='{escape(_company)}'>"
                        f"{escape(_company)}</div>",
                        unsafe_allow_html=True,
                    )
                    try:
                        _chart = build_performance_pattern_trajectory(
                            frame,
                            _company,
                            periods_list,
                            revenue_code=revenue_code,
                            profit_code=profit_code,
                            x_title=revenue_rank_title,
                            y_title=profit_rank_title,
                        )
                        render_altair_chart(
                            _chart,
                            width="stretch",
                            key=f"{key_prefix}_pattern_{_pattern}_{_company}",
                        )
                    except ValueError as _exc:
                        st.warning(f"{_company}: {_exc}")


def render_plan_key_metrics_table(frame, highlight_company):
    render_key_solvency_overview(
        frame,
        highlight_company=highlight_company,
        language=get_language(),
    )


def render_plan_financing_table(financing_data):
    render_major_financing(financing_data, language=get_language())


def render_plan_pending_definition(chart_name):
    st.warning(
        t(
            f"“{chart_display_name(chart_name)}”在新版 Excel 中标记为“待定”，目前没有定义可绘图的明细组成指标。",
            f"“{chart_display_name(chart_name)}” is marked “To be defined” in the new Excel and no plottable component metrics are defined yet.",
        )
    )


def render_plan_solvency_ratio_combo(
    company_panel_count, converted, dense_company_panels, highlight_company, key_prefix, periods
):
    shared_y_domain = solvency_ratio_axis_domain(converted, periods)
    period_colors = report_period_color_map(periods)
    st.caption(
        t(
            "图表说明：柱状图为综合偿付能力充足率；粉色折线为核心偿付能力充足率。"
            "各公司统一使用同一百分比纵轴范围，柱形颜色对应不同报告期。",
            "Chart note: bars show the combined solvency ratio and the pink line shows the core solvency ratio. "
            "All companies share the same percentage axis; bar colours identify reporting periods.",
        )
    )
    render_chart_legend(
        [
            *[(period_colors[period], period, "square") for period in periods],
            ("#FD349C", t("核心偿付能力充足率（%）", "Core solvency ratio (%)"), "line"),
        ]
    )
    render_company_chart_grid(
        converted,
        lambda company_frame: build_solvency_ratio_combo_chart(
            company_frame,
            periods,
            shared_y_domain=shared_y_domain,
            dense_layout=dense_company_panels,
            panel_count=company_panel_count,
        ),
        key_prefix=f"{key_prefix}_solvency_ratio_combo",
        highlight_company=highlight_company,
    )


def render_plan_company_period_bar(
    chart_name,
    company_panel_count,
    converted,
    dense_company_panels,
    highlight_company,
    key_prefix,
    module_override,
    periods,
):
    codes = (
        list(module_override.metric_codes)
        if module_override and module_override.metric_codes
        else list(metric_codes_for_chart(chart_name))
    )
    if codes:
        shared_y_domain = metric_bar_axis_domain(
            converted,
            codes[0],
            periods,
        )
        period_colors = report_period_color_map(periods)
        render_chart_legend([(period_colors[period], period, "square") for period in periods])
        render_company_chart_grid(
            converted,
            lambda company_frame: build_company_period_bar_chart(
                company_frame,
                codes[0],
                periods,
                shared_y_domain=shared_y_domain,
                dense_layout=dense_company_panels,
                panel_count=company_panel_count,
            ),
            key_prefix=f"{key_prefix}_{codes[0]}_bar",
            highlight_company=highlight_company,
        )


def render_plan_capital_ratio_combo(
    chart_name,
    company_panel_count,
    converted,
    dense_company_panels,
    highlight_company,
    key_prefix,
    module_override,
    periods,
):
    cfg = module_override.chart_config if module_override else {}
    chart_periods = annual_period_order(periods)
    if not chart_periods:
        st.warning(
            t(
                "当前没有可用于年度组合图的报告期数据。",
                "No reporting-period data is available for the annual combination chart.",
            )
        )
        return
    if chart_name == "综合充足率变化":
        amount_groups = cfg.get("amount_groups") or {
            "实际资本": ("ACTUAL_CAPITAL",),
            "最低资本": ("MINIMUM_CAPITAL",),
        }
        ratio_code = "COMBINED_SOLVENCY_RATIO"
        ratio_label = t("综合偿付能力充足率（%）", "Combined solvency ratio (%)")
    else:
        amount_groups = cfg.get("amount_groups") or {
            "核心资本": ("CORE_T1_CAPITAL", "CORE_T2_CAPITAL"),
            "最低资本": ("MINIMUM_CAPITAL",),
        }
        ratio_code = "CORE_SOLVENCY_RATIO"
        ratio_label = t("核心偿付能力充足率（%）", "Core solvency ratio (%)")
    grouped_amount_groups = cfg.get("grouped_amount_groups") or {
        "实际资本": ("ACTUAL_CAPITAL",),
        "最低资本": ("MINIMUM_CAPITAL",),
    }
    shared_amount_domain = capital_ratio_amount_axis_domain(
        converted,
        amount_groups,
        chart_periods,
    )
    grouped_amount_domain = capital_ratio_amount_axis_domain(
        converted,
        grouped_amount_groups,
        chart_periods,
        stacked=False,
    )
    st.caption(
        t(
            "按年度口径展示：上方 KPMG Pink 折线为对应充足率，下方堆叠柱为资本规模；"
            "同组所有公司统一使用同一金额纵轴范围，便于直接比较资本规模。",
            "Annual view: the KPMG Pink line shows the solvency ratio and stacked bars below show capital scale. "
            "All companies in the group share the same amount axis for direct comparison.",
        )
    )
    st.markdown("**" + t("堆叠柱组合图", "Stacked bar combination") + "**")
    render_chart_legend(
        [
            *[
                (KPMG_CAPITAL_COMBO_COLORS[index], label, "square")
                for index, label in enumerate(amount_groups)
            ],
            ("#FD349C", ratio_label, "line"),
        ]
    )
    render_company_chart_grid(
        converted,
        lambda company_frame: build_capital_ratio_combo_chart(
            company_frame,
            amount_groups,
            chart_periods,
            chart_name,
            ratio_code=ratio_code,
            ratio_label=ratio_label,
            shared_amount_domain=shared_amount_domain,
            dense_layout=dense_company_panels,
            panel_count=company_panel_count,
            annual_labels=True,
        ),
        key_prefix=f"{key_prefix}_{ratio_code}_combo",
        highlight_company=highlight_company,
    )
    st.markdown("**" + t("并列柱组合图", "Grouped bar combination") + "**")
    st.caption(
        t(
            "新增视图：每个报告期的两项资本各自成柱，上方仍保留对应充足率折线。",
            "New view: the two capital items are shown as separate bars for each reporting period, "
            "with the corresponding solvency ratio line above.",
        )
    )
    render_chart_legend(
        [
            *[
                (KPMG_CAPITAL_COMBO_COLORS[index], label, "square")
                for index, label in enumerate(grouped_amount_groups)
            ],
            ("#FD349C", ratio_label, "line"),
        ]
    )
    render_company_chart_grid(
        converted,
        lambda company_frame: build_capital_ratio_combo_chart(
            company_frame,
            grouped_amount_groups,
            chart_periods,
            chart_name,
            ratio_code=ratio_code,
            ratio_label=ratio_label,
            shared_amount_domain=grouped_amount_domain,
            dense_layout=dense_company_panels,
            panel_count=company_panel_count,
            bar_layout="grouped",
            annual_labels=True,
        ),
        key_prefix=f"{key_prefix}_{ratio_code}_grouped_combo",
        highlight_company=highlight_company,
    )


def render_plan_capital_structure_combo(
    company_panel_count,
    converted,
    dense_company_panels,
    highlight_company,
    key_prefix,
    module_override,
    periods,
):
    cfg = module_override.chart_config if module_override else {}
    capital_structure_groups = cfg.get("amount_groups") or {
        "核心资本": ("CORE_T1_CAPITAL", "CORE_T2_CAPITAL"),
        "附属资本": ("ANC_T1_CAPITAL", "ANC_T2_CAPITAL"),
    }
    shared_amount_domain = capital_ratio_amount_axis_domain(
        converted,
        capital_structure_groups,
        periods,
    )
    st.caption(
        t(
            "上方 KPMG Pink 折线展示核心资本占实际资本的比例，下方堆叠柱展示核心资本与附属资本；"
            "同组所有公司统一使用同一金额纵轴范围，便于直接比较资本规模。",
            "The KPMG Pink line shows core capital as a share of actual capital; stacked bars below show core and ancillary capital. "
            "All companies in the group share the same amount axis.",
        )
    )
    render_chart_legend(
        [
            (KPMG_CAPITAL_COMBO_COLORS[0], t("核心资本", "Core capital"), "square"),
            (KPMG_CAPITAL_COMBO_COLORS[1], t("附属资本", "Ancillary capital"), "square"),
            ("#FD349C", t("核心资本占实际资本的比例", "Core capital / actual capital"), "line"),
        ]
    )
    render_company_chart_grid(
        converted,
        lambda company_frame: build_capital_ratio_combo_chart(
            company_frame,
            capital_structure_groups,
            periods,
            t("核心资本与附属资本结构", "Core and ancillary capital structure"),
            ratio_label=t("核心资本占实际资本的比例（%）", "Core capital / actual capital (%)"),
            share_line=True,
            shared_amount_domain=shared_amount_domain,
            dense_layout=dense_company_panels,
            panel_count=company_panel_count,
        ),
        key_prefix=f"{key_prefix}_core_capital_structure",
        highlight_company=highlight_company,
    )


def render_plan_component_stack(
    chart_name,
    company_panel_count,
    converted,
    dense_company_panels,
    highlight_company,
    key_prefix,
    periods,
):
    specs = COMPONENT_STACK_SPECS.get(chart_name, ())
    if not specs:
        st.warning(
            t(
                f"“{chart_display_name(chart_name)}”尚未配置组成指标。",
                f"No component metrics are configured for “{chart_display_name(chart_name)}”.",
            )
        )
        return
    if chart_name == "计入各级资本的保单未来盈余构成占比":
        st.caption(
            t(
                "各色块表示计入相应资本层级的保单未来盈余占该公司已披露保单未来盈余明细合计的比例；"
                "100%表示该公司披露的保单未来盈余全部计入了同一个资本层级，其他资本层级没有披露这个明细。",
                "Each segment shows policy future surplus in a capital tier as a share of the company’s total disclosed policy future surplus detail. "
                "100% means all disclosed surplus was allocated to one tier; no detail was disclosed for other tiers.",
            )
        )
    elif chart_name == "量化风险最低资本构成":
        st.caption(
            t(
                "柱高按各构成项目/量化风险最低资本的比例展示，实际金额仅保留在悬浮信息中；"
                "数据标签与对应比例折线图一致，负向抵减项目位于零线下方。",
                "Bar heights show each component as a share of quantitative risk minimum capital; amounts are available on hover. "
                "Data labels follow the ratio line, and negative offsets sit below the zero line.",
            )
        )
    elif chart_name == "各类市场风险占比":
        st.caption(
            t(
                "柱高按各构成项目/市场风险最低资本的比例展示，实际金额仅保留在悬浮信息中；"
                "负向风险分散效应位于零线下方。",
                "Bar heights show each component as a share of market risk minimum capital; amounts are available on hover. "
                "The negative diversification effect sits below the zero line.",
            )
        )
    elif chart_name == "各类信用风险占比":
        st.caption(
            t(
                "柱高按各构成项目/信用风险最低资本的比例展示，实际金额仅保留在悬浮信息中；"
                "负向风险分散效应位于零线下方。",
                "Bar heights show each component as a share of credit risk minimum capital; amounts are available on hover. "
                "The negative diversification effect sits below the zero line.",
            )
        )
    else:
        st.caption(
            t(
                "按公司分面展示构成；负向抵减项目位于零线下方，所有颜色均来自 KPMG 色卡。",
                "Composition by company; negative offsets sit below the zero line. All colours come from the KPMG palette.",
            )
        )
    negative_component_codes = (
        (
            "QUANT_RISK_DIVERSIFICATION_EFFECT",
            "CONTRACT_LOSS_ABSORPTION_EFFECT",
        )
        if chart_name == "量化风险最低资本构成"
        else (
            ("MARKET_RISK_DIVERSIFICATION_EFFECT",)
            if chart_name == "各类市场风险占比"
            else (
                ("CREDIT_RISK_DIVERSIFICATION_EFFECT",) if chart_name == "各类信用风险占比" else ()
            )
        )
    )
    proportion_stack = chart_name in COMPONENT_STACK_DENOMINATOR_CODES
    shared_stack_domain = (
        component_stack_proportion_axis_domain(
            converted,
            specs,
            periods,
            label_ratio_codes=(
                QUANT_RISK_STACK_RATIO_CODES if chart_name == "量化风险最低资本构成" else None
            ),
            label_denominator_code=COMPONENT_STACK_DENOMINATOR_CODES[chart_name],
            negative_component_codes=negative_component_codes,
        )
        if proportion_stack
        else component_stack_axis_domain(
            converted,
            specs,
            periods,
            negative_component_codes=negative_component_codes,
        )
    )
    render_chart_legend([(color, translate_text(label), "square") for _, label, color in specs])
    render_company_chart_grid(
        converted,
        lambda company_frame: build_component_stack_chart(
            company_frame,
            specs,
            periods,
            chart_name,
            label_ratio_codes=(
                QUANT_RISK_STACK_RATIO_CODES if chart_name == "量化风险最低资本构成" else None
            ),
            label_denominator_code=(COMPONENT_STACK_DENOMINATOR_CODES.get(chart_name, "")),
            value_labels=False,
            negative_component_codes=negative_component_codes,
            dense_layout=dense_company_panels,
            panel_count=company_panel_count,
            shared_y_domain=shared_stack_domain,
            plot_proportions=proportion_stack,
            min_label_share=(0.10 if chart_name == "各类市场风险占比" else None),
        ),
        key_prefix=f"{key_prefix}_component_stack",
        highlight_company=highlight_company,
    )


def render_plan_capital_amount_combo(
    company_panel_count, converted, dense_company_panels, highlight_company, key_prefix, periods
):
    st.caption(
        t(
            "100%堆叠柱显示核心一级、核心二级、附属一级和附属二级资本构成；"
            "所有公司和报告期的柱高一致，色块高度代表结构占比，悬浮可查看占比和金额。",
            "100% stacked bars show core tier 1, core tier 2, ancillary tier 1 and ancillary tier 2 capital. "
            "Bar heights are equal across companies and periods; segment heights show the mix, with shares and amounts on hover.",
        )
    )
    capital_codes = [
        "CORE_T1_CAPITAL",
        "CORE_T2_CAPITAL",
        "ANC_T1_CAPITAL",
        "ANC_T2_CAPITAL",
    ]
    expected_count = converted["公司"].nunique() * len(periods) * len(capital_codes)
    disclosed_count = (
        converted[converted["指标编码"].astype(str).isin(capital_codes)]
        .drop_duplicates(["公司", "报告期", "指标编码"])
        .shape[0]
    )
    capital_legend = [(color, label, "square") for label, color in CAPITAL_STRUCTURE_COLORS.items()]
    if disclosed_count < expected_count:
        capital_legend.append(("#B8BDC7", t("未披露", "Not disclosed"), "square"))
    render_chart_legend(capital_legend)
    render_company_chart_grid(
        converted,
        lambda company_frame: build_capital_amount_combo(
            company_frame,
            periods,
            dense_layout=dense_company_panels,
            panel_count=company_panel_count,
        ),
        key_prefix=f"{key_prefix}_capital_amount",
        highlight_company=highlight_company,
    )


def render_plan_effect_diverging(chart_name, converted, key_prefix, module_override, periods):
    codes = (
        list(module_override.metric_codes)
        if module_override and module_override.metric_codes
        else list(metric_codes_for_chart(chart_name))
    )
    if codes:
        st.caption(
            t(
                "按公司展示抵减效应的跨期方向与幅度，零线用于区分正向增加和负向抵减。",
                "Offsetting effects by company over time; the zero line separates positive additions from negative offsets.",
            )
        )
        render_altair_chart(
            chart_without_internal_title(
                build_effect_diverging_chart(converted, codes[0], periods)
            ),
            width="stretch",
            key=f"{key_prefix}_{codes[0]}_effect",
        )


def render_plan_capital_efficiency_bubble(
    company_colors, converted, highlight_company, key_prefix, periods
):
    render_capital_efficiency_bubble_fragment(
        converted,
        periods,
        company_colors,
        highlight_company,
        key_prefix,
    )


def render_plan_solvency_matrix(company_colors, converted, highlight_company, key_prefix, periods):
    chart, _ = build_matrix_chart(
        converted,
        "CORE_SOLVENCY_RATIO",
        "COMBINED_SOLVENCY_RATIO",
        periods,
        t("偿付能力矩阵", "Solvency matrix"),
        highlight_company,
        regulatory_lines=True,
        company_colors=company_colors,
    )
    render_altair_chart(
        chart_without_internal_title(chart),
        width="stretch",
        key=f"{key_prefix}_solvency_matrix",
    )
    st.caption(
        t(
            "横轴50%、纵轴100%的红色虚线分别为核心和综合偿付能力充足率监管下限。",
            "Red dashed lines at 50% on the x-axis and 100% on the y-axis are the regulatory minima for the core and combined solvency ratios.",
        )
    )


def render_plan_market_credit_matrix(
    company_colors, converted, highlight_company, key_prefix, periods
):
    chart, _ = build_matrix_chart(
        converted,
        "MARKET_RISK_TO_QUANT_CAPITAL",
        "CREDIT_RISK_TO_QUANT_CAPITAL",
        periods,
        t("市场—信用风险矩阵", "Market–credit risk matrix"),
        highlight_company,
        company_colors=company_colors,
    )
    render_altair_chart(
        chart_without_internal_title(chart),
        width="stretch",
        key=f"{key_prefix}_market_credit_matrix",
    )
    st.caption(
        t(
            "深蓝虚线为样本中位数，用于识别市场风险和信用风险同时偏高的公司。",
            "The dark-blue dashed line is the sample median, highlighting companies with both high market and credit risk.",
        )
    )


def render_plan_risk_ratio_scatter(
    chart_name, company_colors, converted, highlight_company, key_prefix, periods
):
    x_code, y_code, bubble_size_code, denominator_label = (
        RISK_RATIO_SCATTER_SPECS.get(chart_name)
        or RISK_RATIO_SCATTER_SPECS[MARKET_RISK_ASSET_SCATTER]
    )
    if chart_name in RISK_SCATTER_DUAL_VIEW_CHARTS:
        render_risk_ratio_scatter_fragment(
            converted,
            periods,
            company_colors,
            highlight_company,
            key_prefix,
            chart_name,
            x_code,
            y_code,
            bubble_size_code,
            denominator_label,
        )
    else:
        chart, latest_period = build_matrix_chart(
            converted,
            x_code,
            y_code,
            periods,
            chart_name,
            highlight_company,
            company_colors=company_colors,
            percentage_axes=True,
            bubble_size_code=bubble_size_code,
            bubble_size_label=denominator_label,
        )
        centered_chart = chart_without_internal_title(chart).properties(
            width=RISK_SCATTER_DISPLAY_WIDTH
        )
        st.caption(
            t(
                f"展示 {latest_period}；每个气泡代表一家公司，横纵轴均为占{denominator_label}的比例，"
                f"气泡面积代表{denominator_label}规模；"
                "深蓝虚线为样本横纵指标中位数。",
                f"Showing {latest_period}; each bubble is one company. Both axes are shown as a share of "
                f"{translate_text(denominator_label)}; bubble size represents {translate_text(denominator_label)} scale. "
                "The dark-blue dashed line is the sample median of both axes.",
            )
        )
        with st.container(horizontal_alignment="center"):
            render_altair_chart(
                centered_chart,
                width=RISK_SCATTER_DISPLAY_WIDTH,
                key=f"{key_prefix}_risk_asset_scatter",
            )


# 前三大财险公司名称前缀（实际数据名为人保产险/平安产险/太保产险）
TOP3_NONLIFE_PREFIXES = ("人保", "平安", "太保")


def _split_top10_peer_group(frame: pd.DataFrame) -> pd.DataFrame:
    """把“同业分类”里的“前十大”拆成“前三大”/“前4-10”。

    - 公司名以“人保/平安/太保”开头的 → “前三大”
    - 其余原“前十大”公司 → “前4-10”
    - 其他分类原样保留。

    作用于 DataFrame 的“同业分类”列；capability_scores 会按该列分组建立打分池，
    因此前三家在 3 家内做百分位排名，前4-10 在 7 家内做百分位排名。
    """
    if "同业分类" not in frame.columns or frame.empty:
        return frame
    frame = frame.copy()
    company = frame["公司"].astype(str).str.strip()
    category = frame["同业分类"].astype(str).str.strip()
    is_top10 = category.eq("前十大")
    is_top3 = company.str.startswith(TOP3_NONLIFE_PREFIXES)
    frame.loc[is_top10 & is_top3, "同业分类"] = "前三大"
    frame.loc[is_top10 & ~is_top3, "同业分类"] = "前4-10"
    return frame


def render_plan_capability_radar(
    chart_frame, highlight_company, key_prefix, module_override, periods
):
    cfg = module_override.chart_config if module_override else {}
    axes_config = deepcopy(cfg.get("axes") or DEFAULT_CAPABILITY_AXES)
    if is_english():
        for axis in axes_config:
            if isinstance(axis, dict) and axis.get("label"):
                axis["label"] = translate_text(str(axis["label"]))
    axes_labels = [ax["label"] for ax in axes_config]

    # ★ 打分前先把“前十大”拆成“前三大”/“前4-10”：
    #   前三大 = 人保、平安、太保三家财险公司（实际数据名为人保产险/平安产险/太保产险，
    #   故按公司名前缀匹配）；其余前十大公司归入“前4-10”。
    #   拆分作用于 chart_frame 的“同业分类”列，使 capability_scores 的分组打分池
    #   与下方展示分组口径一致：前三家在 3 家内排名，前4-10 在 7 家内排名。
    chart_frame = _split_top10_peer_group(chart_frame)

    latest_period_for_anchor = str(periods[-1]) if periods else ""
    baseline_full = chart_frame[
        chart_frame["报告期"].astype(str).eq(latest_period_for_anchor)
    ].copy()

    score_all, raw_all, latest_period = capability_scores(
        chart_frame,
        axes_config,
        periods,
        baseline_frame=baseline_full,
    )

    if score_all.empty:
        # ---------- 诊断：定位打分池为空的具体原因 ----------
        required_codes = [str(ax.get("code", "")).strip() for ax in axes_config]
        if "指标编码" in chart_frame.columns:
            present_codes = sorted(
                {c for c in chart_frame["指标编码"].dropna().astype(str) if c.strip()}
            )
        else:
            present_codes = []
        missing_codes = [c for c in required_codes if c not in present_codes]
        latest_period = str(periods[-1]) if periods else ""
        if latest_period and "报告期" in chart_frame.columns:
            latest_rows = chart_frame[
                chart_frame["报告期"].astype(str).eq(latest_period)
            ]
            latest_n_companies = latest_rows["公司"].nunique() if "公司" in latest_rows.columns else 0
            latest_n_rows = len(latest_rows)
        else:
            latest_n_rows = 0
            latest_n_companies = 0
        st.warning(
            t(
                "能力五边形当前无法打分，诊断如下：\n"
                f"- 筛选后总记录数：{len(chart_frame)}；\n"
                f"- 最新报告期「{latest_period or '（无）'}」在筛选后仅剩 {latest_n_rows} 行、"
                f"{latest_n_companies} 家公司；\n"
                f"- 要求的指标编码：{'、'.join(required_codes)}；\n"
                f"- 数据中缺失的编码：{'、'.join(missing_codes) if missing_codes else '无（5 个编码都在）'}；\n"
                f"- 数据中实际存在的编码：{'、'.join(present_codes) if present_codes else '无'}。\n"
                "常见原因：89 家数据的「指标编码」列与标准编码不一致（如用了中文名），"
                "或最新报告期这一期缺少上述 5 个指标。",
                "The capability pentagon cannot be scored. Diagnostics:\n"
                f"- Filtered records: {len(chart_frame)};\n"
                f"- Latest reporting period “{latest_period or 'none'}” has only {latest_n_rows} rows and "
                f"{latest_n_companies} companies after filtering;\n"
                f"- Required metric codes: {', '.join(required_codes)};\n"
                f"- Missing codes: {', '.join(missing_codes) if missing_codes else 'None (all 5 codes present)'};\n"
                f"- Codes present: {', '.join(present_codes) if present_codes else 'None'}.\n"
                "Common causes: the metric-code column for the 89 companies does not match the standard codes "
                "(for example, Chinese names were used), or the latest period lacks the five metrics above.",
            )
        )
        return

    # ---------- 取得每家公司的同业分类（此时已是拆分后的“前三大”/“前4-10”） ----------
    category_column = "同业分类" if "同业分类" in chart_frame.columns else None
    if category_column:
        cat_map = (
            chart_frame[["公司", category_column]]
            .dropna()
            .assign(**{category_column: lambda d: d[category_column].astype(str).str.strip()})
            .drop_duplicates(subset=["公司"], keep="last")
            .set_index("公司")[category_column]
            .to_dict()
        )
    else:
        cat_map = {}
    score_all["_分类"] = score_all["公司"].astype(str).map(cat_map).fillna("未分类")

    # ---------- 分类顺序：固定业务分组顺序，未列入的（未分类等）排最后 ----------
    CATEGORY_ORDER = [
        "前三大",
        "前4-10",
        "大型公司",
        "中型公司",
        "小型公司",
        "农业保险",
        "外资系",
        "自保公司",
    ]
    present_categories = list(
        dict.fromkeys(str(category) for category in score_all["_分类"])
    )
    ordered_categories = [c for c in CATEGORY_ORDER if c in present_categories]
    # 顺序表之外的分类（如“未分类”）追加到最后
    for c in present_categories:
        if c not in ordered_categories:
            ordered_categories.append(c)

    # ---------- 样式提示 ----------
    st.caption(
        t(
            f"展示 {latest_period}；每家公司单独一张能力五边形，"
            "按“同业分类”分组排列。"
            "顶点依次为：" + "、".join(axes_labels) + "。"
            "打分在各分组内做百分位排名（前三大 3 家互比、前4-10 7 家互比），"
            "50 分 ≈ 本组中位数水平（灰色虚线五边形）。",
            f"Showing {latest_period}; one capability pentagon per company, grouped by peer group. "
            f"Vertices: {', '.join(axes_labels)}. "
            "Scores are percentile ranks within each group (Top 3 compares 3 companies; Top 4–10 compares 7). "
            "A score of 50 is approximately the group median (grey dashed pentagon).",
        )
    )

    # ---------- 按分类逐组渲染 ----------
    columns_per_row = 5  # 每行 5 家
    panel_size = 230  # 单张小图尺寸
    tracked_company = str(highlight_company or "").strip()

    for category in ordered_categories:
        cat_scores = score_all[score_all["_分类"].astype(str).eq(category)].reset_index(drop=True)
        if cat_scores.empty:
            continue

        # ---- 组标题 ----
        st.markdown(
            f"<div style='color:#00338D;font-size:15px;font-weight:700;"
            f"border-bottom:1.5px solid #00338D;margin:18px 0 8px;padding-bottom:4px;'>"
            f"{escape(category_name(str(category)))}"
            f"<span style='color:#7A8496;font-size:12px;font-weight:400;margin-left:8px;'>"
            f"{t('（', ' (')}{len(cat_scores)}{t(' 家）', ' companies)')}</span></div>",
            unsafe_allow_html=True,
        )

        # ---- 网格布局 ----
        companies = list(cat_scores["公司"].astype(str))
        for start in range(0, len(companies), columns_per_row):
            batch = companies[start : start + columns_per_row]
            cols = st.columns(len(batch), gap="small")
            for col, company in zip(cols, batch):
                with col:
                    is_tracked = (
                        tracked_company and tracked_company != "无" and company == tracked_company
                    )
                    title_color = "#ED2124" if is_tracked else "#00338D"
                    st.markdown(
                        f"<div style='text-align:center;font-size:12px;"
                        f"font-weight:700;color:{title_color};"
                        f"margin:4px 0 0;white-space:nowrap;overflow:hidden;"
                        f"text-overflow:ellipsis;' title='{escape(company)}'>"
                        f"{escape(company)}</div>",
                        unsafe_allow_html=True,
                    )

                    one_company_scores = cat_scores[cat_scores["公司"].astype(str).eq(company)]
                    # 单公司图：统一用 KPMG 蓝，追踪公司用红色
                    single_color = "#ED2124" if is_tracked else "#00338D"
                    single_colors = {company: single_color}

                    try:
                        chart = build_capability_radar_chart(
                            one_company_scores,
                            axes_labels,
                            single_colors,
                            panel_width=panel_size,
                            panel_height=panel_size,
                            show_legend=False,
                            label_font_size=9,
                            median_color="#94A3B8",
                        )
                    except Exception as exc:
                        st.warning(f"⚠️ {company} 的雷达图渲染失败：{exc}")
                        import traceback

                        st.code(traceback.format_exc())
                        continue

                    render_altair_chart(
                        chart_without_internal_title(chart),
                        width="content",
                        key=f"{key_prefix}_radar_{category}_{company}",
                    )

    # ---------- 明细展开 ----------
    with st.expander(t("查看原始数值与得分明细", "View raw values and scores")):
        raw_display = translate_dataframe(raw_all)
        st.dataframe(
            raw_display.style.format(
                {col: "{:,.2f}" for col in raw_display.columns if col != t("公司", "Company")},
                na_rep="—",
            ),
            width="stretch",
            hide_index=True,
        )
        score_display = translate_dataframe(score_all.drop(columns=["_分类"]))
        st.dataframe(
            score_display.style.format(
                {
                    col: "{:.0f}"
                    for col in score_display.columns
                    if col not in {t("公司", "Company"), "_分类"}
                },
                na_rep="0",
            ),
            width="stretch",
            hide_index=True,
        )


# 公司类型分组固定顺序（与能力五边形分组口径一致）
COMPANY_LINE_GROUP_ORDER = [
    "前三大",
    "前4-10",
    "大型公司",
    "中型公司",
    "小型公司",
    "农业保险",
    "外资系",
    "自保公司",
]


def render_plan_company_line_trend(chart_frame, highlight_company, key_prefix, periods):
    """核心指标速览：每家公司一个年度折线小图，按公司类型分组分节展示。"""
    if chart_frame.empty:
        st.warning(
            t(
                "当前筛选范围没有可绘制的数据。",
                "No plottable data in the current selection.",
            )
        )
        return
    codes = sorted(
        {c for c in chart_frame["指标编码"].dropna().astype(str) if c.strip()}
    )
    code = codes[0] if codes else "COMBINED_SOLVENCY_RATIO"

    # 拆分"前十大"为前三大/前4-10
    chart_frame = _split_top10_peer_group(chart_frame)

    # 取每家公司的同业分类（按公司名聚合，同公司分类一致）
    if "同业分类" in chart_frame.columns:
        company_cls = (
            chart_frame[["公司", "同业分类"]]
            .dropna()
            .drop_duplicates(subset=["公司"])
        )
        cls_map = dict(zip(company_cls["公司"].astype(str), company_cls["同业分类"].astype(str)))
    else:
        cls_map = {}

    # 按固定顺序分组，未列入的归"其他"
    groups: dict[str, list[str]] = {g: [] for g in COMPANY_LINE_GROUP_ORDER}
    groups["其他"] = []
    for company in dict.fromkeys(chart_frame["公司"].astype(str)):
        g = cls_map.get(company, "")
        if g in groups:
            groups[g].append(company)
        else:
            groups["其他"].append(company)

    st.caption(
        t(
            "每家公司一个年度小折线图，纵轴为 %；蓝色折线为该公司综合偿付能力充足率走势，"
            "红色虚线为监管下限 100%；仅标注首末年数值，悬停查看各年明细。",
            "One annual line chart per company; the y-axis is in %. The blue line is the company’s combined solvency ratio "
            "and the red dashed line is the 100% regulatory minimum. Only first/last-year values are labelled; hover for annual detail.",
        )
    )

    for group_name in COMPANY_LINE_GROUP_ORDER + ["其他"]:
        companies = groups.get(group_name) or []
        if not companies:
            continue
        sub = chart_frame[chart_frame["公司"].astype(str).isin(companies)]
        if sub.empty:
            continue
        st.markdown(
            f"<div style='margin:14px 0 6px;font-size:14px;font-weight:700;"
            f"color:#00338D;border-left:4px solid #00338D;padding-left:8px;'>"
            f"{category_name(group_name)}"
            f"{t('（', ' (')}{len(companies)}{t(' 家）', ' companies)')}</div>",
            unsafe_allow_html=True,
        )
        try:
            chart = build_company_line_trend_chart(
                sub, code, periods, highlight_company or ""
            )
        except ValueError as exc:
            st.warning(
                t(
                    f"{category_name(group_name)} 无法绘制：{exc}",
                    f"{category_name(group_name)} cannot be drawn: {exc}",
                )
            )
            continue
        render_altair_chart(
            chart_without_internal_title(chart),
            width="stretch",
            key=f"{key_prefix}_line_trend_{code}_{group_name}",
        )


def render_plan_underwriting_efficiency_scatter(
    frame,
    key_prefix,
    module_override,
    *,
    industry_frame=None,
):
    # 承保效益属于“行业概览”，排名应基于上传数据中的全部公司；
    # 没有行业全量数据时才回退到当前筛选范围。
    source = (
        industry_frame
        if isinstance(industry_frame, pd.DataFrame) and not industry_frame.empty
        else frame
    )
    render_underwriting_efficiency_scatter(
        source,
        chart_config=(module_override.chart_config if module_override else {}),
        key_prefix=key_prefix,
    )


def render_plan_industry_premium_structure(
    industry_frame,
    *,
    unit_mode,
    key_prefix,
    module_override,
):
    """Render the fixed-history industry premium overview."""
    cfg = module_override.chart_config if module_override else {}
    annual_scope = str(cfg.get("annual_scope", "") or "").strip()
    default_range = "全部"

    range_options = list(INDUSTRY_PREMIUM_WINDOWS)
    # A new key intentionally resets older sessions that previously defaulted
    # to the five-year window.
    range_key = f"{key_prefix}_industry_premium_range_all_default"
    if st.session_state.get(range_key) not in range_options:
        st.session_state[range_key] = default_range
    toolbar_left, toolbar_right = st.columns(
        [3.2, 1.5],
        vertical_alignment="center",
    )
    with toolbar_left:
        st.caption(t("指标：签单保费 · 行业全量口径", "Metric: signed premium · full industry"))
    with toolbar_right:
        selected_range = st.segmented_control(
            t("显示范围", "Display range"),
            range_options,
            key=range_key,
            label_visibility="collapsed",
            width="stretch",
            format_func=translate_text,
        )
    selected_range = selected_range or default_range
    year_window = INDUSTRY_PREMIUM_WINDOWS[selected_range]
    annual, detail, companies = prepare_industry_premium_annual(
        industry_frame,
        years=year_window,
        annual_scope=annual_scope,
        include_historical=True,
    )
    if annual.empty:
        st.warning(
            t(
                "当前数据没有可用的行业年度累计保费记录。",
                "No usable annual industry premium records are available.",
            )
        )
        return

    complete = annual[annual["完整"].astype(bool)].copy()
    available = annual[
        annual["合计"].notna()
        | annual["车险"].notna()
        | annual["非车险"].notna()
    ].copy()
    if available.empty:
        st.warning(
            t(
                "当前数据没有可用的行业年度保费记录。",
                "No usable annual industry premium records are available.",
            )
        )
        return

    unit = unit_mode if unit_mode in {"十亿元", "亿元", "百万元", "十万元"} else "亿元"
    divisor = {
        "十亿元": 100_000.0,
        "亿元": 10_000.0,
        "百万元": 100.0,
        "十万元": 10.0,
    }[unit]

    def display_value(value) -> float | None:
        if value is None or pd.isna(value):
            return None
        return float(value) / divisor

    value_format = ",.1f" if unit == "十亿元" else ",.0f"

    base = complete if not complete.empty else available
    latest = base.iloc[-1]
    previous = base.iloc[-2] if len(base) >= 2 else None
    latest_is_complete = bool(latest.get("完整", False))
    latest_total = display_value(latest.get("合计"))
    latest_car = display_value(latest.get("车险"))
    latest_non_car = display_value(latest.get("非车险"))
    latest_car_value = 0.0 if latest_car is None or pd.isna(latest_car) else float(latest_car)
    latest_non_car_value = (
        0.0 if latest_non_car is None or pd.isna(latest_non_car) else float(latest_non_car)
    )
    car_share = (
        None
        if not latest_total or latest_total <= 0
        else (latest_car_value / float(latest_total) * 100)
    )
    non_car_share = (
        None
        if not latest_total or latest_total <= 0
        else (latest_non_car_value / float(latest_total) * 100)
    )

    metric_columns = st.columns(4)
    latest_year = str(latest.get("年度", "") or "")
    metric_columns[0].metric(
        t(
            (
                f"最新完整年度合计（{latest_year}）"
                if latest_year and latest_is_complete
                else f"最新年度合计（{latest_year}）"
            ),
            (
                f"Latest complete-year total ({latest_year})"
                if latest_year and latest_is_complete
                else f"Latest-year total ({latest_year})"
            ),
        ),
        "—"
        if latest_total is None
        else f"{latest_total:{value_format}} {unit_name(unit)}",
        border=True,
    )
    previous_total = None if previous is None else previous.get("合计")
    previous_year = None if previous is None else str(previous.get("年度", ""))
    is_year_over_year = bool(
        previous_year
        and str(latest.get("年度", "")).isdigit()
        and previous_year.isdigit()
        and int(latest["年度"]) - int(previous_year) == 1
    )
    growth_label = (
        t("同比增速", "YoY growth")
        if is_year_over_year and latest_is_complete
        else t("较上一有数年度", "vs previous year with data")
    )
    latest_total_raw = latest.get("合计")
    growth_value: float | None = None
    car_growth_value: float | None = None
    non_car_growth_value: float | None = None
    if (
        previous_total is not None
        and pd.notna(previous_total)
        and float(previous_total) != 0
        and latest_total_raw is not None
        and pd.notna(latest_total_raw)
    ):
        growth_value = (float(latest_total_raw) / float(previous_total) - 1) * 100
        metric_columns[1].metric(
            growth_label,
            f"{growth_value:+.1f}%",
            border=True,
        )
        previous_car = previous.get("车险")
        previous_non_car = previous.get("非车险")
        latest_car_raw = latest.get("车险")
        latest_non_car_raw = latest.get("非车险")
        if (
            previous_car is not None
            and pd.notna(previous_car)
            and float(previous_car) != 0
            and latest_car_raw is not None
            and pd.notna(latest_car_raw)
        ):
            car_growth_value = (
                float(latest_car_raw) / float(previous_car) - 1
            ) * 100
        if (
            previous_non_car is not None
            and pd.notna(previous_non_car)
            and float(previous_non_car) != 0
            and latest_non_car_raw is not None
            and pd.notna(latest_non_car_raw)
        ):
            non_car_growth_value = (
                float(latest_non_car_raw) / float(previous_non_car) - 1
            ) * 100
    else:
        metric_columns[1].metric(growth_label, "—", border=True)
    cagr_value: float | None = None
    if len(complete) >= 2 and float(complete.iloc[0]["合计"] or 0) > 0:
        cagr_value = (
            (float(latest["合计"]) / float(complete.iloc[0]["合计"]))
            ** (1 / (len(complete) - 1))
            - 1
        ) * 100
        metric_columns[2].metric(
            t(f"{len(complete)}年复合增速", f"{len(complete)}-year CAGR"),
            f"{cagr_value:+.1f}%",
            border=True,
        )
    else:
        metric_columns[2].metric(t("复合增速", "CAGR"), "—", border=True)
    metric_columns[3].metric(
        t("车险 / 非车险占比", "Motor / non-motor share"),
        "—"
        if car_share is None or non_car_share is None
        else f"{car_share:.0f}% / {non_car_share:.0f}%",
        border=True,
    )

    growth_tone = (
        "up"
        if growth_value is not None and growth_value >= 0
        else "down"
        if growth_value is not None
        else "neutral"
    )
    insight_bullets = [
        t(
            (
                f"车险占比 {car_share:.0f}%，非车险占比 {non_car_share:.0f}%"
                if car_share is not None and non_car_share is not None
                else "车险 / 非车险结构暂不可用"
            ),
            (
                f"Motor {car_share:.0f}%, non-motor {non_car_share:.0f}%"
                if car_share is not None and non_car_share is not None
                else "Motor / non-motor mix is not available"
            ),
        ),
        t(
            (
                f"车险同比 {car_growth_value:+.1f}%，"
                f"非车险同比 {non_car_growth_value:+.1f}%"
                if car_growth_value is not None and non_car_growth_value is not None
                else "分险种同比暂不可比"
            ),
            (
                f"Motor YoY {car_growth_value:+.1f}%, "
                f"non-motor YoY {non_car_growth_value:+.1f}%"
                if car_growth_value is not None and non_car_growth_value is not None
                else "Line-of-business YoY is not comparable"
            ),
        ),
        t(
            (
                f"{len(complete)} 个完整年度复合增速 {cagr_value:+.1f}%"
                if cagr_value is not None
                else "完整年度不足，暂不计算复合增速"
            ),
            (
                f"{len(complete)}-year CAGR {cagr_value:+.1f}%"
                if cagr_value is not None
                else "Insufficient complete years to calculate CAGR"
            ),
        ),
    ]
    render_insight_card(
        title=t("规模与结构", "Scale and structure"),
        headline=t(f"{latest_year} 行业签单保费", f"{latest_year} industry signed premium"),
        value=(
            "—"
            if latest_total is None
            else f"{latest_total:,.0f} {unit_name(unit)}"
        ),
        delta=(
            t("同比不可比", "YoY not comparable")
            if growth_value is None
            else f"{growth_label} {growth_value:+.1f}%"
        ),
        delta_tone=growth_tone,
        bullets=insight_bullets,
        footnote=t(
            "柱状为车险/非车险保费金额（左轴），折线为同比增速（右轴，%）；"
            "历史年度采用内置行业数据，重叠年度以上传数据为准。",
            "Bars show motor/non-motor premium (left axis); lines show YoY growth (right axis, %). "
            "Historical years use built-in industry data; uploaded data prevails for overlapping years.",
        ),
    )

    range_titles = {
        "近5年": t("近五年", "Last 5 years"),
        "近10年": t("近十年", "Last 10 years"),
        "全部": t("历年", "All years"),
    }
    years = annual["年度"].astype(str).tolist()
    year_span = years[0] if len(years) == 1 else f"{years[0]}–{years[-1]}"
    has_growth = int(pd.to_numeric(annual["合计"], errors="coerce").notna().sum()) >= 2
    chart_title = t(
        "保费规模与同比增速" if has_growth else "保费规模与结构",
        "Premium scale and YoY growth" if has_growth else "Premium scale and structure",
    )
    with st.container(key=f"{key_prefix}_industry_premium_chart"):
        st.markdown(
            f"<div class='s7-uw-year-title'>{range_titles[selected_range]}{chart_title}</div>"
            f"<div class='s7-uw-year-sub'>"
            f"{t('左：签单保费规模与同比增速　右：车险 / 非车险占比与同比增速', 'Left: signed premium scale and YoY growth   Right: motor / non-motor mix and YoY growth')}"
            f" · {year_span}</div>",
            unsafe_allow_html=True,
        )
        left_column, right_column = st.columns(2, gap="large")
        with left_column:
            st.markdown(
                f"<div class='s7-premium-panel-title'>{t('签单保费规模与同比增速', 'Signed premium scale and YoY growth')}</div>"
                f"<div class='s7-premium-panel-sub'>{t('柱：年度签单保费　折线：同比增速（右轴，%）', 'Bars: annual signed premium   Line: YoY growth (right axis, %)')}</div>",
                unsafe_allow_html=True,
            )
            left_legend = [
                (COBALT_BLUE, t("签单保费（柱）", "Signed premium (bars)"), "square"),
            ]
            if has_growth:
                left_legend.extend(
                    [
                        (PINK, t("合计同比", "Total YoY"), "line"),
                        ("#94A3B8", t("0% 基准线", "0% baseline"), "dash"),
                        ("#94A3B8", t("基期（无同比）", "Base year (no YoY)"), "outline"),
                    ]
                )
            render_chart_legend(left_legend, align="left")
            try:
                render_altair_chart(
                    build_industry_premium_total_chart(annual, unit_mode=unit),
                    width="stretch",
                    key=f"{key_prefix}_industry_premium_total",
                )
            except ValueError as exc:
                st.warning(translate_text(str(exc)))
        with right_column:
            st.markdown(
                f"<div class='s7-premium-panel-title'>{t('车险 / 非车险占比与同比增速', 'Motor / non-motor mix and YoY growth')}</div>"
                f"<div class='s7-premium-panel-sub'>{t('柱：100% 堆叠占比　折线：车险 / 非车险同比增速（右轴，%）', 'Bars: 100% stacked mix   Lines: motor / non-motor YoY growth (right axis, %)')}</div>",
                unsafe_allow_html=True,
            )
            right_legend = [
                (COBALT_BLUE, t("车险（占比）", "Motor (share)"), "square"),
                (PACIFIC_BLUE, t("非车险（占比）", "Non-motor (share)"), "square"),
            ]
            if has_growth:
                right_legend.extend(
                    [
                        (PURPLE, t("车险同比", "Motor YoY"), "line"),
                        (PINK, t("非车险同比", "Non-motor YoY"), "line"),
                        ("#94A3B8", t("0% 基准线", "0% baseline"), "dash"),
                        ("#94A3B8", t("基期（无同比）", "Base year (no YoY)"), "outline"),
                    ]
                )
            render_chart_legend(right_legend, align="left")
            try:
                render_altair_chart(
                    build_industry_premium_mix_chart(annual, unit_mode=unit),
                    width="stretch",
                    key=f"{key_prefix}_industry_premium_mix",
                )
            except ValueError as exc:
                st.warning(translate_text(str(exc)))
        base_year = years[0] if years and years[0] != "2017" else None
        if has_growth and base_year and len(years) >= 2:
            st.caption(
                t(
                    f"{base_year} 为基期年，不计算同比；同比折线从 {years[1]} 年开始。",
                    f"{base_year} is the base year with no YoY calculation; the YoY line starts in {years[1]}.",
                )
            )

        historical_years = annual.loc[
            annual["来源"].eq(HISTORICAL_SOURCE), "年度"
        ].astype(str).tolist()
        uploaded_years = annual.loc[
            annual["来源"].eq(UPLOAD_SOURCE), "年度"
        ].astype(str).tolist()

        def year_span_text(values: list[str]) -> str:
            return values[0] if len(values) == 1 else f"{values[0]}–{values[-1]}"

        source_parts = []
        if historical_years:
            source_parts.append(
                t(
                    f"{year_span_text(historical_years)} 采用固定行业数据",
                    f"{year_span_text(historical_years)}: built-in industry data",
                )
            )
        if uploaded_years:
            source_parts.append(
                t(
                    f"{year_span_text(uploaded_years)} 采用上传数据",
                    f"{year_span_text(uploaded_years)}: uploaded data",
                )
            )
        source_note = t("；", "; ").join(source_parts)
        if historical_years and uploaded_years:
            source_note += t("，重叠年份以上传数据为准", "; uploaded data prevails for overlapping years")
        st.caption(f"{source_note}。")

    incomplete = annual[~annual["完整"].astype(bool)]
    anomaly_count = int(pd.to_numeric(annual["异常数"], errors="coerce").fillna(0).sum())

    with st.expander(t("查看行业年度明细", "View annual industry detail")):
        summary = annual[["年度", "车险", "非车险", "合计", "来源"]].copy()
        for column in ("车险", "非车险", "合计"):
            summary[column] = pd.to_numeric(summary[column], errors="coerce") / divisor
        summary["车险占比"] = (
            pd.to_numeric(summary["车险"], errors="coerce")
            / pd.to_numeric(summary["合计"], errors="coerce")
        ).map(lambda value: "" if pd.isna(value) else f"{value:.0%}")
        summary = summary.rename(
            columns={
                "车险": f"车险（{unit}）",
                "非车险": f"非车险（{unit}）",
                "合计": f"合计（{unit}）",
            }
        )
        st.markdown("**" + t("年度汇总", "Annual summary") + "**")
        st.dataframe(translate_dataframe(summary), width="stretch", hide_index=True)
        if not incomplete.empty:
            company_count = max(len(companies), 1)
            missing_labels = []
            for row in incomplete.itertuples(index=False):
                if int(row.公司数) == 0:
                    reason = t("缺少年度累计", "missing year-to-date")
                elif int(row.公司数) < company_count * 0.8:
                    reason = t("年度累计覆盖不足", "insufficient year-to-date coverage")
                elif int(row.车险公司数) < company_count * 0.8:
                    reason = t("车险拆分覆盖不足", "insufficient motor split coverage")
                else:
                    reason = t("数据不完整", "incomplete data")
                missing_labels.append(f"{row.年度}{t('（', ' (')}{reason}{t('）', ')')}")
            st.caption(t("数据不完整的年度：", "Years with incomplete data: ") + t("、", ", ").join(missing_labels))
        if anomaly_count:
            st.caption(
                t(
                    f"检测到 {anomaly_count} 条公司年度值疑似单位/量级异常，"
                    "图表未自动改写原始数值；请核对明细。",
                    f"Detected {anomaly_count} company-year values with suspected unit/scale anomalies; "
                    "charts do not overwrite the source values. Please review the detail.",
                )
            )
        display = detail.copy()
        for column in ("车险", "非车险", "合计"):
            if column in display.columns:
                display[column] = pd.to_numeric(display[column], errors="coerce") / divisor
        display = display.rename(
            columns={
                "车险": f"车险（{unit}）",
                "非车险": f"非车险（{unit}）",
                "合计": f"合计（{unit}）",
            }
        )
        if not display.empty:
            st.markdown("**" + t("公司级明细（上传数据）", "Company detail (uploaded data)") + "**")
            st.dataframe(translate_dataframe(display), width="stretch", hide_index=True)


def render_plan_industry_market_share(
    industry_frame,
    *,
    unit_mode,
    key_prefix,
    module_override,
):
    """Render latest-year category market share pies and amount bars."""
    cfg = module_override.chart_config if module_override else {}
    min_coverage = float(cfg.get("min_coverage", 0.8) or 0.8)
    grouping_options = ["拆分：前三大 / 前4-10", "合并：前十大"]
    grouping_key = f"{key_prefix}_market_share_top_grouping"
    if st.session_state.get(grouping_key) not in grouping_options:
        st.session_state[grouping_key] = grouping_options[0]
    st.radio(
        t("前十大展示口径", "Top 10 grouping"),
        grouping_options,
        horizontal=True,
        key=grouping_key,
        format_func=lambda value: (
            t("拆分：前三大 / 前4-10", "Split: Top 3 / Top 4–10")
            if value == grouping_options[0]
            else t("合并：前十大", "Merged: Top 10")
        ),
        help=t(
            "前三大 = 人保、平安、太保；前4-10 = 其余原前十大公司。"
            "切换为“合并”后恢复单一前十大分类。",
            "Top 3 = PICC, Ping An and CPIC; Top 4–10 = the remaining original Top 10 companies. "
            "Switch to “Merged” to restore a single Top 10 category.",
        ),
    )
    top_grouping = (
        "merged"
        if str(st.session_state.get(grouping_key, "")).startswith("合并")
        else "split"
    )
    st.caption(
        t(
            "公司分类来自根目录《财险公司列表.xlsx》；"
            "前三大按公司名前缀匹配人保、平安、太保，其余原前十大公司归入前4-10。",
            "Company categories come from 财险公司列表.xlsx. Top 3 is matched by company-name prefix "
            "to PICC, Ping An and CPIC; the remaining original Top 10 companies form Top 4–10.",
        )
    )
    data = prepare_market_share_data(
        industry_frame,
        min_coverage=min_coverage,
        top_grouping=top_grouping,
    )
    if data is None:
        st.warning(
            t(
                "当前数据没有可用于市场份额分析的年度记录。",
                "No annual records are available for market-share analysis.",
            )
        )
        return

    unit = unit_mode if unit_mode in {"十亿元", "亿元", "百万元", "十万元"} else "亿元"
    divisor = {
        "十亿元": 100_000.0,
        "亿元": 10_000.0,
        "百万元": 100.0,
        "十万元": 10.0,
    }[unit]

    def category_share(metric: str, category: str) -> float | None:
        row = data.categories[data.categories["公司类别"].astype(str).eq(category)]
        if row.empty:
            return None
        value = pd.to_numeric(row["保费占比" if metric == "保费" else "净利润占比"], errors="coerce")
        return None if value.empty or pd.isna(value.iloc[0]) else float(value.iloc[0]) * 100.0

    top3_share = category_share("保费", TOP3_CATEGORY)
    top4_share = category_share("保费", TOP4_10_CATEGORY)
    top10_share = category_share("保费", TOP10_CATEGORY)
    if top3_share is not None and top4_share is not None:
        top10_share = top3_share + top4_share
    profit_top3 = category_share("净利润", TOP3_CATEGORY)
    profit_top10 = category_share("净利润", TOP10_CATEGORY)
    premium_total = pd.to_numeric(data.categories["保费"], errors="coerce").sum() / divisor
    grouping_note = (
        t("前三大 / 前4-10 拆分", "Top 3 / Top 4–10 split")
        if top_grouping == "split"
        else t("前十大合并", "Top 10 merged")
    )
    concentration_text = (
        t(
            f"前三大 {top3_share:.0f}% ｜ 前4-10 {top4_share:.0f}%",
            f"Top 3 {top3_share:.0f}% | Top 4–10 {top4_share:.0f}%",
        )
        if top3_share is not None and top4_share is not None
        else t(
            f"前十大 {top10_share:.0f}%",
            f"Top 10 {top10_share:.0f}%",
        )
        if top10_share is not None
        else t("集中度数据不足", "Insufficient concentration data")
    )
    profit_text = (
        t(
            f"净利润：前三大 {profit_top3:.0f}%，前十大 {profit_top10:.0f}%",
            f"Net profit: Top 3 {profit_top3:.0f}%, Top 10 {profit_top10:.0f}%",
        )
        if profit_top3 is not None and profit_top10 is not None
        else t("净利润集中度数据不足", "Insufficient net profit concentration data")
    )
    render_insight_card(
        title=t("市场份额与集中度", "Market share and concentration"),
        headline=t(f"{data.year} 年行业签单保费", f"{data.year} industry signed premium"),
        value=f"{premium_total:,.0f} {unit_name(unit)}" if premium_total > 0 else "—",
        delta=concentration_text,
        delta_tone="neutral",
        bullets=[
            t("保费集中度：", "Premium concentration: ") + concentration_text,
            profit_text,
            t(
                f"样本覆盖 {data.company_count} 家公司（覆盖率 {data.coverage:.0%}）",
                f"Sample coverage: {data.company_count} companies ({data.coverage:.0%})",
            ),
        ],
        footnote=t(
            "口径：签单保费、净利润与承保利润；"
            "承保利润缺失时按“签单保费 ×（1 - 综合成本率）”测算。",
            "Basis: signed premium, net profit and underwriting profit. "
            "Where underwriting profit is missing, it is estimated as signed premium × (1 − combined ratio).",
        ),
    )

    st.markdown(
        t(
            f"<div class='s7-uw-year-title'>公司类别市场份额（{data.year} 年）</div>"
            f"<div class='s7-uw-year-sub'>保费 / 净利润 / 承保利润占比 · {grouping_note}</div>",
            f"<div class='s7-uw-year-title'>Company category market share ({data.year})</div>"
            f"<div class='s7-uw-year-sub'>Premium / net profit / underwriting profit share · {grouping_note}</div>",
        ),
        unsafe_allow_html=True,
    )
    category_colors = market_share_category_colors(
        data.categories["公司类别"].astype(str).tolist()
    )
    render_chart_legend(
        [
            (category_colors.get(category, "#9AA6B2"), category, "square")
            for category in data.categories["公司类别"].astype(str).tolist()
        ],
        align="left",
    )
    pie_chart = build_market_share_pies(
        data,
        unit_mode=unit_mode,
        showlegend=False,
    )
    st.plotly_chart(
        localize_plotly(pie_chart),
        width="stretch",
        key=f"{key_prefix}_market_share_pies",
    )
    st.caption(
        t(
            "饼图展示各指标正值占比，负值不计入占比。",
            "Pies show positive values only; negative values are excluded from shares.",
        )
    )

    st.markdown(
        t(
            f"<div class='s7-uw-year-title'>公司类别金额对比（{data.year} 年）</div>"
            f"<div class='s7-uw-year-sub'>按公司类别汇总（{unit}） · {grouping_note}</div>",
            f"<div class='s7-uw-year-title'>Company category amounts ({data.year})</div>"
            f"<div class='s7-uw-year-sub'>Aggregated by company category ({unit_name(unit)}) · {grouping_note}</div>",
        ),
        unsafe_allow_html=True,
    )
    bar_chart = build_market_share_amount_bars(data, unit_mode=unit_mode)
    st.plotly_chart(
        localize_plotly(bar_chart),
        width="stretch",
        key=f"{key_prefix}_market_share_bars",
    )
    st.caption(
        t(
            "利润面板以灰色零轴区分正值与负值。",
            "The profit panel uses a grey zero axis to separate positive and negative values.",
        )
    )
    with st.expander(t("查看分类与公司明细", "View category and company detail")):
        category_display = data.categories.copy()
        for column in ("保费", "净利润", "承保利润"):
            category_display[column] = (
                pd.to_numeric(category_display[column], errors="coerce") / divisor
            )
        category_display = category_display.rename(
            columns={
                "保费": f"保费（{unit}）",
                "净利润": f"净利润（{unit}）",
                "承保利润": f"承保利润（{unit}）",
                "保费占比": "保费占比",
                "净利润占比": "净利润占比",
                "承保利润占比": "承保利润占比",
            }
        )
        st.dataframe(
            translate_dataframe(category_display), width="stretch", hide_index=True
        )
        if data.company_detail["承保利润测算"].any():
            st.caption(
                t(
                    "承保利润 = 签单保费 ×（1 - 综合成本率）的测算值。",
                    "Underwriting profit is estimated as signed premium × (1 − combined ratio).",
                )
            )
        company_display = data.company_detail.copy()
        for column in ("保费", "净利润", "承保利润"):
            company_display[column] = (
                pd.to_numeric(company_display[column], errors="coerce") / divisor
            )
        st.dataframe(
            translate_dataframe(company_display), width="stretch", hide_index=True
        )



def render_plan_solvency_disclosure_overview(
    industry_frame,
    *,
    unit_mode,
    key_prefix,
    module_override,
):
    """行业概览：按合计口径展示偿付能力披露整体情况。"""
    overview = prepare_industry_solvency_overview(industry_frame)
    if overview is None:
        st.warning(
            t(
                "当前数据缺少可合计的实际资本（ACTUAL_CAPITAL）与最低资本"
                "（MINIMUM_CAPITAL），无法计算行业整体偿付能力充足率。",
                "The data does not contain summable actual capital (ACTUAL_CAPITAL) and minimum capital "
                "(MINIMUM_CAPITAL), so the industry solvency ratio cannot be calculated.",
            )
        )
        return

    unit = unit_mode if unit_mode in {"十亿元", "亿元", "百万元", "十万元"} else "亿元"
    divisor = {
        "十亿元": 100_000.0,
        "亿元": 10_000.0,
        "百万元": 100.0,
        "十万元": 10.0,
    }[unit]

    def amount_text(value) -> str:
        if value is None or pd.isna(value):
            return "—"
        return f"{float(value) / divisor:,.0f} {unit_name(unit)}"

    def ratio_text(value) -> str:
        if value is None or pd.isna(value):
            return "—"
        return f"{float(value):.1f}%"

    def period_delta(current, previous) -> tuple[str, str]:
        if current is None or previous is None:
            return "", "neutral"
        diff = float(current) - float(previous)
        tone = "up" if diff >= 0 else "down"
        return (
            t(
                f"较 {overview.previous_period} {diff:+.1f} pct",
                f"vs {overview.previous_period} {diff:+.1f} pct",
            ),
            tone,
        )

    combined_delta, combined_tone = period_delta(
        overview.combined_ratio,
        overview.previous_combined_ratio,
    )
    core_delta, core_tone = period_delta(
        overview.core_ratio,
        overview.previous_core_ratio,
    )

    metric_columns = st.columns(4)
    metric_columns[0].metric(
        t("综合偿付能力充足率", "Combined solvency ratio"),
        ratio_text(overview.combined_ratio),
        combined_delta or None,
        delta_color="normal" if combined_tone == "up" else "inverse",
        border=True,
        help=t(
            "Σ 所有纳入口径公司的实际资本 ÷ Σ 最低资本。",
            "Σ actual capital of all included companies ÷ Σ minimum capital.",
        ),
    )
    metric_columns[1].metric(
        t("核心偿付能力充足率", "Core solvency ratio"),
        ratio_text(overview.core_ratio),
        core_delta or None,
        delta_color="normal" if core_tone == "up" else "inverse",
        border=True,
        help=t(
            "Σ（核心一级资本 + 核心二级资本）÷ Σ 最低资本。",
            "Σ (core tier 1 + core tier 2 capital) ÷ Σ minimum capital.",
        ),
    )
    metric_columns[2].metric(
        t("实际资本合计", "Total actual capital"),
        amount_text(overview.actual_capital),
        border=True,
    )
    metric_columns[3].metric(
        t("最低资本合计", "Total minimum capital"),
        amount_text(overview.minimum_capital),
        border=True,
    )

    render_insight_card(
        title=t("偿付能力披露整体情况", "Solvency disclosure overview"),
        headline=t(f"{overview.period} 行业合计口径", f"{overview.period} industry aggregate basis"),
        value=(
            t(
                f"综合 {ratio_text(overview.combined_ratio)}"
                f" / 核心 {ratio_text(overview.core_ratio)}",
                f"Combined {ratio_text(overview.combined_ratio)}"
                f" / Core {ratio_text(overview.core_ratio)}",
            )
            if overview.combined_ratio is not None or overview.core_ratio is not None
            else "—"
        ),
        delta=t(
            f"覆盖 {overview.company_count} 家（{overview.coverage:.0%}）",
            f"Coverage: {overview.company_count} companies ({overview.coverage:.0%})",
        ),
        delta_tone="neutral",
        bullets=[
            t(
                f"综合 = Σ实际资本 {amount_text(overview.actual_capital)} ÷ Σ最低资本 {amount_text(overview.minimum_capital)}",
                f"Combined = Σ actual capital {amount_text(overview.actual_capital)} ÷ Σ minimum capital {amount_text(overview.minimum_capital)}",
            ),
            t(
                f"核心 = Σ核心资本 {amount_text(overview.core_capital)} ÷ Σ最低资本 {amount_text(overview.minimum_capital)}",
                f"Core = Σ core capital {amount_text(overview.core_capital)} ÷ Σ minimum capital {amount_text(overview.minimum_capital)}",
            ),
            t(
                (
                    f"核心资本占实际资本 {overview.core_share:.1f}%"
                    if overview.core_share is not None
                    else "核心资本占实际资本比例暂不可用"
                ),
                (
                    f"Core capital / actual capital {overview.core_share:.1f}%"
                    if overview.core_share is not None
                    else "Core capital / actual capital is not available"
                ),
            ),
        ],
        footnote=t(
            "合计口径仅纳入同时具备相应资本与正的最低资本的公司；"
            "卡片中的比率不是各公司充足率的简单平均。",
            "The aggregate basis includes only companies with the relevant capital and positive minimum capital; "
            "the ratios are not simple averages of company ratios.",
        ),
    )

    detail = overview.detail
    with st.expander(t("查看合计口径公司与公式明细", "View aggregate companies and formulas")):
        if isinstance(detail, pd.DataFrame) and not detail.empty:
            display = detail.copy()
            for column in ("实际资本", "最低资本", "核心资本"):
                if column in display.columns:
                    display[column] = (
                        pd.to_numeric(display[column], errors="coerce") / divisor
                    )
            display = display.rename(
                columns={
                    "实际资本": f"实际资本（{unit}）",
                    "最低资本": f"最低资本（{unit}）",
                    "核心资本": f"核心资本（{unit}）",
                }
            )
            for column in ("综合偿付能力充足率", "核心偿付能力充足率"):
                if column in display.columns:
                    display[column] = pd.to_numeric(
                        display[column], errors="coerce"
                    ).map(lambda value: "" if pd.isna(value) else f"{value:.1f}%")
            st.dataframe(
                translate_dataframe(display), width="stretch", hide_index=True
            )
        st.caption(
            t(
                "公式：综合偿付能力充足率 = Σ所有公司实际资本 / Σ所有公司最低资本；"
                "核心偿付能力充足率 = Σ（核心一级资本 + 核心二级资本）/ Σ所有公司最低资本。"
                "监管口径下，核心资本对应核心一级与核心二级资本之和。",
                "Formula: combined solvency ratio = Σ actual capital / Σ minimum capital; "
                "core solvency ratio = Σ (core tier 1 + core tier 2 capital) / Σ minimum capital. "
                "Under the regulatory basis, core capital equals core tier 1 plus core tier 2 capital.",
            )
        )
