"""Export/import workflow settings across STEP0-STEP8."""

from __future__ import annotations

import hashlib

import streamlit as st
from services.report_profiles import ReportProfile
from services.workflow_settings import (
    apply_workflow_settings_bundle,
    build_workflow_settings_bundle,
    bundle_from_json_bytes,
    bundle_to_json_bytes,
    count_workflow_settings,
)


def _build_export_payload(
    state_snapshot: dict,
    profile_id: str,
    scope: str,
) -> bytes:
    """Build the bundle only when the download is actually requested.

    Serializing a batch workspace hashes every PDF and encodes every page
    match. Doing that on each Streamlit rerun made STEP1 feel frozen after a
    large upload, so the download button receives this callable instead of
    pre-built bytes.
    """
    bundle = build_workflow_settings_bundle(
        state_snapshot,
        profile_id=profile_id,
        scope=scope,
    )
    return bundle_to_json_bytes(bundle)


def _render_import_result() -> None:
    """Show the result of the last import once, after the rerun it triggered."""
    summary = st.session_state.pop("workflow_settings_import_result", None)
    if not isinstance(summary, dict):
        return
    st.success(
        f"已恢复 {summary.get('applied_settings', 0)} 个设置项，"
        f"已绑定 {summary.get('applied_documents', 0)} 家公司的页码配置。"
    )
    if not summary.get("saved_page_targets"):
        st.warning(
            "这份配置文件里没有任何已保存的目标表页码（所有 matches 均为空）。"
            "上传 PDF 后仍需在 STEP1 重新定位；定位完成后再导出配置，页码才会被保存。"
        )
    if summary.get("pending_documents"):
        st.info(
            f"还有 {summary['pending_documents']} 家公司的 PDF 尚未上传；"
            "在 STEP1 上传同名 PDF 后会自动恢复对应页码。"
        )
    rejected = summary.get("rejected_documents") or []
    if rejected:
        st.warning(
            "以下文件与配置指纹不一致，未恢复页码：" + "、".join(rejected)
        )


def render_workflow_settings_transfer(
    active_profile: ReportProfile,
    active_workflow_tab: str,
) -> None:
    """Render a portable settings panel available on every workflow page."""
    # Render the result outside the collapsed expander so a just-imported
    # bundle always gives visible feedback after its rerun.
    _render_import_result()
    with st.expander(
        "工作流设置保存与恢复",
        expanded=False,
        icon=":material/save:",
    ):
        st.caption(
            "配置文件保存当前 Step 或全部 Step 的参数、公司 PDF 页码识别结果、"
            "表格提取方法与图表导航设置。PDF 正文不会嵌入配置文件；重新上传后"
            "按文件名、大小和 SHA256 自动绑定。"
        )
        scope_label = st.radio(
            "保存范围",
            ["当前 Step", "全部 Step"],
            horizontal=True,
            key=f"settings_export_scope_{active_profile.profile_id}",
        )
        scope = (
            active_workflow_tab
            if scope_label == "当前 Step"
            else "全部 Step"
        )
        # Snapshot the session cheaply; serialization is deferred to the click.
        state_snapshot = dict(st.session_state)
        document_count = len(state_snapshot.get("batch_documents") or {})
        setting_count = count_workflow_settings(state_snapshot, scope=scope)
        st.caption(
            f"当前配置范围：{scope}　｜　公司配置：{document_count} 家　｜　"
            f"设置项：{setting_count}"
        )
        st.download_button(
            "导出工作流设置",
            lambda: _build_export_payload(
                state_snapshot,
                active_profile.profile_id,
                scope,
            ),
            f"{active_profile.profile_id}_工作流设置.json",
            mime="application/json",
            icon=":material/download:",
            key=f"download_workflow_settings_{active_profile.profile_id}",
        )

        st.divider()
        uploaded = st.file_uploader(
            "上传工作流设置 JSON，恢复页码和图表配置",
            type=["json"],
            key=f"workflow_settings_upload_{active_profile.profile_id}",
        )
        if uploaded is None:
            return
        payload = uploaded.getvalue()
        digest = hashlib.sha256(payload).hexdigest()
        if st.session_state.get("workflow_settings_import_digest") == digest:
            return
        try:
            restored = bundle_from_json_bytes(payload)
            summary = apply_workflow_settings_bundle(
                restored,
                st.session_state,
                profile_id=active_profile.profile_id,
            )
        except Exception as exc:
            st.error(f"工作流设置读取失败：{exc}")
            return

        st.session_state.workflow_settings_import_digest = digest
        st.session_state.workflow_settings_import_result = summary
        st.rerun()
