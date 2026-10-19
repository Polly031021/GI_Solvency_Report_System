"""侧边栏导航：公司/行业报告导航、Excel 驱动的 STEP7 导航与补充数据上传。"""

from __future__ import annotations

import html

import streamlit as st
from dashboard_components import render_print_control
from services.solvency_navigation import (
    COMPANY_FIRST_LEVELS,
    OVERVIEW_LEVEL,
    PRINT_ALL_LABEL,
    chart_names,
    first_levels_for_codes,
    second_levels,
)

from .cache import load_step7_modules_cached, read_major_financing_upload
from .config import ANALYSIS_TAB_LABELS, STEP7_CONFIG_FILE
from .helpers import keep_valid_widget_state


def render_all_chart_navigation(chart_options: list[str]) -> None:
    """Render read-only chart rows with the same visual rhythm as sidebar radios."""
    if not chart_options:
        return
    rows = "".join(
        (
            '<div class="kpmg-nav-all-item">'
            '<span class="kpmg-nav-all-dot" aria-hidden="true"></span>'
            f"<span>{html.escape(str(name))}</span>"
            "</div>"
        )
        for name in chart_options
    )
    st.markdown(
        (
            '<div class="kpmg-nav-all-charts">'
            '<div class="kpmg-nav-all-label">具体图表（全部展示）</div>'
            f"{rows}</div>"
        ),
        unsafe_allow_html=True,
    )


def render_report_navigation(
    *,
    title: str,
    container_key: str,
    state_prefix: str,
    available_codes: list[str],
    print_key: str,
    industry: bool = False,
) -> None:
    with st.container(key=container_key):
        st.markdown(f"### {title}")

    first_options = (
        first_levels_for_codes(available_codes, industry=True) if industry else COMPANY_FIRST_LEVELS
    )
    level_one_key = f"{state_prefix}_level_one"
    keep_valid_widget_state(level_one_key, first_options)
    main_nav = st.radio("📁 一级模块", first_options, key=level_one_key)
    if main_nav == PRINT_ALL_LABEL:
        render_print_control(key=print_key)
        return

    navigation_level = OVERVIEW_LEVEL if industry else main_nav
    chart_key = f"{state_prefix}_chart"
    second_options = [
        "全部",
        *second_levels(
            navigation_level,
            industry=industry,
            available_codes=available_codes if industry else None,
        ),
    ]
    level_two_key = f"{state_prefix}_level_two"
    previous_level_one_key = f"_{state_prefix}_previous_level_one"
    if st.session_state.get(previous_level_one_key) != main_nav:
        st.session_state[previous_level_one_key] = main_nav
        st.session_state[level_two_key] = second_options[1] if len(second_options) > 1 else "全部"
        st.session_state.pop(chart_key, None)
    keep_valid_widget_state(level_two_key, second_options)
    second_nav = st.radio("📂 二级模块", second_options, key=level_two_key)
    chart_options = chart_names(
        navigation_level,
        second_nav,
        industry=industry,
        available_codes=available_codes if industry else None,
    )
    if second_nav == "全部":
        st.session_state.pop(chart_key, None)
        render_all_chart_navigation(chart_options)
    else:
        keep_valid_widget_state(chart_key, chart_options)
    if chart_options and second_nav != "全部":
        st.radio("具体图表", chart_options, key=chart_key)


def sync_analysis_navigation_from_tab() -> None:
    target = st.session_state.get("workflow_main_tab", "")
    if target in ANALYSIS_TAB_LABELS:
        st.session_state.analysis_page_navigation = target


def _reset_step7_config(profile_id: str) -> None:
    """Drop an uploaded override and fall back to the bundled template."""
    st.session_state.pop(f"step7_notes_bytes_{profile_id}", None)
    st.session_state[f"step7_config_source_{profile_id}"] = "default"
    st.session_state.pop("step7_modules", None)


def render_step7_excel_navigation(profile_id: str) -> None:
    """用默认 Step7 配置构建导航，上传文件仅作为可选覆盖。"""
    st.markdown("### 公司报告导航")

    uploaded = st.file_uploader(
        "STEP7 配置（默认使用内置模板，可上传覆盖）",
        type=["xlsx"],
        key=f"step7_notes_upload_{profile_id}",
        help="默认读取 templates/Step7-偿付能力公司报告展示模块.xlsx；上传文件只在当前会话覆盖默认配置。",
    )
    bytes_key = f"step7_notes_bytes_{profile_id}"
    source_key = f"step7_config_source_{profile_id}"
    if uploaded is not None:
        payload = uploaded.getvalue()
        if st.session_state.get(bytes_key) != payload:
            st.session_state[bytes_key] = payload
            st.session_state[source_key] = "upload"
            st.session_state.pop("step7_modules", None)

    source = st.session_state.get(source_key)
    if source is None:
        if st.session_state.get(bytes_key):
            source = "upload"
        elif st.session_state.get("step7_modules"):
            source = "session"
        elif STEP7_CONFIG_FILE.exists():
            source = "default"
        else:
            source = "builtin"

    modules = []
    if source == "upload":
        workbook_bytes = st.session_state.get(bytes_key)
        if workbook_bytes:
            modules = load_step7_modules_cached(workbook_bytes)
        elif STEP7_CONFIG_FILE.exists():
            source = "default"
        else:
            source = "builtin"
    if source == "default":
        if STEP7_CONFIG_FILE.exists():
            modules = load_step7_modules_cached(STEP7_CONFIG_FILE.read_bytes())
        else:
            source = "builtin"
    elif source == "session":
        modules = st.session_state.get("step7_modules") or []

    st.session_state[source_key] = source
    if source == "default":
        st.caption(f"默认配置：{STEP7_CONFIG_FILE.name}")
    elif source == "builtin":
        st.info("未找到默认 STEP7 配置，Step7 将使用内置 COMPANY_NAVIGATION 配置。")
        st.session_state["step7_modules"] = []
        return
    if source != "default" and STEP7_CONFIG_FILE.exists():
        st.button(
            "恢复默认配置",
            key=f"step7_reset_config_{profile_id}",
            on_click=_reset_step7_config,
            args=(profile_id,),
        )

    st.session_state["step7_modules"] = modules

    level_ones = list(dict.fromkeys(m.level_one for m in modules if m.level_one))
    first_options = [*level_ones, PRINT_ALL_LABEL]
    keep_valid_widget_state("company_nav_level_one", first_options)
    main_nav = st.radio("📁 一级模块", first_options, key="company_nav_level_one")
    if main_nav == PRINT_ALL_LABEL:
        render_print_control(key=f"company_report_print_{profile_id}")
        return

    sub_modules = [m for m in modules if m.level_one == main_nav]
    level_twos = list(dict.fromkeys(m.level_two for m in sub_modules if m.level_two))
    if level_twos:
        second_options = ["全部", *level_twos]
        keep_valid_widget_state("company_nav_level_two", second_options)
        second_nav = st.radio("📂 二级模块", second_options, key="company_nav_level_two")
        if second_nav != "全部":
            sub_modules = [m for m in sub_modules if m.level_two == second_nav]
    else:
        st.session_state["company_nav_level_two"] = "全部"

    # 二级分类即图表入口；不再额外展示“具体图表”单选。
    st.session_state.pop("company_nav_chart", None)


def render_major_financing_upload(profile_id: str) -> None:
    st.markdown("### 补充数据")
    financing_upload = st.file_uploader(
        "上传重大融资信息 Excel",
        type=["xlsx"],
        key=f"major_financing_upload_{profile_id}",
        help="应包含季度、公司名称、增资/发债及综合充足率变动字段。",
    )
    if financing_upload is None:
        return
    try:
        st.session_state.major_financing_data = read_major_financing_upload(
            financing_upload.getvalue(),
            financing_upload.name,
        )
        st.caption(f"已读取 {len(st.session_state.major_financing_data):,} 条融资事件。")
    except Exception as exc:
        st.error(f"重大融资信息读取失败：{exc}")
