"""STEP6 自定义分析：数据源选择、图表配置面板与对比图渲染。"""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import streamlit as st
from dashboard_components import render_kpmg_palette
from services.report_profiles import ReportProfile
from services.solvency_step6_analysis import (
    CHART_TYPES,
    COMPANY_REPORT,
    INDUSTRY_REPORT,
    NO_COMPANY_TYPE_FILTER,
    build_comparison_chart,
    companies_for_quick_selection,
    default_periods,
    filter_analysis_frame,
    metric_options,
    nonblank_values,
    report_scope_frame,
    sort_report_periods,
    visualization_metric_frame,
)
from services.solvency_navigation import (
    KPMG_DEFAULT_COLORS,
    PRINT_ALL_LABEL,
    metric_codes_for_chart,
    resolve_chart_selection,
)
from services.solvency_normalizer import STANDARD_COLUMNS

from .cache import prepare_visualization_source, read_step6_analysis_workbook
from .helpers import keep_valid_widget_state, navigation_metric_codes
from .navigation import (
    render_step7_excel_navigation,
    render_major_financing_upload,
    render_report_navigation,
)


def _render_step6_source_selector(
    active_profile: ReportProfile,
) -> tuple[pd.DataFrame, str, tuple[str, ...], str]:
    """STEP6：数据源选择（直接引用集成数据或上传 Excel）。"""
    source_mode = st.radio(
        "数据源选择",
        ["直接引用集成后的数据", "上传集成表 Excel"],
        horizontal=True,
        key="step6_data_source_choice",
    )

    source = pd.DataFrame(columns=STANDARD_COLUMNS)
    source_label = ""
    skipped_profiles: tuple[str, ...] = ()
    if source_mode == "直接引用集成后的数据":
        source, skipped_profiles = prepare_visualization_source(
            st.session_state.integrated_data,
            active_profile.profile_id,
        )
        source_label = "STEP5 已确认集成数据"
    else:
        step6_upload = st.file_uploader(
            "上传行业集成目标表",
            type=["xlsx"],
            key=f"step6_integrated_upload_{active_profile.profile_id}",
            help="优先读取“标准数据”工作表；至少应包含公司、指标编码、指标名称和数值字段。",
        )
        if step6_upload is not None:
            try:
                source, skipped_profiles = read_step6_analysis_workbook(
                    step6_upload.getvalue(),
                    step6_upload.name,
                    active_profile.profile_id,
                )
                source_label = step6_upload.name
                st.success(f"已读取 {len(source):,} 条可分析记录。")
            except Exception as exc:
                st.error(f"集成表读取失败：{exc}")

    if skipped_profiles:
        st.warning("已跳过不属于当前报告 Profile 的记录：" + "、".join(skipped_profiles))
    st.session_state.step6_analysis_source = source.copy()
    return source, source_label, skipped_profiles, source_mode


def _render_step6_sidebar(
    active_profile: ReportProfile,
    is_project_member: bool,
    analysis_tabs_active: bool,
    industry_available_metric_codes: list[str],
) -> None:
    """STEP6：侧边栏公司/行业导航与补充数据上传。"""
    if analysis_tabs_active:
        with st.sidebar:
            # ★ 换成 Excel 驱动的 Step7 侧边栏
            render_step7_excel_navigation(active_profile.profile_id)

            # 行业分析导航保持原样
            if is_project_member:
                render_report_navigation(
                    title="行业分析导航",
                    container_key="kpmg_industry_nav_title",
                    state_prefix="industry_nav",
                    available_codes=industry_available_metric_codes,
                    print_key=f"industry_report_print_{active_profile.profile_id}",
                    industry=True,
                )
            render_major_financing_upload(active_profile.profile_id)


def _render_step6_config_panel(module_frame: pd.DataFrame) -> SimpleNamespace:
    """STEP6：核心配置面板；返回图表渲染所需的全部设置。"""
    with st.expander(
        "核心配置面板",
        expanded=True,
        icon=":material/tune:",
    ):
        settings_col, layout_col, unit_col = st.columns([1.5, 1, 1])
        metric_labels, metric_lookup = metric_options(module_frame)
        keep_valid_widget_state("step6_metric", metric_labels)
        with settings_col:
            chart_type = st.selectbox(
                "图表类型",
                CHART_TYPES,
                key="step6_chart_type",
            )
            selected_metric = (
                st.selectbox(
                    "选择显示指标",
                    metric_labels,
                    key="step6_metric",
                    disabled=not metric_labels,
                    placeholder="暂无可用指标",
                )
                if metric_labels
                else ""
            )

            peer_group_options = nonblank_values(module_frame, "同业分类")
            company_type_filter_options = [
                NO_COMPANY_TYPE_FILTER,
                *peer_group_options,
            ]
            keep_valid_widget_state(
                "step6_company_type_quick_select",
                company_type_filter_options,
            )
            selected_company_type = st.selectbox(
                "按公司类型快速选择",
                company_type_filter_options,
                key="step6_company_type_quick_select",
            )
            all_company_options = companies_for_quick_selection(
                module_frame,
            )
            selected_type_companies = companies_for_quick_selection(
                module_frame,
                selected_company_type,
            )
            previous_company_type = st.session_state.get(
                "_step6_previous_company_type_quick_select"
            )
            if previous_company_type != selected_company_type:
                if selected_company_type == NO_COMPANY_TYPE_FILTER:
                    current_companies = st.session_state.get("step6_companies", [])
                    retained_companies = [
                        company for company in current_companies if company in all_company_options
                    ]
                    st.session_state.step6_companies = retained_companies or all_company_options[:2]
                else:
                    st.session_state.step6_companies = selected_type_companies
                st.session_state._step6_previous_company_type_quick_select = selected_company_type
            elif "step6_companies" not in st.session_state:
                st.session_state.step6_companies = all_company_options[:2]
            keep_valid_widget_state(
                "step6_companies",
                all_company_options,
                multiple=True,
            )
            selected_comparison_companies = st.multiselect(
                "选择对比公司",
                all_company_options,
                key="step6_companies",
                placeholder="选择一家或多家公司",
            )

        comparison_frame = (
            module_frame[module_frame["公司"].isin(selected_comparison_companies)].copy()
            if selected_comparison_companies
            else module_frame.iloc[0:0].copy()
        )
        with layout_col:
            layout_mode = st.radio(
                "布局视角",
                ["以公司为横轴", "以报告期为横轴"],
                key="step6_layout_mode",
            )
            decimals = st.number_input(
                "小数位数",
                min_value=0,
                max_value=4,
                value=2,
                step=1,
                key="step6_decimals",
            )
            show_labels = st.toggle(
                "显示数据标签",
                value=True,
                key="step6_show_labels",
            )

        selected_metric_code = metric_lookup.get(selected_metric, "")
        metric_preview = filter_analysis_frame(
            module_frame,
            metric_code=selected_metric_code,
        )
        preview_units = nonblank_values(metric_preview, "单位")
        unit_options = ["原始数值"]
        if any(unit in {"元", "万元", "亿元"} for unit in preview_units):
            unit_options.extend(["亿元", "十亿元"])
        if any(unit == "倍" for unit in preview_units):
            unit_options.append("百分比(%)")
        keep_valid_widget_state("step6_unit_mode", unit_options)
        with unit_col:
            unit_mode = st.selectbox(
                "数值单位换算",
                unit_options,
                key="step6_unit_mode",
            )
            y_axis_title = st.text_input(
                "Y轴单位显示修改",
                value="",
                placeholder="留空则使用指标单位",
                key="step6_y_axis_title",
            )
            transparent = st.toggle(
                "开启透明背景模式",
                value=False,
                key="step6_transparent",
            )
            show_average = st.toggle(
                "平均值线",
                value=False,
                key="step6_show_average",
            )
            average_color = (
                st.color_picker(
                    "基准线颜色",
                    value="#ED2124",
                    key="step6_average_color",
                )
                if show_average
                else "#ED2124"
            )

        period_options = sort_report_periods(comparison_frame["报告期"].tolist())
        keep_valid_widget_state(
            "step6_periods",
            period_options,
            multiple=True,
        )
        time_col, scope_col = st.columns([2, 1])
        with time_col:
            selected_periods = st.multiselect(
                "对比时间",
                period_options,
                default=(
                    default_periods(period_options)
                    if "step6_periods" not in st.session_state
                    else None
                ),
                key="step6_periods",
                placeholder="选择报告期",
            )

        period_frame = (
            comparison_frame[comparison_frame["报告期"].isin(selected_periods)].copy()
            if selected_periods
            else comparison_frame.iloc[0:0].copy()
        )
        scope_options = nonblank_values(period_frame, "期间口径")
        keep_valid_widget_state("step6_period_scope", scope_options)
        with scope_col:
            period_scope = (
                st.selectbox(
                    "期间口径",
                    scope_options,
                    key="step6_period_scope",
                    disabled=not scope_options,
                    placeholder="暂无期间口径",
                )
                if scope_options
                else ""
            )
        scoped_frame = filter_analysis_frame(
            period_frame,
            period_scope=period_scope,
        )
    return SimpleNamespace(
        scoped_frame=scoped_frame,
        metric_code=metric_lookup.get(selected_metric, ""),
        chart_type=chart_type,
        layout_mode=layout_mode,
        show_labels=show_labels,
        decimals=int(decimals),
        unit_mode=unit_mode,
        y_axis_title=y_axis_title,
        transparent=transparent,
        show_average=show_average,
        average_color=average_color,
        comparison_companies=list(selected_comparison_companies),
    )


def _render_step6_chart_panel(settings: SimpleNamespace, source_label: str) -> None:
    """STEP6：按配置渲染对比图与图表明细。"""
    selected_metric_code = settings.metric_code
    chart_type = settings.chart_type
    layout_mode = settings.layout_mode
    show_labels = settings.show_labels
    decimals = settings.decimals
    unit_mode = settings.unit_mode
    y_axis_title = settings.y_axis_title
    transparent = settings.transparent
    show_average = settings.show_average
    average_color = settings.average_color
    selected_comparison_companies = settings.comparison_companies
    scoped_frame = settings.scoped_frame
    chart_frame = filter_analysis_frame(
        scoped_frame,
        metric_code=selected_metric_code,
    )
    if chart_frame.empty:
        st.info("请至少选择一个公司、一个报告期和一个可用指标。")
    else:
        chart_frame = chart_frame.copy()
        units = nonblank_values(chart_frame, "单位")
        chart_plot_frame = chart_frame.copy()
        if unit_mode in {"亿元", "十亿元"}:
            target_yuan = 100_000_000 if unit_mode == "亿元" else 1_000_000_000
            source_yuan = {"元": 1, "万元": 10_000, "亿元": 100_000_000}
            chart_plot_frame["数值"] = chart_plot_frame.apply(
                lambda row: (
                    pd.to_numeric(pd.Series([row["数值"]]), errors="coerce").iloc[0]
                    * source_yuan.get(str(row.get("单位", "")).strip(), target_yuan)
                    / target_yuan
                ),
                axis=1,
            )
            chart_plot_frame["单位"] = unit_mode
        elif unit_mode == "百分比(%)":
            multiple_mask = chart_plot_frame["单位"].astype(str).str.strip().eq("倍")
            chart_plot_frame.loc[multiple_mask, "数值"] = (
                pd.to_numeric(
                    chart_plot_frame.loc[multiple_mask, "数值"],
                    errors="coerce",
                )
                * 100
            )
            chart_plot_frame.loc[multiple_mask, "单位"] = "%"

        duplicate_columns = ["公司", "报告期", "指标编码", "期间口径"]
        duplicate_count = int(chart_frame.duplicated(duplicate_columns, keep=False).sum())
        if duplicate_count:
            st.warning(
                f"当前筛选结果中有 {duplicate_count:,} 条重复粒度记录，"
                "图表将全部保留展示，请检查集成数据。"
            )
        if len(units) > 1:
            st.warning("同一指标存在多个单位：" + "、".join(units))

        st.markdown("#### :material/palette: 自定义图例标签与颜色")
        legend_items = (
            sort_report_periods(chart_plot_frame["报告期"].tolist())
            if layout_mode == "以公司为横轴"
            else nonblank_values(chart_plot_frame, "公司")
        )
        legend_label_map: dict[str, str] = {}
        legend_color_map: dict[str, str] = {}
        legend_columns = st.columns(min(4, max(1, len(legend_items))))
        for index, item in enumerate(legend_items):
            with legend_columns[index % len(legend_columns)]:
                st.caption(f"原始值：{item}")
                legend_label_map[item] = st.text_input(
                    "显示名称",
                    value=item,
                    key=f"step6_legend_label_{layout_mode}_{index}",
                )
                legend_color_map[item] = st.color_picker(
                    "选择颜色",
                    value=KPMG_DEFAULT_COLORS[index % len(KPMG_DEFAULT_COLORS)],
                    key=f"step6_legend_color_{layout_mode}_{index}",
                )

        metric_name = str(chart_frame.iloc[0]["指标名称"])
        st.markdown(f"#### {metric_name}")
        st.caption(
            f"数据源：{source_label or '当前上传文件'}　｜　"
            f"对比公司：{len(selected_comparison_companies)} 家　｜　"
            f"对比期间：{chart_frame['报告期'].nunique()} 个　｜　"
            f"记录数：{len(chart_frame)} 条"
        )
        chart = build_comparison_chart(
            chart_plot_frame,
            chart_type,
            sort_report_periods(chart_frame["报告期"].tolist()),
            show_labels=show_labels,
            decimals=int(decimals),
            layout_mode=layout_mode,
            legend_label_map=legend_label_map,
            legend_color_map=legend_color_map,
            y_axis_title=y_axis_title,
            transparent=transparent,
            show_average=show_average,
            average_color=average_color,
        )
        st.altair_chart(chart, width="stretch")

        with st.expander("查看图表明细数据"):
            display_columns = [
                "公司",
                "公司类型",
                "同业分类",
                "报告期",
                "一级模块",
                "二级模块",
                "指标编码",
                "指标名称",
                "期间口径",
                "数值",
                "单位",
                "来源类型",
                "来源文件",
                "来源工作表",
                "计算逻辑",
            ]
            st.dataframe(
                chart_frame[display_columns].sort_values(["报告期", "公司"]),
                width="stretch",
                hide_index=True,
            )


def render_analysis_tab(
    active_profile: ReportProfile,
    is_project_member: bool,
    analysis_tabs_active: bool,
    active_workflow_tab: str,
) -> None:
    """STEP6 自定义偿付能力对标分析。"""
    st.subheader("自定义偿付能力对标分析")
    source, source_label, skipped_profiles, source_mode = _render_step6_source_selector(
        active_profile
    )
    navigation_source = report_scope_frame(
        visualization_metric_frame(source),
        COMPANY_REPORT,
    )
    industry_navigation_source = report_scope_frame(
        visualization_metric_frame(source),
        INDUSTRY_REPORT,
    )
    industry_available_metric_codes = navigation_metric_codes(industry_navigation_source)

    _render_step6_sidebar(
        active_profile,
        is_project_member,
        analysis_tabs_active,
        industry_available_metric_codes,
    )
    if navigation_source.empty:
        if not source.empty:
            st.info("当前数据仅包含校验类记录；这些记录保留在 STEP5，但不会进入可视化指标。")
        elif source_mode == "直接引用集成后的数据":
            st.info("STEP5 尚无已确认的集成数据，请先完成集成，或切换为上传集成表 Excel。")
        elif source_label == "":
            st.info("请上传一份标准窄表格式的集成 Excel。")
    else:
        render_kpmg_palette()
        selected_level_one = st.session_state.get("company_nav_level_one", "")
        selected_level_two = st.session_state.get("company_nav_level_two", "全部")
        module_frame = navigation_source.copy()
        if selected_level_one and selected_level_one != PRINT_ALL_LABEL:
            selected_charts = resolve_chart_selection(
                selected_level_one,
                selected_level_two,
                st.session_state.get("company_nav_chart", ""),
            )
            selected_codes = list(
                dict.fromkeys(
                    code
                    for chart_name in selected_charts
                    for code in metric_codes_for_chart(chart_name)
                )
            )
            module_frame = module_frame[
                module_frame["指标编码"].astype(str).str.strip().isin(selected_codes)
            ]
        if module_frame.empty and not navigation_source.empty:
            st.info("当前一级/二级模块下没有可视化指标，请在侧边栏选择其他模块。")
        if not module_frame.empty:
            settings = _render_step6_config_panel(module_frame)
            _render_step6_chart_panel(settings, source_label)
