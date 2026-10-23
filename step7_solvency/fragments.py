"""气泡图/散点图的局部放大联动（Streamlit fragment，交互不触发整页重跑）。"""

from __future__ import annotations

import pandas as pd
import streamlit as st
from services.solvency_step7_charts import (
    build_capital_efficiency_bubble_chart,
    capital_efficiency_bubble_default_domain,
    combine_capital_efficiency_bubble_charts,
    combine_linked_matrix_charts,
    build_matrix_chart,
    risk_ratio_scatter_default_domain,
)

from services.report_i18n import chart_name as chart_display_name, t, translate_text
from .rendering import render_altair_chart
from .selection import bubble_company_color_map
from .specs import (
    BUBBLE_FULL_PANEL_WIDTH,
    BUBBLE_ZOOM_PANEL_WIDTH,
    RISK_SCATTER_FULL_PANEL_WIDTH,
    RISK_SCATTER_ZOOM_PANEL_WIDTH,
)


@st.fragment
def render_capital_efficiency_bubble_fragment(
    converted: pd.DataFrame,
    periods: list[str],
    company_colors: dict[str, str],
    highlight_company: str,
    key_prefix: str,
) -> None:
    """Keep magnifier interaction entirely inside Vega without Python reruns."""
    bubble_panel_height = 430
    state_prefix = "capital_efficiency_bubble_zoom"
    signature_key = f"{state_prefix}_signature"
    revision_key = f"{state_prefix}_revision"
    available_companies = tuple(dict.fromkeys(converted["公司"].dropna().astype(str)))
    requested_highlight = str(highlight_company or "").strip()
    tracked_company = requested_highlight if requested_highlight in available_companies else ""
    company_colors = bubble_company_color_map(
        available_companies,
        tracked_company,
    )
    try:
        default_domain, latest_period = capital_efficiency_bubble_default_domain(
            converted,
            periods,
            company_colors,
            tracked_company,
        )
    except ValueError as exc:
        st.warning(translate_text(str(exc)))
        return
    sample_signature = (
        latest_period,
        available_companies,
        tracked_company,
    )
    if (
        st.session_state.get(signature_key) != sample_signature
        or revision_key not in st.session_state
    ):
        st.session_state[signature_key] = sample_signature
        st.session_state[revision_key] = int(st.session_state.get(revision_key, 0)) + 1
    st.session_state.setdefault(revision_key, 0)
    # Keep the Vega selection entirely browser-side.  A revision-specific name
    # resets the brush without registering the chart as a Streamlit input.
    selection_name = f"capital_efficiency_zoom_{st.session_state[revision_key]}"
    try:
        full_chart, latest_period = build_capital_efficiency_bubble_chart(
            converted,
            periods,
            company_colors,
            tracked_company,
            panel_label=t("全样本图", "Full sample"),
            chart_width=BUBBLE_FULL_PANEL_WIDTH,
            chart_height=bubble_panel_height,
            show_company_legend=True,
            apply_theme=False,
            linked_selection_name=selection_name,
        )
    except ValueError as exc:
        st.warning(str(exc))
        return

    with st.container(
        horizontal=True,
        vertical_alignment="center",
        key=f"capital_bubble_print_controls_{key_prefix}",
    ):
        if st.button(
            t("定位追踪公司", "Locate tracked company")
            if tracked_company
            else t("恢复默认范围", "Reset default range"),
            icon=":material/my_location:",
            key=f"{state_prefix}_reset",
        ):
            st.session_state[revision_key] += 1
            st.rerun(scope="fragment")
        st.caption(
            t(
                "拖动主图灰色选区可移动观察范围，滚轮可缩放；"
                "当前局部视图会直接用于打印，打印时自动隐藏选区框。",
                "Drag the grey selection on the main chart to move the view or scroll to zoom. "
                "The current zoomed view is used directly for printing, where the selection box is hidden.",
            )
        )

    overlap_chart = None
    overlap_error = ""
    try:
        overlap_chart, _ = build_capital_efficiency_bubble_chart(
            converted,
            periods,
            company_colors,
            tracked_company,
            zoom_to_overlap_region=True,
            panel_label=t("局部样本放大", "Zoomed sample"),
            chart_width=BUBBLE_ZOOM_PANEL_WIDTH,
            chart_height=bubble_panel_height,
            show_company_legend=True,
            show_axis_titles=False,
            apply_theme=False,
            linked_selection_name=selection_name,
        )
        overlap_chart.to_dict(validate=True)
    except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
        overlap_error = str(exc)
    st.caption(
        t(
            f"展示 {latest_period}：横轴为实际资本/注册资本，纵轴为核心资本/注册资本，"
            "气泡面积代表认可资产规模；浅灰虚线为全样本横纵指标中位数。",
            f"Showing {latest_period}: the x-axis is actual capital / registered capital and the y-axis is "
            "core capital / registered capital; bubble size represents recognised assets. "
            "The light-grey dashed line is the full-sample median of both axes.",
        )
    )
    if overlap_chart is not None:
        bubble_pair = combine_capital_efficiency_bubble_charts(
            full_chart,
            overlap_chart,
            selection_name=selection_name,
            selection_domain=default_domain,
        )
        render_altair_chart(
            bubble_pair,
            width="content",
        )
    else:
        render_altair_chart(
            full_chart,
            width="content",
            key=f"{key_prefix}_capital_efficiency_bubble_full_v8",
        )
    if overlap_error:
        st.caption(
            t(
                f"局部放大图暂不可用：{overlap_error}",
                f"Zoomed chart is not available: {overlap_error}",
            )
        )
    st.caption(
        t(
            "局部图横纵轴与主图选区实时联动；设置追踪公司后，默认选区会围绕该公司及其邻近公司。",
            "The zoomed chart’s axes follow the main chart selection in real time. "
            "With a tracked company, the default selection centres on that company and its peers.",
        )
    )


@st.fragment
def render_risk_ratio_scatter_fragment(
    converted: pd.DataFrame,
    periods: list[str],
    company_colors: dict[str, str],
    highlight_company: str,
    key_prefix: str,
    chart_name: str,
    x_code: str,
    y_code: str,
    bubble_size_code: str,
    denominator_label: str,
) -> None:
    """Render linked full/local risk bubbles without Python selection reruns."""
    panel_height = 430
    state_prefix = f"risk_ratio_scatter_zoom_{x_code.lower()}_{y_code.lower()}"
    signature_key = f"{state_prefix}_signature"
    revision_key = f"{state_prefix}_revision"
    available_companies = tuple(dict.fromkeys(converted["公司"].dropna().astype(str)))
    requested_highlight = str(highlight_company or "").strip()
    tracked_company = requested_highlight if requested_highlight in available_companies else ""
    sample_signature = (
        "adaptive_risk_bubble_axes_v2",
        chart_name,
        tuple(periods),
        available_companies,
        tracked_company,
    )
    if (
        st.session_state.get(signature_key) != sample_signature
        or revision_key not in st.session_state
    ):
        st.session_state[signature_key] = sample_signature
        st.session_state[revision_key] = int(st.session_state.get(revision_key, 0)) + 1
    st.session_state.setdefault(revision_key, 0)
    selection_name = f"risk_ratio_zoom_{x_code.lower()}_{st.session_state[revision_key]}"
    try:
        default_domain, latest_period = risk_ratio_scatter_default_domain(
            converted,
            x_code,
            y_code,
            periods,
            chart_display_name(chart_name),
            tracked_company,
            company_colors,
            bubble_size_code=bubble_size_code,
            bubble_size_label=denominator_label,
        )
        full_chart, _ = build_matrix_chart(
            converted,
            x_code,
            y_code,
            periods,
            chart_display_name(chart_name),
            tracked_company,
            company_colors=company_colors,
            percentage_axes=True,
            panel_label=t("全样本图", "Full sample"),
            chart_width=RISK_SCATTER_FULL_PANEL_WIDTH,
            chart_height=panel_height,
            linked_selection_name=selection_name,
            apply_theme=False,
            bubble_size_code=bubble_size_code,
            bubble_size_label=denominator_label,
        )
    except ValueError as exc:
        st.warning(translate_text(str(exc)))
        return

    with st.container(
        horizontal=True,
        vertical_alignment="center",
        key=f"risk_scatter_print_controls_{x_code.lower()}",
    ):
        if st.button(
            t("定位追踪公司", "Locate tracked company")
            if tracked_company
            else t("恢复默认范围", "Reset default range"),
            icon=":material/my_location:",
            key=f"{state_prefix}_reset",
        ):
            st.session_state[revision_key] += 1
            st.rerun(scope="fragment")
        st.caption(
            t(
                "拖动全样本图灰色选区可移动观察范围，滚轮可缩放；"
                "当前局部视图会直接用于打印，打印时自动隐藏选区框。",
                "Drag the grey selection on the full-sample chart to move the view or scroll to zoom. "
                "The current zoomed view is used directly for printing, where the selection box is hidden.",
            )
        )

    local_chart = None
    local_error = ""
    try:
        local_chart, _ = build_matrix_chart(
            converted,
            x_code,
            y_code,
            periods,
            chart_display_name(chart_name),
            tracked_company,
            company_colors=company_colors,
            percentage_axes=True,
            zoom_to_overlap_region=True,
            panel_label=t("局部样本放大", "Zoomed sample"),
            chart_width=RISK_SCATTER_ZOOM_PANEL_WIDTH,
            chart_height=panel_height,
            show_axis_titles=False,
            linked_selection_name=selection_name,
            apply_theme=False,
            bubble_size_code=bubble_size_code,
            bubble_size_label=denominator_label,
            show_size_legend=False,
        )
        local_chart.to_dict(validate=True)
    except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
        local_error = str(exc)

    st.caption(
        t(
            f"展示 {latest_period}；每个气泡代表一家公司，横纵轴均为占{denominator_label}的比例，"
            f"气泡面积代表{denominator_label}规模；"
            "横纵轴根据各指标量级自动采用%、‰或基点（bp），具体单位见坐标轴标题；"
            "浅灰虚线为全样本横纵指标中位数。",
            f"Showing {latest_period}; each bubble is one company. Both axes are shown as a share of "
            f"{translate_text(denominator_label)}; bubble size represents {translate_text(denominator_label)} scale. "
            "Axes automatically use %, ‰ or basis points (bp) according to the metric scale; see the axis titles. "
            "The light-grey dashed line is the full-sample median of both axes.",
        )
    )
    if local_chart is not None:
        linked_chart = combine_linked_matrix_charts(
            full_chart,
            local_chart,
            selection_name=selection_name,
            selection_domain=default_domain,
        )
        render_altair_chart(linked_chart, width="content")
    else:
        render_altair_chart(
            full_chart,
            width="content",
            key=f"{key_prefix}_{x_code}_risk_scatter_full_v2",
        )
    if local_error:
        st.caption(
            t(
                f"局部放大图暂不可用：{local_error}",
                f"Zoomed chart is not available: {local_error}",
            )
        )
    st.caption(
        t(
            "局部图横纵轴与全样本图选区实时联动；设置追踪公司后，默认选区会围绕该公司及其邻近公司。",
            "The zoomed chart’s axes follow the full-sample selection in real time. "
            "With a tracked company, the default selection centres on that company and its peers.",
        )
    )
