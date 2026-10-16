"""STEP5 数据集成：标准窄表/外部宽表两种接入方式的转换预览与确认。"""

from __future__ import annotations

import pandas as pd
import streamlit as st
from services.report_profiles import ReportProfile
from services.solvency_dataset_adapter import (
    add_missing_derived_metrics,
    standard_workbook_bytes,
)
from services.solvency_normalizer import (
    STANDARD_COLUMNS,
    narrow_table_view,
    upgrade_standard_frame,
)

from .cache import (
    cached_complete_step5_metrics,
    cached_convert_external_workbook,
    cached_read_standard_workbook,
)


def _preview_standard_uploads(
    active_profile: ReportProfile, uploads: list
) -> None:
    """STEP5：读取标准窄表并生成集成预览。"""
    try:
        frames = []
        summaries = []
        skipped_profiles: set[str] = set()
        for upload in uploads:
            standardized = cached_read_standard_workbook(
                upload.getvalue(), upload.name
            )
            standardized["报告类型"] = (
                standardized["报告类型"]
                .fillna("")
                .astype(str)
                .str.strip()
                .replace("", active_profile.profile_id)
            )
            mismatched = set(
                standardized.loc[
                    standardized["报告类型"] != active_profile.profile_id,
                    "报告类型",
                ].astype(str)
            )
            skipped_profiles.update(mismatched)
            standardized = standardized[
                standardized["报告类型"] == active_profile.profile_id
            ]
            if not standardized.empty:
                frames.append(standardized)
            summaries.append({
                "来源文件": upload.name,
                "数据类型": "标准窄表",
                "记录数": len(standardized),
            })
        integration_preview = (
            pd.concat(frames, ignore_index=True)
            if frames
            else pd.DataFrame(columns=STANDARD_COLUMNS)
        )
        # 无论寿险还是财险，预览时都计算派生指标
        st.session_state.integration_preview = cached_complete_step5_metrics(integration_preview)
        st.session_state.integration_sheet_summary = pd.DataFrame(summaries)
        st.session_state.integration_mapping_summary = pd.DataFrame()
        st.session_state.integration_logic_checks = pd.DataFrame()
        st.session_state.integration_warnings = (
            ["已跳过其他报告类型的数据：" + "、".join(sorted(skipped_profiles))]
            if skipped_profiles
            else []
        )
        st.session_state.integration_preview_mode = "标准窄表"
    except Exception as exc:
        st.error(f"标准窄表读取失败：{exc}")
def _preview_external_uploads(
    active_profile: ReportProfile,
    companies: pd.DataFrame,
    taxonomy: pd.DataFrame,
    uploads: list,
) -> None:
    """STEP5：外部宽表精确映射并生成集成预览。"""
    try:
        company_type_map = dict(zip(companies["公司"], companies["公司类别"]))
        company_type_items = tuple(sorted(company_type_map.items()))
        with st.spinner(f"正在转换 {len(uploads)} 个宽表文件…"):
            converted = [
                cached_convert_external_workbook(
                    upload.getvalue(),
                    upload.name,
                    taxonomy,
                    company_type_items,
                    active_profile.profile_id,
                )
                for upload in uploads
            ]
        integration_preview = pd.concat(
            [item.data for item in converted], ignore_index=True
        )
        st.session_state.integration_preview = cached_complete_step5_metrics(
            integration_preview
        )
        st.session_state.integration_sheet_summary = pd.concat(
            [item.sheet_summary for item in converted], ignore_index=True
        )
        st.session_state.integration_mapping_summary = pd.concat(
            [item.mapping_summary for item in converted], ignore_index=True
        ).drop_duplicates(subset=["来源字段", "指标编码"], keep="last")
        st.session_state.integration_logic_checks = pd.concat(
            [item.logic_checks for item in converted], ignore_index=True
        )
        st.session_state.integration_warnings = [
            warning for item in converted for warning in item.warnings
        ]
        st.session_state.integration_preview_mode = "外部宽表转窄表"
    except Exception as exc:
        st.error(f"外部宽表转换失败：{exc}")
def render_integration_tab(
    active_profile: ReportProfile,
    companies: pd.DataFrame,
    taxonomy: pd.DataFrame,
) -> str:
    """STEP5：数据接入与集成前预览；返回当前接入方式。"""
    st.subheader("多公司、多期间数据集成")
    st.caption(
        "两种接入方式互斥；确认后将以本次预览结果作为后续 "
        f"STEP6-STEP8 的 {active_profile.profile_id} 集成数据。"
    )
    integration_mode = st.segmented_control(
        "数据接入方式",
        ["标准窄表", "外部宽表转窄表"],
        default="标准窄表",
        key="integration_mode",
        width="stretch",
    )
    if integration_mode == "标准窄表":
        uploads = st.file_uploader(
            "上传一个或多个标准窄表文件",
            type=["xlsx"],
            accept_multiple_files=True,
            key="integrated_standard_uploads",
        )
        if uploads and st.button(
            "读取并预览标准窄表", type="primary", key="preview_standard_data"
        ):
            _preview_standard_uploads(active_profile, uploads)
    else:
        if active_profile.profile_id != "LIFE_SOLVENCY":
            st.info("当前外部 CROSS 宽表映射仅适用于寿险偿付能力 Profile。")
            uploads = []
        else:
            uploads = st.file_uploader(
                "上传一个或多个外部宽表文件",
                type=["xlsx"],
                accept_multiple_files=True,
                key="integrated_external_uploads",
            )
            st.caption(
                "转换时将同步生成“行业合计”窄表记录：金额指标按有效公司求和，"
                "比率和倍数按行业合计分子、分母重新计算；行业风险构成采用底稿中的展示名称。"
            )
        if uploads and st.button(
            "精确映射并转换预览", type="primary", key="preview_external_data"
        ):
            _preview_external_uploads(active_profile, companies, taxonomy, uploads)
    return integration_mode
def render_step5_review(
    preview: pd.DataFrame,
    integration_mode: str,
    step5_is_open: bool,
) -> None:
    """STEP5：集成前预览确认与已确认集成数据展示。"""
    identity_columns = {"原始公司名称", "标准公司名称", "公司统一编码"}
    if step5_is_open and not identity_columns.issubset(preview.columns):
        preview = upgrade_standard_frame(preview)
        st.session_state.integration_preview = preview
    if (
        step5_is_open
        and not preview.empty
        and st.session_state.integration_preview_mode == integration_mode
    ):
            st.markdown("#### 集成前预览")
            identity_changes = preview.loc[
                preview["原始公司名称"].astype(str).str.strip()
                != preview["标准公司名称"].astype(str).str.strip(),
                ["原始公司名称", "标准公司名称", "公司统一编码", "公司类型"],
            ].drop_duplicates()
            if not identity_changes.empty:
                st.info(
                    f"已识别 {len(identity_changes):,} 组历史公司名称，"
                    "后续跨期分析将按标准公司名称和统一编码归集。"
                )
                with st.expander("查看公司历史名称映射"):
                    st.dataframe(identity_changes, width="stretch", hide_index=True)
            if not st.session_state.integration_sheet_summary.empty:
                st.dataframe(
                    st.session_state.integration_sheet_summary,
                    width="stretch",
                    hide_index=True,
                )
            for warning in st.session_state.integration_warnings:
                st.warning(warning)
            if not st.session_state.integration_mapping_summary.empty:
                with st.expander("查看指标精确映射"):
                    st.dataframe(
                        st.session_state.integration_mapping_summary,
                        width="stretch",
                        hide_index=True,
                    )
            if not st.session_state.integration_logic_checks.empty:
                with st.expander("查看派生指标逻辑校验", expanded=True):
                    st.dataframe(
                        st.session_state.integration_logic_checks,
                        width="stretch",
                        hide_index=True,
                    )
            st.caption(
                f"转换后共 {len(preview):,} 条窄表记录。确认后将替换当前集成数据，"
                "不与其他接入方式并行合并。"
            )
            st.dataframe(
                narrow_table_view(preview).head(1000),
                width="stretch",
                hide_index=True,
            )
            if st.button(
                "确认采用该数据集",
                type="primary",
                key="confirm_integrated_data",
            ):
                # 确认时强制补全派生指标，确保集成数据包含所有派生行
                enriched_data = add_missing_derived_metrics(preview)
                st.session_state.integrated_data = enriched_data.copy()
                st.success("已更新集成数据（含派生指标），STEP6-STEP8 将使用本次确认的数据集。")
    if step5_is_open and not st.session_state.integrated_data.empty:
        st.markdown("#### 当前已确认集成数据")
        display_limit = 1000
        integrated_data = st.session_state.integrated_data
        st.caption(
            f"当前共 {len(integrated_data):,} 条记录；页面仅展示前 "
            f"{min(display_limit, len(integrated_data)):,} 条，下载文件仍包含全部记录。"
        )
        integrated_narrow_display = narrow_table_view(integrated_data)
        st.dataframe(
            integrated_narrow_display.head(display_limit),
            width="stretch",
            hide_index=True,
        )
        st.download_button(
            "下载行业集成数据",
            standard_workbook_bytes(integrated_data),
            "偿付能力行业集成数据.xlsx",
        )
