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
    BREAKEVEN_COLOR,
    CAPITAL_AMOUNT_CODES,
    CAPITAL_STRUCTURE_COLORS,
    COMPONENT_LABELS,
    EXPENSE_COLOR,
    HISTORICAL_SOURCE,
    INDUSTRY_PREMIUM_WINDOWS,
    LOSS_COLOR,
    UPLOAD_SOURCE,
    annual_period_order,
    build_loss_expense_panel_chart,
    build_capability_radar_grid,
    build_company_line_trend_chart,
    build_industry_premium_mix_chart,
    build_industry_premium_total_chart,
    build_market_share_amount_bars,
    build_market_share_pies,
    market_share_category_colors,
    build_structure_change_chart,
    build_premium_growth_structure_chart,
    group_premium_growth_panels,
    group_loss_expense_panels,
    loss_expense_axis_domain,
    prepare_premium_growth_panel,
    prepare_loss_expense_panel,
    premium_growth_axis_domain,
    build_capital_amount_combo,
    build_capital_ratio_combo_chart,
    capital_structure_industry_ratios,
    quant_risk_industry_sentence,
    insurance_risk_industry_sentence,
    insurance_risk_composition_metrics,
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
    render_chart_summary,
    render_company_chart_grid,
)
from .selection import ordered_companies
from .summaries import (
    capital_amount_summary,
    capital_structure_summary,
    loss_expense_summary,
    premium_growth_summary,
    solvency_ratio_summary,
)
from .specs import (
    COMPONENT_STACK_DENOMINATOR_CODES,
    COMPONENT_STACK_SPECS,
    QUANT_RISK_STACK_RATIO_CODES,
    RISK_RATIO_SCATTER_SPECS,
    RISK_SCATTER_DISPLAY_WIDTH,
    RISK_SCATTER_DUAL_VIEW_CHARTS,
    DEFAULT_CAPABILITY_AXES,
    CAPABILITY_PERCENTILE_FLOOR,
    CAPABILITY_INDUSTRY_BENCHMARK_GROUPS,
    CAPABILITY_GROUP_SCORE_FLOORS,
)
from .underwriting import render_underwriting_efficiency_scatter
from .insight_cards import render_insight_card

from .plan_inputs import capability_scores


@st.cache_data(show_spinner=False, max_entries=128)
def _cached_capability_grid(
    score_rows: tuple[tuple[object, ...], ...],
    axis_labels: tuple[str, ...],
    colors: tuple[tuple[str, str], ...],
    titles: tuple[tuple[str, str], ...],
    reference: tuple[float, ...],
    columns: int,
    panel_size: int,
) -> object:
    """Cache the Altair spec so widget reruns do not rebuild every pentagon."""
    labels = list(axis_labels)
    frame = pd.DataFrame(
        score_rows,
        columns=["公司", *[f"{label}_得分" for label in labels]],
    )
    return build_capability_radar_grid(
        frame,
        labels,
        dict(colors),
        facet_titles=dict(titles),
        reference_scores=list(reference) if reference else None,
        columns=columns,
        panel_width=panel_size,
        panel_height=panel_size,
        label_font_size=9,
    )


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
    columns_per_row = 4

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
        # 只展示最近三年；保留配置的上限，但不允许超过三年。
        years = max(1, min(int(cfg.get("years", 3)), 3))
    except (TypeError, ValueError):
        years = 3
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
    render_chart_summary(
        premium_growth_summary(panels, unit_label=unit_mode, frame=converted)
    )
    st.caption(
        t(
            f"公司按“{group_column}”分组，每家公司平铺展示最近 {years} 个年度："
            "左轴为车险/非车险的 100% 结构占比，"
            "右轴折线为总保费同比增速；"
            "非车险按“总签单保费 - 车险”计算。"
            "柱内标注结构占比，折线在每个年度均标注同比增速。",
            f"Companies grouped by “{category_name(group_column)}”; each company shows the latest "
            f"{years} years side by side. The left axis is the 100% motor/non-motor mix; the "
            "right-axis line is total premium growth. Non-motor premium is total signed premium less motor. "
            "Mix labels appear inside the bars, and the growth line is labelled for every year.",
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

    columns_per_row = 4
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
            cols = st.columns(columns_per_row, gap="small")
            for slot, panel in enumerate(batch):
                with cols[slot]:
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


def render_plan_loss_expense_combo(
    chart_name,
    frame,
    *,
    module_override,
):
    """Render three-year loss/expense stacks for every selected company."""
    companies = ordered_companies(frame)
    if not companies:
        st.warning(t("当前没有公司数据。", "No company data."))
        return
    cfg = module_override.chart_config if module_override else {}
    try:
        # 只展示最近三年；保留配置的上限，但不允许超过三年。
        years = max(1, min(int(cfg.get("years", 3) or 3), 3))
    except (TypeError, ValueError):
        years = 3
    group_column = str(cfg.get("group_column", "同业分类")).strip() or "同业分类"
    panels = [
        prepare_loss_expense_panel(
            frame,
            company,
            years=years,
            group_column=group_column,
        )
        for company in companies
    ]
    panels = [panel for panel in panels if not panel.bar_frame.empty]
    if not panels:
        st.warning(
            t(
                "当前没有同时具备赔付率和费用率的年度数据。",
                "No annual data with both loss ratio and expense ratio is available.",
            )
        )
        return

    y_domain = loss_expense_axis_domain(panels)
    render_chart_summary(loss_expense_summary(panels, frame=frame))
    st.caption(
        t(
            f"每家公司平铺展示最近 {years} 个年度：下方为赔付率，上方为费用率，"
            "柱高合计为综合成本率；红色虚线为 100% 盈亏平衡线。"
            "柱顶数字为综合成本率，*表示按赔付率加费用率测算的值。",
            f"Each company displays the latest {years} years side by side. Loss ratio is below "
            "and expense ratio above; their combined height is the combined ratio. The red "
            "dashed line marks 100% break-even. Values above the bars show the combined ratio, "
            "with * indicating a calculated loss-plus-expense ratio.",
        )
    )
    render_chart_legend(
        [
            (LOSS_COLOR, t("赔付率", "Loss ratio"), "square"),
            (EXPENSE_COLOR, t("费用率", "Expense ratio"), "square"),
            (BREAKEVEN_COLOR, t("100% 盈亏平衡线", "100% break-even line"), "dash"),
        ]
    )

    columns_per_row = 4
    for group_index, (group_name, group_panels) in enumerate(
        group_loss_expense_panels(panels)
    ):
        st.markdown(
            f"<div style='margin:18px 0 8px;font-size:14px;font-weight:700;"
            f"color:#00338D;border-left:4px solid #00338D;padding-left:8px;'>"
            f"{escape(category_name(group_name))}"
            f"{t('（', ' (')}{len(group_panels)}{t(' 家）', ' companies)')}</div>",
            unsafe_allow_html=True,
        )
        for index in range(0, len(group_panels), columns_per_row):
            batch = group_panels[index : index + columns_per_row]
            cols = st.columns(columns_per_row, gap="small")
            for slot, panel in enumerate(batch):
                with cols[slot]:
                    try:
                        chart = build_loss_expense_panel_chart(
                            panel,
                            y_domain=y_domain,
                        )
                        render_altair_chart(
                            chart,
                            width="stretch",
                            key=(
                                f"{chart_name}_{group_index}_{index}_"
                                f"{panel.company}"
                            ),
                        )
                    except Exception as exc:
                        st.error(
                            t(
                                f"❌ {panel.company} 赔付率/费用率图渲染失败：{exc}",
                                f"❌ {panel.company} loss/expense chart failed: {exc}",
                            )
                        )


def render_plan_performance_pattern_grid(
    frame,
    key_prefix,
    module_override,
    rank_frame=None,
):
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

    # 排名池使用全部公司（不受公司/类型筛选影响），展示仍按当前选择；
    # 否则选中一家公司时排名会变成“1/1”，与报告中其他口径不一致。
    _rank_source = frame
    if isinstance(rank_frame, pd.DataFrame) and not rank_frame.empty:
        _rank_source = rank_frame.copy()
        _rank_source["数值"] = pd.to_numeric(_rank_source["数值"], errors="coerce")
        _rank_source["报告期"] = _rank_source["报告期"].astype(str)
        _rank_source["公司"] = (
            _rank_source["公司"].fillna("").astype(str).str.strip()
        )
        _rank_source = _rank_source[
            _rank_source["公司"].ne("") & _rank_source["公司"].ne("nan")
        ]

    all_companies = list(
        dict.fromkeys(c for c in _df["公司"].dropna().astype(str) if c and c != "nan")
    )
    rank_companies = list(
        dict.fromkeys(
            c for c in _rank_source["公司"].dropna().astype(str) if c and c != "nan"
        )
    )
    n_companies = len(all_companies)
    n_rank_companies = len(rank_companies)
    if n_companies == 0:
        st.warning(t("无公司数据。", "No company data."))
        return

    def _annual_rank_metric(period_df: pd.DataFrame, code: str) -> pd.DataFrame:
        """Pick one annual-cumulative value per company for ranking.

        The company-performance chart compares Q4 snapshots, so rankings must
        use 本年累计数 rather than a single-quarter record.  Using 本季度数 here
        would mix quarterly profit with annual profit and produce ranks that
        contradict the summary (for example, 众安 2022 net profit ranks 16th
        on a full-year basis but looked like 2nd on a quarterly basis).
        """
        rows = period_df[
            period_df["指标编码"].astype(str).eq(code)
        ][["公司", "数值", "期间口径"]].dropna(subset=["数值"])
        if "期间口径" in rows.columns:
            annual = rows[
                rows["期间口径"]
                .fillna("")
                .astype(str)
                .str.contains("本年累计|年度累计|全年累计", regex=True, na=False)
            ]
            if not annual.empty:
                rows = annual
        return rows.drop_duplicates("公司", keep="last")[["公司", "数值"]]

    rank_rows: list[dict] = []
    for period in periods_list:
        period_df = _rank_source[_rank_source["报告期"].eq(period)]
        revenue = _annual_rank_metric(period_df, revenue_code)
        profit = _annual_rank_metric(period_df, profit_code)
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
    period_counts = rank_df.groupby("报告期")["公司"].nunique()
    period_sample_span = (
        f"{int(period_counts.min())}–{int(period_counts.max())}"
        if not period_counts.empty
        else "—"
    )

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

    # ---------- 4. 模块总结（与行业概览一致的一张解读卡） ----------
    latest_period = periods_list[-1] if periods_list else ""
    latest_rank = rank_df[rank_df["报告期"].astype(str).eq(str(latest_period))]
    latest_sample_n = (
        int(latest_rank["公司"].nunique())
        if not latest_rank.empty
        else n_rank_companies
    )
    period_span = (
        f"{periods_list[0]}–{periods_list[-1]}"
        if len(periods_list) > 1
        else (periods_list[0] if periods_list else "—")
    )

    def _top_names(column: str, limit: int = 3) -> str:
        rows = latest_rank.dropna(subset=[column]).sort_values(column).head(limit)
        if rows.empty:
            return t("暂无", "n/a")
        names = [
            f"{str(row['公司'])}{t('（第', ' (#')}{int(row[column])}{t('名）', ')')}"
            for _, row in rows.iterrows()
        ]
        return t("、", ", ").join(names)

    pattern_counts = [
        f"{category_name(pattern)} {len(grouped[pattern])}{t(' 家', '')}"
        for pattern in active_patterns
        if grouped.get(pattern)
    ]
    pattern_summary = " / ".join(pattern_counts) if pattern_counts else t("暂无", "n/a")

    track_changes: list[tuple[str, int, int, int]] = []
    for company in all_companies:
        series = (
            rank_df[rank_df["公司"].astype(str).eq(company)]
            .sort_values("报告期")["利润排名"]
            .dropna()
        )
        if len(series) < 2:
            continue
        first_rank, last_rank = int(series.iloc[0]), int(series.iloc[-1])
        track_changes.append((company, first_rank, last_rank, first_rank - last_rank))

    bullets: list[str] = [
        t(
            (
                f"排名池为全部 {n_rank_companies} 家公司，当前展示 {n_companies} 家 · "
                f"{period_span}（{len(periods_list)} 个报告期）；"
                f"各期有效样本 {period_sample_span} 家"
            )
            if n_companies != n_rank_companies
            else (
                f"覆盖全部 {n_companies} 家公司 · {period_span}"
                f"（{len(periods_list)} 个报告期）；各期有效样本 {period_sample_span} 家"
            ),
            (
                f"Ranked against all {n_rank_companies} companies; showing {n_companies} · "
                f"{period_span} ({len(periods_list)} periods); "
                f"effective sample per period {period_sample_span}"
            )
            if n_companies != n_rank_companies
            else (
                f"All {n_companies} companies · {period_span} "
                f"({len(periods_list)} periods); effective sample per period {period_sample_span}"
            ),
        ),
        t("业绩模式分布：" + pattern_summary, "Pattern mix: " + pattern_summary),
    ]
    profit_top_text = _top_names("利润排名")
    revenue_top_text = _top_names("收入排名")
    if profit_top_text != t("暂无", "n/a"):
        bullets.append(
            t(
                "最新一期利润前三：" + profit_top_text,
                "Latest top 3 by profit: " + profit_top_text,
            )
        )
    if revenue_top_text != t("暂无", "n/a"):
        bullets.append(
            t(
                "最新一期收入前三：" + revenue_top_text,
                "Latest top 3 by revenue: " + revenue_top_text,
            )
        )
    if track_changes:
        best = max(track_changes, key=lambda item: item[3])
        worst = min(track_changes, key=lambda item: item[3])
        move_parts: list[str] = []
        if best[3] > 0:
            move_parts.append(
                t(
                    f"改善最多 {best[0]}（利润第 {best[1]} → {best[2]} 名）",
                    f"Biggest gain {best[0]} (profit rank {best[1]} → {best[2]})",
                )
            )
        if worst[3] < 0:
            move_parts.append(
                t(
                    f"回落最多 {worst[0]}（利润第 {worst[1]} → {worst[2]} 名）",
                    f"Biggest drop {worst[0]} (profit rank {worst[1]} → {worst[2]})",
                )
            )
        if move_parts:
            bullets.append("；".join(move_parts) if not is_english() else "; ".join(move_parts))

    render_insight_card(
        title=t("公司业绩展示", "Company performance"),
        eyebrow=t("经营表现", "Operating performance"),
        headline=t(
            f"{latest_period} 收入—利润排名轨迹",
            f"{latest_period} revenue–profit rank trajectory",
        ),
        value=t(
            f"{n_companies} 家展示 / {n_rank_companies} 家排名",
            f"{n_companies} shown / {n_rank_companies} ranked",
        ),
        delta=t(f"{len(active_patterns)} 类模式", f"{len(active_patterns)} patterns"),
        delta_tone="neutral",
        bullets=bullets,
        footnote=t(
            f"排名按{revenue_label}、{profit_label}的全年累计数在全部公司内降序计算，第 1 名位于右上角；"
            "每张小图坐标轴按该公司自身排名区间缩放，刻度仍为真实名次。",
            f"Ranks use full-year cumulative {revenue_label} and {profit_label} across all companies; rank 1 is top-right. "
            "Each panel is scaled to the company’s own rank range, while ticks remain actual ranks.",
        ),
    )

    st.caption(
        t(
            f"横轴为「{revenue_rank_title}」，纵轴为「{profit_rank_title}」；"
            "数值越小表示排名越靠前。"
            "公司按利润排名年度轨迹归类为：逐年提升 / 稳健经营 / V形反转 / 大起大落 / 持续恶化。",
            f"The x-axis is “{revenue_rank_title}” and the y-axis is “{profit_rank_title}”. "
            "Lower values indicate a better rank. Companies are grouped by their annual "
            "profit-rank trajectory.",
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
    try:
        panel_width = int(cfg.get("panel_width", 235) or 235)
    except (TypeError, ValueError):
        panel_width = 235
    try:
        panel_height = int(cfg.get("panel_height", 200) or 200)
    except (TypeError, ValueError):
        panel_height = 200
    panel_width = max(180, min(320, panel_width))
    panel_height = max(170, min(280, panel_height))

    pattern_hints = {
        "逐年提升": t("利润排名持续改善", "Profit rank improves consistently"),
        "稳健经营": t("排名波动幅度较小", "Rank fluctuates within a narrow band"),
        "V形反转": t("先降后升，触底反弹", "Falls then recovers"),
        "倒V形反转": t("先升后降，冲高回落", "Rises then gives back"),
        "大起大落": t("方向多次反转，波动较大", "Multiple reversals and wide swings"),
        "持续恶化": t("利润排名连续下滑", "Profit rank deteriorates consistently"),
        "其他": t("样本期数不足 3 期", "Fewer than three periods"),
    }

    for _pattern_index, _pattern in enumerate(active_patterns):
        _companies = grouped[_pattern]
        # 模式标题横幅（整行）
        _hint = pattern_hints.get(_pattern, "")
        _hint_html = (
            f"<span class='s7-pattern-group__hint'>{escape(_hint)}</span>"
            if _hint
            else ""
        )
        st.markdown(
            f"<div class='s7-pattern-group'>"
            f"<span class='s7-pattern-group__name'>{escape(category_name(_pattern))}</span>"
            f"<span class='s7-pattern-group__count'>"
            f"{len(_companies)}{t(' 家', '')}</span>"
            f"{_hint_html}"
            f"</div>",
            unsafe_allow_html=True,
        )
        # 该组公司按 grid_cols 横向分批，每批一行
        for _start in range(0, len(_companies), grid_cols):
            _batch = _companies[_start : _start + grid_cols]
            _cols = st.columns(grid_cols, gap="small")
            for _slot, _company in enumerate(_batch):
                with _cols[_slot]:
                    _company_rank_df = rank_df[
                        rank_df["公司"].astype(str).eq(_company)
                    ].sort_values("报告期")
                    _latest_company_rank = _company_rank_df.tail(1)
                    _pill_html: list[str] = []
                    if not _latest_company_rank.empty:
                        _latest_row = _latest_company_rank.iloc[0]
                        for _label, _column in (
                            (revenue_label, "收入排名"),
                            (profit_label, "利润排名"),
                        ):
                            _rank_value = _latest_row.get(_column)
                            if pd.notna(_rank_value):
                                _pill_html.append(
                                    f"<span class='s7-pattern-pill'>"
                                    f"{escape(str(_label))} #{int(_rank_value)}</span>"
                                )
                    _period_count = int(_company_rank_df["报告期"].nunique())
                    if _period_count:
                        _pill_html.append(
                            f"<span class='s7-pattern-pill s7-pattern-pill--muted'>"
                            f"{_period_count}{t(' 期', ' periods')}</span>"
                        )
                    with st.container(
                        key=(
                            f"s7_performance_panel_{key_prefix}_"
                            f"{_pattern_index}_{_start + _slot}"
                        )
                    ):
                        st.markdown(
                            f"<div class='s7-pattern-panel-head'>"
                            f"<div class='s7-pattern-panel-title' "
                            f"title='{escape(_company)}'>{escape(_company)}</div>"
                            f"</div>"
                            f"<div class='s7-pattern-panel-meta'>{''.join(_pill_html)}</div>",
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
                                x_value_title=revenue_label,
                                y_value_title=profit_label,
                                panel_width=panel_width,
                                panel_height=panel_height,
                                rank_frame=_rank_source,
                            )
                            render_altair_chart(
                                _chart,
                                width="stretch",
                                key=(
                                    f"{key_prefix}_pattern_{_pattern_index}_"
                                    f"{_start + _slot}"
                                ),
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
    render_chart_summary(
        solvency_ratio_summary(
            converted,
            "COMBINED_SOLVENCY_RATIO",
            periods,
            t("综合偿付能力充足率", "combined solvency ratio"),
            threshold=100.0,
        )
    )
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
    if chart_name in {"综合充足率变化", "综合偿付能力充足率"}:
        amount_groups = cfg.get("amount_groups") or {
            "实际资本": ("ACTUAL_CAPITAL",),
            "最低资本": ("MINIMUM_CAPITAL",),
        }
        ratio_code = "COMBINED_SOLVENCY_RATIO"
        ratio_label = t("综合偿付能力充足率（%）", "Combined solvency ratio (%)")
        summary_label = t("综合偿付能力充足率", "combined solvency ratio")
        regulatory_floor = 100.0
    else:
        amount_groups = cfg.get("amount_groups") or {
            "核心资本": ("CORE_T1_CAPITAL", "CORE_T2_CAPITAL"),
            "最低资本": ("MINIMUM_CAPITAL",),
        }
        ratio_code = "CORE_SOLVENCY_RATIO"
        ratio_label = t("核心偿付能力充足率（%）", "Core solvency ratio (%)")
        summary_label = t("核心偿付能力充足率", "core solvency ratio")
        regulatory_floor = 50.0
    render_chart_summary(
        solvency_ratio_summary(
            converted,
            ratio_code,
            chart_periods,
            summary_label,
            threshold=regulatory_floor,
        )
    )
    st.caption(
        t(
            "按年度口径展示：上方 KPMG Pink 折线为对应充足率，下方堆叠柱为资本规模；"
            "每家公司按自身资本规模自动缩放金额纵轴，柱上数字更易读。",
            "Annual view: the KPMG Pink line shows the solvency ratio and stacked bars below show capital scale. "
            "Each company uses its own amount scale so the in-bar values stay readable.",
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
            shared_amount_domain=None,
            dense_layout=dense_company_panels,
            panel_count=company_panel_count,
            annual_labels=True,
        ),
        key_prefix=f"{key_prefix}_{ratio_code}_combo",
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
    core_codes = capital_structure_groups.get("核心资本", ())
    ancillary_codes = capital_structure_groups.get("附属资本", ())
    render_chart_summary(
        capital_structure_summary(
            converted,
            core_codes,
            ancillary_codes,
            periods,
            label=t("核心资本", "core capital"),
        )
    )
    industry_ratios = capital_structure_industry_ratios(
        converted,
        core_codes,
        ancillary_codes,
        periods,
    )
    if industry_ratios is None:
        core_ratio = None
        ancillary_ratio = None
        ratio_period = t("最新报告期", "latest period")
    else:
        core_ratio, ancillary_ratio, ratio_period = industry_ratios
    core_display = f"{core_ratio:.1f}%" if core_ratio is not None else "—"
    ancillary_display = (
        f"{ancillary_ratio:.1f}%" if ancillary_ratio is not None else "—"
    )
    core_definition = (
        t(
            "核心资本是指在持续经营状态下和破产清算状态下均可以吸收损失的资本。",
            "Core capital is capital that can absorb losses both on a going-concern basis "
            "and in liquidation.",
        )
    )
    ancillary_definition = (
        t(
            "附属资本是指在破产清算状态下可以吸收损失的资本。",
            "Ancillary capital is capital that can absorb losses in liquidation.",
        )
    )
    structure_observation = (
        t(
            "小型、外资系、农业保险和自保公司的核心资本占比较为相似，"
            "处于80%至100%区间；资本补充债券等进行资本补充，"
            "以支持偿付能力提升和业务持续健康发展。",
            "Small companies, foreign-invested insurers, agricultural insurers and captive "
            "insurers have broadly similar core capital shares, within the 80%–100% range. "
            "These companies use instruments such as capital supplementary bonds to support "
            "solvency and sustainable business development.",
        )
    )
    supplement_observation = (
        t(
            "部分集团子公司、大型公司及中型公司的附属资本占比较高，"
            "通常是在保司偿付能力充足率持续承压的情况下，通过发行次级定期债务、"
            "资本补充债券等进行资本补充，以支持偿付能力提升和业务持续健康发展。",
            "Some group subsidiaries, large companies and medium-sized companies have relatively "
            "high ancillary capital shares. When solvency ratios are under pressure, they commonly "
            "issue subordinated term debt or capital supplementary bonds to support solvency and "
            "sustainable business development.",
        )
    )
    st.markdown(
        '<div class="s7-capital-insight">'
        '<div class="s7-capital-insight__head">'
        '<div>'
        f'<div class="s7-capital-insight__title">{escape(t("核心资本与附属资本结构", "Core and ancillary capital structure"))}</div>'
        f'<div class="s7-capital-insight__sub">{escape(t("资本口径说明与行业结构观察", "Capital definitions and industry structure"))} · {escape(str(ratio_period))}</div>'
        '</div>'
        f'<span class="s7-capital-insight__badge">{escape(t("资本结构", "Capital structure"))}</span>'
        '</div>'
        '<div class="s7-capital-stats">'
        '<div class="s7-capital-stat">'
        f'<div class="s7-capital-stat__label">{escape(t("行业核心资本占比", "Core capital share"))}</div>'
        f'<div class="s7-capital-stat__value">{escape(core_display)}</div>'
        f'<div class="s7-capital-stat__note">{escape(t("核心资本 ÷ 实际资本", "Core capital ÷ actual capital"))}</div>'
        '</div>'
        '<div class="s7-capital-stat s7-capital-stat--pink">'
        f'<div class="s7-capital-stat__label">{escape(t("行业附属资本占比", "Ancillary capital share"))}</div>'
        f'<div class="s7-capital-stat__value">{escape(ancillary_display)}</div>'
        f'<div class="s7-capital-stat__note">{escape(t("附属资本 ÷ 实际资本", "Ancillary capital ÷ actual capital"))}</div>'
        '</div>'
        '<div class="s7-capital-stat s7-capital-stat--muted">'
        f'<div class="s7-capital-stat__label">{escape(t("观察口径", "Observation basis"))}</div>'
        f'<div class="s7-capital-stat__value s7-capital-stat__value--small">{escape(t("行业汇总", "Industry aggregate"))}</div>'
        f'<div class="s7-capital-stat__note">{escape(t("按实际资本加权汇总", "Weighted by actual capital"))}</div>'
        '</div>'
        '</div>'
        '<div class="s7-capital-grid">'
        '<div class="s7-capital-card">'
        f'<div class="s7-capital-card__title"><span class="s7-capital-tag">{escape(t("定义", "Definition"))}</span>{escape(t("核心资本", "Core capital"))}</div>'
        f'<p class="s7-capital-card__body">{escape(core_definition)}</p>'
        '</div>'
        '<div class="s7-capital-card">'
        f'<div class="s7-capital-card__title"><span class="s7-capital-tag">{escape(t("定义", "Definition"))}</span>{escape(t("附属资本", "Ancillary capital"))}</div>'
        f'<p class="s7-capital-card__body">{escape(ancillary_definition)}</p>'
        '</div>'
        '<div class="s7-capital-card s7-capital-card--accent">'
        f'<div class="s7-capital-card__title"><span class="s7-capital-tag s7-capital-tag--pink">{escape(t("观察", "Observation"))}</span>{escape(t("结构特征", "Structure"))}</div>'
        f'<p class="s7-capital-card__body">{escape(structure_observation)}</p>'
        '</div>'
        '<div class="s7-capital-card s7-capital-card--accent">'
        f'<div class="s7-capital-card__title"><span class="s7-capital-tag s7-capital-tag--pink">{escape(t("观察", "Observation"))}</span>{escape(t("资本补充动因", "Capital replenishment drivers"))}</div>'
        f'<p class="s7-capital-card__body">{escape(supplement_observation)}</p>'
        '</div>'
        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        t(
            "上方 KPMG Pink 折线展示核心资本占实际资本的比例，下方堆叠柱展示核心资本与附属资本；"
            "每家公司按自身资本规模自动缩放金额纵轴，柱上数字更易读。",
            "The KPMG Pink line shows core capital as a share of actual capital; stacked bars below show core and ancillary capital. "
            "Each company uses its own amount scale so the in-bar values stay readable.",
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
            shared_amount_domain=None,
            dense_layout=dense_company_panels,
            panel_count=company_panel_count,
            annual_labels=bool(cfg.get("annual_labels")),
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
    module_override=None,
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
    negative_component_codes = (
        (
            "NON_LIFE_INSURANCE_RISK_DIVERSIFICATION_EFFECT",
        )
        if chart_name == "保险风险构成"
        else (
            ("QUANT_RISK_DIVERSIFICATION_EFFECT",)
            if chart_name == "量化风险最低资本构成"
            else (
                ("MARKET_RISK_DIVERSIFICATION_EFFECT",)
                if chart_name == "各类市场风险占比"
                else (
                    ("CREDIT_RISK_DIVERSIFICATION_EFFECT",)
                    if chart_name == "各类信用风险占比"
                    else ()
                )
            )
        )
    )
    if chart_name == "量化风险最低资本构成":
        summary_sentence = quant_risk_industry_sentence(
            converted,
            specs,
            periods,
            negative_component_codes=negative_component_codes,
        )
        if summary_sentence:
            st.markdown(
                '<div class="solvency-report-analysis">'
                f'<p class="solvency-note-default">'
                f'{escape(translate_text(summary_sentence))}</p>'
                "</div>",
                unsafe_allow_html=True,
            )
    elif chart_name == "保险风险构成":
        group_column = str(
            module_override.chart_config.get("group_column", "同业分类")
            if module_override
            else "同业分类"
        ).strip() or "同业分类"
        summary_sentence = insurance_risk_industry_sentence(
            converted,
            periods,
            group_column=group_column,
        )
        risk_metrics = insurance_risk_composition_metrics(
            converted,
            periods,
            group_column=group_column,
        )
        if risk_metrics:
            risk_period = str(risk_metrics.get("period", ""))
            premium_mean = float(risk_metrics["premium_mean"])
            catastrophe_mean = float(risk_metrics["catastrophe_mean"])
            catastrophe_median = float(risk_metrics["catastrophe_median"])
            diversification_mean = risk_metrics.get("diversification_mean")
            risk_sample = int(risk_metrics["n"])
            top_groups = [
                str(group) for group in risk_metrics.get("top_groups", [])
            ]
            top_groups_text = (
                t(
                    "、".join(top_groups),
                    ", ".join(category_name(group) for group in top_groups),
                )
                if top_groups
                else t("暂无明显集中", "no clear concentration")
            )
            risk_bullets = [
                t(
                    f"保费及准备金风险平均占 {premium_mean:.1f}%，"
                    f"巨灾风险平均占 {catastrophe_mean:.1f}%（中位数 {catastrophe_median:.1f}%）",
                    f"Premium and reserve risk averages {premium_mean:.1f}%; "
                    f"catastrophe risk averages {catastrophe_mean:.1f}% "
                    f"(median {catastrophe_median:.1f}%)",
                ),
            ]
            if diversification_mean is not None:
                risk_bullets.append(
                    t(
                        f"风险分散效应平均抵减 {float(diversification_mean):.1f}%",
                        f"The diversification effect offsets {float(diversification_mean):.1f}% on average",
                    )
                )
            risk_bullets.append(
                t(
                    f"保费及准备金风险占比较高的类型：{top_groups_text}",
                    f"Groups with above-average premium/reserve risk: {top_groups_text}",
                )
            )
            risk_bullets.append(
                t(
                    f"巨灾风险最高：{risk_metrics['highest_company']}"
                    f"（{float(risk_metrics['highest_value']):.1f}%）",
                    f"Highest catastrophe risk: {risk_metrics['highest_company']} "
                    f"({float(risk_metrics['highest_value']):.1f}%)",
                )
            )
            if summary_sentence:
                risk_bullets.append(translate_text(summary_sentence))
            render_insight_card(
                title=t("保险风险构成", "Insurance risk composition"),
                eyebrow=t("最低资本变动", "Minimum capital change"),
                headline=t(
                    f"{risk_period} 非寿险业务保险风险最低资本构成",
                    f"{risk_period} non-life insurance risk composition",
                ),
                value=t(
                    f"保费及准备金风险 {premium_mean:.1f}%",
                    f"Premium/reserve risk {premium_mean:.1f}%",
                ),
                delta=t(f"{risk_sample} 家样本", f"{risk_sample} companies"),
                delta_tone="neutral",
                bullets=risk_bullets,
                footnote=t(
                    "占比 = 各构成项 ÷ 非寿险业务保险风险最低资本；"
                    "风险分散效应为负向抵减项，图中位于零线下方。",
                    "Share = component ÷ non-life insurance risk minimum capital. "
                    "The diversification effect is a negative offset shown below zero.",
                ),
            )
        elif summary_sentence:
            st.markdown(
                '<div class="solvency-report-analysis">'
                f'<p class="solvency-note-default">'
                f'{escape(translate_text(summary_sentence))}</p>'
                "</div>",
                unsafe_allow_html=True,
            )
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
    elif chart_name == "保险风险构成":
        st.caption(
            t(
                "柱高按各构成项目/非寿险业务保险风险最低资本的比例展示，实际金额仅保留在悬浮信息中；"
                "负向风险分散位于零线下方。",
                "Bar heights show each component as a share of non-life insurance risk minimum "
                "capital; amounts are available on hover. The negative diversification effect "
                "sits below the zero line.",
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
        ("NON_LIFE_INSURANCE_RISK_DIVERSIFICATION_EFFECT",)
        if chart_name == "保险风险构成"
        else (
            (
                "QUANT_RISK_DIVERSIFICATION_EFFECT",
                "CONTRACT_LOSS_ABSORPTION_EFFECT",
            )
            if chart_name == "量化风险最低资本构成"
            else (
                ("MARKET_RISK_DIVERSIFICATION_EFFECT",)
                if chart_name == "各类市场风险占比"
                else (
                    ("CREDIT_RISK_DIVERSIFICATION_EFFECT",)
                    if chart_name == "各类信用风险占比"
                    else ()
                )
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
    annual_labels = bool(
        module_override.chart_config.get("annual_labels")
        if module_override
        else False
    )
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
            annual_labels=annual_labels,
        ),
        key_prefix=f"{key_prefix}_component_stack",
        highlight_company=highlight_company,
    )


def render_plan_capital_amount_combo(
    company_panel_count,
    converted,
    dense_company_panels,
    highlight_company,
    key_prefix,
    periods,
    module_override=None,
):
    render_chart_summary(capital_amount_summary(converted, periods))
    st.caption(
        t(
            "100%堆叠柱显示核心一级和附属一级资本构成；"
            "所有公司和报告期的柱高一致，色块高度代表结构占比，悬浮可查看占比和金额。",
            "100% stacked bars show core tier 1 and ancillary tier 1 capital. "
            "Bar heights are equal across companies and periods; segment heights show the mix, with shares and amounts on hover.",
        )
    )
    capital_codes = list(CAPITAL_AMOUNT_CODES)
    expected_count = converted["公司"].nunique() * len(periods) * len(capital_codes)
    disclosed_count = (
        converted[converted["指标编码"].astype(str).isin(capital_codes)]
        .drop_duplicates(["公司", "报告期", "指标编码"])
        .shape[0]
    )
    capital_legend = [
        (
            CAPITAL_STRUCTURE_COLORS[COMPONENT_LABELS[code]],
            COMPONENT_LABELS[code],
            "square",
        )
        for code in capital_codes
    ]
    if disclosed_count < expected_count:
        capital_legend.append(("#B8BDC7", t("未披露", "Not disclosed"), "square"))
    render_chart_legend(capital_legend)
    annual_labels = bool(
        module_override.chart_config.get("annual_labels")
        if module_override
        else False
    )
    render_company_chart_grid(
        converted,
        lambda company_frame: build_capital_amount_combo(
            company_frame,
            periods,
            dense_layout=dense_company_panels,
            panel_count=company_panel_count,
            annual_labels=annual_labels,
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

CAPABILITY_AXIS_LABELS = {
    "ACTUAL_CAPITAL": "资本实力",
    "COMBINED_SOLVENCY_RATIO": "偿付能力",
    "ROE": "盈利能力",
    "COMBINED_RATIO": "承保风控能力",
    "PREMIUM_GROWTH": "业务拓展",
}


def _normalize_capability_axes(axes_config: list[dict]) -> list[dict]:
    """Normalise old Excel axes to the five capability dimensions.

    Older templates used TOTAL_ASSETS and plain metric labels.  The current
    report replaces total assets with the combined ratio and labels the five
    vertices as capabilities, while keeping backward compatibility with an
    uploaded template that has not been refreshed.
    """
    normalized: list[dict] = []
    for raw_axis in deepcopy(axes_config):
        if not isinstance(raw_axis, dict):
            continue
        axis = dict(raw_axis)
        code = str(axis.get("code", "")).strip()
        if code == "TOTAL_ASSETS":
            axis["code"] = "COMBINED_RATIO"
            axis["label"] = "承保风控能力"
            axis["period"] = "本年累计数"
            axis["scoring"] = "average"
            axis["lower_is_better"] = True
            axis["average_center"] = 60.0
            axis["average_span"] = 0.10
        elif code in CAPABILITY_AXIS_LABELS:
            axis["label"] = CAPABILITY_AXIS_LABELS[code]
        normalized.append(axis)
    return normalized


def _split_top10_peer_group(frame: pd.DataFrame) -> pd.DataFrame:
    """把“同业分类”里的“前十大”拆成“前三大”/“前4-10”。

    - 公司名以“人保/平安/太保”开头的 → “前三大”
    - 其余原“前十大”公司 → “前4-10”
    - 其他分类原样保留。

    作用于 DataFrame 的“同业分类”列；capability_scores 会按该列从全量
    基准数据建立同类别打分池。
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
    chart_frame,
    highlight_company,
    key_prefix,
    module_override,
    periods,
    *,
    industry_frame=None,
):
    cfg = module_override.chart_config if module_override else {}
    axes_config = _normalize_capability_axes(
        cfg.get("axes") or DEFAULT_CAPABILITY_AXES
    )
    if is_english():
        for axis in axes_config:
            if isinstance(axis, dict) and axis.get("label"):
                axis["label"] = translate_text(str(axis["label"]))
    axes_labels = [ax["label"] for ax in axes_config]

    # ★ 打分前先把“前十大”拆成“前三大”/“前4-10”：
    #   前三大 = 人保、平安、太保三家财险公司（实际数据名为人保产险/平安产险/太保产险，
    #   故按公司名前缀匹配）；其余前十大公司归入“前4-10”。
    #   拆分作用于 chart_frame 的“同业分类”列，使 capability_scores 的分组打分池
    #   与下方展示分组口径一致。
    chart_frame = _split_top10_peer_group(chart_frame)

    # 打分下限与可选跨类别对标分组可由 Excel 图表配置覆盖，默认见 specs.py。
    try:
        score_floor = float(cfg.get("score_floor", CAPABILITY_PERCENTILE_FLOOR))
    except (TypeError, ValueError):
        score_floor = CAPABILITY_PERCENTILE_FLOOR
    score_floor = min(100.0, max(0.0, score_floor))

    configured_groups = cfg.get("industry_benchmark_groups")
    if isinstance(configured_groups, (list, tuple, set)):
        benchmark_groups = frozenset(
            str(name).strip() for name in configured_groups if str(name).strip()
        )
    else:
        benchmark_groups = CAPABILITY_INDUSTRY_BENCHMARK_GROUPS

    configured_floors = cfg.get("group_score_floors")
    if isinstance(configured_floors, dict):
        group_score_floors = {
            str(group): {
                str(axis): float(value)
                for axis, value in mapping.items()
                if isinstance(mapping, dict)
            }
            for group, mapping in configured_floors.items()
            if isinstance(mapping, dict)
        }
    else:
        group_score_floors = CAPABILITY_GROUP_SCORE_FLOORS

    latest_period_for_anchor = str(periods[-1]) if periods else ""
    baseline_source = (
        industry_frame
        if isinstance(industry_frame, pd.DataFrame) and not industry_frame.empty
        else chart_frame
    )
    baseline_source = _split_top10_peer_group(baseline_source)
    baseline_full = baseline_source[
        baseline_source["报告期"].astype(str).eq(latest_period_for_anchor)
    ].copy()

    score_all, raw_all, latest_period = capability_scores(
        chart_frame,
        axes_config,
        periods,
        baseline_frame=baseline_full,
        score_floor=score_floor,
        industry_benchmark_groups=benchmark_groups,
        group_score_floors=group_score_floors,
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

    # ---------- 组内参考五边形：各顶点得分中位数 ----------
    score_columns = [f"{label}_得分" for label in axes_labels]
    score_matrix = score_all[score_columns].apply(pd.to_numeric, errors="coerce")
    company_avg = score_matrix.mean(axis=1, skipna=True)
    category_reference: dict[str, list[float]] = {}
    for category in ordered_categories:
        cat_scores_for_ref = score_all[score_all["_分类"].astype(str).eq(category)]
        reference: list[float] = []
        for column in score_columns:
            values = pd.to_numeric(cat_scores_for_ref[column], errors="coerce").dropna()
            reference.append(float(values.median()) if not values.empty else float("nan"))
        category_reference[category] = reference

    # ---------- 图表上方的总结性解读 ----------
    overall_avg = float(company_avg.mean()) if company_avg.notna().any() else None
    sample_n = int(company_avg.notna().sum())
    dim_means = sorted(
        (
            (label, float(score_matrix[f"{label}_得分"].mean()))
            for label in axes_labels
            if score_matrix[f"{label}_得分"].notna().any()
        ),
        key=lambda item: item[1],
    )

    def _category_average(name: str) -> float | None:
        values = company_avg[score_all["_分类"].astype(str).eq(name)]
        return float(values.mean()) if values.notna().any() else None

    group_bits: list[str] = []
    for group in ("前三大", "前4-10"):
        group_value = _category_average(group)
        if group_value is not None:
            group_bits.append(f"{category_name(group)} {group_value:.0f}")
    other_values = company_avg[
        ~score_all["_分类"].astype(str).isin(["前三大", "前4-10"])
    ]
    if other_values.notna().any():
        other_avg = float(other_values.mean())
        group_bits.append(t(f"其余公司 {other_avg:.0f}", f"Others {other_avg:.0f}"))

    bullets: list[str] = []
    if group_bits:
        bullets.append(
            t(
                "五维均分：" + " / ".join(group_bits),
                "Five-axis average: " + " / ".join(group_bits),
            )
        )
    if dim_means:
        dim_scores_in_order = [
            (label, float(score_matrix[f"{label}_得分"].mean()))
            for label in axes_labels
            if score_matrix[f"{label}_得分"].notna().any()
        ]
        dim_text = " / ".join(
            f"{label} {value:.0f}" for label, value in dim_scores_in_order
        )
        bullets.append(
            t(
                "五维能力：" + dim_text,
                "Five capabilities: " + dim_text,
            )
        )
    if len(dim_means) >= 2:
        weakest_label, weakest_value = dim_means[0]
        strongest_label, strongest_value = dim_means[-1]
        bullets.append(
            t(
                f"优势能力 {strongest_label}（{strongest_value:.0f} 分），"
                f"短板能力 {weakest_label}（{weakest_value:.0f} 分）",
                f"Strongest capability {strongest_label} ({strongest_value:.0f}); "
                f"weakest capability {weakest_label} ({weakest_value:.0f})",
            )
        )
    bullets.append(
        t(
            "承保风控能力按同类别综合成本率平均值打分：同类别平均=60 分，"
            "综合成本率越低得分越高。",
            "Underwriting risk control is scored against the peer-category average "
            "combined ratio (peer-category average = 60; lower is better).",
        )
    )

    tracked_company = str(highlight_company or "").strip()
    if tracked_company and tracked_company != "无":
        tracked_rows = score_all[
            score_all["公司"].astype(str).eq(tracked_company)
        ]
        if not tracked_rows.empty:
            tracked_avg = float(company_avg.loc[tracked_rows.index[0]])
            if not pd.isna(tracked_avg):
                tracked_rank = int((company_avg > tracked_avg).sum() + 1)
                bullets.append(
                    t(
                        f"追踪公司 {tracked_company}：五维均分 {tracked_avg:.0f}，"
                        f"展示公司中第 {tracked_rank}/{sample_n}",
                        f"Tracked {tracked_company}: five-axis average {tracked_avg:.0f}, "
                        f"rank {tracked_rank}/{sample_n} among displayed companies",
                    )
                )

    render_insight_card(
        title=t("能力五边形", "Capability pentagon"),
        eyebrow=t("能力对标", "Capability benchmark"),
        headline=t(
            f"{latest_period or '—'} 行业五维能力画像",
            f"{latest_period or '—'} five-dimension capability profile",
        ),
        value=(
            t(f"均分 {overall_avg:.0f}", f"Average {overall_avg:.0f}")
            if overall_avg is not None
            else "—"
        ),
        delta=t(f"{sample_n} 家样本", f"{sample_n} companies"),
        delta_tone="neutral",
        bullets=bullets,
        footnote=t(
            "五个顶点依次对应资本实力、偿付能力、盈利能力、承保风控能力和业务拓展；"
            "资本实力、盈利能力、业务拓展按公司所在同业类别内的全量样本相对位次打分，"
            "承保风控能力按同类别综合成本率平均值打分（同类别平均=60 分，越低越优），"
            "偿付能力按绝对档位打分。"
            f"排名得分映射到 {score_floor:.0f}–100 分；前三大业务拓展单独映射到 70–100 分。"
            "灰色虚线为本组各顶点中位数。",
            "The five vertices are capital strength, solvency, profitability, underwriting "
            "risk control and business expansion. Capital strength, profitability and business "
            "expansion are rank-scored against the company's full peer category; underwriting "
            "risk control is scored against the peer-category-average combined ratio "
            "(average = 60; lower is better); solvency "
            f"uses absolute bands. Rank scores map to {score_floor:.0f}–100; the Top 3 business-"
            "expansion axis uses 70–100. The grey dashed pentagon is the group median for each axis.",
        ),
    )

    # ---------- 样式提示 ----------
    st.caption(
        t(
            f"展示 {latest_period}；每家公司单独一张能力五边形，"
            "按“同业分类”分组排列。"
            "五个顶点依次为：" + "、".join(axes_labels) + "。"
            "资本实力、盈利能力、业务拓展按公司所在同业类别的全量样本相对位次；"
            "承保风控能力按综合成本率相对同类别平均（60 分）打分；"
            "偿付能力按绝对档位打分。"
            f"排名得分映射到 {score_floor:.0f}–100 分；前三大业务拓展按 70–100 分映射。"
            "评分池包含全量数据中的同类别公司，不受展示公司筛选影响；"
            "灰色虚线为本组各顶点中位数。",
            f"Showing {latest_period}; one capability pentagon per company, grouped by peer group. "
            f"Vertices: {', '.join(axes_labels)}. "
            "Capital strength, profitability and business expansion are rank-scored against the "
            "company's full peer category; underwriting risk control is scored against the "
            "peer-category-average combined ratio (60); solvency "
            f"uses absolute bands. Rank scores map to {score_floor:.0f}–100; the Top 3 business-"
            "expansion axis uses a 70–100 scale. Scoring peers include every company in the same "
            "category from the full dataset, regardless of the display-company filter. The grey "
            "dashed pentagon is the group median for each axis.",
        )
    )

    # ---------- 按分类逐组渲染 ----------
    # 每个分面图最多 10 家（2 行 × 5 列），兼顾渲染速度与打印分页。
    companies_per_chart = 10
    columns_per_row = 5
    panel_size = 230  # 单张小图尺寸

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

        # ---- 网格布局：一行 5 家公司合并为同一个分面图 ----
        # 之前每家公司一个 Streamlit 图表元素（全部展示时约 90 个），
        # Vega 初始化和 DOM 挂载是主要耗时；改为每行一个分面图后元素数
        # 降到约 1/5。公司名直接作为分面标题，也与五边形严格对齐。
        companies = list(cat_scores["公司"].astype(str))
        for start in range(0, len(companies), companies_per_chart):
            batch = companies[start : start + companies_per_chart]
            batch_frame = cat_scores[
                cat_scores["公司"].astype(str).isin(batch)
            ].copy()
            batch_colors: dict[str, str] = {}
            batch_titles: dict[str, str] = {}
            for company in batch:
                is_tracked = bool(
                    tracked_company
                    and tracked_company != "无"
                    and company == tracked_company
                )
                batch_colors[company] = "#ED2124" if is_tracked else "#00338D"
                # 追踪公司用 ★ 前缀标识（分面标题不支持逐值颜色）。
                batch_titles[company] = f"★ {company}" if is_tracked else company

            score_columns_for_grid = [
                f"{label}_得分" for label in axes_labels
            ]
            score_rows = tuple(
                tuple(row)
                for row in batch_frame[
                    ["公司", *score_columns_for_grid]
                ].itertuples(index=False, name=None)
            )
            reference = tuple(category_reference.get(category) or ())
            try:
                chart = _cached_capability_grid(
                    score_rows,
                    tuple(axes_labels),
                    tuple(batch_colors.items()),
                    tuple(batch_titles.items()),
                    reference,
                    min(columns_per_row, len(batch)),
                    panel_size,
                )
            except Exception as exc:
                st.warning(f"⚠️ 能力五边形渲染失败：{exc}")
                import traceback

                st.code(traceback.format_exc())
                continue

            with st.container(
                key=f"s7_capability_row_{key_prefix}_{category}_{start}",
                horizontal_alignment="center",
            ):
                render_altair_chart(
                    chart,
                    width="content",
                    key=f"{key_prefix}_radar_{category}_{start}",
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

    latest_year = str(latest.get("年度", "") or "")
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
    cagr_value: float | None = None
    if len(complete) >= 2 and float(complete.iloc[0]["合计"] or 0) > 0:
        cagr_value = (
            (float(latest["合计"]) / float(complete.iloc[0]["合计"]))
            ** (1 / (len(complete) - 1))
            - 1
        ) * 100

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
    latest_total_raw_for_summary = latest.get("合计")
    summary_total = (
        float(latest_total_raw_for_summary) / 1_000_000.0
        if latest_total_raw_for_summary is not None
        and pd.notna(latest_total_raw_for_summary)
        else None
    )
    summary_unit_label = t("百亿元", "RMB 10bn")
    render_insight_card(
        title=t("规模与结构", "Scale and structure"),
        headline=t(f"{latest_year} 行业签单保费", f"{latest_year} industry signed premium"),
        value=(
            "—"
            if summary_total is None
            else f"{summary_total:,.1f} {summary_unit_label}"
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
            "Company categories come from the property insurance company list. Top 3 is matched by company-name prefix "
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
    profit_top4 = category_share("净利润", TOP4_10_CATEGORY)
    profit_top10 = category_share("净利润", TOP10_CATEGORY)
    if profit_top3 is not None and profit_top4 is not None:
        profit_top10 = profit_top3 + profit_top4
    premium_total = pd.to_numeric(data.categories["保费"], errors="coerce").sum() / divisor
    premium_sample_count = int(round(float(data.coverage) * int(data.company_count)))
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
    if profit_top3 is not None and profit_top10 is not None:
        profit_text = t(
            f"净利润主要集中在前三大保险公司：前三大 {profit_top3:.1f}%，前十大 {profit_top10:.1f}%",
            f"Net profit is concentrated in the top three insurers: Top 3 {profit_top3:.1f}%, Top 10 {profit_top10:.1f}%",
        )
    elif profit_top10 is not None:
        profit_text = t(
            f"净利润主要集中在前十大保险公司：前十大 {profit_top10:.1f}%",
            f"Net profit is concentrated in the top ten insurers: Top 10 {profit_top10:.1f}%",
        )
    else:
        profit_text = t("净利润集中度数据不足", "Insufficient net profit concentration data")
    if top3_share is not None:
        concentration_headline = t(
            f"前三大签单保费占比 {top3_share:.1f}%",
            f"Top 3 signed-premium share {top3_share:.1f}%",
        )
    elif top10_share is not None:
        concentration_headline = t(
            f"前十大签单保费占比 {top10_share:.1f}%",
            f"Top 10 signed-premium share {top10_share:.1f}%",
        )
    else:
        concentration_headline = "—"
    if top3_share is not None and top4_share is not None:
        premium_concentration_text = t(
            f"签单保费：前三大 {top3_share:.1f}% ｜ 前4-10 {top4_share:.1f}% ｜ 前十大 {top10_share:.1f}%",
            f"Signed premium: Top 3 {top3_share:.1f}% | Top 4–10 {top4_share:.1f}% | Top 10 {top10_share:.1f}%",
        )
    elif top10_share is not None:
        premium_concentration_text = t(
            f"签单保费：前十大 {top10_share:.1f}%",
            f"Signed premium: Top 10 {top10_share:.1f}%",
        )
    else:
        premium_concentration_text = concentration_text
    render_insight_card(
        title=t("市场份额与集中度", "Market share and concentration"),
        headline=t(
            f"{data.year} 年市场集中度",
            f"{data.year} market concentration",
        ),
        value=concentration_headline,
        delta=grouping_note,
        delta_tone="neutral",
        bullets=[
            premium_concentration_text,
            profit_text,
            t(
                f"样本覆盖 {premium_sample_count} 家公司（覆盖率 {data.coverage:.0%}）",
                f"Sample coverage: {premium_sample_count} companies ({data.coverage:.0%})",
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
