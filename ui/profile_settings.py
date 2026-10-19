"""Profile 设置区：切换报告类型、上传高级配置、下载核对与路径解析。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st
from services.report_profiles import ProfileValidationError, ReportProfile
from services.solvency_metric_registry import extend_taxonomy

from .cache import (
    export_profile_workbook,
    read_profile_registry,
    read_taxonomy,
    read_uploaded_profile,
)
from .config import CONFIG_DIR, NON_LIFE_PROFILE_FILE, ROOT
from .session import reset_profile_results


def render_profile_settings(
    profiles: dict[str, ReportProfile], is_project_member: bool
) -> ReportProfile:
    """Profile 设置区：切换报告类型、上传高级配置、下载或核对。"""
    with st.expander(
        "报告 Profile 设置",
        expanded=False,
        icon=":material/tune:",
    ):
        profile_settings_tab_labels = (
            "切换报告类型",
            "上传高级配置",
            "下载或核对",
        )
        if is_project_member:
            profile_switch_tab, profile_upload_tab, profile_review_tab = st.tabs(
                profile_settings_tab_labels,
                default=profile_settings_tab_labels[0],
                key="profile_settings_tabs",
            )
        else:
            profile_switch_tab = st.container()

        if is_project_member:
            with profile_upload_tab:
                st.caption(
                    "目标表和定位关键词可以通过配置工作簿维护。上传后先进行结构与引用校验，"
                    "仅在当前浏览器会话中生效，不会覆盖项目内的正式配置。"
                )
                try:
                    default_profile_label = NON_LIFE_PROFILE_FILE.relative_to(ROOT)
                except ValueError:
                    default_profile_label = NON_LIFE_PROFILE_FILE
                st.caption(
                    f"默认本地 Profile：`{default_profile_label}`。"
                    "修改该文件后会自动失效并重新读取；也可点击下方按钮立即刷新。"
                )
                if not NON_LIFE_PROFILE_FILE.exists():
                    st.warning(
                        f"默认 Profile 文件不存在：{NON_LIFE_PROFILE_FILE}",
                        icon=":material/warning:",
                    )
                if st.button(
                    "立即刷新本地 Profile 配置",
                    key="refresh_local_profile",
                    icon=":material/refresh:",
                ):
                    read_profile_registry.clear()
                    st.rerun()
                uploaded_profile_file = st.file_uploader(
                    "上传报告 Profile 配置工作簿",
                    type=["xlsx"],
                    key="report_profile_upload",
                )
                if uploaded_profile_file is not None:
                    uploaded_bytes = uploaded_profile_file.getvalue()
                    try:
                        uploaded_profile = read_uploaded_profile(
                            uploaded_bytes,
                            uploaded_profile_file.name,
                        )
                        profiles[uploaded_profile.profile_id] = uploaded_profile
                        st.success(
                            f"配置校验通过：{uploaded_profile.profile_name}，"
                            f"共 {len(uploaded_profile.tables)} 张目标表。"
                        )
                    except ProfileValidationError as exc:
                        st.error(f"配置工作簿未启用：{exc}")

        profile_ids = list(profiles)
        if st.session_state.active_profile_id not in profile_ids:
            st.session_state.active_profile_id = profile_ids[0]

        with profile_switch_tab:
            st.segmented_control(
                "当前报告类型",
                profile_ids,
                format_func=lambda profile_id: profiles[profile_id].profile_name,
                selection_mode="single",
                required=True,
                key="active_profile_id",
                on_change=reset_profile_results,
                width="stretch",
                help="寿险与财险使用相互隔离的公司范围、目标表、关键词和比较数据。",
            )
            st.caption(
                "切换后会清空当前未保存的 PDF、页码、提取表格和标准化结果，"
                "防止不同 Profile 的数据混用。"
            )

        active_profile = profiles[st.session_state.active_profile_id]
        if st.session_state.active_profile_runtime != active_profile.runtime_version:
            st.session_state.active_profile_runtime = active_profile.runtime_version
            st.session_state.locator_config_version = ""
            st.session_state.monitor_results = pd.DataFrame()
            st.session_state.monitor_single_result = None
            st.session_state.pop("solvency_target_tables", None)

        if is_project_member:
            with profile_review_tab:
                st.caption(
                    f"Profile：{active_profile.config_version}　|　"
                    f"工作簿架构：v{active_profile.workbook_schema_version}　|　"
                    f"比较范围：同一 profile 内跨公司、跨期　|　"
                    f"目标表：{len(active_profile.tables)} 张"
                )
                st.caption(
                    f"v2 配置：版式变体 {len(active_profile.layout_variants)} 条，"
                    f"字段字典 {len(active_profile.field_dictionary)} 条，"
                    f"完整性规则 {len(active_profile.completeness_rules)} 条，"
                    f"公司来源 {len(active_profile.companies)} 家，"
                    f"Gold 样本 {len(active_profile.gold_samples)} 条。"
                    "旧版三表工作簿仍可上传。"
                )
                st.dataframe(
                    pd.DataFrame(active_profile.tables).reindex(
                        columns=["table_id", "table_name", "max_pages"],
                    ),
                    width="stretch",
                    hide_index=True,
                )
                st.download_button(
                    "下载当前 Profile 配置工作簿",
                    export_profile_workbook(active_profile),
                    f"{active_profile.profile_id}_profile.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    width="stretch",
                )
    return active_profile


def resolve_profile_paths(active_profile: ReportProfile) -> tuple[Path | None, Path]:
    """解析当前 Profile 的指标字典与勾稽规则文件路径。"""
    taxonomy_path = (
        active_profile.resource_path("normalization", "taxonomy_file")
        if not active_profile.field_dictionary
        else None
    )

    # ★ 财险强制使用本地完整的 solvency_taxonomy.xlsx（内嵌字典只有 16 条，缺 S05 指标）
    if active_profile.profile_id == "NON_LIFE_SOLVENCY":
        taxonomy_path = CONFIG_DIR / "solvency_taxonomy.xlsx"

    rules_path = active_profile.resource_path("validation", "validation_rules_file")
    return taxonomy_path, rules_path


def resolve_profile_taxonomy(
    active_profile: ReportProfile,
    taxonomy_path: Path | None,
) -> pd.DataFrame:
    """按 Profile 解析指标字典；寿险额外补齐派生指标。"""
    if active_profile.profile_id == "NON_LIFE_SOLVENCY":
        # ★ 财险强制使用本地完整的 solvency_taxonomy.xlsx
        taxonomy = read_taxonomy(str(taxonomy_path))
    else:
        taxonomy = (
            active_profile.taxonomy_frame()
            if active_profile.field_dictionary
            else read_taxonomy(str(taxonomy_path))
        )
    if active_profile.profile_id == "LIFE_SOLVENCY":
        taxonomy = extend_taxonomy(taxonomy)
    return taxonomy


def render_page_heading(active_profile: ReportProfile) -> None:
    """页面主标题与当前 Profile 说明。"""
    st.markdown(
        (
            '<section class="platform-page-heading">'
            "<h1>保险报告处理与分析平台</h1>"
            '<p class="platform-profile-caption">'
            f"当前 profile：{active_profile.profile_name}（{active_profile.profile_id}）｜"
            "支持同一报告类型内跨公司、跨期比较"
            "</p></section>"
        ),
        unsafe_allow_html=True,
    )
