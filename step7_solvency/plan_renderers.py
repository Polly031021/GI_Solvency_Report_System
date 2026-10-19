"""STEP7 各类组合图的具体渲染实现，由 plans.render_combination_analysis 调度。"""

from __future__ import annotations

from html import escape

import pandas as pd
import streamlit as st
from dashboard_components import render_key_solvency_overview, render_major_financing
from services.solvency_navigation import (
    KPMG_CAPITAL_COMBO_COLORS,
    MARKET_RISK_ASSET_SCATTER,
    metric_codes_for_chart,
)
from services.solvency_step7_charts import (
    CAPITAL_STRUCTURE_COLORS,
    build_capability_radar_chart,
    build_company_line_trend_chart,
    build_industry_premium_structure_chart,
    build_structure_change_chart,
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
)
from .fragments import (
    render_capital_efficiency_bubble_fragment,
    render_risk_ratio_scatter_fragment,
)
from .rendering import (
    chart_without_internal_title,
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

from .plan_inputs import capability_scores


def render_plan_structure_change_chart(chart_name, frame):
    companies = ordered_companies(frame)
    if not companies:
        st.warning("当前没有公司数据。")
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
                    st.altair_chart(chart, use_container_width=True)
                except Exception as e:
                    # ★ 详细报错 + 打印堆栈，便于排查
                    import traceback

                    st.error(f"❌ {company} 图表渲染失败：{e}")
                    with st.expander("查看详细错误", expanded=False):
                        st.code(traceback.format_exc())


def render_plan_performance_pattern_grid(frame, key_prefix, module_override):
    companies = ordered_companies(frame)
    if not companies:
        st.warning("当前没有公司数据。")
        return

    cfg = module_override.chart_config if module_override else {}
    revenue_code = str(cfg.get("revenue_code", "SIGNED_PREMIUM")).strip()
    profit_code = str(cfg.get("profit_code", "NET_PROFIT")).strip()
    pattern_overrides: dict = cfg.get("pattern_overrides") or {}

    periods_list = sorted(frame["报告期"].dropna().astype(str).unique().tolist())

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
        st.warning("无公司数据。")
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
            f"缺少 {revenue_code} 或 {profit_code} 数据，"
            "无法计算排名。请检查 STEP5/STEP6 集成数据。"
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
        st.warning("没有公司能被归类到任何业绩模式。")
        return

    # ---------- 4. 展示说明 ----------
    st.caption(
        f"横轴为「{revenue_code}」在样本内的排名，纵轴为「{profit_code}」排名；"
        "反向坐标（第 1 名在右上角）。"
        "每家小图的坐标轴按该公司自身排名范围自适应缩放，便于看出排名波动；"
        "刻度数字仍为真实名次。公司按利润排名年度轨迹归类为："
        "逐年提升 / 稳健经营 / V形反转 / 大起大落 / 持续恶化。"
    )
    # ★ 新增：数据诊断展开区，帮用户排查"某年缺失"的具体原因
    with st.expander("🔍 数据诊断：查看每家公司的收入/利润数据覆盖情况", expanded=False):
        st.caption(
            f"下表显示每家公司每个报告期是否有「{revenue_code}」和「{profit_code}」数据。"
            "❌ 表示该期该指标缺失（未披露或未集成）。"
            "若发现大量 ❌，请到 STEP5/STEP6 检查原始数据是否完整。"
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
                        f"有{revenue_code}": "✅" if _has_revenue else "❌",
                        f"有{profit_code}": "✅" if _has_profit else "❌",
                    }
                )
        st.dataframe(
            pd.DataFrame(_diag_rows),
            width="stretch",
            hide_index=True,
        )

    # ---------- 5. 分列并排渲染 ----------
    _pattern_cols = st.columns(len(active_patterns), gap="small")
    for _col, _pattern in zip(_pattern_cols, active_patterns):
        with _col:
            # 列标题（模仿参考图的橙红色横幅）
            st.markdown(
                f"<div style='background:#D94330; color:#FFFFFF; "
                f"text-align:center; padding:6px 0; border-radius:3px; "
                f"font-weight:700; font-size:13px; margin-bottom:10px;'>"
                f"{escape(_pattern)}</div>",
                unsafe_allow_html=True,
            )
            # 该列下的公司，纵向堆叠
            for _company in grouped[_pattern]:
                # ★ 新增：在公司图上方显示公司名（红色小标题）
                st.markdown(
                    f"<div style='text-align:center; "
                    f"font-size:11px; font-weight:700; color:#C8102E; "
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
                    )
                    st.altair_chart(
                        _chart,  # ★ 改动：不再清空 title
                        width="content",
                        key=f"{key_prefix}_pattern_{_pattern}_{_company}",
                    )
                except ValueError as _exc:
                    st.warning(f"{_company}：{_exc}")


def render_plan_key_metrics_table(frame, highlight_company):
    render_key_solvency_overview(frame, highlight_company=highlight_company)


def render_plan_financing_table(financing_data):
    render_major_financing(financing_data)


def render_plan_pending_definition(chart_name):
    st.warning(f"“{chart_name}”在新版 Excel 中标记为“待定”，目前没有定义可绘图的明细组成指标。")


def render_plan_solvency_ratio_combo(
    company_panel_count, converted, dense_company_panels, highlight_company, key_prefix, periods
):
    shared_y_domain = solvency_ratio_axis_domain(converted, periods)
    period_colors = report_period_color_map(periods)
    st.caption(
        "图表说明：柱状图为综合偿付能力充足率；粉色折线为核心偿付能力充足率。"
        "各公司统一使用同一百分比纵轴范围，柱形颜色对应不同报告期。"
    )
    render_chart_legend(
        [
            *[(period_colors[period], period, "square") for period in periods],
            ("#FD349C", "核心偿付能力充足率（%）", "line"),
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
    if chart_name == "综合充足率变化":
        amount_groups = cfg.get("amount_groups") or {
            "实际资本": ("ACTUAL_CAPITAL",),
            "最低资本": ("MINIMUM_CAPITAL",),
        }
        ratio_code = "COMBINED_SOLVENCY_RATIO"
        ratio_label = "综合偿付能力充足率（%）"
    else:
        amount_groups = cfg.get("amount_groups") or {
            "核心资本": ("CORE_T1_CAPITAL", "CORE_T2_CAPITAL"),
            "最低资本": ("MINIMUM_CAPITAL",),
        }
        ratio_code = "CORE_SOLVENCY_RATIO"
        ratio_label = "核心偿付能力充足率（%）"
    grouped_amount_groups = cfg.get("grouped_amount_groups") or {
        "实际资本": ("ACTUAL_CAPITAL",),
        "最低资本": ("MINIMUM_CAPITAL",),
    }
    shared_amount_domain = capital_ratio_amount_axis_domain(
        converted,
        amount_groups,
        periods,
    )
    grouped_amount_domain = capital_ratio_amount_axis_domain(
        converted,
        grouped_amount_groups,
        periods,
        stacked=False,
    )
    st.caption(
        "上方 KPMG Pink 折线展示对应充足率，下方堆叠柱展示资本规模；"
        "同组所有公司统一使用同一金额纵轴范围，便于直接比较资本规模。"
    )
    st.markdown("**堆叠柱组合图**")
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
            periods,
            chart_name,
            ratio_code=ratio_code,
            ratio_label=ratio_label,
            shared_amount_domain=shared_amount_domain,
            dense_layout=dense_company_panels,
            panel_count=company_panel_count,
        ),
        key_prefix=f"{key_prefix}_{ratio_code}_combo",
        highlight_company=highlight_company,
    )
    st.markdown("**并列柱组合图**")
    st.caption("新增视图：每个报告期的两项资本各自成柱，" "上方仍保留对应充足率折线。")
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
            periods,
            chart_name,
            ratio_code=ratio_code,
            ratio_label=ratio_label,
            shared_amount_domain=grouped_amount_domain,
            dense_layout=dense_company_panels,
            panel_count=company_panel_count,
            bar_layout="grouped",
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
        "上方 KPMG Pink 折线展示核心资本占实际资本的比例，下方堆叠柱展示核心资本与附属资本；"
        "同组所有公司统一使用同一金额纵轴范围，便于直接比较资本规模。"
    )
    render_chart_legend(
        [
            (KPMG_CAPITAL_COMBO_COLORS[0], "核心资本", "square"),
            (KPMG_CAPITAL_COMBO_COLORS[1], "附属资本", "square"),
            ("#FD349C", "核心资本占实际资本的比例", "line"),
        ]
    )
    render_company_chart_grid(
        converted,
        lambda company_frame: build_capital_ratio_combo_chart(
            company_frame,
            capital_structure_groups,
            periods,
            "核心资本与附属资本结构",
            ratio_label="核心资本占实际资本的比例（%）",
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
        st.warning(f"“{chart_name}”尚未配置组成指标。")
        return
    if chart_name == "计入各级资本的保单未来盈余构成占比":
        st.caption(
            "各色块表示计入相应资本层级的保单未来盈余占该公司已披露保单未来盈余明细合计的比例；"
            "100%表示该公司披露的保单未来盈余全部计入了同一个资本层级，其他资本层级没有披露这个明细。"
        )
    elif chart_name == "量化风险最低资本构成":
        st.caption(
            "柱高按各构成项目/量化风险最低资本的比例展示，实际金额仅保留在悬浮信息中；"
            "数据标签与对应比例折线图一致，负向抵减项目位于零线下方。"
        )
    elif chart_name == "各类市场风险占比":
        st.caption(
            "柱高按各构成项目/市场风险最低资本的比例展示，实际金额仅保留在悬浮信息中；"
            "负向风险分散效应位于零线下方。"
        )
    elif chart_name == "各类信用风险占比":
        st.caption(
            "柱高按各构成项目/信用风险最低资本的比例展示，实际金额仅保留在悬浮信息中；"
            "负向风险分散效应位于零线下方。"
        )
    else:
        st.caption("按公司分面展示构成；负向抵减项目位于零线下方，所有颜色均来自 KPMG 色卡。")
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
    render_chart_legend([(color, label, "square") for _, label, color in specs])
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
        "100%堆叠柱显示核心一级、核心二级、附属一级和附属二级资本构成；"
        "所有公司和报告期的柱高一致，色块高度代表结构占比，悬浮可查看占比和金额。"
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
        capital_legend.append(("#B8BDC7", "未披露", "square"))
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
        st.caption("按公司展示抵减效应的跨期方向与幅度，零线用于区分正向增加和负向抵减。")
        st.altair_chart(
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
        "偿付能力矩阵",
        highlight_company,
        regulatory_lines=True,
        company_colors=company_colors,
    )
    st.altair_chart(
        chart_without_internal_title(chart),
        width="stretch",
        key=f"{key_prefix}_solvency_matrix",
    )
    st.caption("横轴50%、纵轴100%的红色虚线分别为核心和综合偿付能力充足率监管下限。")


def render_plan_market_credit_matrix(
    company_colors, converted, highlight_company, key_prefix, periods
):
    chart, _ = build_matrix_chart(
        converted,
        "MARKET_RISK_TO_QUANT_CAPITAL",
        "CREDIT_RISK_TO_QUANT_CAPITAL",
        periods,
        "市场—信用风险矩阵",
        highlight_company,
        company_colors=company_colors,
    )
    st.altair_chart(
        chart_without_internal_title(chart),
        width="stretch",
        key=f"{key_prefix}_market_credit_matrix",
    )
    st.caption("深蓝虚线为样本中位数，用于识别市场风险和信用风险同时偏高的公司。")


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
            f"展示 {latest_period}；每个气泡代表一家公司，横纵轴均为占{denominator_label}的比例，"
            f"气泡面积代表{denominator_label}规模；"
            "深蓝虚线为样本横纵指标中位数。"
        )
        with st.container(horizontal_alignment="center"):
            st.altair_chart(
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
    axes_config = cfg.get("axes") or DEFAULT_CAPABILITY_AXES
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
            "能力五边形当前无法打分，诊断如下：\n"
            f"- 筛选后总记录数：{len(chart_frame)}；\n"
            f"- 最新报告期「{latest_period or '（无）'}」在筛选后仅剩 {latest_n_rows} 行、"
            f"{latest_n_companies} 家公司；\n"
            f"- 要求的指标编码：{'、'.join(required_codes)}；\n"
            f"- 数据中缺失的编码：{'、'.join(missing_codes) if missing_codes else '无（5 个编码都在）'}；\n"
            f"- 数据中实际存在的编码：{'、'.join(present_codes) if present_codes else '无'}。\n"
            "常见原因：89 家数据的「指标编码」列与标准编码不一致（如用了中文名），"
            "或最新报告期这一期缺少上述 5 个指标。"
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
        f"展示 {latest_period}；每家公司单独一张能力五边形，"
        "按“同业分类”分组排列。"
        "顶点依次为：" + "、".join(axes_labels) + "。"
        "打分在各分组内做百分位排名（前三大 3 家互比、前4-10 7 家互比），"
        "50 分 ≈ 本组中位数水平（灰色虚线五边形）。"
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
            f"{escape(str(category))}"
            f"<span style='color:#7A8496;font-size:12px;font-weight:400;margin-left:8px;'>"
            f"（{len(cat_scores)} 家）</span></div>",
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

                    st.altair_chart(
                        chart_without_internal_title(chart),
                        width="content",
                        key=f"{key_prefix}_radar_{category}_{company}",
                    )

    # ---------- 明细展开 ----------
    with st.expander("查看原始数值与得分明细"):
        st.dataframe(
            raw_all.style.format(
                {col: "{:,.2f}" for col in raw_all.columns if col != "公司"},
                na_rep="—",
            ),
            width="stretch",
            hide_index=True,
        )
        st.dataframe(
            score_all.drop(columns=["_分类"]).style.format(
                {col: "{:.0f}" for col in score_all.columns if col not in {"公司", "_分类"}},
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
        st.warning("当前筛选范围没有可绘制的数据。")
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
        "每家公司一个年度小折线图，纵轴为 %；蓝色折线为该公司综合偿付能力充足率走势，"
        "红色虚线为监管下限 100%；仅标注首末年数值，悬停查看各年明细。"
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
            f"{group_name}（{len(companies)} 家）</div>",
            unsafe_allow_html=True,
        )
        try:
            chart = build_company_line_trend_chart(
                sub, code, periods, highlight_company or ""
            )
        except ValueError as exc:
            st.warning(f"{group_name} 无法绘制：{exc}")
            continue
        st.altair_chart(
            chart_without_internal_title(chart),
            width="stretch",
            key=f"{key_prefix}_line_trend_{code}_{group_name}",
        )


def render_plan_underwriting_efficiency_scatter(frame, key_prefix, module_override):
    render_underwriting_efficiency_scatter(
        frame,  # ← 直接把当前页面的 frame 传进去
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
    """Render the five-year industry premium overview with an all-company scope."""
    cfg = module_override.chart_config if module_override else {}
    year_window = int(cfg.get("years", 5) or 5)
    annual_scope = str(cfg.get("annual_scope", "") or "").strip()
    annual, detail, companies = prepare_industry_premium_annual(
        industry_frame,
        years=year_window,
        annual_scope=annual_scope,
    )
    if annual.empty:
        st.warning("当前数据没有可用的行业年度累计保费记录。")
        return

    complete = annual[annual["完整"].astype(bool)].copy()
    available = annual[
        annual["合计"].notna()
        | annual["车险"].notna()
        | annual["非车险"].notna()
    ].copy()
    if available.empty:
        st.warning("当前数据没有可用的行业年度保费记录。")
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

    st.caption("指标：签单保费")
    metric_columns = st.columns(4)
    latest_year = str(latest.get("年度", "") or "")
    metric_columns[0].metric(
        (
            f"最新完整年度合计（{latest_year}）"
            if latest_year and latest_is_complete
            else f"最新年度合计（{latest_year}）"
        ),
        "—" if latest_total is None else f"{latest_total:{value_format}} {unit}",
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
        "同比增速"
        if is_year_over_year and latest_is_complete
        else "较上一有数年度"
    )
    latest_total_raw = latest.get("合计")
    if (
        previous_total is not None
        and pd.notna(previous_total)
        and float(previous_total) != 0
        and latest_total_raw is not None
        and pd.notna(latest_total_raw)
    ):
        growth = (float(latest_total_raw) / float(previous_total) - 1) * 100
        metric_columns[1].metric(
            growth_label,
            f"{growth:+.1f}%",
            border=True,
        )
    else:
        metric_columns[1].metric(growth_label, "—", border=True)
    if len(complete) >= 2 and float(complete.iloc[0]["合计"] or 0) > 0:
        cagr = (
            (float(latest["合计"]) / float(complete.iloc[0]["合计"]))
            ** (1 / (len(complete) - 1))
            - 1
        ) * 100
        metric_columns[2].metric(
            f"{len(complete)}年复合增速",
            f"{cagr:+.1f}%",
            border=True,
        )
    else:
        metric_columns[2].metric("复合增速", "—", border=True)
    metric_columns[3].metric(
        "车险 / 非车险占比",
        "—"
        if car_share is None or non_car_share is None
        else f"{car_share:.0f}% / {non_car_share:.0f}%",
        border=True,
    )

    legend_items = [
        ("#1E49E2", "车险（柱）", "square"),
        ("#00B8F5", "非车险（柱）", "square"),
        ("#7213EA", "车险（趋势线）", "line"),
        ("#FD349C", "非车险（趋势线）", "line"),
        ("#00338D", "合计（趋势线）", "line"),
    ]
    render_chart_legend(legend_items)
    try:
        chart = build_industry_premium_structure_chart(annual, unit_mode=unit)
        st.altair_chart(
            chart,
            width="stretch",
            key=f"{key_prefix}_industry_premium_structure",
        )
    except ValueError as exc:
        st.warning(str(exc))
        return

    incomplete = annual[~annual["完整"].astype(bool)]
    anomaly_count = int(pd.to_numeric(annual["异常数"], errors="coerce").fillna(0).sum())

    with st.expander("查看行业年度明细"):
        if not incomplete.empty:
            company_count = max(len(companies), 1)
            missing_labels = []
            for row in incomplete.itertuples(index=False):
                if int(row.公司数) == 0:
                    reason = "缺少年度累计"
                elif int(row.公司数) < company_count * 0.8:
                    reason = "年度累计覆盖不足"
                elif int(row.车险公司数) < company_count * 0.8:
                    reason = "车险拆分覆盖不足"
                else:
                    reason = "数据不完整"
                missing_labels.append(f"{row.年度}（{reason}）")
            st.caption("数据不完整的年度：" + "、".join(missing_labels))
        if anomaly_count:
            st.caption(
                f"检测到 {anomaly_count} 条公司年度值疑似单位/量级异常，"
                "图表未自动改写原始数值；请核对明细。"
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
        st.dataframe(display, width="stretch", hide_index=True)
