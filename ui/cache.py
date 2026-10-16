"""统一的 Streamlit 缓存层：工作簿/字典/规则读取、PDF 渲染与导出行数据。"""

from __future__ import annotations

import io
from pathlib import Path

import fitz
import pandas as pd
import streamlit as st
from services.report_profiles import (
    ReportProfile,
    load_profile_registry,
    load_profile_workbook,
    profile_workbook_bytes,
)
from services.solvency_dataset_adapter import (
    add_missing_derived_metrics,
    convert_external_workbook,
    read_standard_workbook,
)
from services.solvency_company_identity import load_peer_group_config
from services.solvency_step3_standardizer import target_template_workbook_bytes
from services.solvency_step6_analysis import prepare_analysis_frame
from services.solvency_financing_analysis import read_major_financing_workbook
from services.step7_excel_config import load_step7_config
from services.solvency_gold_standard import load_gold_manifest
from services.solvency_normalizer import load_taxonomy
from services.solvency_pdf_locator import analyze_document_type
from services.solvency_validator import load_validation_rules, validation_status_summary

from .config import GOLD_MANIFEST, PROFILE_DIR, ROOT


@st.cache_data(show_spinner=False)
def read_profile_registry() -> dict[str, ReportProfile]:
    return load_profile_registry(PROFILE_DIR, ROOT)


@st.cache_data(show_spinner=False)
def read_peer_groups(
    path: Path,
    modified_ns: int,
) -> tuple[dict[str, str], str]:
    del modified_ns
    return load_peer_group_config(path)


@st.cache_data(show_spinner=False)
def read_uploaded_profile(
    workbook_bytes: bytes,
    source_name: str,
) -> ReportProfile:
    return load_profile_workbook(
        workbook_bytes,
        project_root=ROOT,
        source_name=source_name,
    )


@st.cache_data(show_spinner=False)
def export_profile_workbook(profile: ReportProfile) -> bytes:
    return profile_workbook_bytes(profile)


@st.cache_data(show_spinner=False)
def prepare_visualization_source(
    frame: pd.DataFrame,
    report_profile_id: str,
) -> tuple[pd.DataFrame, tuple[str, ...]]:
    enriched = add_missing_derived_metrics(frame) if report_profile_id == "LIFE_SOLVENCY" else frame
    return prepare_analysis_frame(enriched, report_profile_id)


@st.cache_data(show_spinner=False)
def read_step6_analysis_workbook(
    workbook_bytes: bytes,
    source_name: str,
    report_profile_id: str,
) -> tuple[pd.DataFrame, tuple[str, ...]]:
    frame = read_standard_workbook(workbook_bytes, source_name)
    return prepare_visualization_source(frame, report_profile_id)


@st.cache_data(show_spinner=False, max_entries=8)
def read_major_financing_upload(
    workbook_bytes: bytes,
    source_name: str,
) -> pd.DataFrame:
    return read_major_financing_workbook(workbook_bytes, source_name)


@st.cache_data(show_spinner=False)
def read_taxonomy(path: str) -> pd.DataFrame:
    return load_taxonomy(path)


@st.cache_data(show_spinner=False)
def read_rules(path: str) -> pd.DataFrame:
    return load_validation_rules(path)


@st.cache_data(show_spinner=False)
def read_gold_manifest() -> dict:
    return load_gold_manifest(GOLD_MANIFEST) if GOLD_MANIFEST.exists() else {"cases": []}


@st.cache_data(show_spinner=False)
def cached_analyze_document_type(pdf_bytes: bytes):
    """Classify the uploaded PDF once per file; safe to cache on bytes."""
    return analyze_document_type(pdf_bytes)


@st.cache_data(show_spinner=False)
def render_pdf_page(pdf_bytes: bytes, page_number: int, zoom: float = 1.8) -> bytes:
    """将指定物理页渲染为PNG，用于人工核实。"""
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        if page_number < 1 or page_number > len(document):
            raise ValueError(f"页码 {page_number} 超出文档范围。")
        page = document.load_page(page_number - 1)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return pixmap.tobytes("png")
    finally:
        document.close()


@st.cache_data(show_spinner=False, max_entries=8)
def dataframe_to_xlsx(frame: pd.DataFrame, sheet_name: str) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name=sheet_name, index=False)
    return output.getvalue()


@st.cache_data(show_spinner=False, max_entries=12)
def cached_convert_external_workbook(
    workbook_bytes: bytes,
    filename: str,
    taxonomy: pd.DataFrame,
    company_type_items: tuple[tuple[str, str], ...],
    report_profile_id: str,
):
    return convert_external_workbook(
        workbook_bytes,
        filename,
        taxonomy,
        dict(company_type_items),
        report_profile_id=report_profile_id,
    )


@st.cache_data(show_spinner=False, max_entries=12)
def cached_read_standard_workbook(
    workbook_bytes: bytes,
    filename: str,
) -> pd.DataFrame:
    return read_standard_workbook(workbook_bytes, filename)


@st.cache_data(show_spinner=False, max_entries=8)
def cached_complete_step5_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    return add_missing_derived_metrics(frame)


@st.cache_data(show_spinner=False, max_entries=8)
def validation_results_to_xlsx(results: pd.DataFrame) -> bytes:
    summary = validation_status_summary(results)
    category_summary = (
        results.groupby(["check_type", "status"], dropna=False)
        .size()
        .rename("数量")
        .reset_index()
        .rename(columns={"check_type": "检查类别", "status": "状态"})
    )
    issues = results[~results["status"].isin(["通过", "不适用"])].copy()
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="状态汇总", index=False)
        category_summary.to_excel(writer, sheet_name="分类汇总", index=False)
        results.to_excel(writer, sheet_name="检查明细", index=False)
        issues.to_excel(writer, sheet_name="待处理事项", index=False)
    return output.getvalue()


@st.cache_data(show_spinner=False, max_entries=8)
def cached_step3_target_template(
    target_catalog: pd.DataFrame,
    profile_name: str,
) -> bytes:
    return target_template_workbook_bytes(target_catalog, profile_name)


@st.cache_data(show_spinner=False)
def load_step7_modules_cached(workbook_bytes: bytes):
    return load_step7_config(io.BytesIO(workbook_bytes))
