"""应用入口：只负责登录门禁、全局样式与各工作流页面的编排。

各 STEP 的具体实现见 ui/ 包下的对应模块；本文件保持精简，方便快速定位。
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from ui.auth import initialize_login_state, logout, render_login_page
from ui.cache import (
    prepare_visualization_source,
    profile_registry_token,
    read_profile_registry,
)
from ui.config import ANALYSIS_TAB_LABELS, PROJECT_MEMBER_ROLE, WORKFLOW_TAB_LABELS
from ui.navigation import sync_analysis_navigation_from_tab
from ui.profile_settings import (
    render_page_heading,
    render_profile_settings,
    resolve_profile_paths,
    resolve_profile_taxonomy,
)
from ui.report_pages import (
    render_company_report_workspace,
    render_industry_report_workspace,
)
from ui.session import initialize_state
from ui.settings_transfer import render_workflow_settings_transfer
from ui.step0_monitor import load_monitor_companies, render_monitor_tab
from ui.step1_locator import render_locator_tab
from ui.step2_extraction import render_extraction_tab
from ui.step3_standardization import render_standardization_tab
from ui.step4_validation import render_validation_tab
from ui.step5_integration import render_step5_review, render_integration_tab
from ui.step6_analysis import render_analysis_sidebar, render_analysis_tab
from ui.theme import render_analysis_tab_styles, render_app_styles


st.set_page_config(page_title="保险公司报告分析处理平台", page_icon="🛡️", layout="wide")


def main() -> None:
    """应用入口：登录门禁 + 全流程页面编排。"""
    initialize_login_state()
    if not st.session_state.logged_in:
        render_login_page()
        st.stop()

    render_app_styles()
    initialize_state()
    is_project_member = st.session_state.user_role == PROJECT_MEMBER_ROLE
    with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
        st.caption(
            f"当前身份：{st.session_state.user_role}　｜　"
            f"AI：{st.session_state.get('llm_provider', '自定义节点')} / "
            f"{st.session_state.get('llm_model', '') or '未配置'}"
        )
        st.button(
            "退出登录",
            icon=":material/logout:",
            key="logout",
            on_click=logout,
            width="content",
        )

    if not is_project_member:
        st.info(
            "普通用户通道仅开放 STEP6 自定义分析与 STEP7 公司报告。",
            icon=":material/lock:",
        )
    profiles = read_profile_registry(profile_registry_token())
    active_profile = render_profile_settings(profiles, is_project_member)
    taxonomy_path, rules_path = resolve_profile_paths(active_profile)
    render_page_heading(active_profile)
    visible_workflow_labels = (
        WORKFLOW_TAB_LABELS
        if is_project_member
        else WORKFLOW_TAB_LABELS[6:8]
    )
    requested_workflow_tab = st.session_state.get(
        "workflow_main_tab",
        visible_workflow_labels[0],
    )
    visible_tabs = st.tabs(
        visible_workflow_labels,
        default=visible_workflow_labels[0],
        key="workflow_main_tab",
        on_change=sync_analysis_navigation_from_tab,
    )
    tabs = (
        {index: tab for index, tab in enumerate(visible_tabs)}
        if is_project_member
        else {6: visible_tabs[0], 7: visible_tabs[1]}
    )
    active_workflow_tab = st.session_state.get(
        "workflow_main_tab",
        requested_workflow_tab,
    )
    if requested_workflow_tab in visible_workflow_labels:
        active_workflow_tab = requested_workflow_tab
    analysis_tabs_active = active_workflow_tab in ANALYSIS_TAB_LABELS
    if analysis_tabs_active:
        render_analysis_tab_styles()
    render_workflow_settings_transfer(active_profile, active_workflow_tab)

    # Streamlit 的 st.tabs 会执行所有页签内容。批量上传 89 份 PDF 时，
    # 让未激活的 STEP 也反复读取文件和处理数据会造成明显卡顿，因此只在
    # 当前页签真正激活时渲染对应工作流。
    if is_project_member:
        if active_workflow_tab == WORKFLOW_TAB_LABELS[0]:
            with tabs[0]:
                render_monitor_tab(active_profile)
        elif active_workflow_tab == WORKFLOW_TAB_LABELS[1]:
            with tabs[1]:
                render_locator_tab(active_profile)
        elif active_workflow_tab == WORKFLOW_TAB_LABELS[2]:
            with tabs[2]:
                render_extraction_tab(active_profile)
        elif active_workflow_tab == WORKFLOW_TAB_LABELS[3]:
            taxonomy = resolve_profile_taxonomy(active_profile, taxonomy_path)
            with tabs[3]:
                render_standardization_tab(active_profile, taxonomy, is_project_member)
        elif active_workflow_tab == WORKFLOW_TAB_LABELS[4]:
            taxonomy = resolve_profile_taxonomy(active_profile, taxonomy_path)
            with tabs[4]:
                render_validation_tab(active_profile, rules_path, taxonomy)
        elif active_workflow_tab == WORKFLOW_TAB_LABELS[5]:
            taxonomy = resolve_profile_taxonomy(active_profile, taxonomy_path)
            with tabs[5]:
                integration_mode = render_integration_tab(
                    active_profile,
                    load_monitor_companies(),
                    taxonomy,
                )
            render_step5_review(
                st.session_state.integration_preview,
                integration_mode,
                bool(tabs[5].open),
            )

    analysis_source = pd.DataFrame()
    if active_workflow_tab in (
        WORKFLOW_TAB_LABELS[6],
        WORKFLOW_TAB_LABELS[7],
        WORKFLOW_TAB_LABELS[8],
    ):
        raw_analysis_source = (
            st.session_state.integrated_data
            if not st.session_state.integrated_data.empty
            else st.session_state.standard_data
        )
        analysis_source, _ = prepare_visualization_source(
            raw_analysis_source,
            active_profile.profile_id,
        )

    if active_workflow_tab == WORKFLOW_TAB_LABELS[6]:
        with tabs[6]:
            render_analysis_tab(
                active_profile,
                is_project_member,
                analysis_tabs_active,
                active_workflow_tab,
            )
    elif active_workflow_tab == WORKFLOW_TAB_LABELS[7]:
        render_analysis_sidebar(
            active_profile,
            is_project_member,
            analysis_source,
        )
        with tabs[7]:
            company_report_source = st.session_state.step6_analysis_source
            if company_report_source.empty:
                company_report_source = analysis_source
            render_company_report_workspace(company_report_source)
    elif (
        is_project_member
        and active_workflow_tab == WORKFLOW_TAB_LABELS[8]
    ):
        render_analysis_sidebar(
            active_profile,
            is_project_member,
            analysis_source,
        )
        with tabs[8]:
            render_industry_report_workspace(analysis_source)


if __name__ == "__main__":
    main()
