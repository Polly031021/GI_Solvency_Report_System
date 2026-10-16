"""会话状态管理：默认值、初始化、切换 Profile 时的清理与 AI 配置校验。"""

from __future__ import annotations

from threading import Lock

import pandas as pd
import streamlit as st
from services.solvency_normalizer import STANDARD_COLUMNS, upgrade_standard_frame

from .config import DEFAULT_PROFILE_ID, STANDARD_SCHEMA_VERSION


def new_state_defaults() -> dict[str, object]:
    """Return fresh per-session values shared by initialization and profile reset."""
    return {
        "pdf_bytes": None,
        "pdf_name": "",
        "metadata": {},
        "document_type_profile": None,
        "page_matches": [],
        "auto_page_matches": [],
        "raw_tables": [],
        "table_candidates": {},
        "selected_table_candidates": {},
        "ai_extraction_logs": [],
        "ai_workbook_bytes": b"",
        "llm_base_url": "https://api.deepseek.com/v1",
        "llm_model": "",
        "llm_api_key": "",
        "auto_vision_retry": True,
        "force_vision_mode": False,
        # STEP2 提取方式（文字版页面走规则法，扫描版页面走大模型）
        "extraction_method": "",
        "allow_llm_fallback": True,
        "extraction_edit_events": [],
        "extraction_number_repairs": [],
        "extraction_number_reviews": [],
        "extraction_regions": {},
        "standard_data": pd.DataFrame(columns=STANDARD_COLUMNS),
        "standard_workbook_bytes": b"",
        "standard_target_catalog": pd.DataFrame(),
        "standard_target_summary": pd.DataFrame(),
        "standardization_warnings": [],
        "standard_template_label": "",
        "integrated_data": pd.DataFrame(columns=STANDARD_COLUMNS),
        "integration_preview": pd.DataFrame(columns=STANDARD_COLUMNS),
        "integration_sheet_summary": pd.DataFrame(),
        "integration_mapping_summary": pd.DataFrame(),
        "integration_logic_checks": pd.DataFrame(),
        "integration_warnings": [],
        "integration_preview_mode": "",
        "step6_analysis_source": pd.DataFrame(columns=STANDARD_COLUMNS),
        "major_financing_data": pd.DataFrame(),
        "standard_schema_version": 0,
        "validation_results": pd.DataFrame(),
        "monitor_results": pd.DataFrame(),
        "monitor_single_result": None,
        "edited_pages": {},
        "pages_confirmed": False,
        "normalization_diagnostics": pd.DataFrame(),
        "locator_config_version": "",
        "extraction_grid_cache": {},
        "extraction_image_cache": {},
        "extraction_cache_lock": Lock(),
        "active_profile_id": DEFAULT_PROFILE_ID,
        "active_profile_runtime": "",
    }


def initialize_state() -> None:
    for key, value in new_state_defaults().items():
        if key not in st.session_state:
            st.session_state[key] = value
    if st.session_state.standard_schema_version < STANDARD_SCHEMA_VERSION:
        for key in ("standard_data", "integrated_data", "integration_preview"):
            st.session_state[key] = upgrade_standard_frame(st.session_state.get(key))
        st.session_state.standard_schema_version = STANDARD_SCHEMA_VERSION


def reset_profile_results() -> None:
    """Clear report-specific state when the user switches Profile."""
    empty_values = new_state_defaults()
    for key in (
        "llm_base_url",
        "llm_model",
        "llm_api_key",
        "auto_vision_retry",
        "force_vision_mode",
        "standard_schema_version",
        "extraction_cache_lock",
        "active_profile_id",
    ):
        empty_values.pop(key)
    for key, value in empty_values.items():
        st.session_state[key] = value
    st.session_state.pop("solvency_target_tables", None)
    for key in list(st.session_state):
        if str(key).startswith("page_edit_"):
            del st.session_state[key]


def llm_credentials_ready() -> bool:
    """判断登录页配置的 Base URL / 模型 / API Key 是否齐全。"""
    return all(
        str(st.session_state.get(key, "")).strip()
        for key in ("llm_base_url", "llm_model", "llm_api_key")
    )
