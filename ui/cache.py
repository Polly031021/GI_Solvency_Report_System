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
from services.solvency_company_identity import (
    CompanyMaster,
    load_company_master_workbook,
    load_peer_group_config,
)
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
def read_profile_registry(registry_token: str = "") -> dict[str, ReportProfile]:
    del registry_token
    return load_profile_registry(PROFILE_DIR, ROOT)


def profile_registry_token() -> str:
    """Profile 工作簿指纹：文件修改后自动触发缓存失效。"""
    parts: list[str] = []
    for path in sorted(PROFILE_DIR.glob("*_profile.xlsx")):
        if path.name.startswith("~$"):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        parts.append(f"{path.name}:{stat.st_mtime_ns}:{stat.st_size}")
    return "|".join(parts)


@st.cache_data(show_spinner=False)
def read_peer_groups(
    path: Path,
    modified_ns: int,
) -> tuple[dict[str, str], str]:
    del modified_ns
    return load_peer_group_config(path)


@st.cache_data(show_spinner=False)
def read_company_master(
    path: Path,
    modified_ns: int,
    alias_path: Path | None = None,
    alias_modified_ns: int = 0,
) -> CompanyMaster:
    del modified_ns, alias_modified_ns
    return load_company_master_workbook(
        path,
        alias_workbook=alias_path,
    )


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
def render_pdf_page(
    pdf_bytes: bytes,
    page_number: int,
    zoom: float = 1.8,
    highlight: tuple[float, float, float, float] | None = None,
) -> bytes:
    """将指定物理页渲染为PNG，用于人工核实。

    ``highlight`` 为 (x0, top, x1, bottom)（PDF 点）：会画上红框，用于直观确认
    一页多表时系统实际提取的是哪一块区域。
    """
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        if page_number < 1 or page_number > len(document):
            raise ValueError(f"页码 {page_number} 超出文档范围。")
        page = document.load_page(page_number - 1)
        if highlight:
            try:
                page.draw_rect(
                    fitz.Rect(*[float(value) for value in highlight]),
                    color=(0.92, 0.11, 0.18),
                    width=2,
                )
            except Exception:
                pass
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
def validation_results_to_xlsx(
    results: pd.DataFrame,
    evidence: pd.DataFrame | None = None,
    standard_data: pd.DataFrame | None = None,
) -> bytes:
    summary = validation_status_summary(results)
    category_summary = (
        results.groupby(["check_type", "status"], dropna=False)
        .size()
        .rename("数量")
        .reset_index()
        .rename(columns={"check_type": "检查类别", "status": "状态"})
    )
    issues = results[~results["status"].isin(["通过", "不适用"])].copy()
    result_labels = {
        "result_id": "结果编号",
        "check_type": "检查类别",
        "severity": "严重程度",
        "rule_id": "规则ID",
        "rule_name": "规则名称",
        "company": "公司",
        "report_period": "报告期",
        "period": "期间组",
        "actual": "披露值/实际数",
        "expected": "系统计算值/应有数",
        "difference": "差异",
        "tolerance": "容差",
        "tolerance_unit": "容差单位",
        "status": "状态",
        "failure_reason": "失败原因",
        "formula": "校验公式",
        "notes": "检查说明",
        "involved_metrics": "涉及指标编码",
        "source_pages": "来源页码",
        "suggestion": "处理建议",
        "evidence_count": "STEP3依据条数",
    }
    evidence_labels = {
        "result_id": "结果编号",
        "record_id": "STEP3记录号",
        "metric_code": "指标编码",
        "metric_name": "指标名称",
        "role": "检查角色",
        "period": "期间组",
        "status": "记录状态",
        "actual_value": "STEP3数值/披露值",
        "expected_value": "应有值/标准值",
        "unit": "单位",
        "source_file": "来源文件",
        "source_sheet": "来源工作表",
        "source_page": "来源页码",
        "message": "问题说明",
    }

    def _visible(frame: pd.DataFrame, labels: dict[str, str]) -> pd.DataFrame:
        columns = [column for column in labels if column in frame.columns]
        return frame[columns].rename(columns=labels)

    detail_frame = _visible(results, result_labels)
    issue_frame = _visible(issues, result_labels)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="状态汇总", index=False)
        category_summary.to_excel(writer, sheet_name="分类汇总", index=False)
        detail_frame.to_excel(writer, sheet_name="检查明细", index=False)
        issue_frame.to_excel(writer, sheet_name="待处理事项", index=False)

        issue_evidence = evidence
        if (
            isinstance(evidence, pd.DataFrame)
            and not evidence.empty
            and "result_id" in evidence.columns
            and "result_id" in issues.columns
        ):
            issue_ids = set(issues["result_id"].astype(str))
            issue_evidence = evidence[
                evidence["result_id"].astype(str).isin(issue_ids)
            ]
        if isinstance(issue_evidence, pd.DataFrame) and not issue_evidence.empty:
            _visible(issue_evidence, evidence_labels).to_excel(
                writer, sheet_name="失败原因与STEP3依据", index=False
            )
            if isinstance(standard_data, pd.DataFrame) and not standard_data.empty:
                record_ids: list[int] = []
                for value in issue_evidence.get(
                    "record_id", pd.Series(dtype=object)
                ).tolist():
                    try:
                        record_id = int(float(value))
                    except (TypeError, ValueError):
                        continue
                    if 1 <= record_id <= len(standard_data):
                        record_ids.append(record_id)
                if record_ids:
                    record_ids = list(dict.fromkeys(record_ids))
                    related = standard_data.iloc[[value - 1 for value in record_ids]].copy()
                    related.insert(0, "STEP3记录号", record_ids)
                    related_columns = [
                        column
                        for column in (
                            "STEP3记录号", "公司", "报告期", "指标编码", "指标名称",
                            "期间口径", "数值", "单位", "来源工作表", "来源页码",
                            "指标属性", "来源类型", "备注", "计算逻辑", "来源文件",
                        )
                        if column in related.columns
                    ]
                    related[related_columns].to_excel(
                        writer, sheet_name="STEP3相关记录", index=False
                    )
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
