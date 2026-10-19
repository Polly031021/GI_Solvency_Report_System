"""公司级偿付能力对标报告主流程（页面编排与各图表入口）。"""

from __future__ import annotations

from datetime import date

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
    SINGLE_METRIC_TREND,
    TREND_WITH_COMPANY_BARS,
    chart_plan_for,
    PERFORMANCE_PATTERN_GRID,
)
from services.solvency_report_notes import company_notes_template

from .plans import render_combination_analysis
from .rendering import render_image_override_controls, render_report_metric
from .selection import (
    company_scope_for_types,
    metric_sort_options,
    module_by_chart_name,
    reset_company_selection_for_types,
    selected_chart_names,
    sort_companies_by_metric,
    step7_modules_from_state,
    sync_company_selection_state,
    valid_state,
)
from .specs import ALL_COMPANY_TYPES, PICTURE_DIR, SORT_ASCENDING, SORT_DESCENDING
from .theme import render_report_style


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
        "<div class='solvency-report-title'>公司级偿付能力对标报告</div>", unsafe_allow_html=True
    )
    if data is None or data.empty:
        st.info("请先在 Step5 确认集成数据，或在 Step6 上传集成表。")
        return

    # ★ 从 session_state 兜底读取 Excel 模块列表
    if step7_modules is None:
        step7_modules = step7_modules_from_state()

    frame = company_detail_rows(data).copy()
    if frame.empty:
        st.info("当前数据没有公司明细记录。")
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
        st.info("当前数据没有可用报告期。")
        return

    notes = render_report_notes_editor(
        title="公司内容分析与注释输入",
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
    with st.expander("公司级图表设置与图片覆盖", expanded=False, icon=":material/tune:"):
        c0, c1, c2, c3, c4 = st.columns([1, 2, 1, 1, 1])
        peer_groups = nonblank_values(frame, "同业分类")
        type_options = [ALL_COMPANY_TYPES, *peer_groups]
        st.session_state.setdefault("s7_company_types", [ALL_COMPANY_TYPES])
        valid_state("s7_company_types", type_options, multiple=True)
        with c0:
            selected_types_raw = st.multiselect(
                "公司类型",
                type_options,
                key="s7_company_types",
                on_change=reset_company_selection_for_types,
                args=(frame,),
                help="沿用偿付能力平台的‘同业分类’字段进行筛选。",
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
            selected_companies = st.multiselect(
                "展示公司",
                all_companies,
                key=company_selection_key,
                placeholder="选择一家或多家公司",
            )
        with c2:
            unit_options = ["十亿元", "亿元", "百万元", "十万元"]
            valid_state("s7_unit_mode", unit_options)
            unit_mode = st.selectbox(
                "显示单位",
                unit_options,
                key="s7_unit_mode",
            )
        with c3:
            highlight_options = ["无", *selected_companies]
            valid_state("s7_highlight_company", highlight_options)
            highlight_company = st.selectbox(
                "特定追踪",
                highlight_options,
                key="s7_highlight_company",
            )
        with c4:
            enable_ai = st.toggle(
                "一键AI分析",
                value=False,
                key="s7_enable_ai",
                help="使用登录页配置的大模型接口，为当前图表生成一句同业对标点评。",
            )
            ai_data_consent = False
            if enable_ai:
                ai_data_consent = st.checkbox(
                    "确认将当前图表中的公司名称和指标数据发送至已配置的大模型服务",
                    value=False,
                    key="s7_ai_data_consent",
                )
                if not ai_data_consent:
                    st.caption("未确认前不会调用模型接口或发送数据。")

        st.divider()
        sort_options, sort_lookup = metric_sort_options(company_source)
        valid_state("s7_sort_field", sort_options)
        sc1, sc2, sc3 = st.columns([2, 2, 3])
        with sc1:
            sort_field = st.selectbox(
                "图表展示顺序依据",
                sort_options,
                key="s7_sort_field",
            )
        with sc2:
            sort_order = st.radio(
                "排序方向",
                [SORT_DESCENDING, SORT_ASCENDING],
                horizontal=True,
                key="s7_sort_order",
            )
        with sc3:
            st.caption("图表类型：按指标特征自动匹配（系统内置）")
        selected_companies = sort_companies_by_metric(
            company_source,
            selected_companies,
            sort_lookup.get(sort_field, ""),
            all_periods[-1],
            descending=sort_order == SORT_DESCENDING,
        )

        st.markdown("#### 手动上传图片（PNG/JPG）")
        image_overrides = render_image_override_controls(available_chart_names, notes)

    selected_periods = all_periods
    show_labels = True
    decimals = 2
    transparent = True
    show_average = False
    if not selected_companies:
        st.info("请在报告配置中至少选择一家展示公司。")
        return
    scoped = filter_analysis_frame(frame, companies=selected_companies, periods=selected_periods)
    scoped["_s7_company_order"] = pd.Categorical(
        scoped["公司"], categories=selected_companies, ordered=True
    )
    scoped = scoped.sort_values(["_s7_company_order", "报告期"]).drop(columns="_s7_company_order")

    chart_names = selected_chart_names(scoped, modules=step7_modules)
    if not chart_names:
        st.info("请在侧边栏公司报告导航中选择具体图表。")
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
        company_label = (
            "、".join(selected_companies)
            if len(selected_companies) <= 4
            else f"{len(selected_companies)} 家公司"
        )
        render_report_cover(
            title="保险公司偿付能力公司对标报告",
            subtitle=f"{period_label} · {company_label}",
            date_text=f"{today.year}年{today.month}月",
            picture_dir=PICTURE_DIR,
        )

    for chart_index, chart_name in enumerate(chart_names):
        if print_all and chart_index:
            st.markdown("<div class='solvency-report-page-break'></div>", unsafe_allow_html=True)

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

        # 标题：Excel 有二级就拼上，没有就省掉
        title_parts = [p for p in (level_one, level_two, chart_name) if p]
        title_text = " - ".join(title_parts)

        with st.container(key=f"s7_report_module_{chart_index}"):
            st.markdown(
                f"<div class='solvency-module-title'>{title_text}</div>",
                unsafe_allow_html=True,
            )

            # 分析/注释：优先用 Excel 的
            chart_note = notes.get(chart_name, {})
            render_report_analysis(chart_note)

            override = image_overrides.get(chart_name)
            if override:
                st.image(
                    override["data"],
                    caption=f"图片覆盖：{override['name']}",
                    width="stretch",
                )
            else:
                # ★ 决定图表类型：Excel 优先，其次回退到 chart_plan_for
                CHART_KIND_ALIASES = {
                    "能力五边形": CAPABILITY_RADAR,
                    "能力雷达图": CAPABILITY_RADAR,
                    "承保效益散点图": UNDERWRITING_EFFICIENCY_SCATTER,  # ★ 新增
                    "结构变化组合图": STRUCTURE_CHANGE_CHART,  # ★ 新增
                    "业绩模式分列图": PERFORMANCE_PATTERN_GRID,  # ★ 新增
                    "公司业绩轨迹图": PERFORMANCE_PATTERN_GRID,  # ★ 新增（兼容旧写法）
                }
                if module_chart_kind:
                    plan_kind = CHART_KIND_ALIASES.get(module_chart_kind, module_chart_kind)
                else:
                    plan_kind = chart_plan_for(chart_name).kind

                available_codes = set(scoped["指标编码"].fillna("").astype(str))
                missing_codes = [c for c in module_entry_metric_codes if c not in available_codes]

                if module_requires_all and missing_codes:
                    st.warning(
                        f"“{chart_name}”缺少 {len(missing_codes)} 个必要指标，暂无法按 Excel 指定格式完整绘图。"
                    )
                    st.caption("缺失指标编码：" + "、".join(missing_codes))
                elif plan_kind in {SINGLE_METRIC_TREND, TREND_WITH_COMPANY_BARS, COMPANY_BAR_TREND}:
                    # 指标编码：Excel 优先
                    codes_for_this_chart = module_metric_codes or tuple(
                        metric_codes_for_chart(chart_name)
                    )
                    for code in codes_for_this_chart:
                        render_report_metric(
                            scoped,
                            code,
                            periods=selected_periods,
                            show_labels=show_labels,
                            decimals=decimals,
                            unit_mode=unit_mode,
                            transparent=transparent,
                            show_average=show_average,
                            highlight_company=highlight_company,
                            enable_ai=enable_ai and ai_data_consent,
                            key_prefix=f"s7_{chart_index}",
                            company_panels=(plan_kind == COMPANY_BAR_TREND),
                            company_period_bars=(plan_kind == TREND_WITH_COMPANY_BARS),
                        )
                else:
                    render_combination_analysis(
                        scoped,
                        chart_name,
                        periods=selected_periods,
                        unit_mode=unit_mode,
                        highlight_company=highlight_company,
                        key_prefix=f"s7_{chart_index}",
                        financing_data=financing_data,
                        module_override=module,  # ★ 新增
                        industry_frame=frame,
                    )
            render_report_footnote(chart_note)

    if print_all:
        render_report_back_cover(picture_dir=PICTURE_DIR)

    with st.expander("查看报告底层数据"):
        st.dataframe(scoped, width="stretch", hide_index=True)
