"""公司级偿付能力对标报告主流程（页面编排与各图表入口）。"""

from __future__ import annotations

from datetime import date
import re

import pandas as pd
import streamlit as st
from dashboard_components import (
    company_detail_rows,
    render_report_analysis,
    render_report_back_cover,
    render_report_cover,
    render_report_footnote,
    render_report_notes_editor,
)
from services.solvency_navigation import (
    COMPANY_NAVIGATION,
    PRINT_ALL_LABEL,
    metric_codes_for_chart,
)
from services.step7_excel_config import Step7Module
from services.solvency_step6_analysis import (
    filter_analysis_frame,
    nonblank_values,
    sort_report_periods,
)
from services.solvency_step7_chart_plans import (
    COMPANY_BAR_TREND,
    CAPABILITY_RADAR,
    UNDERWRITING_EFFICIENCY_SCATTER,
    STRUCTURE_CHANGE_CHART,
    SOLVENCY_DISCLOSURE_OVERVIEW,
    SINGLE_METRIC_TREND,
    TREND_WITH_COMPANY_BARS,
    chart_plan_for,
    PERFORMANCE_PATTERN_GRID,
)
from services.solvency_step7_charts import annual_period_order
from services.solvency_report_notes import company_notes_template
from services.report_i18n import (
    LANG_ZH,
    LANGUAGE_LABELS,
    LANGUAGE_OPTIONS,
    category_name,
    chart_name as localized_chart_name,
    get_language,
    is_english,
    level_name,
    set_language,
    t,
    translate_dataframe,
    translate_default_analysis,
    translate_metric_frame,
    unit_name,
)

from .plans import render_combination_analysis
from .rendering import render_image_override_controls, render_report_metric
from .selection import (
    company_scope_for_types,
    metric_sort_options,
    multiselect_popover,
    module_by_chart_name,
    selected_chart_names,
    sort_companies_by_metric,
    step7_modules_from_state,
    sync_company_selection_state,
    valid_state,
)
from .specs import ALL_COMPANY_TYPES, PICTURE_DIR, SORT_ASCENDING, SORT_DESCENDING
from .theme import render_report_style


# 这三张图固定展示最近三年；保费增速与赔付率需要多留一年基数。
THREE_YEAR_DISPLAY_CHARTS = {"保费增速", "赔付率与费用率"}
THREE_YEAR_TREND_CHARTS = {"投资收益率"}


def configured_chart_periods(
    periods: list[str],
    chart_config: dict | None,
    chart_name: str = "",
) -> list[str]:
    """Apply optional year limits and annual labels to one chart."""
    config = chart_config or {}
    ordered = sort_report_periods(periods)
    try:
        start_year = int(config.get("year_start"))
    except (TypeError, ValueError):
        start_year = None
    try:
        end_year = int(config.get("year_end"))
    except (TypeError, ValueError):
        end_year = None

    if start_year is not None or end_year is not None:
        limited: list[str] = []
        for period in ordered:
            match = re.search(r"(20\d{2})", str(period))
            if not match:
                continue
            year = int(match.group(1))
            if start_year is not None and year < start_year:
                continue
            if end_year is not None and year > end_year:
                continue
            limited.append(period)
        ordered = limited
    if config.get("annual_labels"):
        ordered = annual_period_order(ordered)
    try:
        year_limit = int(config.get("years"))
    except (TypeError, ValueError):
        year_limit = None
    if chart_name in THREE_YEAR_DISPLAY_CHARTS:
        # 多保留一年历史，保证第一个展示年度也能算出同比增速。
        year_limit = None
    elif chart_name in THREE_YEAR_TREND_CHARTS:
        year_limit = 3
    if year_limit is not None and year_limit > 0:
        ordered = ordered[-year_limit:]
    return ordered


def show_step_7_solvency(
    data: pd.DataFrame,
    financing_data: pd.DataFrame | None = None,
    step7_modules: list[Step7Module] | None = None,  # ★ 新增
) -> None:
    """Company report page following the annual-platform report workflow.

    图表清单、顺序、类型、指标编码全部来自 Step7 Excel 注释表（step7_modules）；
    未提供时回退到 COMPANY_NAVIGATION 的内置配置。
    """
    render_report_style()
    st.markdown(
        "<div class='solvency-report-title'>"
        + t("公司级偿付能力对标报告", "Company Solvency Benchmarking Report")
        + "</div>",
        unsafe_allow_html=True,
    )
    current_label = "English" if is_english() else "中文"
    selected_label = st.segmented_control(
        "展示语言 / Display language",
        list(LANGUAGE_OPTIONS),
        default=current_label,
        key="s7_language_display",
        label_visibility="collapsed",
    )
    if selected_label:
        set_language(LANGUAGE_LABELS.get(selected_label, LANG_ZH))
    if data is None or data.empty:
        st.info(
            t(
                "请先在 Step5 确认集成数据，或在 Step6 上传集成表。",
                "Confirm integrated data in Step 5, or upload an integrated workbook in Step 6.",
            )
        )
        return

    # ★ 从 session_state 兜底读取 Excel 模块列表
    if step7_modules is None:
        step7_modules = step7_modules_from_state()

    frame = company_detail_rows(data).copy()
    if frame.empty:
        st.info(t("当前数据没有公司明细记录。", "No company detail records are available."))
        return

    # ★ 修复（新增）：公司名称归一化。
    #   数据源里同一家公司会以两种名字出现，例如：
    #       "平安产险"         —— 覆盖保费、承保、利润等经营类指标
    #       "平安产险（偿付能力）" —— 覆盖充足率、实际资本、最低资本等指标
    #   如果不去掉"（偿付能力）"尾缀，Step7 就会把同一家公司当成两家，
    #   导致公司筛选器显示 13 家（8 + 5 个带后缀的重复项）。
    frame["公司"] = (
        frame["公司"]
        .astype(str)
        .str.replace(r"[（(]\s*偿付能力\s*[)）]\s*$", "", regex=True)
        .str.strip()
    )

    frame["数值"] = pd.to_numeric(frame["数值"], errors="coerce")
    frame = frame.dropna(subset=["数值"])

    # ★ 修复（新增）：归一化后，同一公司/指标/报告期/期间口径可能出现重复行
    #   （一个来源叫"平安产险"，另一个来源叫"平安产险（偿付能力）"，合并后同粒度出现两行）。
    #   这里按"后出现优先"去重，避免图表出现重复点。
    #   注意：不同"指标编码"不会被误删——保费、充足率等指标编码不同，都会保留。
    _dedup_keys = [
        _key for _key in ("公司", "指标编码", "报告期", "期间口径") if _key in frame.columns
    ]
    if _dedup_keys:
        frame = frame.drop_duplicates(subset=_dedup_keys, keep="last")

    all_periods = sort_report_periods(frame["报告期"])
    if not all_periods:
        st.info(t("当前数据没有可用报告期。", "No reporting periods are available."))
        return

    frame = translate_metric_frame(frame)

    notes = render_report_notes_editor(
        title=t("公司内容分析与注释输入", "Company analysis and note inputs"),
        key_prefix="step7",
        template=company_notes_template(),
    )

    # ★ 优先用 Excel 模块列表；没有则回退到 COMPANY_NAVIGATION
    if step7_modules:
        available_chart_names = [m.chart_name for m in step7_modules]
    else:
        available_chart_names = list(
            dict.fromkeys(entry.chart_name for entry in COMPANY_NAVIGATION)
        )
    image_overrides: dict[str, dict[str, object]] = {}
    with st.expander(
        t("公司级图表设置与图片覆盖", "Company chart settings and image overrides"),
        expanded=False,
        icon=":material/tune:",
    ):
        c0, c1, c2, c3, c4 = st.columns([1.35, 2.3, 1, 1, 1])
        peer_groups = nonblank_values(frame, "同业分类")
        type_options = [ALL_COMPANY_TYPES, *peer_groups]
        with c0:
            selected_types_raw = multiselect_popover(
                t("公司类型", "Company type"),
                type_options,
                key="s7_company_types",
                default=[ALL_COMPANY_TYPES],
                format_func=category_name,
                help=t(
                    "沿用偿付能力平台的‘同业分类’字段进行筛选。",
                    "Filter using the peer-group field from the solvency platform.",
                ),
            )
        company_source, all_companies = company_scope_for_types(
            frame,
            selected_types_raw,
        )
        company_selection_key = sync_company_selection_state(
            selected_types_raw,
            all_companies,
        )
        with c1:
            selected_companies = multiselect_popover(
                t("展示公司", "Companies"),
                all_companies,
                key=company_selection_key,
                default=all_companies,
            )
        with c2:
            unit_options = ["十亿元", "亿元", "百万元", "十万元"]
            valid_state("s7_unit_mode", unit_options)
            unit_mode = st.selectbox(
                t("显示单位", "Display unit"),
                unit_options,
                key="s7_unit_mode",
                format_func=unit_name,
            )
        with c3:
            highlight_options = ["无", *selected_companies]
            valid_state("s7_highlight_company", highlight_options)
            highlight_company = st.selectbox(
                t("特定追踪", "Track company"),
                highlight_options,
                key="s7_highlight_company",
            )
        with c4:
            enable_ai = st.toggle(
                t("一键AI分析", "One-click AI commentary"),
                value=False,
                key="s7_enable_ai",
                help=t(
                    "使用登录页配置的大模型接口，为当前图表生成一句同业对标点评。",
                    "Use the model configured on the sign-in page to generate a peer-benchmarking comment for the current chart.",
                ),
            )
            ai_data_consent = False
            if enable_ai:
                ai_data_consent = st.checkbox(
                    t(
                        "确认将当前图表中的公司名称和指标数据发送至已配置的大模型服务",
                        "I confirm that company names and metric data in the current chart may be sent to the configured model service",
                    ),
                    value=False,
                    key="s7_ai_data_consent",
                )
                if not ai_data_consent:
                    st.caption(
                        t(
                            "未确认前不会调用模型接口或发送数据。",
                            "No model call or data transfer occurs until you confirm.",
                        )
                    )

        st.divider()
        sort_options, sort_lookup = metric_sort_options(company_source)
        valid_state("s7_sort_field", sort_options)
        sc1, sc2, sc3 = st.columns([2, 2, 3])
        with sc1:
            sort_field = st.selectbox(
                t("图表展示顺序依据", "Chart sort metric"),
                sort_options,
                key="s7_sort_field",
                format_func=lambda value: t(value, "Default (original list order)") if value == "默认（按列表原始顺序）" else value,
            )
        with sc2:
            sort_order = st.radio(
                t("排序方向", "Sort direction"),
                [SORT_DESCENDING, SORT_ASCENDING],
                horizontal=True,
                key="s7_sort_order",
                format_func=lambda value: (
                    t("降序（从大到小）", "Descending (high to low)")
                    if value == SORT_DESCENDING
                    else t("升序（从小到大）", "Ascending (low to high)")
                ),
            )
        with sc3:
            st.caption(
                t(
                    "图表类型：按指标特征自动匹配（系统内置）",
                    "Chart type: automatically matched to metric characteristics (built in)",
                )
            )
        selected_companies = sort_companies_by_metric(
            company_source,
            selected_companies,
            sort_lookup.get(sort_field, ""),
            all_periods[-1],
            descending=sort_order == SORT_DESCENDING,
        )

        st.markdown("#### " + t("手动上传图片（PNG/JPG）", "Manual image upload (PNG/JPG)"))
        image_overrides = render_image_override_controls(available_chart_names, notes)

    selected_periods = all_periods
    show_labels = True
    decimals = 2
    transparent = True
    show_average = False
    if not selected_companies:
        st.info(
            t(
                "请在报告配置中至少选择一家展示公司。",
                "Select at least one company in the report settings.",
            )
        )
        return
    scoped = filter_analysis_frame(frame, companies=selected_companies, periods=selected_periods)
    scoped["_s7_company_order"] = pd.Categorical(
        scoped["公司"], categories=selected_companies, ordered=True
    )
    scoped = scoped.sort_values(["_s7_company_order", "报告期"]).drop(columns="_s7_company_order")

    chart_names = selected_chart_names(scoped, modules=step7_modules)
    if not chart_names:
        st.info(
            t(
                "请在侧边栏公司报告导航中选择具体图表。",
                "Select a chart from the company report navigation in the sidebar.",
            )
        )
        return
    print_all = st.session_state.get("company_nav_level_one") == PRINT_ALL_LABEL
    if print_all:
        # ... 渲染封面（这段保持不变）...
        today = date.today()
        period_label = (
            selected_periods[0]
            if len(selected_periods) == 1
            else f"{selected_periods[0]}–{selected_periods[-1]}"
        )
        if is_english():
            company_label = (
                ", ".join(selected_companies)
                if len(selected_companies) <= 4
                else f"{len(selected_companies)} companies"
            )
        else:
            company_label = (
                "、".join(selected_companies)
                if len(selected_companies) <= 4
                else f"{len(selected_companies)} 家公司"
            )
        render_report_cover(
            title=t("保险公司偿付能力公司对标报告", "Insurance Company Solvency Benchmarking Report"),
            subtitle=f"{period_label} · {company_label}",
            date_text=(
                f"{today.year}-{today.month:02d}"
                if is_english()
                else f"{today.year}年{today.month}月"
            ),
            picture_dir=PICTURE_DIR,
        )

    for chart_index, chart_name in enumerate(chart_names):
        if print_all and chart_index:
            st.markdown(
                "<div class='solvency-report-page-break'></div>",
                unsafe_allow_html=True,
            )

        # ★ 先尝试从 Excel 模块里找元数据
        module = module_by_chart_name(step7_modules, chart_name) if step7_modules else None

        # ★ 兼容没有 Excel 的情形：沿用 COMPANY_NAVIGATION
        if module is None:
            entries = [entry for entry in COMPANY_NAVIGATION if entry.chart_name == chart_name]
            level_one = entries[0].level_one if entries else ""
            level_two = entries[0].level_two if entries else ""
            module_chart_kind = None
            module_metric_codes: tuple[str, ...] = ()
            module_requires_all = entries[0].requires_all if entries else False
            module_entry_metric_codes = entries[0].metric_codes if entries else ()
        else:
            level_one = module.level_one
            level_two = module.level_two
            module_chart_kind = module.chart_kind
            module_metric_codes = module.metric_codes
            module_requires_all = module.requires_all  # ★ 改用 Excel 的值
            module_entry_metric_codes = module.metric_codes

        if module is not None and str(module.module_id).strip():
            module_identity = str(module.module_id).strip()
        else:
            module_identity = f"{chart_index}_{chart_name}"
        module_expander_key = f"s7_module_expander_{module_identity}"

        # 标题：Excel 有二级就拼上，没有就省掉；二级与图表同名时只保留一次。
        title_parts = []
        display_parts = (
            level_name(level_one),
            level_name(level_two),
            localized_chart_name(chart_name),
        )
        for part in display_parts:
            if part and (not title_parts or title_parts[-1] != part):
                title_parts.append(part)
        title_text = " - ".join(title_parts)

        with st.container(key=f"s7_report_module_{chart_index}"):
            with st.container(key=f"s7_module_expander_wrap_{chart_index}"):
                with st.expander(
                    title_text,
                    expanded=True,
                    key=module_expander_key,
                ):
                    chart_periods = configured_chart_periods(
                        selected_periods,
                        module.chart_config if module else {},
                        chart_name,
                    )
                    if not chart_periods:
                        st.warning(f"“{chart_name}”在配置的年度范围内没有可用数据。")
                        continue
                    chart_scope = scoped[
                        scoped["报告期"].fillna("").astype(str).isin(chart_periods)
                    ].copy()

                    # 分析/注释：优先用 Excel 的；英文模式只翻译默认分析内容。
                    chart_note = dict(notes.get(chart_name, {}) or {})
                    if is_english() and chart_note.get("分析内容-默认"):
                        chart_note["分析内容-默认"] = translate_default_analysis(
                            chart_note["分析内容-默认"]
                        )
                    render_report_analysis(chart_note)

                    override = image_overrides.get(chart_name)
                    if override:
                        st.image(
                            override["data"],
                            caption=t("图片覆盖：", "Image override: ")
                            + str(override["name"]),
                            width="stretch",
                        )
                    else:
                        # ★ 决定图表类型：Excel 优先，其次回退到 chart_plan_for
                        CHART_KIND_ALIASES = {
                            "能力五边形": CAPABILITY_RADAR,
                            "能力雷达图": CAPABILITY_RADAR,
                            "承保效益散点图": UNDERWRITING_EFFICIENCY_SCATTER,
                            "结构变化组合图": STRUCTURE_CHANGE_CHART,
                            "业绩模式分列图": PERFORMANCE_PATTERN_GRID,
                            "公司业绩轨迹图": PERFORMANCE_PATTERN_GRID,
                            "偿付能力整体情况卡片": SOLVENCY_DISCLOSURE_OVERVIEW,
                            "偿付能力披露整体情况": SOLVENCY_DISCLOSURE_OVERVIEW,
                        }
                        if module_chart_kind:
                            plan_kind = CHART_KIND_ALIASES.get(
                                module_chart_kind,
                                module_chart_kind,
                            )
                        else:
                            plan_kind = chart_plan_for(chart_name).kind

                        available_codes = set(
                            chart_scope["指标编码"].fillna("").astype(str)
                        )
                        missing_codes = [
                            code
                            for code in module_entry_metric_codes
                            if code not in available_codes
                        ]

                        if module_requires_all and missing_codes:
                            st.warning(
                                t(
                                    f"“{localized_chart_name(chart_name)}”缺少 {len(missing_codes)} 个必要指标，暂无法按 Excel 指定格式完整绘图。",
                                    f"“{localized_chart_name(chart_name)}” is missing {len(missing_codes)} required metrics and cannot yet be drawn in the Excel-specified format.",
                                )
                            )
                            st.caption(
                                t("缺失指标编码：", "Missing metric codes: ")
                                + t("、", ", ").join(missing_codes)
                            )
                        elif plan_kind in {
                            SINGLE_METRIC_TREND,
                            TREND_WITH_COMPANY_BARS,
                            COMPANY_BAR_TREND,
                        }:
                            codes_for_this_chart = module_metric_codes or tuple(
                                metric_codes_for_chart(chart_name)
                            )
                            for code in codes_for_this_chart:
                                show_metric_average = (
                                    show_average or code == "INVESTMENT_RETURN"
                                )
                                render_report_metric(
                                    chart_scope,
                                    code,
                                    periods=chart_periods,
                                    show_labels=show_labels,
                                    decimals=decimals,
                                    unit_mode=unit_mode,
                                    transparent=transparent,
                                    show_average=show_metric_average,
                                    highlight_company=highlight_company,
                                    enable_ai=enable_ai and ai_data_consent,
                                    key_prefix=f"s7_{chart_index}",
                                    company_panels=(plan_kind == COMPANY_BAR_TREND),
                                    company_period_bars=(
                                        plan_kind == TREND_WITH_COMPANY_BARS
                                    ),
                                    annual_labels=bool(
                                        module.chart_config.get("annual_labels")
                                        if module
                                        else False
                                    ),
                                )
                        else:
                            try:
                                render_combination_analysis(
                                    chart_scope,
                                    chart_name,
                                    periods=chart_periods,
                                    unit_mode=unit_mode,
                                    highlight_company=highlight_company,
                                    key_prefix=f"s7_{chart_index}",
                                    financing_data=financing_data,
                                    module_override=module,
                                    industry_frame=frame,
                                )
                            except (ValueError, KeyError) as exc:
                                st.warning(
                                    t(
                                        f"“{localized_chart_name(chart_name)}”暂无法绘制：{exc}",
                                        f"“{localized_chart_name(chart_name)}” cannot be drawn yet: {exc}",
                                    )
                                )
                    render_report_footnote(chart_note, language=get_language())

    if print_all:
        render_report_back_cover(picture_dir=PICTURE_DIR)

    with st.expander(t("查看报告底层数据", "View underlying report data")):
        st.dataframe(
            translate_dataframe(scoped),
            width="stretch",
            hide_index=True,
        )
