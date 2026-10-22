"""Shared multi-PDF workspace helpers used by STEP1-STEP4."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd
import streamlit as st
from services.batch_workflow import sync_documents_from_uploads
from services.report_profiles import ReportProfile
from services.solvency_pdf_locator import PageMatch
from services.workflow_settings import apply_pending_settings_to_batch_documents


def ensure_batch_state() -> None:
    st.session_state.setdefault("batch_documents", {})
    st.session_state.setdefault("batch_active_company", "")


def batch_documents(active_profile: ReportProfile) -> dict[str, dict]:
    ensure_batch_state()
    documents = st.session_state.get("batch_documents", {})
    return {
        company: document
        for company, document in documents.items()
        if document.get("profile_id") == active_profile.profile_id
    }


def render_batch_uploader(
    active_profile: ReportProfile,
    *,
    ready: bool = True,
) -> dict[str, dict]:
    ensure_batch_state()
    uploaded = st.file_uploader(
        "一次选择多份 PDF（文件名将作为公司名）",
        type=["pdf"],
        accept_multiple_files=True,
        key=f"batch_shared_pdfs_{active_profile.profile_id}",
        disabled=not ready,
        help=(
            "上传后所有 PDF 共用同一文件池：STEP1 分别定位和核对页码，"
            "STEP2/3/4 自动处理全部文件。建议文件名使用公司简称。"
        ),
    )
    if uploaded:
        names = [Path(item.name).stem for item in uploaded]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            st.warning(
                "以下公司名重复，后上传文件将覆盖前一份：" + "、".join(duplicates),
                icon=":material/warning:",
            )
        st.session_state.batch_documents = sync_documents_from_uploads(
            st.session_state.get("batch_documents", {}),
            uploaded,
            profile_id=active_profile.profile_id,
        )
        pending = st.session_state.get("pending_batch_workflow_settings")
        if pending:
            applied = apply_pending_settings_to_batch_documents(
                st.session_state.batch_documents,
                pending,
            )
            if applied:
                st.session_state.pending_batch_workflow_settings = [
                    item
                    for item in pending
                    if str(item.get("company", "")) not in set(applied)
                ]
                st.success(
                    "已自动恢复以下公司的页码设置："
                    + "、".join(applied)
                )
    elif not batch_documents(active_profile):
        st.info("请选择两份及以上 PDF；只处理一份时仍可使用左侧单份流程。")
    return batch_documents(active_profile)


def selected_batch_document(
    active_profile: ReportProfile,
    documents: dict[str, dict],
    *,
    key_prefix: str,
) -> dict | None:
    if not documents:
        return None
    companies = list(documents)
    current = st.session_state.get("batch_active_company")
    if current not in companies:
        current = companies[0]
        st.session_state.batch_active_company = current
    selected = st.selectbox(
        "当前核对文件",
        companies,
        index=companies.index(current),
        key=f"batch_active_pdf_{key_prefix}_{active_profile.profile_id}",
    )
    st.session_state.batch_active_company = selected
    return documents[selected]


def batch_status_frame(documents: dict[str, dict]) -> pd.DataFrame:
    rows = []
    for company, document in documents.items():
        matches = document.get("matches") or []
        located = sum(bool(match.pages) for match in matches)
        conflicts = sum(bool(getattr(match, "review_required", False)) for match in matches)
        raw_tables = document.get("raw_tables") or []
        standard_data = document.get("standard_data")
        standard_rows = len(standard_data) if isinstance(standard_data, pd.DataFrame) else 0
        rows.append(
            {
                "公司": company,
                "PDF": document.get("pdf_name", ""),
                "页数": document.get("metadata", {}).get("页数", ""),
                "定位": document.get("locate_status", "") or "未执行",
                "定位逻辑": document.get("locate_method", "") or "—",
                "目标表": f"{located}/{len(matches)}" if matches else "—",
                "冲突": conflicts,
                "页码确认": "是" if document.get("pages_confirmed") else "否",
                "提取": document.get("extraction_status", "") or "未执行",
                "提取逻辑": document.get("extraction_method", "") or "—",
                "中间表": len(raw_tables),
                "标准化": document.get("standard_status", "") or "未执行",
                "标准记录": standard_rows,
                "说明": document.get("standard_note")
                or document.get("extraction_note")
                or document.get("locate_note", ""),
            }
        )
    return pd.DataFrame(rows)


def failed_batch_companies(
    documents: dict[str, dict],
    stage: str,
) -> list[str]:
    """Return company names that failed one pipeline stage."""
    failed: list[str] = []
    for company, document in documents.items():
        if stage == "locate":
            status = str(document.get("locate_status", "") or "")
            matches = document.get("matches") or []
            has_pages = any(bool(getattr(match, "pages", [])) for match in matches)
            bad = bool(status) and (
                status not in {"成功", "部分成功"} or not has_pages
            )
        elif stage == "extraction":
            status = str(document.get("extraction_status", "") or "")
            bad = bool(status) and (
                status != "成功" or not document.get("raw_tables")
            )
        elif stage == "standardization":
            status = str(document.get("standard_status", "") or "")
            bad = bool(status) and (
                status != "成功"
                or not isinstance(document.get("standard_data"), pd.DataFrame)
                or document["standard_data"].empty
            )
        else:
            bad = False
        if bad:
            failed.append(company)
    return failed


def render_batch_status(
    active_profile: ReportProfile,
    *,
    detail_key: str,
    show_details: bool = False,
) -> dict[str, dict]:
    documents = batch_documents(active_profile)
    if not documents:
        return documents
    confirmed = sum(bool(doc.get("pages_confirmed")) for doc in documents.values())
    extracted = sum(bool(doc.get("raw_tables")) for doc in documents.values())
    standardized = sum(
        isinstance(doc.get("standard_data"), pd.DataFrame)
        and not doc["standard_data"].empty
        for doc in documents.values()
    )
    cols = st.columns(4)
    cols[0].metric("文件", len(documents), border=True)
    cols[1].metric("页码已确认", confirmed, border=True)
    cols[2].metric("已提取", extracted, border=True)
    cols[3].metric("已标准化", standardized, border=True)
    render_batch_pipeline_progress(documents)
    if st.toggle(
        "显示逐公司明细",
        value=show_details,
        key=f"batch_status_details_{detail_key}_{active_profile.profile_id}",
    ):
        st.dataframe(
            batch_status_frame(documents),
            width="stretch",
            hide_index=True,
        )
    for stage, label in (
        ("locate", "定位"),
        ("extraction", "提取"),
        ("standardization", "标准化"),
    ):
        failed = failed_batch_companies(documents, stage)
        if failed:
            st.error(
                f"{label}未成功（{len(failed)} 家）：" + "、".join(failed)
            )
    return documents


def render_batch_pipeline_progress(documents: dict[str, dict]) -> None:
    """Persistent STEP1-STEP3 progress bar that also shows current logic."""
    total = len(documents)
    if not total:
        return
    located = sum(
        document.get("locate_status") in {"成功", "部分成功"}
        for document in documents.values()
    )
    confirmed = sum(
        bool(document.get("pages_confirmed"))
        for document in documents.values()
    )
    extracted = sum(
        bool(document.get("raw_tables"))
        for document in documents.values()
    )
    standardized = sum(
        isinstance(document.get("standard_data"), pd.DataFrame)
        and not document["standard_data"].empty
        for document in documents.values()
    )
    stages = (
        ("定位", located),
        ("页码确认", confirmed),
        ("提取", extracted),
        ("标准化", standardized),
    )
    overall = sum(done for _, done in stages) / (total * len(stages))
    st.progress(
        overall,
        text=(
            f"批处理总进度 {overall:.0%}　｜　"
            + "　".join(
                f"{label} {done}/{total}"
                for label, done in stages
            )
        ),
    )
    last_operation = str(
        st.session_state.get("batch_last_operation", "") or ""
    ).strip()
    if last_operation:
        st.caption(f"最近一次任务：{last_operation}")


def page_match_by_name(document: dict, table_name: str) -> PageMatch | None:
    return next(
        (
            match
            for match in document.get("matches", [])
            if match.table_name == table_name
        ),
        None,
    )


def replace_document(
    active_profile: ReportProfile,
    company: str,
    updated: dict,
) -> None:
    documents = dict(st.session_state.get("batch_documents", {}))
    documents[company] = updated
    st.session_state.batch_documents = documents


def clear_batch_workspace(active_profile: ReportProfile) -> None:
    documents = dict(st.session_state.get("batch_documents", {}))
    for company in [
        company
        for company, document in documents.items()
        if document.get("profile_id") == active_profile.profile_id
    ]:
        documents.pop(company, None)
    st.session_state.batch_documents = documents
    st.session_state.batch_active_company = ""
    st.session_state.pop(f"batch_shared_pdfs_{active_profile.profile_id}", None)


def uploaded_file_names(uploads: Iterable) -> list[str]:
    return [Path(str(getattr(item, "name", ""))).stem for item in uploads]
