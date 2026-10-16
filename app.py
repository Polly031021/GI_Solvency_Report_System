from __future__ import annotations

# Incremented when Step7 filter state handling changes. Keeping the revision in
# the entry point makes Streamlit reload the active session without restarting
# the server or discarding the user's integrated data.
STEP7_FILTER_RUNTIME_REVISION = 3

import dataclasses
import io
import zipfile
import hmac
import html
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from threading import Lock
from types import SimpleNamespace

import fitz
import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

from dashboard_components import render_kpmg_palette, render_print_control
from services.report_profiles import (
    ProfileValidationError,
    ReportProfile,
    load_profile_registry,
    load_profile_workbook,
    profile_workbook_bytes,
)
from services.solvency_ai_table_extractor import extraction_logs_frame
from services.solvency_hybrid_pipeline import locate_tables_hybrid
from services.solvency_dataset_adapter import (
    add_missing_derived_metrics,
    convert_external_workbook,
    read_standard_workbook,
    standard_workbook_bytes,
)
from services.solvency_company_identity import load_peer_group_config
from services.registered_capital_extractor import append_registered_capital
from services.solvency_step3_standardizer import (
    _target_summary,
    read_target_template,
    result_workbook_bytes,
    standardize_to_target,
    step3_metric_catalog,
    target_template_workbook_bytes,
)
from services.solvency_step6_analysis import (
    CHART_TYPES,
    COMPANY_REPORT,
    INDUSTRY_REPORT,
    NO_COMPANY_TYPE_FILTER,
    build_comparison_chart,
    companies_for_quick_selection,
    default_periods,
    filter_analysis_frame,
    metric_options,
    nonblank_values,
    prepare_analysis_frame,
    report_scope_frame,
    sort_report_periods,
    visualization_metric_frame,
)
from services.solvency_metric_registry import extend_taxonomy
from services.solvency_financing_analysis import read_major_financing_workbook
from services.step7_excel_config import load_step7_config
from services.solvency_navigation import (
    COMPANY_FIRST_LEVELS,
    KPMG_DEFAULT_COLORS,
    OVERVIEW_LEVEL,
    PRINT_ALL_LABEL,
    chart_names,
    first_levels_for_codes,
    metric_codes_for_chart,
    resolve_chart_selection,
    second_levels,
)
from services.solvency_gold_standard import (
    evaluate_gold_case,
    find_gold_case,
    load_gold_manifest,
)
from services.solvency_normalizer import (
    STANDARD_COLUMNS,
    load_taxonomy,
    narrow_table_view,
    upgrade_standard_frame,
)
from services.solvency_pdf_locator import (
    PageMatch,
    analyze_document_type,
    extract_report_metadata,
    report_identity_warning,
)
from services.solvency_validator import (
    load_validation_rules,
    validate_standard_data,
    validation_status_summary,
)
from step7_solvency import show_step_7_solvency
from step8_solvency import show_step_8_solvency

from services.batch_extraction import (
    FlexibleTable,
    convert_table_rows_to_wan,
    detect_table_unit,
    extract_single_page,
    extract_single_page_vision,
    sanitize_scalar,
    sanitize_tree,
)

from services.batch_processor import (
    build_context_from_profile,
    locate_one_pdf_bytes,
    process_one_pdf_bytes,
)

ROOT = Path(__file__).resolve().parent
CONFIG_DIR = ROOT / "config"
PROFILE_DIR = CONFIG_DIR / "report_profiles"
# ★ 默认 profile 改为财险，登录后直接进入 NON_LIFE_SOLVENCY
DEFAULT_PROFILE_ID = "NON_LIFE_SOLVENCY"
STANDARD_SCHEMA_VERSION = 2
GOLD_MANIFEST = ROOT / "gold_standard" / "manifest.json"
PEER_GROUP_CONFIG = CONFIG_DIR / "solvency_peer_groups.json"
WORKFLOW_TAB_LABELS = (
    "STEP0 报告监控",
    "STEP1 页码定位",
    "STEP2 表格提取",
    "STEP3 标准化",
    "STEP4 勾稽检查",
    "STEP5 数据集成",
    "STEP6 自定义分析",
    "STEP7 公司报告",
    "STEP8 行业分析",
)
ANALYSIS_TAB_LABELS = WORKFLOW_TAB_LABELS[6:]


st.set_page_config(page_title="保险公司报告分析处理平台", page_icon="🛡️", layout="wide")


LOGIN_AI_PROVIDERS = {
    "阿里云百炼 (通义千问)": (
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen3.7-plus",
    ),
    "DeepSeek (深度求索)": ("https://api.deepseek.com/v1", "deepseek-v4-flash"),
    "月之暗面 (Kimi)": ("https://api.moonshot.cn/v1", "moonshot-v1-8k"),
    "智谱AI (GLM-4)": ("https://open.bigmodel.cn/api/paas/v4", "glm-4"),
    "OpenAI (ChatGPT)": ("https://api.openai.com/v1", "gpt-4o"),
    "自定义私有化节点": ("", ""),
}
DEFAULT_LOGIN_AI_PROVIDER = "阿里云百炼 (通义千问)"
NORMAL_USER_ROLE = "普通用户"
PROJECT_MEMBER_ROLE = "项目组成员"


def _initialize_login_state() -> None:
    """Initialize authentication and shared model settings for this browser tab."""
    default_base_url, default_model = LOGIN_AI_PROVIDERS[DEFAULT_LOGIN_AI_PROVIDER]
    defaults = {
        "logged_in": False,
        "user_role": None,
        "llm_provider": DEFAULT_LOGIN_AI_PROVIDER,
        "llm_base_url": default_base_url,
        "llm_model": default_model,
        "llm_api_key": "",
        "login_base_url_input": default_base_url,
        "login_model_input": default_model,
        "login_api_key_input": "",
        "login_user_role": NORMAL_USER_ROLE,
        "login_security_code": "",
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _provider_name_from_base_url(base_url: str) -> str:
    normalized_url = str(base_url or "").strip().rstrip("/").lower()
    for provider, (provider_url, _) in LOGIN_AI_PROVIDERS.items():
        if normalized_url == provider_url.strip().rstrip("/").lower():
            return provider
    return "自定义私有化节点"


def _expected_login_code(role: str) -> str:
    """Allow deployments to override the reference platform's two access codes."""
    env_name = (
        "SOLVENCY_PROJECT_ACCESS_CODE"
        if role == PROJECT_MEMBER_ROLE
        else "SOLVENCY_USER_ACCESS_CODE"
    )
    fallback = "KPMG666" if role == PROJECT_MEMBER_ROLE else "KPMG1234"
    return os.getenv(env_name, fallback)


def _logout() -> None:
    st.session_state.logged_in = False
    st.session_state.user_role = None
    st.session_state.llm_api_key = ""
    st.session_state.login_api_key_input = ""
    st.session_state.login_security_code = ""
    st.session_state.pop("workflow_main_tab", None)
    st.session_state.pop("profile_settings_tabs", None)


def _render_login_page() -> None:
    st.markdown(
        """
        <style>
        header {visibility: hidden;}
        .stApp {background: linear-gradient(135deg, #040B16 0%, #0A1931 50%, #002266 100%); color: #E2E8F0;}
        [data-testid="column"]:nth-of-type(2) {background: rgba(255,255,255,0.08); backdrop-filter: blur(25px); -webkit-backdrop-filter: blur(25px); border-radius: 20px; border: 1px solid rgba(0,243,255,0.3); box-shadow: 0 15px 35px rgba(0,0,0,0.5), inset 0 0 15px rgba(0,243,255,0.15); padding: 40px 30px; margin-top: 6vh;}
        .title-glow {background: linear-gradient(90deg, #FFFFFF 0%, #76D2FF 50%, #00F3FF 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; font-weight: 900; filter: drop-shadow(0 0 15px rgba(0,243,255,0.6));}
        div[data-baseweb="input"]>div, div[data-baseweb="select"]>div {background: #F4F6FA!important; border: 1px solid rgba(0,243,255,0.3)!important; border-radius: 8px!important;}
        div[data-baseweb="input"]>div:focus-within, div[data-baseweb="select"]>div:focus-within {border-color: #00F3FF!important; box-shadow: 0 0 12px rgba(0,243,255,0.5)!important;}
        div[data-testid="stRadio"] label p {color: #FFFFFF!important; font-weight: bold!important; font-size: 14px!important;}
        label p, .stSelectbox label p, .stTextInput label p {color: #00F3FF!important; letter-spacing: 1px!important;}
        div[data-baseweb="input"] input {color: #31333F!important; -webkit-text-fill-color: #31333F!important; caret-color: #00338D!important; font-size: 14px!important;}
        div[data-baseweb="input"] input::placeholder {color: #7A8290!important; -webkit-text-fill-color: #7A8290!important; opacity: 1!important;}
        div[data-baseweb="select"] span {color: #31333F!important; -webkit-text-fill-color: #31333F!important; font-size: 14px!important;}
        div[data-baseweb="select"] svg {color: #31333F!important; fill: #31333F!important;}
        button[kind="primary"] {background: linear-gradient(90deg, #0044CC, #0088FF)!important; border: 1px solid rgba(0,243,255,0.5)!important; box-shadow: 0 0 15px rgba(0,136,255,0.4)!important; color: white!important; font-weight: bold!important; letter-spacing: 2px!important; border-radius: 8px!important; margin-top: 5px!important;}
        button[kind="primary"]:hover {box-shadow: 0 0 25px rgba(0,243,255,0.8)!important; transform: scale(1.02);}
        [data-testid="stPopover"] {display: flex; justify-content: flex-end;}
        [data-testid="stPopover"] button {background: transparent!important; border: none!important; box-shadow: none!important; color: #94A3B8!important; font-size: 12px!important; font-weight: normal!important; padding: 0!important; min-height: 0!important; text-decoration: underline; margin-top: 8px; margin-bottom: 15px;}
        [data-testid="stPopover"] button:hover {color: #00F3FF!important;}
        </style>
        """,
        unsafe_allow_html=True,
    )

    _, center, _ = st.columns([1, 1.5, 1])
    with center:
        st.markdown(
            "<div style='text-align:center; margin-bottom:30px;'>"
            "<h1 style='font-size:32px; margin:0;'><span class='title-glow'>"
            "偿付能力数智分析平台</span></h1>"
            "<p style='color:#76D2FF; font-size:11px; letter-spacing:4px; "
            "margin-top:8px; font-weight:bold;'>SOLVENCY INTELLIGENCE</p></div>",
            unsafe_allow_html=True,
        )
        selected_role = st.radio(
            "访问权限",
            [NORMAL_USER_ROLE, PROJECT_MEMBER_ROLE],
            horizontal=True,
            key="login_user_role",
            label_visibility="collapsed",
        )
        st.text_input(
            "安全验证",
            type="password",
            key="login_security_code",
            placeholder="请输入内部安全码",
        )
        # ---------- AI 提供商选择 ----------
        ai_provider = st.selectbox(
            "选择 AI 提供商",
            options=list(LOGIN_AI_PROVIDERS.keys()),
            index=list(LOGIN_AI_PROVIDERS.keys()).index(DEFAULT_LOGIN_AI_PROVIDER),
            key="login_ai_provider",
        )

        if ai_provider == "自定义私有化节点":
            # 用户自定义模式：显示输入框
            st.text_input(
                "RPC 接口地址",
                key="login_base_url_input",
                placeholder="https://example.com/v1",
                help="填写兼容 /chat/completions 的接口根地址。",
            )
            st.text_input(
                "指定模型版本",
                key="login_model_input",
                placeholder="填写实际可用的模型名称",
            )
        else:
            # 预设模式：自动填充，显示只读信息
            base_url, model_name = LOGIN_AI_PROVIDERS[ai_provider]
            # 将值存入 session_state，以便登录时使用
            st.session_state.login_base_url_input = base_url
            st.session_state.login_model_input = model_name
            st.markdown(f"**RPC 接口地址**: `{base_url}`")
            st.markdown(f"**指定模型版本**: `{model_name}`")
        st.text_input(
            "请填写您使用AI的API Key",
            type="password",
            key="login_api_key_input",
            placeholder=" sk-... (不填则仅开放报告检测、数据合并及可视化本地分析功能)",
        )
        if "api.openai.com" in str(st.session_state.login_base_url_input).lower():
            st.markdown(
                "<p style='font-size:11px; color:#F87171; text-align:right; "
                "margin-top:4px; margin-bottom:0;'>⚠️ 严禁向境外节点传输涉密数据</p>",
                unsafe_allow_html=True,
            )
        with st.popover("如何获取API key?"):
            st.markdown(
                "**1.** 前往各大模型官方开放平台注册账号。\n\n"
                "**2.** 在控制台生成 `sk-` 开头的密钥。\n\n"
                "**3.** 复制填入上方输入框即可体验 AI 功能。\n\n"
                "登录页中的接口地址、模型版本和 API Key 由 Step1、Step2 与 Step7 共用。"
            )

        if st.button("启 动 系 统", type="primary", width="stretch"):
            supplied_code = str(st.session_state.login_security_code)
            if not hmac.compare_digest(supplied_code, _expected_login_code(selected_role)):
                st.error(f"❌ 拒绝访问：{selected_role}安全码错误")
            else:
                st.session_state.logged_in = True
                st.session_state.user_role = selected_role
                st.session_state.llm_base_url = str(
                    st.session_state.login_base_url_input
                ).strip()
                st.session_state.llm_provider = _provider_name_from_base_url(
                    st.session_state.llm_base_url
                )
                st.session_state.llm_model = str(
                    st.session_state.login_model_input
                ).strip()
                st.session_state.llm_api_key = str(
                    st.session_state.login_api_key_input
                ).strip()
                st.session_state.pop("workflow_main_tab", None)
                st.session_state.pop("profile_settings_tabs", None)
                st.rerun()

        st.markdown(
            "<div style='text-align:center; color:#94A3B8; font-size:11px; "
            "margin-top:30px; letter-spacing:1px;'>系统版本：v0.3.1 © 2026<br>"
            "保险报告处理与分析平台</div>",
            unsafe_allow_html=True,
        )


def _render_app_styles() -> None:
    """注入平台主样式（含打印样式）。"""
    st.markdown(
        """
        <style>
        /* 保留 Streamlit 为固定顶栏提供的原生顶部安全间距；不要在这里缩小
           padding-top，否则首个 Profile 操作区会被 Deploy/菜单工具栏覆盖。 */
        [data-testid="stMainBlockContainer"] {padding-bottom: 2rem;}
        [data-testid="stAppViewContainer"] {background:#F4F7FC;}
        h1, h2, h3 {color:#00338D;}
        .platform-page-heading h1 {margin:0 0 0.35rem;}
        .platform-profile-caption {
          margin:0 0 0.75rem; color:rgba(49,51,63,.6); font-size:0.875rem;
        }
        [data-testid="stMetric"] {background:#F4F7FC; border-left:4px solid #00338D; padding:12px; border-radius:5px;}
        /* Remove only obsolete STEP7 report modules left behind during a rerun in
           both screen and print media. Controls and the current report stay visible;
           stale chart copies cannot accumulate or consume a printed page. */
        [class*="st-key-s7_report_module_"][data-stale="true"],
        [class*="st-key-s7_report_module_"]:has(
          [data-testid="stElementContainer"][data-stale="true"]
        ),
        [data-testid="stElementContainer"][data-stale="true"]:has(
          [class*="st-key-s7_report_module_"]
        ) {
          display:none!important;
        }
        @page { size: 13.333in 7.5in; margin: 0.35in; }
        @media print {
          [data-testid="stSidebar"],
          [data-testid="stHeader"],
          [data-testid="stToolbar"],
          [data-testid="stDecoration"],
          [data-testid="stStatusWidget"],
          [data-testid="stFileUploader"],
          [data-testid="stSegmentedControl"],
          [data-testid="stSelectbox"],
          [data-testid="stMultiSelect"],
          [data-testid="stButton"],
          [data-testid="stDownloadButton"],
          div[role="tablist"],
          .platform-page-heading,
          footer { display: none !important; }
          [data-testid="stElementContainer"]:has(.platform-page-heading) {
            display: none !important;
          }
          html, body, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
            width: 100% !important;
            max-width: none !important;
            overflow: visible !important;
            background: #ffffff !important;
          }
          [data-testid="stMainBlockContainer"] {
            width: 100% !important;
            max-width: none !important;
            padding: 0 !important;
          }
          [data-testid="stExpander"] details > div { display: block !important; }
          [data-testid="stMetric"], [data-testid="stDataFrame"], [data-testid="stExpander"] {
            break-inside: avoid-page;
          }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


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
    enriched = (
        add_missing_derived_metrics(frame)
        if report_profile_id == "LIFE_SOLVENCY"
        else frame
    )
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


def _finalize_company_frame(
    frame: pd.DataFrame,
    rename_map: dict[str, str] | None = None,
    *,
    fill_missing: bool = True,
) -> pd.DataFrame:
    """统一公司清单列名，清理空值并按公司名去重。"""
    companies = frame.rename(columns=rename_map) if rename_map else frame
    for column in ("公司", "公司类别", "报告披露地址"):
        series = companies[column]
        if fill_missing:
            series = series.fillna("")
        companies[column] = series.astype(str).str.strip()
    return (
        companies[companies["公司"] != ""]
        .drop_duplicates(subset=["公司"], keep="last")
        .reset_index(drop=True)
    )


def dedupe_column_names(columns) -> list[str]:
    """空列名补“列N”，重复列名追加 _2/_3…。"""
    seen: dict[str, int] = {}
    names: list[str] = []
    for index, column in enumerate(columns):
        name = str(column).strip() or f"列{index + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 1
        names.append(name)
    return names


def llm_credentials_ready() -> bool:
    """判断登录页配置的 Base URL / 模型 / API Key 是否齐全。"""
    return all(
        str(st.session_state.get(key, "")).strip()
        for key in ("llm_base_url", "llm_model", "llm_api_key")
    )


@st.cache_data(show_spinner=False)
def read_companies(
    source_path: str,
    sheet_name: str,
    header: int,
    company_column: str,
    company_type_column: str,
    report_url_column: str,
) -> pd.DataFrame:
    companies = pd.read_excel(
        source_path,
        sheet_name=sheet_name,
        header=header,
    )
    required = [company_column, company_type_column, report_url_column]
    missing = [column for column in required if column not in companies.columns]
    if missing:
        raise ValueError(f"公司网址配置缺少字段：{', '.join(missing)}")
    return _finalize_company_frame(
        companies[required],
        {
            company_column: "公司",
            company_type_column: "公司类别",
            report_url_column: "报告披露地址",
        },
    )


def prepare_embedded_companies(profile: ReportProfile) -> pd.DataFrame:
    companies = profile.company_frame()
    if companies.empty:
        return companies
    return _finalize_company_frame(
        companies,
        {
            str(profile.monitoring["company_name_column"]): "公司",
            str(profile.monitoring["company_type_column"]): "公司类别",
            str(profile.monitoring["report_url_column"]): "报告披露地址",
        },
    )


def keep_valid_widget_state(
    key: str,
    options: list[str],
    *,
    multiple: bool = False,
) -> None:
    """Discard stale selections when upstream Step6 filters change."""
    if key not in st.session_state:
        return
    if multiple:
        current = st.session_state.get(key) or []
        st.session_state[key] = [value for value in current if value in options]
    elif st.session_state.get(key) not in options:
        del st.session_state[key]


def render_all_chart_navigation(chart_options: list[str]) -> None:
    """Render read-only chart rows with the same visual rhythm as sidebar radios."""
    if not chart_options:
        return
    rows = "".join(
        (
            '<div class="kpmg-nav-all-item">'
            '<span class="kpmg-nav-all-dot" aria-hidden="true"></span>'
            f'<span>{html.escape(str(name))}</span>'
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


def navigation_metric_codes(frame: pd.DataFrame) -> list[str]:
    if "指标编码" not in frame.columns:
        return []
    return frame["指标编码"].dropna().astype(str).str.strip().tolist()


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
        first_levels_for_codes(available_codes, industry=True)
        if industry
        else COMPANY_FIRST_LEVELS
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
        st.session_state[level_two_key] = (
            second_options[1] if len(second_options) > 1 else "全部"
        )
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


@st.cache_data(show_spinner=False)
def _load_step7_modules_cached(workbook_bytes: bytes):
    return load_step7_config(io.BytesIO(workbook_bytes))


def _render_step7_excel_navigation(profile_id: str) -> None:
    """用 Step7 注释表构建侧边栏一级/二级/图表导航。"""
    st.markdown("### 公司报告导航")

    uploaded = st.file_uploader(
        "上传 STEP7 注释表（首次使用或更新配置时）",
        type=["xlsx"],
        key=f"step7_notes_upload_{profile_id}",
        help="包含 模块ID / 一级分类 / 二级分类 / 对应图表名称 / 图表类型 / 指标编码 等列。",
    )
    if uploaded is not None:
        st.session_state[f"step7_notes_bytes_{profile_id}"] = uploaded.getvalue()

    workbook_bytes = st.session_state.get(f"step7_notes_bytes_{profile_id}")
    if not workbook_bytes:
        st.info("尚未上传 STEP7 注释表，Step7 将使用内置 COMPANY_NAVIGATION 配置。")
        st.session_state["step7_modules"] = []
        return

    modules = _load_step7_modules_cached(workbook_bytes)
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

    chart_options = [m.chart_name for m in sub_modules]
    if len(chart_options) > 1:
        keep_valid_widget_state("company_nav_chart", chart_options)
        st.radio("具体图表", chart_options, key="company_nav_chart")
    elif chart_options:
        st.session_state["company_nav_chart"] = chart_options[0]

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
        st.caption(
            f"已读取 {len(st.session_state.major_financing_data):,} 条融资事件。"
        )
    except Exception as exc:
        st.error(f"重大融资信息读取失败：{exc}")


def render_company_report_workspace(
    data: pd.DataFrame,
) -> None:
    """Render Step7 with normal page reruns so settings control all content below."""
    step7_modules = st.session_state.get("step7_modules") or None
    show_step_7_solvency(
        data,
        st.session_state.major_financing_data,
        step7_modules=step7_modules,
    )


def render_industry_report_workspace(
    data: pd.DataFrame,
) -> None:
    """Render Step8 with the same normal page-rerun model as Step6 and Step7."""
    show_step_8_solvency(data, st.session_state.major_financing_data)


def target_period_terms(
    year: int,
    period: str,
    frequency: str = "QUARTERLY",
) -> list[str]:
    if frequency.upper() == "ANNUAL":
        return [
            f"{year}年度",
            f"{year}年年度报告",
            f"{year}年年报",
            f"annualreport{year}",
        ]
    quarter = period
    quarter_number = int(quarter[-1])
    chinese_number = {1: "一", 2: "二", 3: "三", 4: "四"}[quarter_number]
    return [
        f"{year}{quarter}",
        f"{year}年第{quarter_number}季度",
        f"{year}年第{chinese_number}季度",
        f"{year}年{quarter_number}季度",
        f"{year}年{chinese_number}季度",
    ]


def check_company_report(
    row: pd.Series,
    year: int,
    period: str,
    *,
    frequency: str = "QUARTERLY",
    report_name: str = "目标报告",
    report_terms: tuple[str, ...] = (),
    timeout: int = 15,
) -> dict:
    company = str(row.get("公司", "")).strip()
    category = str(row.get("公司类别", "")).strip()
    url = str(row.get("报告披露地址", "")).strip()
    checked_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    target_period = str(year) if frequency.upper() == "ANNUAL" else f"{year}{period}"
    base = {
        "公司": company,
        "公司类别": category,
        "报告类型": report_name,
        "目标报告期": target_period,
        "检查结果": "",
        "HTTP状态": "",
        "匹配关键词": "",
        "披露地址": url,
        "检查时间": checked_at,
        "说明": "",
    }
    if not url.lower().startswith(("http://", "https://")):
        return {**base, "检查结果": "未配置有效链接", "说明": "请维护系统公司网址配置。"}

    try:
        response = requests.get(
            url,
            timeout=(5, timeout),
            allow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
            },
        )
        base["HTTP状态"] = response.status_code
        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "").lower()
        if "application/pdf" in content_type or response.content[:4] == b"%PDF":
            metadata = extract_report_metadata(response.content)
            matched = metadata.get("报告年度") == year
            if frequency.upper() != "ANNUAL":
                matched = matched and metadata.get("报告季度") == period
            return {
                **base,
                "检查结果": "已更新" if matched else "PDF报告期不匹配",
                "匹配关键词": str(metadata.get("报告期", "")),
                "说明": "链接直接返回PDF，已按PDF首页报告期判断。",
            }

        soup = BeautifulSoup(response.text, "html.parser")
        visible_text = soup.get_text(" ", strip=True)
        link_text = " ".join(
            f"{tag.get_text(' ', strip=True)} {tag.get('href', '')}"
            for tag in soup.find_all("a")
        )
        searchable = re.sub(r"\s+", "", f"{visible_text} {link_text}")
        terms = target_period_terms(year, period, frequency)
        matched_terms = [term for term in terms if term.lower() in searchable.lower()]
        normalized_report_terms = tuple(
            item for item in report_terms if str(item).strip()
        )
        has_report_term = (
            any(
                str(term).lower() in searchable.lower()
                for term in normalized_report_terms
            )
            if normalized_report_terms
            else True
        )

        if matched_terms and has_report_term:
            result = "已更新"
            note = "页面中同时发现目标报告期和报告类型关键词。"
        elif matched_terms:
            result = "疑似已更新，需人工核对"
            note = "发现目标报告期，但未在静态页面文本中发现报告类型关键词。"
        else:
            result = "未发现目标报告"
            note = "静态页面未发现目标报告期；动态加载页面或反爬虫网站需人工打开核对。"
        return {
            **base,
            "检查结果": result,
            "匹配关键词": "、".join(matched_terms),
            "说明": note,
        }
    except requests.RequestException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", "")
        return {
            **base,
            "HTTP状态": status,
            "检查结果": "访问失败，需人工核对",
            "说明": str(exc)[:300],
        }
    except Exception as exc:
        return {**base, "检查结果": "检查异常，需人工核对", "说明": str(exc)[:300]}


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


def parse_page_numbers(raw_value: str, total_pages: int) -> tuple[list[int], list[str]]:
    """解析逗号、分号或空格分隔的物理页码。"""
    valid_pages: list[int] = []
    invalid_values: list[str] = []
    for token in re.split(r"[,，;；\s]+", str(raw_value).strip()):
        if not token:
            continue
        if not token.isdigit():
            invalid_values.append(token)
            continue
        page_number = int(token)
        if page_number == 0:
            continue
        if 1 <= page_number <= total_pages:
            if page_number not in valid_pages:
                valid_pages.append(page_number)
        else:
            invalid_values.append(token)
    return valid_pages, invalid_values


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


def sync_analysis_navigation_from_tab() -> None:
    target = st.session_state.get("workflow_main_tab", "")
    if target in ANALYSIS_TAB_LABELS:
        st.session_state.analysis_page_navigation = target


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


def _load_monitor_companies() -> pd.DataFrame:
    """读取本地财险公司清单并规范列名。"""
    try:
        companies = pd.read_excel("财险公司列表.xlsx")
    except Exception:
        companies = pd.DataFrame(columns=["公司", "公司类别", "报告披露地址"])
        st.warning("未找到 财险公司列表.xlsx，STEP0 公司清单为空。")

    # 兼容“公司名称 / 披露地址”等历史列名；其余清理逻辑与其他入口共用
    return _finalize_company_frame(
        companies,
        {
            "公司名称": "公司",
            "公司类别": "公司类别",
            "披露地址": "报告披露地址",
        },
        fill_missing=False,
    )


def _render_monitor_single_company(
    active_profile: ReportProfile,
    filtered_companies: pd.DataFrame,
    target_year: int,
    target_period: str,
) -> None:
    """STEP0：逐个公司打开披露页面并检查是否更新。"""
    st.markdown("#### 逐个查看")
    if filtered_companies.empty:
        st.warning("当前类别筛选下没有公司。")
    else:
        selected_company = st.selectbox(
            "选择需要检查的公司",
            filtered_companies["公司"].tolist(),
            key="monitor_selected_company",
        )
        selected_row = filtered_companies[
            filtered_companies["公司"] == selected_company
        ].iloc[0]

        link_col, check_col = st.columns(2)
        link_col.link_button(
            "打开该公司披露页面",
            selected_row["报告披露地址"],
            width="stretch",
        )
        if check_col.button(
            "检查该公司是否更新",
            key="monitor_one",
            type="primary",
            width="stretch",
        ):
            with st.spinner(f"正在检查 {selected_company}..."):
                st.session_state.monitor_single_result = check_company_report(
                    selected_row,
                    target_year,
                    target_period,
                    frequency=active_profile.frequency,
                    report_name=active_profile.profile_name,
                    report_terms=tuple(
                        active_profile.monitoring.get("report_terms", [])
                    ),
                )

        if st.session_state.monitor_single_result:
            single_result = st.session_state.monitor_single_result
            status = single_result["检查结果"]
            if status == "已更新":
                st.success(f"{single_result['公司']}：{status}")
            elif "失败" in status or "异常" in status:
                st.error(f"{single_result['公司']}：{status}")
            else:
                st.warning(f"{single_result['公司']}：{status}")
            st.dataframe(
                pd.DataFrame([single_result]),
                width="stretch",
                hide_index=True,
                column_config={
                    "披露地址": st.column_config.LinkColumn(
                        "披露地址",
                        display_text="打开页面",
                    )
                },
            )


def _render_monitor_batch_check(
    active_profile: ReportProfile,
    filtered_companies: pd.DataFrame,
    target_year: int,
    target_period: str,
) -> None:
    """STEP0：并发检查当前筛选范围内的全部公司。"""
    st.markdown("#### 所有公司检查")
    with st.expander("批量检查设置"):
        worker_count = st.slider(
            "并发检查数量",
            min_value=1,
            max_value=8,
            value=4,
            help="数量越大速度越快，但部分保险公司网站可能触发访问限制。",
        )
        request_timeout = st.slider(
            "单个网站读取超时（秒）",
            min_value=5,
            max_value=30,
            value=15,
        )

    if st.button(
        f"检查当前筛选的全部 {len(filtered_companies)} 家公司",
        key="monitor_all",
        disabled=filtered_companies.empty,
        width="stretch",
    ):
        progress = st.progress(0, text="正在准备批量检查...")
        result_rows = []
        records = list(filtered_companies.iterrows())

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_map = {
                executor.submit(
                    check_company_report,
                    row,
                    target_year,
                    target_period,
                    frequency=active_profile.frequency,
                    report_name=active_profile.profile_name,
                    report_terms=tuple(
                        active_profile.monitoring.get("report_terms", [])
                    ),
                    timeout=request_timeout,
                ): order
                for order, (_, row) in enumerate(records)
            }
            for completed, future in enumerate(as_completed(future_map), start=1):
                result = future.result()
                result["_排序"] = future_map[future]
                result_rows.append(result)
                progress.progress(
                    completed / len(future_map),
                    text=f"已检查 {completed}/{len(future_map)}：{result['公司']}",
                )

        st.session_state.monitor_results = (
            pd.DataFrame(result_rows)
            .sort_values("_排序")
            .drop(columns="_排序")
            .reset_index(drop=True)
        )
        progress.empty()

    if not st.session_state.monitor_results.empty:
        results = st.session_state.monitor_results
        summary = results["检查结果"].value_counts()
        summary_columns = st.columns(min(len(summary), 4))
        for index, (status, count) in enumerate(summary.items()):
            summary_columns[index % len(summary_columns)].metric(status, int(count))

        result_filter = st.multiselect(
            "筛选检查结果",
            results["检查结果"].drop_duplicates().tolist(),
            default=results["检查结果"].drop_duplicates().tolist(),
            key="monitor_result_filter",
        )
        result_view = results[results["检查结果"].isin(result_filter)]
        st.dataframe(
            result_view,
            width="stretch",
            hide_index=True,
            column_config={
                "披露地址": st.column_config.LinkColumn(
                    "披露地址",
                    display_text="打开页面",
                )
            },
        )
        st.download_button(
            "下载本次检查结果",
            dataframe_to_xlsx(results, "报告更新检查"),
            f"{active_profile.profile_id}_报告更新检查_"
            f"{target_year}{'' if target_period == 'ANNUAL' else target_period}.xlsx",
            width="stretch",
        )


def render_monitor_tab(active_profile: ReportProfile) -> pd.DataFrame:
    """STEP0 报告监控；返回公司清单，供 STEP5 映射公司类型。"""
    st.subheader(f"{active_profile.profile_name}监控")
    companies = _load_monitor_companies()
    year_col, quarter_col = st.columns(2)
    target_year = int(year_col.number_input("报告年度", 2020, 2050, 2026))
    if active_profile.frequency.upper() == "ANNUAL":
        target_period = "ANNUAL"
        quarter_col.text_input("报告期间", value="年度", disabled=True)
    else:
        target_period = quarter_col.selectbox(
            "报告季度",
            ["Q1", "Q2", "Q3", "Q4"],
        )

    all_categories = sorted(companies["公司类别"].dropna().unique().tolist())
    selected_categories = st.multiselect(
        "公司类别",
        all_categories,
        default=all_categories,
        key="monitor_company_categories",
    )
    filtered_companies = companies[
        companies["公司类别"].isin(selected_categories)
    ].reset_index(drop=True)

    st.caption(
        f"系统公司范围共 {len(companies)} 家；当前筛选 {len(filtered_companies)} 家。"
        f"运行时使用 {active_profile.profile_id} 的公司来源配置。"
    )
    st.dataframe(
        filtered_companies,
        width="stretch",
        hide_index=True,
        column_config={
            "报告披露地址": st.column_config.LinkColumn(
                "报告披露地址",
                display_text="打开页面",
            )
        },
    )
    _render_monitor_single_company(active_profile, filtered_companies, target_year, target_period)
    _render_monitor_batch_check(active_profile, filtered_companies, target_year, target_period)
    return companies


def _render_locator_review_panel(total_pages: int, gold_case: dict | None) -> None:
    """STEP1：核对自动定位结果，并提供人工修改物理页码的入口。"""
    if st.session_state.auto_page_matches:
        st.markdown("---")
        st.markdown("#### 结果核对")
        st.caption("若表格跨页，请用逗号分隔物理页码，例如：26, 27。未找到时可直接手工输入。")

        edited_pages: dict[str, list[int]] = {}
        updated_matches: list[PageMatch] = []
        conflict_matches = [
            item
            for item in st.session_state.auto_page_matches
            if item.review_required
        ]
        if conflict_matches:
            st.warning(
                "以下目标的定位证据存在冲突，已隔离等待人工核对："
                + "、".join(item.table_name for item in conflict_matches)
            )
        for match in st.session_state.auto_page_matches:
            input_key = f"page_edit_{match.table_id}"
            if input_key not in st.session_state:
                st.session_state[input_key] = ", ".join(map(str, match.pages))
            raw_value = st.text_input(match.table_name, key=input_key)
            valid_pages, invalid_values = parse_page_numbers(raw_value, total_pages)
            previous_pages = st.session_state.edited_pages.get(match.table_id)
            if previous_pages is not None and previous_pages != valid_pages:
                st.session_state.pages_confirmed = False
            edited_pages[match.table_id] = valid_pages
            updated_matches.append(
                PageMatch(
                    table_id=match.table_id,
                    table_name=match.table_name,
                    pages=valid_pages,
                    score=match.score,
                    evidence=match.evidence,
                    review_required=match.review_required,
                    review_reason=match.review_reason,
                    sources=match.sources,
                    strategy_id=match.strategy_id,
                    table_config=match.table_config,
                    confidence=match.confidence,
                    confidence_basis=match.confidence_basis,
                )
            )
            auto_text = ", ".join(map(str, match.pages)) if match.pages else "未自动找到"
            evidence_text = match.evidence or "无明确关键词证据"
            confidence = int(getattr(match, "confidence", 0) or 0)
            confidence_basis = (
                getattr(match, "confidence_basis", "") or "未评估"
            )
            manual_override = bool(valid_pages) and valid_pages != match.pages
            confidence_status = (
                f"置信度 {confidence}%（{confidence_basis}）"
                if confidence
                else "置信度 0%（无自动定位证据，请手动填写）"
            )
            if manual_override:
                confidence_status += "｜已手动修改页码"
            st.caption(f"自动定位：{auto_text}　|　识别依据：{evidence_text}")
            if confidence >= 85:
                st.success(confidence_status)
            elif confidence >= 65:
                st.info(confidence_status)
            elif confidence > 0:
                st.warning(confidence_status + "，建议人工确认。")
            else:
                st.error(confidence_status)
            if match.review_required:
                st.error(f"定位证据冲突：{match.review_reason}")
            if invalid_values:
                st.warning(f"以下页码或内容无效，已忽略：{', '.join(invalid_values)}")

        st.session_state.edited_pages = edited_pages
        st.session_state.page_matches = updated_matches

        if gold_case:
            st.markdown("##### 真实PDF金标准 - 页码定位")
            st.dataframe(
                evaluate_gold_case(
                    gold_case,
                    matches=updated_matches,
                ),
                width="stretch",
                hide_index=True,
            )

        if st.button(
            "确认页码，进入下一步",
            key="confirm_solvency_pages",
            width="stretch",
        ):
            st.session_state.pages_confirmed = True
            valid_count = sum(bool(item.pages) for item in updated_matches)
            conflict_note = (
                "；定位冲突已由人工确认"
                if conflict_matches else ""
            )
            st.success(
                f"页码已确认：{len(updated_matches)}类目标中有"
                f" {valid_count} 类配置了有效页码{conflict_note}。"
                "请前往 STEP2 提取表格。"
            )


def _render_locator_target_panel(
    active_profile: ReportProfile,
    feature_config: dict,
    table_configs: list[dict],
    table_config_by_name: dict[str, dict],
    incoming: bytes,
    total_pages: int,
    gold_case: dict | None,
) -> None:
    """STEP1：目标表选择、定位方法选择与定位执行。"""
    st.markdown("#### 检索目标设定")
    all_table_names = [item["table_name"] for item in table_configs]
    required_table_names = [
        item["table_name"]
        for item in table_configs
        if item.get("required", True)
    ]
    selected_table_names = st.multiselect(
        "请选择需要定位的报表：",
        all_table_names,
        default=required_table_names,
        key="solvency_target_tables",
    )
    selected_configs = [
        table_config_by_name[name]
        for name in selected_table_names
        if name in table_config_by_name
    ]

    # --- 文档类型识别与定位方法推荐 ---
    doc_profile = st.session_state.get("document_type_profile")
    if doc_profile is not None:
        method_hint = (
            "规则法（离线，零模型调用）"
            if doc_profile.recommended_method == "RULE"
            else "大模型法（需要API Key）"
        )
        st.info(
            f"**文档类型：{doc_profile.label}**（可检索文本页 "
            f"{doc_profile.text_pages}/{doc_profile.total_pages}）\n\n"
            f"**推荐识别方法：{method_hint}**\n\n"
            f"{doc_profile.notes}"
        )
    else:
        st.caption("未能识别文档类型，请手动选择定位方法。")

    locator_method = st.radio(
        "定位方法",
        [
            "规则法（离线，零模型调用）",
            "大模型法（语义+视觉混合）",
        ],
        index=(
            0
            if doc_profile is None
            or doc_profile.recommended_method == "RULE"
            else 1
        ),
        key=f"locator_method_{active_profile.profile_id}",
        help=(
            "规则法只使用本地边界分析、表格雷达和目录换算，结果确定可复现；"
            "大模型法会调用登录页配置的模型，适合扫描版或版式异常报告。"
        ),
    )
    use_rule_mode = locator_method.startswith("规则法")
    credentials_ready = llm_credentials_ready()
    llm_verify = False
    if use_rule_mode:
        llm_verify = st.checkbox(
            "规则法完成后用大模型复核",
            value=False,
            key=f"locator_llm_verify_{active_profile.profile_id}",
            disabled=not credentials_ready,
            help=(
                "规则法先独立出结果，再调用一次大模型交叉验证：一致则提升置信度，"
                "不一致则标记人工确认。未配置API Key时不可用。"
            ),
        )
        if not credentials_ready:
            st.caption("当前未配置AI接口：规则法可正常使用，大模型复核不可用。")

    configured_provider = st.session_state.get("llm_provider", "自定义节点")
    configured_model = st.session_state.get("llm_model", "") or "未配置"
    st.caption(
        f"AI 配置来自登录页：{configured_provider} / {configured_model}。"
        "Step1、Step2 与 Step7 共用该配置；如需更改，请退出后重新登录。"
    )

    method_needs_llm = (not use_rule_mode) or llm_verify
    locate_clicked = st.button(
        "启动智能定位",
        type="primary",
        key="locate_tables",
        width="stretch",
    )
    rescan_clicked = st.button(
        "重新扫描",
        key="rescan_locator",
        width="stretch",
        disabled=not st.session_state.auto_page_matches,
        help=(
            "清除当前定位结果并按上方设置重新扫描PDF；"
            "手工修改过的页码会被新结果覆盖，请重新核对。"
        ),
    )
    if rescan_clicked:
        _clear_page_locations()
        _drop_page_edit_widgets(table_configs)

    if locate_clicked or rescan_clicked:
        if not selected_configs:
            st.error("请至少选择一张报表。")
        elif method_needs_llm and not credentials_ready:
            st.error(
                "当前定位方法需要AI配置。请退出登录，在登录页填写 "
                "Base URL、模型名称和 API Key 后重新进入；"
                "或切换为规则法（离线）。"
            )
        else:
            spinner_text = (
                "正在执行规则法定位（本地边界分析+表格雷达+目录换算）..."
                if use_rule_mode
                else "正在检测PDF文字层并执行语义、结构与图片页码定位..."
            )
            if use_rule_mode and llm_verify:
                spinner_text = "规则法定位完成，正在调用大模型复核..."
            with st.spinner(spinner_text):
                selected_feature_config = {
                    **feature_config,
                    "tables": selected_configs,
                }
                matches, locator_message = locate_tables_hybrid(
                    incoming,
                    selected_feature_config,
                    api_key=st.session_state.llm_api_key,
                    base_url=st.session_state.llm_base_url,
                    model=st.session_state.llm_model,
                    profile_context=active_profile.locator_context(),
                    mode="rule" if use_rule_mode else "hybrid",
                    llm_verify=llm_verify,
                )
                st.session_state.auto_page_matches = matches
                st.session_state.page_matches = matches
                st.session_state.pages_confirmed = False
                st.session_state.edited_pages = {
                    item.table_id: list(item.pages)
                    for item in matches
                }
                for item in matches:
                    st.session_state[f"page_edit_{item.table_id}"] = ", ".join(map(str, item.pages))
            located_count = sum(bool(item.pages) for item in matches)
            if located_count:
                st.success(
                    f"定位完成：{located_count}类目标已找到候选页。"
                    "请结合右侧页面预览和置信度提示进行校准。"
                )
            else:
                st.warning("尚未自动找到目标页，请查看定位说明或直接填写物理页码。")
            if "失败" in locator_message:
                st.warning(locator_message)
            else:
                st.info(locator_message)
    _render_locator_review_panel(total_pages, gold_case)


def _render_locator_preview_panel(incoming: bytes, total_pages: int) -> None:
    """STEP1：右侧 PDF 原页预览。"""
    st.markdown("#### 页面预览")
    if st.session_state.auto_page_matches and st.session_state.page_matches:
        preview_matches = st.session_state.page_matches
        preview_ids = [item.table_id for item in preview_matches]
        preview_name_map = {item.table_id: item.table_name for item in preview_matches}
        selected_preview_id = st.selectbox(
            "选择要预览的报表：",
            preview_ids,
            format_func=lambda table_id: preview_name_map[table_id],
            key="solvency_preview_table",
        )
        selected_match = next(
            item for item in preview_matches
            if item.table_id == selected_preview_id
        )
        pages_to_preview = selected_match.pages

        if not pages_to_preview:
            st.info("尚未配置有效页码，请在左侧输入物理页码。")
        else:
            if len(pages_to_preview) > 1:
                current_page = st.radio(
                    "该报表包含多页，请切换预览：",
                    pages_to_preview,
                    horizontal=True,
                    key=f"preview_page_{selected_preview_id}",
                )
            else:
                current_page = pages_to_preview[0]

            try:
                preview_image = render_pdf_page(incoming, current_page)
                st.image(
                    preview_image,
                    caption=f"当前预览：第 {current_page} 页 / 共 {total_pages} 页",
                    width="stretch",
                )
            except Exception as exc:
                st.warning(f"页面预览失败：{exc}")
    else:
        st.info("启动智能定位后，此处将显示对应PDF页面。")


def _clear_page_locations() -> None:
    """清空页码定位结果（保留已上传的 PDF）。"""
    for key, value in (
        ("auto_page_matches", []),
        ("page_matches", []),
        ("edited_pages", {}),
        ("pages_confirmed", False),
    ):
        st.session_state[key] = value


def _drop_page_edit_widgets(table_configs: list[dict]) -> None:
    """移除逐表页码输入框的会话状态，避免残留上一次的页码。"""
    for item in table_configs:
        st.session_state.pop(f"page_edit_{item['table_id']}", None)


def _clear_extraction_results(*, clear_caches: bool = False) -> None:
    """清空提取结果与下游标准化结果；clear_caches 同时重置提取缓存。"""
    values = {
        "raw_tables": [],
        "ai_extraction_logs": [],
        "ai_workbook_bytes": b"",
        "standard_data": pd.DataFrame(columns=STANDARD_COLUMNS),
        "validation_results": pd.DataFrame(),
        "normalization_diagnostics": pd.DataFrame(),
    }
    if clear_caches:
        values.update({
            "table_candidates": {},
            "selected_table_candidates": {},
            "extraction_grid_cache": {},
            "extraction_image_cache": {},
            "extraction_cache_lock": Lock(),
        })
    for key, value in values.items():
        st.session_state[key] = value


def render_locator_tab(active_profile: ReportProfile) -> None:
    """STEP1 智能页码定位。"""
    st.subheader("📑 智能页码定位")
    st.caption("系统结合页面语义推断与表格结构雷达定位目标表；跨页页码可在左侧人工修改，右侧同步显示PDF原页。")

    feature_config = dict(active_profile.feature_config)
    table_configs = feature_config.get("tables", [])
    config_version = active_profile.runtime_version
    if st.session_state.locator_config_version != config_version:
        st.session_state.locator_config_version = config_version
        _clear_page_locations()
        _clear_extraction_results()
        _drop_page_edit_widgets(table_configs)
    table_config_by_name = {item["table_name"]: item for item in table_configs}

    uploaded_pdf = st.file_uploader(
        f"拖拽或选择一份{active_profile.profile_name} PDF",
        type=["pdf"],
        key=f"report_pdf_{active_profile.profile_id}",
    )

    if uploaded_pdf is None:
        st.info("上传PDF并启动智能定位后，此处将显示可编辑页码和对应页面预览。")
    else:
        incoming = uploaded_pdf.getvalue()
        if incoming != st.session_state.pdf_bytes:
            st.session_state.pdf_bytes = incoming
            st.session_state.pdf_name = uploaded_pdf.name
            st.session_state.metadata = extract_report_metadata(incoming)
            try:
                st.session_state.document_type_profile = (
                    cached_analyze_document_type(incoming)
                )
            except Exception:
                st.session_state.document_type_profile = None
            # --- 提取整份 PDF 文本，用于抓取注册资本等公司信息 ---
            # ★ 双栏 PDF 的文本层会把标签和值分开排，注册资本常出现在第 3 页，
            #   为兼容各种排版，这里一次性读全文档文本；对几十页的 PDF 也就
            #   几百 KB，本地内存无压力。
            try:
                doc = fitz.open(stream=incoming, filetype="pdf")
                st.session_state.metadata["first_page_text"] = "\n".join(
                    (doc.load_page(_i).get_text() or "")
                    for _i in range(len(doc))
                )
                doc.close()
            except Exception as e:
                st.session_state.metadata["first_page_text"] = ""
            # ----------------------------------------------------------
            _clear_page_locations()
            _clear_extraction_results(clear_caches=True)
            _drop_page_edit_widgets(table_configs)

        total_pages = int(st.session_state.metadata.get("页数", 0) or 0)
        st.caption(f"当前文件：{uploaded_pdf.name}　|　文档共 {total_pages} 页")
        identity_warning = report_identity_warning(
            uploaded_pdf.name,
            st.session_state.metadata,
        )
        if identity_warning:
            st.warning(identity_warning, icon=":material/warning:")
        gold_case = find_gold_case(incoming, read_gold_manifest())
        if gold_case:
            st.success(
                f"已匹配真实PDF金标准：{gold_case['case_id']}。"
                "定位和提取结果会自动显示基准对比。"
            )
        with st.expander("查看自动识别的报告基本信息"):
            st.json(st.session_state.metadata)
        col_left, col_spacer, col_right = st.columns([1, 0.05, 1.2])
        with col_left:
            _render_locator_target_panel(
                active_profile, feature_config, table_configs, table_config_by_name,
                incoming, total_pages, gold_case,
            )
        with col_right:
            _render_locator_preview_panel(incoming, total_pages)


def _render_extraction_settings() -> None:
    """STEP2：逐页提取与重试设置。"""
    with st.expander("逐页提取与重试设置", expanded=not bool(st.session_state.raw_tables)):
        configured_model = st.session_state.get("llm_model", "") or "未设置"
        st.caption(f"使用登录页中的 AI 设置，当前模型：{configured_model}")
        option_cols = st.columns(2)
        with option_cols[0]:
            st.toggle(
                "单页提取失败时自动图片扫描重试",
                key="auto_vision_retry",
                help="只重试失败的物理页；模型不支持图片时会改用该页的加强版文本纠错。",
            )
        with option_cols[1]:
            st.toggle(
                "所有页面直接使用图片扫描模式",
                key="force_vision_mode",
                help=(
                    "系统已会自动识别无文字层页面；此开关用于文字层存在但排版异常、"
                    "仍希望所有已选页强制走图片识别的情况。"
                ),
            )
        st.caption("API Key仅保存在当前浏览器会话内，不会写入配置文件或导出的Excel。")


# ---- 跨页表格合并：表头识别与单位换算 ----
_HEADER_ALIAS = {
    "本期金额": "本季度数",
    "本期数": "本季度数",
    "上期金额": "本年度累计数",
    "上期数": "本年度累计数",
    "本期": "本季度数",
    "上期": "本年度累计数",
    "本年累计": "本年度累计数",
}
_HEADER_HINT_TOKENS = (
    "指标名称", "项目", "行次", "本季度", "上季度",
    "本年累计", "期末", "期初", "下季度", "本期", "上期",
)
_HEADER_HINTS = (
    "指标名称", "项目", "行次", "本季度", "上季度",
    "本年累计", "期末", "期初", "下季度",
    "保费风险", "准备金风险", "本期", "上期",
)


def canonical_header_cell(cell: object) -> str:
    """把表头别名（本期/上期/本年累计等）归一到统一写法。"""
    text = str(cell).strip()
    return _HEADER_ALIAS.get(text, text)


def looks_like_header(row) -> bool:
    """判断某一行是否像表头；纯括号说明行（如“（审计后）”）不算表头。"""
    text = "".join(str(c) for c in row)
    if not text.strip():
        return False
    stripped = re.sub(r"[（(][^）)]*[）)]", "", text).strip()
    if not stripped:
        return False
    return any(token in text for token in _HEADER_HINT_TOKENS)


def header_richness(row: list) -> int:
    """表头丰富度 = 命中关键词数 × 10 + 非空单元格数，越大越像表头。"""
    text = "".join(str(c) for c in row)
    hits = sum(1 for token in _HEADER_HINT_TOKENS if token in text)
    nonempty = sum(1 for cell in row if str(cell).strip())
    return hits * 10 + nonempty


def locate_header_row_index(raw_rows: list[list]) -> int:
    """在合并后的原始行中定位表头行。

    优先取同时含“本季度 + 上季度”或“本季度 + 本年累计”的行；
    组合没找到时取关键词命中最多的行，全部未命中才回退到第 0 行。
    """
    header_index = 0
    best_hits = 0
    for index, row in enumerate(raw_rows[:30]):
        row_text = "".join(str(c) for c in row)
        if "本季度" in row_text and ("上季度" in row_text or "本年累计" in row_text):
            return index
        hits = sum(1 for hint in _HEADER_HINTS if hint in row_text)
        if hits > best_hits:
            best_hits = hits
            header_index = index
    return header_index if best_hits else 0


def normalize_table_unit(
    grid: list[list],
    combined_raw_text: str,
    table_id: str,
    pages: list[int],
) -> tuple[list[list], str]:
    """嗅探表格单位并换算为万元；单位不可识别时按数值量级兜底。"""
    table_unit = detect_table_unit(grid)
    # ★ 仅在原逻辑失败（table_unit 为空）时扩大到全文，避免被其他页的“万元”误命中
    if not table_unit and combined_raw_text:
        raw_lines = [
            line for line in str(combined_raw_text).splitlines() if line.strip()
        ]
        fallback_unit = detect_table_unit([[line] for line in raw_lines])
        if fallback_unit:
            table_unit = fallback_unit
            st.session_state.setdefault("_unit_fallback_events", []).append({
                "table_id": table_id,
                "page": pages,
                "detected": table_unit,
            })

    if table_unit and table_unit != "万元":
        grid = convert_table_rows_to_wan(grid, table_unit)
        if table_unit in {"元", "千元"}:
            table_unit = "万元"
    elif grid and len(grid) > 1:
        value_cols = [
            index
            for index, header in enumerate(grid[0])
            if isinstance(header, str)
            and any(
                key in header
                for key in ["本季度末", "上季度末", "期末", "期初", "本季度数", "本年累计"]
            )
        ]
        for row in range(1, len(grid)):
            for column in value_cols:
                if column < len(grid[row]) and grid[row][column]:
                    try:
                        raw = str(grid[row][column]).replace(",", "")
                        value = float(raw)
                        if abs(value) > 1e12:
                            grid[row][column] = str(value / 10000)
                    except (ValueError, TypeError):
                        pass
    return grid, table_unit


def build_tables_from_page_results(
    results: dict[str, list[tuple]], use_vision: bool
) -> list[FlexibleTable]:
    """把逐页提取结果按目标表拼接，并做表头归一、单位换算。"""
    raw_tables = []
    for table_id, page_dfs in results.items():
        if not page_dfs:
            continue
        # 按页码排序
        page_dfs.sort(key=lambda x: x[0])

        # ============================================================
        # ★ 修复：不再用 pd.concat 依赖列名自动对齐。
        # 原因：同一张表跨页时，AI 可能对每一页返回不同的表头文本
        #      （如第 14 页"本季度数/本年度累计数"、第 15 页"本期金额/上期金额"），
        #      pd.concat 会把列名做并集，导致各页真实数据被错位或丢失。
        # 新策略：按"行"直接拼接，以第一页第一行为统一表头，
        #       后续页的重复表头行自动丢弃，剩余全部保留。
        # ============================================================
        _all_rows: list[list] = []
        for _page, _df, *_ in page_dfs:
            if _df is None or _df.empty:
                continue
            _all_rows.extend(_df.fillna("").astype(str).values.tolist())

        if not _all_rows:
            continue

        # ★ 跨页时挑选"最像表头"的那一行作为统一表头，而不是固定第一页。
        # 原因：财险的"主要经营指标"表跨 14、15 页，14 页表头是
        #      "监测指标 | 本季度 | 上季度"，15 页表头是
        #      "指标名称 | 本季度数 | 本年度累计数"。如果选 14 页表头，
        #      15 页的"本年度累计数"会被误解释为"上季度"。
        _header_candidates: list[tuple[int, list]] = []
        for _i, _r in enumerate(_all_rows[:20]):
            _candidate = [canonical_header_cell(c) for c in _r]
            if looks_like_header(_candidate):
                _header_candidates.append((_i, _candidate))

        if _header_candidates:
            # 选"丰富度最高"的作为统一表头
            _start_idx, _header_row = max(
                _header_candidates, key=lambda item: header_richness(item[1])
            )
        else:
            _start_idx = 0
            _header_row = [canonical_header_cell(c) for c in _all_rows[0]]

        # ---- 拼 body：跳过表头行本身 + 丢弃所有"与主表头完全相同"的后续表头 ----
        _header_row_key = tuple(str(c).strip() for c in _header_row)
        _body_rows: list[list] = []
        for _i, _r in enumerate(_all_rows):
            if _i == _start_idx:
                continue
            _r_key = tuple(str(c).strip() for c in _r)
            if _r_key == _header_row_key:
                # 后续页的重复表头行，直接丢弃
                continue
            # 后续页数据行的列名不作归一化处理（只对表头行归一）
            _body_rows.append([str(c).strip() for c in _r])

        merged_df = pd.DataFrame([_header_row] + _body_rows)

        # ★ 先提取页码信息（供下方单位嗅探使用）
        extracted_pages_int = [item[0] for item in page_dfs]
        extracted_pages_str = [str(p) for p in extracted_pages_int]
        pages_concat = ",".join(extracted_pages_str)

        # ★ 修复：item[2] 是 AI 提取日志，不是 PDF 原文。
        raw_texts = []
        _pdf_bytes_for_unit = st.session_state.get("pdf_bytes")
        if _pdf_bytes_for_unit and extracted_pages_int:
            try:
                _doc = fitz.open(
                    stream=_pdf_bytes_for_unit,
                    filetype="pdf",
                )
                for _p in extracted_pages_int:
                    if 1 <= _p <= len(_doc):
                        raw_texts.append(
                            _doc.load_page(_p - 1).get_text() or ""
                        )
                _doc.close()
            except Exception:
                raw_texts = []
        combined_raw_text = "\n".join(t for t in raw_texts if t)

        # 查找预期页码（用于缺失提示）
        expected_pages = []
        for match in st.session_state.page_matches:
            if match.table_id == table_id:
                expected_pages = list(match.pages)
                break

        # 如果缺页，添加备注行
        if expected_pages and len(extracted_pages_int) < len(expected_pages):
            missing_pages = [p for p in expected_pages if p not in extracted_pages_int]
            note_row = pd.DataFrame([{
                "注意": f"仅提取了 {len(extracted_pages_int)}/{len(expected_pages)} 页，缺失页码：{missing_pages}"
            }])
            merged_df = pd.concat([merged_df, note_row], ignore_index=True).copy()

        # 查找对应的 table 配置
        table_match = next((m for m in st.session_state.page_matches if m.table_id == table_id), None)

        safe_table_config = (
            {k: sanitize_scalar(v) for k, v in table_match.table_config.items()}
            if table_match and isinstance(table_match.table_config, dict)
            else None
        )
        # ★ 修复：merged_df 的列名是 0,1,2...，第一行才是真表头
        raw_rows = merged_df.fillna("").astype(str).values.tolist()
        if not raw_rows:
            continue

        _header_idx = locate_header_row_index(raw_rows)


        header_row = [str(c).strip() for c in raw_rows[_header_idx]]
        # 表头行之前的内容（说明文字、章节标题）另存，不进数据区
        preamble_rows = raw_rows[:_header_idx]
        data_rows = raw_rows[_header_idx + 1:]
        # 去掉后续页重复的表头行
        data_rows = [row for row in data_rows if row != raw_rows[_header_idx]]

        # ★ 表头去重（空列名补“列N”，重名追加 _2/_3…）
        header_row = dedupe_column_names(header_row)

        # ★ 修复：AI 把"本季末"/"上季末"丢成"（过渡期政策）"时，按列位置回填
        #   适用条件：至少 1 列仍是未知（空或纯括号），且列数 >= 3
        _header_text = "".join(str(c) for c in header_row)
        _has_quarter_hint = any(
            tok in _header_text
            for tok in ("本季度", "本季", "上季度", "上季", "下季度")
        )
        if (
            not _has_quarter_hint
            and len(header_row) >= 3
        ):
            # 只在第 1/2/3 列按经验回填（其他列保持原样）
            _period_by_index = {
                1: "本季度末",
                2: "上季度末",
                3: "下季度预测数",
            }
            for _pos, _fallback in _period_by_index.items():
                if _pos >= len(header_row):
                    break
                _cell = str(header_row[_pos]).strip()
                _stripped = re.sub(
                    r"[（(][^）)]*[）)]", "", _cell
                ).strip()
                if not _stripped:
                    header_row[_pos] = _fallback

        grid = [header_row] + data_rows

        grid_raw = [list(r) for r in grid]  # 留存原始网格备查

        # ---------- 单位嗅探与换算 ----------
        grid, table_unit = normalize_table_unit(
            grid,
            combined_raw_text,
            table_id,
            extracted_pages_int,
        )

        # ---------- 生成 DataFrame 副本（供 to_frame 使用） ----------
        # ★ 列名去重：空列名补“列N”，重复列名追加 _2/_3…
        deduped_columns = dedupe_column_names(grid[0])
        grid[0] = deduped_columns
        df_captured = pd.DataFrame(
            [list(r) for r in grid[1:]],
            columns=deduped_columns,
        )

        # ---------- 创建 FlexibleTable ----------
        table_obj = FlexibleTable(
            table_id=table_id,
            table_name=next((m.table_name for m in st.session_state.page_matches if m.table_id == table_id), table_id),
            page=extracted_pages_int[0] if extracted_pages_int else 1,
            source_pages=list(extracted_pages_int),
            rows=grid,
            to_frame=lambda *args, df=df_captured, **kwargs: df,   # 使用 df_captured
            strategy="并发" + ("视觉" if use_vision else "文本"),
            quality_score=1.0,
            evidence="",
            table_config=safe_table_config,
            unit_records=[],
            unit=table_unit,
            unit_source=table_unit or "未识别",
            source_grid_raw=grid_raw,
        )
        raw_tables.append(table_obj)
    return raw_tables


def _store_extraction_result(
    logs: list[dict], raw_tables: list[FlexibleTable]
) -> None:
    """保存提取结果并生成 STEP2 标准化提取工作簿。"""
    st.session_state.raw_tables = raw_tables
    st.session_state.ai_extraction_logs = logs

    # 生成 Excel 数据（确保每个表独立写入）
    output = io.BytesIO()
    try:
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            # 1. 写入提取日志
            log_df = pd.DataFrame(logs)
            if not log_df.empty:
                log_df.to_excel(writer, sheet_name="提取日志", index=False)

            # 2. 写入每个表格（独立 Sheet）
            for table in raw_tables:
                df = table.to_frame()
                if df.empty:
                    continue
                # 清理表名
                sheet_name = re.sub(r'[\\/*?:\[\]]', '', table.table_name)[:31]
                if not sheet_name:
                    sheet_name = "Sheet"
                # 避免重名
                idx = 1
                original_name = sheet_name
                while sheet_name in writer.sheets:
                    sheet_name = f"{original_name[:27]}_{idx}"
                    idx += 1
                df.to_excel(writer, sheet_name=sheet_name, index=False)

            # 3. 如果没有任何表格，写入提示
            if not raw_tables:
                pd.DataFrame({"提示": ["没有提取到任何表格数据"]}).to_excel(
                    writer, sheet_name="提示", index=False
                )

        st.session_state.ai_workbook_bytes = output.getvalue()
    except Exception as e:
        st.warning(f"生成 Excel 文件失败：{e}")
        st.session_state.ai_workbook_bytes = b""


def extraction_log_entry(
    task: dict, use_vision: bool, status: str, note: str
) -> dict:
    """构造一行逐页提取日志。"""
    return {
        "目标表": task["table_name"],
        "物理页码": task["page"],
        "识别方式": "视觉" if use_vision else "文本",
        "状态": status,
        "说明": note,
    }


def _run_extraction(active_profile: ReportProfile, tasks: list[dict]) -> None:
    """STEP2：并发提取各页、合并跨页表格并生成工作簿。"""
    progress_bar = st.progress(0)
    status_text = st.empty()
    total = len(tasks)
    results = {}  # table_id -> list of DataFrames
    logs = []
    with st.status("正在并发提取各页面...", expanded=True) as status:
        # 根据是否强制图片模式选择引擎
        use_vision = st.session_state.force_vision_mode
        max_workers = 4  # 可调节

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_task = {}
            for task in tasks:
                if use_vision:
                    future = executor.submit(
                        extract_single_page_vision,
                        st.session_state.pdf_bytes,
                        task["page"],
                        task["table_name"],
                        st.session_state.llm_api_key,
                        st.session_state.llm_base_url,
                        st.session_state.llm_model
                    )
                else:
                    future = executor.submit(
                        extract_single_page,
                        st.session_state.pdf_bytes,
                        task["page"],
                        task["table_name"],
                        st.session_state.llm_api_key,
                        st.session_state.llm_base_url,
                        st.session_state.llm_model
                    )
                future_to_task[future] = task

            completed = 0
            for future in as_completed(future_to_task):
                task = future_to_task[future]
                df, log = future.result()
                completed += 1
                progress_bar.progress(completed / total)
                status_text.text(f"已处理 {completed}/{total} 页：{task['table_name']} 第 {task['page']} 页")

                if df is not None and not df.empty:
                    table_id = task["table_id"]
                    if table_id not in results:
                        results[table_id] = []
                    # ★ 把 raw_text 一起存进去，用于单位嗅探
                    results[table_id].append((task["page"], df, log))
                    logs.append(
                        extraction_log_entry(
                            task, use_vision, "成功", f"提取到 {len(df)} 行数据"
                        )
                    )
                else:
                    logs.append(
                        extraction_log_entry(
                            task, use_vision, "失败", log[:200] if log else "无数据返回"
                        )
                    )
        raw_tables = build_tables_from_page_results(results, use_vision)
        _store_extraction_result(logs, raw_tables)
    failed_count = sum(1 for log in logs if log["状态"] == "失败")
    if failed_count:
        st.warning(f"有 {failed_count} 页提取失败，请查看下方日志。")
    else:
        st.success("所有页面提取成功！")


def _render_extraction_launcher(active_profile: ReportProfile) -> None:
    """STEP2：提取按钮、前置校验与任务编排。"""
    if st.button("开始智能提取", type="primary", key="extract_tables"):
        if not llm_credentials_ready():
            st.error(
                "当前登录会话的 AI 配置不完整。请退出登录，在登录页填写 "
                "Base URL、模型名称和 API Key 后重新进入。"
            )
        else:
            # 准备任务列表
            tasks = []
            for match in st.session_state.page_matches:
                for page_num in match.pages:
                    tasks.append({
                        "table_id": match.table_id,
                        "table_name": match.table_name,
                        "page": page_num
                    })

            if not tasks:
                st.warning("没有需要提取的页面，请确认页码配置。")
            else:
                _run_extraction(active_profile, tasks)


def _render_extraction_results() -> None:
    """STEP2：中间表预览、金标准对比与下载。"""
    if st.session_state.raw_tables:
        tables = st.session_state.raw_tables
        gold_case = find_gold_case(
            st.session_state.pdf_bytes,
            read_gold_manifest(),
        )
        if gold_case:
            with st.expander("真实PDF金标准 - 表格提取结果", expanded=True):
                st.dataframe(
                    evaluate_gold_case(
                        gold_case,
                        matches=st.session_state.page_matches,
                        tables=tables,
                    ),
                    width="stretch",
                    hide_index=True,
                )

        def format_reconstructed_table(index: int) -> str:
            item = tables[index]
            pages = item.source_pages or [item.page]
            page_label = "、".join(map(str, pages))
            return f"{item.table_name} - 第{page_label}页 - {item.strategy}"

        selected_table_index = st.selectbox(
            "标准化中间表预览",
            range(len(tables)),
            format_func=format_reconstructed_table,
            key="ai_table_preview",
        )
        selected_table = tables[selected_table_index]
        source_pages = selected_table.source_pages or [selected_table.page]

        metric_cols = st.columns(3)
        metric_cols[0].metric("处理方式", selected_table.strategy)
        metric_cols[1].metric("来源页数", len(source_pages))
        metric_cols[2].metric("质量评分", f"{selected_table.quality_score:.1f}")
        if selected_table.evidence:
            st.caption(f"自动校验依据：{selected_table.evidence}")

        table_col, pdf_col = st.columns([1.15, 1], gap="large")
        with table_col:
            st.markdown("##### 网格化重构结果")
            st.dataframe(selected_table.to_frame(include_unit_footer=True), width="stretch", hide_index=True)
        with pdf_col:
            # 将 source_pages 从字符串还原为整数列表
            source_pages_raw = selected_table.source_pages
            if isinstance(source_pages_raw, str):
                source_pages_list = [int(p.strip()) for p in source_pages_raw.split(',') if p.strip().isdigit()]
            else:
                source_pages_list = source_pages_raw if isinstance(source_pages_raw, list) else [source_pages_raw]

            selected_source_page = st.selectbox(
                "PDF原页核实",
                options=source_pages_list,
                format_func=lambda value: f"第 {value} 页",
                key=f"ai_source_page_{selected_table.table_id}",
            )
            try:
                st.image(
                    render_pdf_page(st.session_state.pdf_bytes, selected_source_page),
                    caption=f"PDF物理第 {selected_source_page} 页",
                    width="stretch",
                )
            except Exception as exc:
                st.warning(f"PDF页面预览失败：{exc}")

        if st.session_state.ai_workbook_bytes:
            st.download_button(
                "下载STEP2标准化提取Excel",
                st.session_state.ai_workbook_bytes,
                "偿付能力报告_STEP2标准化提取.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )


def render_extraction_tab(active_profile: ReportProfile) -> None:
    """STEP2 表格智能转换。"""
    st.subheader("表格智能转换")
    st.caption(
        "采用逐页结构化提取：每个物理页独立处理，失败页单独进行图片扫描或网格纠错，"
        "所有页面成功后再按人工确认的页码顺序确定性拼接；任何一页失败都不会输出残缺跨页表。"
    )
    if not st.session_state.page_matches or not st.session_state.pages_confirmed:
        st.info("请先在 STEP1 上传报告、完成页码定位并人工确认物理页码。")
    else:
        _render_extraction_settings()
        _render_extraction_launcher(active_profile)
    if st.session_state.ai_extraction_logs:
        st.markdown("##### 提取与重试日志")
        logs = st.session_state.ai_extraction_logs
        if logs and isinstance(logs[0], dict):
            # 自定义列顺序，与原始日志列名保持一致
            columns = ["目标表", "物理页码", "识别方式", "状态", "说明"]
            df = pd.DataFrame(logs, columns=columns)
        else:
            # 兼容旧格式（对象列表）
            df = extraction_logs_frame(logs)
        st.dataframe(df, width="stretch", hide_index=True)
        _render_extraction_results()


def _render_step3_single_tab(
    active_profile: ReportProfile, taxonomy: pd.DataFrame
) -> None:
    """STEP3：单份 PDF 目标表标准填报。"""
    st.subheader(f"{active_profile.profile_name}目标表标准填报")
    with st.container(border=True):
        st.markdown("#### 功能说明")
        st.write(
            "按目标指标清单将 STEP2 提取结果填入标准窄表，并自动完成单位换算与派生指标计算。"
            "输出字段和可用指标范围与 STEP5 的精确映射口径完全一致。"
        )

    include_derived = active_profile.profile_id == "LIFE_SOLVENCY"
    system_target_catalog = step3_metric_catalog(
        taxonomy,
        include_derived=include_derived,
    )

    use_default_target = st.toggle(
        "使用系统默认目标表",
        value=True,
        key="step3_use_default_target",
        help="默认目标表直接引用 STEP5 当前可映射指标目录。关闭后可上传自定义指标子集。",
    )
    target_upload = None
    if use_default_target:
        with st.container(horizontal=True, vertical_alignment="center"):
            st.caption(
                f"当前默认目标表包含 {len(system_target_catalog):,} 个指标，"
                "可下载后将不需要的指标设为“不启用”。"
            )
            st.download_button(
                "下载系统目标表",
                cached_step3_target_template(
                    system_target_catalog,
                    active_profile.profile_name,
                ),
                f"{active_profile.profile_id}_STEP3目标表.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                icon=":material/download:",
                key=f"download_step3_target_{active_profile.profile_id}",
            )
    else:
        target_upload = st.file_uploader(
            "上传自定义目标表",
            type=["xlsx"],
            key="step3_target_upload",
            help="建议使用系统目标表修改“启用”列；指标编码不得超出 STEP3 正式指标字典。",
        )

    company_type = active_profile.company_types[0]
    peer_group_map, default_peer_group = read_peer_groups(
        PEER_GROUP_CONFIG,
        PEER_GROUP_CONFIG.stat().st_mtime_ns,
    )
    standardize_submitted = st.button(
        "启动目标表标准填报",
        type="primary",
        icon=":material/auto_fix_high:",
        disabled=(
            not st.session_state.raw_tables
            or (not use_default_target and target_upload is None)
        ),
        key="step3_standardize_target",
    )

    if not st.session_state.raw_tables:
        st.info("请先在 STEP2 完成表格提取。")
    elif not use_default_target and target_upload is None:
        st.info("请上传自定义目标表，或切回系统默认目标表。")

    if standardize_submitted:
        st.session_state.standard_data = pd.DataFrame(columns=STANDARD_COLUMNS)
        st.session_state.validation_results = pd.DataFrame()
        st.session_state.standard_workbook_bytes = b""
        st.session_state.normalization_diagnostics = pd.DataFrame()
        st.session_state.standard_target_summary = pd.DataFrame()
        st.session_state.standardization_warnings = []
        with st.status("正在进行目标表标准填报…", expanded=True) as status_box:
            try:
                for table in st.session_state.raw_tables:
                    if isinstance(getattr(table, "table_config", None), dict):
                        table.table_config = sanitize_tree(table.table_config)
                    if not isinstance(getattr(table, "evidence", ""), str):
                        table.evidence = sanitize_scalar(table.evidence)
                    if not isinstance(getattr(table, "table_name", None), str):
                        table.table_name = sanitize_scalar(table.table_name)

                status_box.write("正在读取目标指标清单并校验 STEP3 正式指标范围…")
                if use_default_target:
                    target_catalog = system_target_catalog
                    template_warnings: tuple[str, ...] = ()
                    template_label = "系统默认目标表"
                else:
                    target_catalog, template_warnings = read_target_template(
                        target_upload.getvalue(),
                        target_upload.name,
                        system_target_catalog,
                    )
                    template_label = target_upload.name

                status_box.write("正在匹配披露指标、识别期间口径并统一计量单位…")
                metadata = sanitize_tree({
                    **st.session_state.metadata,
                    "来源文件": st.session_state.pdf_name,
                    # ★ 兜底：把 first_page_text 显式带过来
                    "first_page_text": str(
                        st.session_state.metadata.get("first_page_text", "")
                    ),
                    "导入批次": (
                        f"{Path(st.session_state.pdf_name).stem}:"
                        f"{st.session_state.metadata.get('报告期', '')}"
                    ),
                })
                result = standardize_to_target(
                    st.session_state.raw_tables,
                    taxonomy,
                    metadata,
                    company_type,
                    target_catalog,
                    report_profile_id=active_profile.profile_id,
                    allowed_company_types=active_profile.company_types,
                    peer_group_map=peer_group_map,
                    default_peer_group=default_peer_group,
                    include_derived=include_derived,
                    template_warnings=template_warnings,
                )

                status_box.write("正在生成 STEP5 可直接读取的标准窄表工作簿…")
                st.session_state.standard_data = result.data

                # ---------- 自动补注册资本（单份） ----------
                _company_name = ""
                if (
                    not st.session_state.standard_data.empty
                    and "公司" in st.session_state.standard_data.columns
                ):
                    _company_name = str(st.session_state.standard_data["公司"].iloc[0]).strip()
                if not _company_name:
                    _company_name = Path(st.session_state.pdf_name).stem

                _report_period = str(st.session_state.metadata.get("报告期", "")).strip()
                if not _report_period:
                    _report_period = "2025Q4"

                st.session_state.standard_data = append_registered_capital(
                    st.session_state.standard_data,
                    pdf_text=st.session_state.metadata.get("first_page_text", ""),
                    pdf_bytes=st.session_state.pdf_bytes,
                    pdf_name=st.session_state.pdf_name,
                    company_name=_company_name,
                    report_period=_report_period,
                    company_type=company_type,
                )

                # ★ 关键：把更新后的 standard_data 写回 result，再生成 workbook
                # frozen dataclass 不能直接赋值，需要用 dataclasses.replace 生成新对象
                result = dataclasses.replace(
                    result,
                    data=st.session_state.standard_data,
                    target_summary=_target_summary(
                        result.target_catalog,
                        st.session_state.standard_data,
                    ),
                )
                st.session_state.standard_workbook_bytes = result_workbook_bytes(result)
                st.session_state.normalization_diagnostics = result.diagnostics
                st.session_state.standard_target_catalog = result.target_catalog
                st.session_state.standard_target_summary = result.target_summary
                st.session_state.standardization_warnings = list(result.warnings)
                st.session_state.standard_template_label = template_label
                status_box.update(
                    label="目标表标准填报完成",
                    state="complete",
                    expanded=False,
                )
            except Exception as exc:
                status_box.update(
                    label="目标表标准填报失败",
                    state="error",
                    expanded=True,
                )
                st.error(f"标准填报失败：{exc}")
                import traceback
                st.exception(exc)
                st.code(traceback.format_exc())

    if not st.session_state.standard_target_summary.empty:
        summary = st.session_state.standard_target_summary
        filled_targets = int((summary["填报状态"] == "已填报").sum())
        missing_targets = int((summary["填报状态"] == "未填报").sum())
        with st.container(horizontal=True):
            st.metric("目标指标", len(summary), border=True)
            st.metric("已填报指标", filled_targets, border=True)
            st.metric("未填报指标", missing_targets, border=True)
            st.metric("窄表记录", len(st.session_state.standard_data), border=True)
        st.caption(f"本次使用：{st.session_state.standard_template_label}")
        for warning in st.session_state.standardization_warnings:
            st.warning(warning)
        if missing_targets:
            with st.expander("查看未填报目标指标", expanded=True):
                st.dataframe(
                    summary[summary["填报状态"] == "未填报"],
                    width="stretch",
                    hide_index=True,
                )

    if not st.session_state.normalization_diagnostics.empty:
        with st.expander("查看未匹配或歧义诊断"):
            st.dataframe(
                st.session_state.normalization_diagnostics,
                width="stretch",
                hide_index=True,
            )

    if not st.session_state.standard_data.empty:
        derived_count = int(
            st.session_state.standard_data["指标属性"].isin(["计算", "校验"]).sum()
        )
        converted_rows = int(
            st.session_state.standard_data["备注"]
            .astype(str)
            .str.contains("已换算为", regex=False)
            .sum()
        )
        pending_unit_rows = int(
            st.session_state.standard_data["备注"]
            .astype(str)
            .str.contains("未识别原始单位", regex=False)
            .sum()
        )
        with st.container(horizontal=True):
            st.metric("派生及校验记录", derived_count, border=True)
            st.metric("单位换算记录", converted_rows, border=True)
            st.metric("单位待核对记录", pending_unit_rows, border=True)
        st.markdown("#### 标准窄表预览")
        step3_narrow_display = narrow_table_view(st.session_state.standard_data)
        st.dataframe(step3_narrow_display, width="stretch", hide_index=True)
        st.download_button(
            "下载已填报的标准窄表",
            (
                st.session_state.standard_workbook_bytes
                or standard_workbook_bytes(st.session_state.standard_data)
            ),
            f"{Path(st.session_state.pdf_name).stem}_STEP3标准窄表.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            icon=":material/download:",
        )


def _render_batch_prerequisites(is_project_member: bool) -> bool:
    """STEP3 批处理：权限与 AI 配置检查。"""
    _batch_ready = True
    if not is_project_member:
        st.info("批量处理仅对项目组成员开放。", icon=":material/lock:")
        _batch_ready = False
    if not llm_credentials_ready():
        st.warning(
            "当前会话的 AI 配置不完整。请退出登录，在登录页填写 "
            "Base URL、模型名称和 API Key 后重新进入。",
            icon=":material/warning:",
        )
        _batch_ready = False
    return _batch_ready


def _render_batch_upload(active_profile: ReportProfile, ready: bool) -> list:
    """STEP3 批处理：多份 PDF 上传区。"""
    _batch_ready = ready
    # ---------- 上传区 ----------
    uploaded_pdfs = st.file_uploader(
        "一次拖拽或选择多份 PDF（文件名 = 公司名，例如「平安产险.pdf」）",
        type=["pdf"],
        accept_multiple_files=True,
        key=f"batch_pdfs_{active_profile.profile_id}",
        disabled=not _batch_ready,
    )

    if uploaded_pdfs:
        _names = [Path(f.name).stem for f in uploaded_pdfs]
        _dup_names = sorted({n for n in _names if _names.count(n) > 1})
        if _dup_names:
            st.warning(
                "以下公司名重复，请确保文件名唯一："
                + "、".join(_dup_names),
                icon=":material/warning:",
            )
        st.success(f"已选择 {len(uploaded_pdfs)} 份 PDF。")
    return uploaded_pdfs


def _render_batch_settings() -> SimpleNamespace:
    """STEP3 批处理：并发数、图片识别与跳过策略设置。"""
    with st.expander("批处理设置", expanded=True):
        _c1, _c2, _c3 = st.columns(3)
        with _c1:
            _batch_workers = st.slider(
                "并发数", min_value=1, max_value=5, value=3,
                key="batch_workers",
                help="越高越快，但可能触发平台限流。",
            )
        with _c2:
            _batch_vision = st.toggle(
                "强制图片识别",
                value=False,
                key="batch_force_vision",
                help="文字层异常时开启。",
            )
        with _c3:
            _batch_only_new = st.toggle(
                "跳过已成功过的公司",
                value=False,
                key="batch_skip_success",
                help="上一次批量结果里状态为「成功」的公司会跳过，只重跑失败/异常的。",
            )
    return SimpleNamespace(
        workers=int(_batch_workers),
        vision=bool(_batch_vision),
        only_new=bool(_batch_only_new),
    )


def _prepare_batch_context(active_profile: ReportProfile, use_vision: bool):
    """构建批处理上下文；失败时提示并中断本次渲染。"""
    with st.status("正在准备批处理上下文…", expanded=False) as status_box:
        try:
            context = build_context_from_profile(
                active_profile,
                api_key=st.session_state.llm_api_key,
                base_url=st.session_state.llm_base_url,
                model=st.session_state.llm_model,
                use_vision=use_vision,
                company_type_map={},
            )
            status_box.update(label="批处理上下文已就绪", state="complete")
        except Exception as exc:
            status_box.update(label="上下文准备失败", state="error")
            st.error(f"上下文准备失败：{exc}")
            st.stop()
    return context


def batch_summary_frame(
    results: list[dict], *, note_limit: int | None = None
) -> pd.DataFrame:
    """把批处理结果整理成汇总表；note_limit 用于限制说明列长度。"""
    rows = []
    for item in results:
        note = str(item.get("说明", ""))
        rows.append({
            "公司": item["公司"],
            "状态": item["状态"],
            "定位表数": item.get("定位表数", 0),
            "提取表数": item.get("提取表数", 0),
            "窄表记录数": item.get("窄表记录数", 0),
            "说明": note[:note_limit] if note_limit else note,
        })
    return pd.DataFrame(rows)


def _render_batch_locate(
    active_profile: ReportProfile,
    uploaded_pdfs: list,
    settings: SimpleNamespace,
    ready: bool,
) -> None:
    """STEP3 批处理阶段 1：批量定位。"""
    _batch_ready = ready
    _batch_workers = settings.workers
    _batch_vision = settings.vision
    st.markdown("### 阶段 1：批量定位")
    st.caption("系统对每份 PDF 只跑页码定位，不调用大模型提取表格，结果供人工核对。")
    _locate_clicked = st.button(
        "🎯 开始批量定位",
        type="primary",
        key="batch_locate",
        disabled=not _batch_ready or not uploaded_pdfs,
        width="stretch",
    )

    if _locate_clicked and uploaded_pdfs:
        _ctx = _prepare_batch_context(active_profile, _batch_vision)

        _locate_progress = st.progress(0, text="正在定位…")
        _locate_results: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=_batch_workers) as _pool:
            _futs = {
                _pool.submit(
                    locate_one_pdf_bytes,
                    Path(_f.name).stem,
                    _f.getvalue(),
                    _ctx,
                ): Path(_f.name).stem
                for _f in uploaded_pdfs
            }
            for _i, _fut in enumerate(as_completed(_futs), 1):
                _res = _fut.result()
                _locate_results[_res["公司"]] = _res
                _locate_progress.progress(
                    _i / len(_futs),
                    text=f"已定位 {_i}/{len(_futs)}：{_res['公司']}",
                )
        _locate_progress.empty()

        # ★ 新增：缓存每份 PDF 的原始 bytes，供阶段 2 页面预览使用
        st.session_state["batch_pdf_bytes"] = {
            Path(_f.name).stem: _f.getvalue()
            for _f in uploaded_pdfs
        }

        st.session_state["batch_located"] = _locate_results
        st.session_state.pop("batch_last_results", None)  # 清上一轮结果
        st.success(f"已完成 {len(_locate_results)} 家公司的定位，请核对页码。")


def _render_batch_page_review() -> None:
    """STEP3 批处理阶段 2：核对并修正页码（含页面预览）。"""
    # ============================================================
    # 阶段 2：页码确认与编辑
    # ============================================================
    _located = st.session_state.get("batch_located")
    _edited_overrides: dict[str, dict[str, list[int]]] = {}
    if _located:
        st.markdown("### 阶段 2：核对并修正页码")
        st.caption(
            "🔴 标红的行表示系统定位存在冲突或不确定，请重点核对。"
            "其他行如无问题，可直接进入下一步。"
        )

        _conflict_count = 0
        _no_match_count = 0
        for _company, _info in _located.items():
            if _info["状态"] != "成功":
                st.error(f"❌ {_company}：定位失败 — {_info['说明']}")
                continue

            _matches = _info.get("matches") or []
            if not _matches:
                st.warning(f"⚠️ {_company}：未定位到任何目标表")
                _no_match_count += 1
                continue

            _edited_overrides[_company] = {}
            with st.expander(f"📄 {_company}（{len(_matches)} 张表）", expanded=False):
                for _m in _matches:
                    _auto_pages = ", ".join(map(str, _m.pages)) if _m.pages else ""
                    _input_key = f"batch_page_{_company}_{_m.table_id}"
                    if _input_key not in st.session_state:
                        st.session_state[_input_key] = _auto_pages

                    _is_conflict = getattr(_m, "review_required", False)
                    if _is_conflict:
                        _conflict_count += 1

                    _col1, _col2 = st.columns([1, 2])
                    with _col1:
                        st.markdown(
                            f"**{_m.table_name}**"
                            + (" 🔴" if _is_conflict else "")
                        )
                        if _is_conflict:
                            st.caption(f"⚠ {_m.review_reason}")
                    with _col2:
                        _raw = st.text_input(
                            "页码（逗号分隔）",
                            key=_input_key,
                            label_visibility="collapsed",
                            placeholder="例如：26,27",
                        )
                        _pages, _invalid = parse_page_numbers(_raw, 9999)
                        if _invalid:
                            st.warning(f"无效页码：{_invalid}")
                        _edited_overrides[_company][_m.table_name] = _pages
                        if _m.pages and _pages != list(_m.pages):
                            st.caption(f"原自动定位：{_auto_pages} → 已修改")

                # ============================================================
                # ★ 新增：页面预览区
                # 用当前输入框里的页码（非原始定位），用户改完立刻能看效果
                # ============================================================
                st.markdown("---")
                st.markdown("**📷 页面预览**")

                _preview_table_names = [m.table_name for m in _matches]
                _preview_table_map = {m.table_name: m for m in _matches}
                _selected_preview_table = st.selectbox(
                    "选择要预览的表",
                    _preview_table_names,
                    key=f"batch_preview_table_{_company}",
                )
                _sel_match = _preview_table_map[_selected_preview_table]

                # 优先用输入框当前值；若为空，回退到原始自动定位
                _current_raw = st.session_state.get(
                    f"batch_page_{_company}_{_sel_match.table_id}", ""
                )
                _current_pages, _ = parse_page_numbers(_current_raw, 9999)
                if not _current_pages and _sel_match.pages:
                    _current_pages = list(_sel_match.pages)

                if not _current_pages:
                    st.info("该表当前无有效页码，请在上方输入。")
                else:
                    if len(_current_pages) > 1:
                        _sel_page = st.radio(
                            "切换页",
                            _current_pages,
                            horizontal=True,
                            key=f"batch_preview_page_{_company}_{_sel_match.table_id}",
                        )
                    else:
                        _sel_page = _current_pages[0]

                    _pdf_bytes_cache = st.session_state.get("batch_pdf_bytes", {})
                    if _company in _pdf_bytes_cache:
                        try:
                            _img = render_pdf_page(
                                _pdf_bytes_cache[_company], _sel_page
                            )
                            st.image(
                                _img,
                                caption=f"{_company} — 第 {_sel_page} 页",
                                width="stretch",
                            )
                        except Exception as _e:
                            st.warning(f"预览失败：{_e}")
                    else:
                        st.warning(
                            "PDF 缓存不可用（可能未完成定位或已清空）。"
                            "请重新点击「🎯 开始批量定位」。"
                        )

        if _conflict_count:
            st.warning(f"⚠️ 共 {_conflict_count} 张表存在定位冲突，建议修正后再提取。")
        elif _no_match_count == 0:
            st.success("✅ 所有目标表定位无冲突，可直接提取。")

        st.session_state["batch_page_overrides"] = _edited_overrides


def _render_batch_extract(
    active_profile: ReportProfile,
    uploaded_pdfs: list,
    settings: SimpleNamespace,
    ready: bool,
) -> None:
    """STEP3 批处理阶段 3：执行提取。"""
    _batch_ready = ready
    _batch_workers = settings.workers
    _batch_vision = settings.vision
    _batch_only_new = settings.only_new
    _located = st.session_state.get("batch_located")
    # ============================================================
    # 阶段 3：执行提取
    # ============================================================
    st.markdown("### 阶段 3：执行提取")
    _extract_clicked = st.button(
        "🚀 确认页码，开始批量提取",
        type="primary",
        key="batch_extract",
        disabled=not _batch_ready or not uploaded_pdfs or not _located,
        width="stretch",
    )

    if _extract_clicked and uploaded_pdfs:
        _prev_results = st.session_state.get("batch_last_results", {})
        _prev_success_names = {
            n for n, r in _prev_results.items() if r.get("状态") == "成功"
        }

        _ctx2 = _prepare_batch_context(active_profile, _batch_vision)

        _todo = []
        _skipped = []
        for _f in uploaded_pdfs:
            _name = Path(_f.name).stem
            if _batch_only_new and _name in _prev_success_names:
                _skipped.append(_name)
                continue
            _todo.append((_name, _f.getvalue()))

        if _skipped:
            st.info(
                f"跳过 {len(_skipped)} 家已成功的公司：{'、'.join(_skipped)}"
            )

        if not _todo:
            st.warning("没有需要处理的文件。")
        else:
            _progress = st.progress(0, text="准备中…")
            _progress_text = st.empty()
            _live_log = st.empty()

            _results: list[dict] = []
            _total = len(_todo)
            _overrides_all = st.session_state.get("batch_page_overrides", {})

            with ThreadPoolExecutor(max_workers=_batch_workers) as _pool:
                _futures = {
                    _pool.submit(
                        process_one_pdf_bytes,
                        _name,
                        _bytes,
                        _ctx2,
                        _overrides_all.get(_name, {}),
                    ): _name
                    for _name, _bytes in _todo
                }
                for _i, _fut in enumerate(as_completed(_futures), 1):
                    _name = _futures[_fut]
                    try:
                        _res = _fut.result()
                    except Exception as _e:
                        _res = {
                            "公司": _name,
                            "状态": "异常",
                            "定位表数": 0,
                            "提取表数": 0,
                            "窄表记录数": 0,
                            "说明": str(_e)[:200],
                            "workbook_bytes": b"",
                        }
                    _results.append(_res)

                    _progress.progress(
                        _i / _total,
                        text=f"已完成 {_i}/{_total}：{_res['公司']} — {_res['状态']}",
                    )
                    _progress_text.text(_res.get("说明", ""))

                    _live_log.dataframe(
                        batch_summary_frame(_results, note_limit=80),
                        width="stretch",
                        hide_index=True,
                    )

            _progress.empty()
            _progress_text.empty()

            st.session_state["batch_last_results"] = {
                _r["公司"]: _r for _r in _results
            }
            st.session_state["batch_last_active_profile"] = active_profile.profile_id


def _render_batch_results(active_profile: ReportProfile) -> None:
    """STEP3 批处理：结果汇总、打包下载与清空。"""
    # ============================================================
    # 结果展示与下载（保留原逻辑）
    # ============================================================
    _stored = st.session_state.get("batch_last_results", {})
    _stored_profile = st.session_state.get("batch_last_active_profile", "")
    if _stored and _stored_profile == active_profile.profile_id:
        _res_list = list(_stored.values())
        _ok = sum(1 for r in _res_list if r.get("状态") == "成功")
        _fail = len(_res_list) - _ok

        with st.container(border=True):
            st.markdown("#### 批处理结果")
            _m1, _m2, _m3 = st.columns(3)
            _m1.metric("总数", len(_res_list))
            _m2.metric("✅ 成功", _ok)
            _m3.metric("⚠️ 需复核", _fail)

            _summary_df = batch_summary_frame(_res_list)
            st.dataframe(_summary_df, width="stretch", hide_index=True)

            if _ok > 0:
                _zip_buffer = io.BytesIO()
                with zipfile.ZipFile(_zip_buffer, "w", zipfile.ZIP_DEFLATED) as _zf:
                    for _r in _res_list:
                        if _r.get("workbook_bytes"):
                            _fname = f"{_r['公司']}_STEP3标准窄表.xlsx"
                            _zf.writestr(_fname, _r["workbook_bytes"])

                    _summary_xlsx = dataframe_to_xlsx(_summary_df, "批处理汇总")
                    _zf.writestr("_批处理汇总.xlsx", _summary_xlsx)

                _zip_bytes = _zip_buffer.getvalue()

                _dl1, _dl2 = st.columns(2)
                with _dl1:
                    st.download_button(
                        f"📦 下载全部结果（{_ok} 家，ZIP）",
                        _zip_bytes,
                        file_name=(
                            f"{active_profile.profile_id}_批量提取"
                            f"_{len(_res_list)}家.zip"
                        ),
                        mime="application/zip",
                        type="primary",
                        width="stretch",
                        key="batch_dl_zip",
                    )
                with _dl2:
                    st.download_button(
                        "📊 下载汇总表",
                        dataframe_to_xlsx(_summary_df, "批处理汇总"),
                        file_name=f"{active_profile.profile_id}_批处理汇总.xlsx",
                        mime=(
                            "application/vnd.openxmlformats-officedocument"
                            ".spreadsheetml.sheet"
                        ),
                        width="stretch",
                        key="batch_dl_summary",
                    )

            if _fail > 0:
                with st.expander(f"⚠️ {_fail} 家未成功，点开查看明细", expanded=False):
                    st.dataframe(
                        _summary_df[_summary_df["状态"] != "成功"],
                        width="stretch",
                        hide_index=True,
                    )

            if st.button("🗑️ 清空当前批处理结果", key="batch_clear"):
                st.session_state.pop("batch_last_results", None)
                st.session_state.pop("batch_last_active_profile", None)
                st.session_state.pop("batch_located", None)
                st.session_state.pop("batch_page_overrides", None)
                st.rerun()


def _render_step3_batch_tab(
    active_profile: ReportProfile, is_project_member: bool
) -> None:
    """STEP3：批量处理多家公司。"""
    st.subheader("🗂️ 批量提取多家公司偿付能力数据")
    st.caption(
        "两阶段批处理：先定位预览（可人工修正页码），再执行提取与标准化，"
        "避免因页码定位错误导致整份报告数据缺失。"
    )
    batch_ready = _render_batch_prerequisites(is_project_member)
    st.caption(
        f"当前 Profile：{active_profile.profile_name}（{active_profile.profile_id}）｜"
        f"默认公司类型：{active_profile.company_types[0]}"
    )
    uploaded_pdfs = _render_batch_upload(active_profile, batch_ready)
    settings = _render_batch_settings()
    _render_batch_locate(active_profile, uploaded_pdfs, settings, batch_ready)
    _render_batch_page_review()
    _render_batch_extract(active_profile, uploaded_pdfs, settings, batch_ready)
    _render_batch_results(active_profile)


def render_standardization_tab(
    active_profile: ReportProfile,
    taxonomy: pd.DataFrame,
    is_project_member: bool,
) -> None:
    """STEP3 标准化：单份处理 + 批量处理。"""
    _step3_single_tab, _step3_batch_tab = st.tabs(
        ["📄 单份处理（当前 PDF）", "🗂️ 批量处理（多家公司）"]
    )
    with _step3_single_tab:
        _render_step3_single_tab(active_profile, taxonomy)
    with _step3_batch_tab:
        _render_step3_batch_tab(active_profile, is_project_member)


def render_validation_tab(
    active_profile: ReportProfile,
    rules_path: Path,
    taxonomy: pd.DataFrame,
) -> None:
    """STEP4 勾稽检查。"""
    st.subheader(f"{active_profile.profile_name}勾稽检查")
    st.caption(
        "检查 STEP3 标准窄表的数据规范、六项关键指标完整性、公式勾稽和跨表一致性；"
        "检查过程只读，不会自动修改标准化结果。"
    )
    if st.session_state.standard_data.empty:
        st.info("请先完成 STEP3 标准化。")
    elif st.button(
        "执行勾稽检查",
        type="primary",
        key="validate_data",
        icon=":material/fact_check:",
    ):
        st.session_state.validation_results = validate_standard_data(
            st.session_state.standard_data,
            read_rules(str(rules_path)),
            taxonomy,
        )
    validation_results = st.session_state.validation_results
    required_result_columns = {"check_type", "rule_name", "status", "severity"}
    if not validation_results.empty and not required_result_columns.issubset(validation_results.columns):
        st.info("Step4 规则已升级，请重新点击“执行勾稽检查”生成新版结果。")
    elif not validation_results.empty:
        status_counts = validation_results["status"].astype(str).value_counts().to_dict()
        summary_columns = st.columns(6)
        summary_columns[0].metric("检查项", f"{len(validation_results):,}")
        summary_columns[1].metric("通过", f"{status_counts.get('通过', 0):,}")
        summary_columns[2].metric("未通过", f"{status_counts.get('未通过', 0):,}")
        summary_columns[3].metric("缺失", f"{status_counts.get('缺失', 0):,}")
        summary_columns[4].metric("需复核", f"{status_counts.get('需复核', 0):,}")
        summary_columns[5].metric(
            "不适用/错误",
            f"{status_counts.get('不适用', 0) + status_counts.get('执行错误', 0):,}",
        )

        blocking_count = sum(
            status_counts.get(status, 0)
            for status in ("未通过", "缺失", "执行错误")
        )
        if blocking_count:
            st.warning(
                f"发现 {blocking_count:,} 项未通过、缺失或执行错误。"
                "请根据处理建议返回 STEP2/STEP3 修正后重新检查。"
            )
        elif status_counts.get("需复核", 0):
            st.info("公式勾稽未发现阻断项，但仍有重复记录等事项需要人工复核。")
        else:
            st.success("第一阶段勾稽检查已通过，可以继续进入 STEP5 数据集成。")

        available_statuses = [
            status for status in ("未通过", "缺失", "需复核", "执行错误", "通过", "不适用")
            if status_counts.get(status, 0)
        ]
        issue_statuses = [
            status for status in ("未通过", "缺失", "需复核", "执行错误")
            if status in available_statuses
        ]
        filter_columns = st.columns(2)
        selected_statuses = filter_columns[0].multiselect(
            "状态筛选",
            available_statuses,
            default=issue_statuses or available_statuses,
            key="step4_status_filter",
        )
        available_types = list(dict.fromkeys(validation_results["check_type"].astype(str)))
        selected_types = filter_columns[1].multiselect(
            "检查类别",
            available_types,
            default=available_types,
            key="step4_type_filter",
        )
        filtered_results = validation_results[
            validation_results["status"].astype(str).isin(selected_statuses)
            & validation_results["check_type"].astype(str).isin(selected_types)
        ].copy()
        display_columns = {
            "check_type": "检查类别",
            "severity": "严重程度",
            "rule_name": "规则名称",
            "company": "公司",
            "report_period": "报告期",
            "period": "期间组",
            "actual": "披露值/实际数",
            "expected": "系统计算值/应有数",
            "difference": "差异",
            "tolerance": "容差",
            "status": "状态",
            "notes": "检查说明",
            "involved_metrics": "涉及指标编码",
            "source_pages": "来源页码",
            "suggestion": "处理建议",
        }
        st.dataframe(
            filtered_results[list(display_columns)].rename(columns=display_columns),
            width="stretch",
            hide_index=True,
            column_config={
                "披露值/实际数": st.column_config.NumberColumn(format="%.4f"),
                "系统计算值/应有数": st.column_config.NumberColumn(format="%.4f"),
                "差异": st.column_config.NumberColumn(format="%.4f"),
                "容差": st.column_config.NumberColumn(format="%.4f"),
            },
        )
        st.download_button(
            "下载勾稽检查工作簿",
            validation_results_to_xlsx(validation_results),
            "偿付能力_STEP4勾稽检查结果.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            icon=":material/download:",
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


def _render_step5_review(
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


def _render_step6_source_selector(
    active_profile: ReportProfile,
) -> tuple[pd.DataFrame, str, tuple[str, ...], str]:
    """STEP6：数据源选择（直接引用集成数据或上传 Excel）。"""
    source_mode = st.radio(
        "数据源选择",
        ["直接引用集成后的数据", "上传集成表 Excel"],
        horizontal=True,
        key="step6_data_source_choice",
    )

    source = pd.DataFrame(columns=STANDARD_COLUMNS)
    source_label = ""
    skipped_profiles: tuple[str, ...] = ()
    if source_mode == "直接引用集成后的数据":
        source, skipped_profiles = prepare_visualization_source(
            st.session_state.integrated_data,
            active_profile.profile_id,
        )
        source_label = "STEP5 已确认集成数据"
    else:
        step6_upload = st.file_uploader(
            "上传行业集成目标表",
            type=["xlsx"],
            key=f"step6_integrated_upload_{active_profile.profile_id}",
            help="优先读取“标准数据”工作表；至少应包含公司、指标编码、指标名称和数值字段。",
        )
        if step6_upload is not None:
            try:
                source, skipped_profiles = read_step6_analysis_workbook(
                    step6_upload.getvalue(),
                    step6_upload.name,
                    active_profile.profile_id,
                )
                source_label = step6_upload.name
                st.success(f"已读取 {len(source):,} 条可分析记录。")
            except Exception as exc:
                st.error(f"集成表读取失败：{exc}")

    if skipped_profiles:
        st.warning(
            "已跳过不属于当前报告 Profile 的记录："
            + "、".join(skipped_profiles)
        )
    st.session_state.step6_analysis_source = source.copy()
    return source, source_label, skipped_profiles, source_mode


def _render_step6_sidebar(
    active_profile: ReportProfile,
    is_project_member: bool,
    analysis_tabs_active: bool,
    industry_available_metric_codes: list[str],
) -> None:
    """STEP6：侧边栏公司/行业导航与补充数据上传。"""
    if analysis_tabs_active:
        with st.sidebar:
            # ★ 换成 Excel 驱动的 Step7 侧边栏
            _render_step7_excel_navigation(active_profile.profile_id)

            # 行业分析导航保持原样
            if is_project_member:
                render_report_navigation(
                    title="行业分析导航",
                    container_key="kpmg_industry_nav_title",
                    state_prefix="industry_nav",
                    available_codes=industry_available_metric_codes,
                    print_key=f"industry_report_print_{active_profile.profile_id}",
                    industry=True,
                )
            render_major_financing_upload(active_profile.profile_id)


def _render_step6_config_panel(module_frame: pd.DataFrame) -> SimpleNamespace:
    """STEP6：核心配置面板；返回图表渲染所需的全部设置。"""
    with st.expander(
        "核心配置面板",
        expanded=True,
        icon=":material/tune:",
    ):
        settings_col, layout_col, unit_col = st.columns([1.5, 1, 1])
        metric_labels, metric_lookup = metric_options(module_frame)
        keep_valid_widget_state("step6_metric", metric_labels)
        with settings_col:
            chart_type = st.selectbox(
                "图表类型",
                CHART_TYPES,
                key="step6_chart_type",
            )
            selected_metric = st.selectbox(
                "选择显示指标",
                metric_labels,
                key="step6_metric",
                disabled=not metric_labels,
                placeholder="暂无可用指标",
            ) if metric_labels else ""

            peer_group_options = nonblank_values(module_frame, "同业分类")
            company_type_filter_options = [
                NO_COMPANY_TYPE_FILTER,
                *peer_group_options,
            ]
            keep_valid_widget_state(
                "step6_company_type_quick_select",
                company_type_filter_options,
            )
            selected_company_type = st.selectbox(
                "按公司类型快速选择",
                company_type_filter_options,
                key="step6_company_type_quick_select",
            )
            all_company_options = companies_for_quick_selection(
                module_frame,
            )
            selected_type_companies = companies_for_quick_selection(
                module_frame,
                selected_company_type,
            )
            previous_company_type = st.session_state.get(
                "_step6_previous_company_type_quick_select"
            )
            if previous_company_type != selected_company_type:
                if selected_company_type == NO_COMPANY_TYPE_FILTER:
                    current_companies = st.session_state.get("step6_companies", [])
                    retained_companies = [
                        company
                        for company in current_companies
                        if company in all_company_options
                    ]
                    st.session_state.step6_companies = (
                        retained_companies or all_company_options[:2]
                    )
                else:
                    st.session_state.step6_companies = selected_type_companies
                st.session_state._step6_previous_company_type_quick_select = (
                    selected_company_type
                )
            elif "step6_companies" not in st.session_state:
                st.session_state.step6_companies = all_company_options[:2]
            keep_valid_widget_state(
                "step6_companies",
                all_company_options,
                multiple=True,
            )
            selected_comparison_companies = st.multiselect(
                "选择对比公司",
                all_company_options,
                key="step6_companies",
                placeholder="选择一家或多家公司",
            )

        comparison_frame = (
            module_frame[
                module_frame["公司"].isin(selected_comparison_companies)
            ].copy()
            if selected_comparison_companies
            else module_frame.iloc[0:0].copy()
        )
        with layout_col:
            layout_mode = st.radio(
                "布局视角",
                ["以公司为横轴", "以报告期为横轴"],
                key="step6_layout_mode",
            )
            decimals = st.number_input(
                "小数位数",
                min_value=0,
                max_value=4,
                value=2,
                step=1,
                key="step6_decimals",
            )
            show_labels = st.toggle(
                "显示数据标签",
                value=True,
                key="step6_show_labels",
            )

        selected_metric_code = metric_lookup.get(selected_metric, "")
        metric_preview = filter_analysis_frame(
            module_frame,
            metric_code=selected_metric_code,
        )
        preview_units = nonblank_values(metric_preview, "单位")
        unit_options = ["原始数值"]
        if any(unit in {"元", "万元", "亿元"} for unit in preview_units):
            unit_options.extend(["亿元", "十亿元"])
        if any(unit == "倍" for unit in preview_units):
            unit_options.append("百分比(%)")
        keep_valid_widget_state("step6_unit_mode", unit_options)
        with unit_col:
            unit_mode = st.selectbox(
                "数值单位换算",
                unit_options,
                key="step6_unit_mode",
            )
            y_axis_title = st.text_input(
                "Y轴单位显示修改",
                value="",
                placeholder="留空则使用指标单位",
                key="step6_y_axis_title",
            )
            transparent = st.toggle(
                "开启透明背景模式",
                value=False,
                key="step6_transparent",
            )
            show_average = st.toggle(
                "平均值线",
                value=False,
                key="step6_show_average",
            )
            average_color = (
                st.color_picker(
                    "基准线颜色",
                    value="#ED2124",
                    key="step6_average_color",
                )
                if show_average
                else "#ED2124"
            )

        period_options = sort_report_periods(comparison_frame["报告期"].tolist())
        keep_valid_widget_state(
            "step6_periods",
            period_options,
            multiple=True,
        )
        time_col, scope_col = st.columns([2, 1])
        with time_col:
            selected_periods = st.multiselect(
                "对比时间",
                period_options,
                default=(
                    default_periods(period_options)
                    if "step6_periods" not in st.session_state
                    else None
                ),
                key="step6_periods",
                placeholder="选择报告期",
            )

        period_frame = (
            comparison_frame[
                comparison_frame["报告期"].isin(selected_periods)
            ].copy()
            if selected_periods
            else comparison_frame.iloc[0:0].copy()
        )
        scope_options = nonblank_values(period_frame, "期间口径")
        keep_valid_widget_state("step6_period_scope", scope_options)
        with scope_col:
            period_scope = st.selectbox(
                "期间口径",
                scope_options,
                key="step6_period_scope",
                disabled=not scope_options,
                placeholder="暂无期间口径",
            ) if scope_options else ""
        scoped_frame = filter_analysis_frame(
            period_frame,
            period_scope=period_scope,
        )
    return SimpleNamespace(
        scoped_frame=scoped_frame,
        metric_code=metric_lookup.get(selected_metric, ""),
        chart_type=chart_type,
        layout_mode=layout_mode,
        show_labels=show_labels,
        decimals=int(decimals),
        unit_mode=unit_mode,
        y_axis_title=y_axis_title,
        transparent=transparent,
        show_average=show_average,
        average_color=average_color,
        comparison_companies=list(selected_comparison_companies),
    )


def _render_step6_chart_panel(settings: SimpleNamespace, source_label: str) -> None:
    """STEP6：按配置渲染对比图与图表明细。"""
    selected_metric_code = settings.metric_code
    chart_type = settings.chart_type
    layout_mode = settings.layout_mode
    show_labels = settings.show_labels
    decimals = settings.decimals
    unit_mode = settings.unit_mode
    y_axis_title = settings.y_axis_title
    transparent = settings.transparent
    show_average = settings.show_average
    average_color = settings.average_color
    selected_comparison_companies = settings.comparison_companies
    scoped_frame = settings.scoped_frame
    chart_frame = filter_analysis_frame(
        scoped_frame,
        metric_code=selected_metric_code,
    )
    if chart_frame.empty:
        st.info("请至少选择一个公司、一个报告期和一个可用指标。")
    else:
        chart_frame = chart_frame.copy()
        units = nonblank_values(chart_frame, "单位")
        chart_plot_frame = chart_frame.copy()
        if unit_mode in {"亿元", "十亿元"}:
            target_yuan = 100_000_000 if unit_mode == "亿元" else 1_000_000_000
            source_yuan = {"元": 1, "万元": 10_000, "亿元": 100_000_000}
            chart_plot_frame["数值"] = chart_plot_frame.apply(
                lambda row: (
                    pd.to_numeric(pd.Series([row["数值"]]), errors="coerce").iloc[0]
                    * source_yuan.get(str(row.get("单位", "")).strip(), target_yuan)
                    / target_yuan
                ),
                axis=1,
            )
            chart_plot_frame["单位"] = unit_mode
        elif unit_mode == "百分比(%)":
            multiple_mask = chart_plot_frame["单位"].astype(str).str.strip().eq("倍")
            chart_plot_frame.loc[multiple_mask, "数值"] = (
                pd.to_numeric(
                    chart_plot_frame.loc[multiple_mask, "数值"],
                    errors="coerce",
                )
                * 100
            )
            chart_plot_frame.loc[multiple_mask, "单位"] = "%"

        duplicate_columns = ["公司", "报告期", "指标编码", "期间口径"]
        duplicate_count = int(chart_frame.duplicated(duplicate_columns, keep=False).sum())
        if duplicate_count:
            st.warning(
                f"当前筛选结果中有 {duplicate_count:,} 条重复粒度记录，"
                "图表将全部保留展示，请检查集成数据。"
            )
        if len(units) > 1:
            st.warning("同一指标存在多个单位：" + "、".join(units))

        st.markdown("#### :material/palette: 自定义图例标签与颜色")
        legend_items = (
            sort_report_periods(chart_plot_frame["报告期"].tolist())
            if layout_mode == "以公司为横轴"
            else nonblank_values(chart_plot_frame, "公司")
        )
        legend_label_map: dict[str, str] = {}
        legend_color_map: dict[str, str] = {}
        legend_columns = st.columns(min(4, max(1, len(legend_items))))
        for index, item in enumerate(legend_items):
            with legend_columns[index % len(legend_columns)]:
                st.caption(f"原始值：{item}")
                legend_label_map[item] = st.text_input(
                    "显示名称",
                    value=item,
                    key=f"step6_legend_label_{layout_mode}_{index}",
                )
                legend_color_map[item] = st.color_picker(
                    "选择颜色",
                    value=KPMG_DEFAULT_COLORS[index % len(KPMG_DEFAULT_COLORS)],
                    key=f"step6_legend_color_{layout_mode}_{index}",
                )

        metric_name = str(chart_frame.iloc[0]["指标名称"])
        st.markdown(f"#### {metric_name}")
        st.caption(
            f"数据源：{source_label or '当前上传文件'}　｜　"
            f"对比公司：{len(selected_comparison_companies)} 家　｜　"
            f"对比期间：{chart_frame['报告期'].nunique()} 个　｜　"
            f"记录数：{len(chart_frame)} 条"
        )
        chart = build_comparison_chart(
            chart_plot_frame,
            chart_type,
            sort_report_periods(chart_frame["报告期"].tolist()),
            show_labels=show_labels,
            decimals=int(decimals),
            layout_mode=layout_mode,
            legend_label_map=legend_label_map,
            legend_color_map=legend_color_map,
            y_axis_title=y_axis_title,
            transparent=transparent,
            show_average=show_average,
            average_color=average_color,
        )
        st.altair_chart(chart, width="stretch")

        with st.expander("查看图表明细数据"):
            display_columns = [
                "公司",
                "公司类型",
                "同业分类",
                "报告期",
                "一级模块",
                "二级模块",
                "指标编码",
                "指标名称",
                "期间口径",
                "数值",
                "单位",
                "来源类型",
                "来源文件",
                "来源工作表",
                "计算逻辑",
            ]
            st.dataframe(
                chart_frame[display_columns].sort_values(["报告期", "公司"]),
                width="stretch",
                hide_index=True,
            )


def render_analysis_tab(
    active_profile: ReportProfile,
    is_project_member: bool,
    analysis_tabs_active: bool,
    active_workflow_tab: str,
) -> None:
    """STEP6 自定义偿付能力对标分析。"""
    st.subheader("自定义偿付能力对标分析")
    source, source_label, skipped_profiles, source_mode = (
        _render_step6_source_selector(active_profile)
    )
    navigation_source = report_scope_frame(
        visualization_metric_frame(source),
        COMPANY_REPORT,
    )
    available_metric_codes = navigation_metric_codes(navigation_source)
    industry_navigation_source = report_scope_frame(
        visualization_metric_frame(source),
        INDUSTRY_REPORT,
    )
    industry_available_metric_codes = navigation_metric_codes(
        industry_navigation_source
    )

    _render_step6_sidebar(
        active_profile,
        is_project_member,
        analysis_tabs_active,
        industry_available_metric_codes,
    )
    if navigation_source.empty:
        if not source.empty:
            st.info("当前数据仅包含校验类记录；这些记录保留在 STEP5，但不会进入可视化指标。")
        elif source_mode == "直接引用集成后的数据":
            st.info("STEP5 尚无已确认的集成数据，请先完成集成，或切换为上传集成表 Excel。")
        elif source_label == "":
            st.info("请上传一份标准窄表格式的集成 Excel。")
    else:
        render_kpmg_palette()
        selected_level_one = st.session_state.get("company_nav_level_one", "")
        selected_level_two = st.session_state.get("company_nav_level_two", "全部")
        module_frame = navigation_source.copy()
        if selected_level_one and selected_level_one != PRINT_ALL_LABEL:
            selected_charts = resolve_chart_selection(
                selected_level_one,
                selected_level_two,
                st.session_state.get("company_nav_chart", ""),
            )
            selected_codes = list(dict.fromkeys(
                code
                for chart_name in selected_charts
                for code in metric_codes_for_chart(chart_name)
            ))
            module_frame = module_frame[
                module_frame["指标编码"].astype(str).str.strip().isin(selected_codes)
            ]
        if module_frame.empty and not navigation_source.empty:
            st.info("当前一级/二级模块下没有可视化指标，请在侧边栏选择其他模块。")
        if not module_frame.empty:
            settings = _render_step6_config_panel(module_frame)
            _render_step6_chart_panel(settings, source_label)


def _render_profile_settings(
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


def _resolve_profile_paths(active_profile: ReportProfile) -> tuple[Path | None, Path]:
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


def _render_page_heading(active_profile: ReportProfile) -> None:
    """页面主标题与当前 Profile 说明。"""
    st.markdown(
        (
            '<section class="platform-page-heading">'
            '<h1>保险报告处理与分析平台</h1>'
            '<p class="platform-profile-caption">'
            f"当前 profile：{active_profile.profile_name}（{active_profile.profile_id}）｜"
            "支持同一报告类型内跨公司、跨期比较"
            "</p></section>"
        ),
        unsafe_allow_html=True,
    )


def _render_analysis_tab_styles() -> None:
    """STEP6-STEP8 侧边栏与导航样式。"""
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"] { background:#FFFFFF; border-right:1px solid #D9E2F1; }
        [data-testid="stSidebar"] h3 { color:#0C233C !important; font-size:18px !important; }
        [data-testid="stSidebar"] [data-testid="stRadio"] { margin-bottom:8px; }
        [data-testid="stExpander"] details {
          border:1px solid #D5DEEC; border-radius:8px; background:#F7F9FC;
        }
        [data-testid="stExpander"] details[open] { border-color:#B8C9E5; }
        [data-testid="stExpander"] summary { color:#0C233C; font-weight:650; }
        .st-key-kpmg_company_nav_title, .st-key-kpmg_industry_nav_title {
          background:#FFFFFF; border-radius:8px; padding:8px 10px;
          box-shadow:0 4px 14px rgba(12,35,60,.06); margin:8px 0 10px 0;
        }
        .kpmg-nav-all-charts {
          margin:0 0 8px; color:#0C233C; font-family:inherit;
          font-size:0.875rem; font-weight:400; line-height:1.4;
        }
        .kpmg-nav-all-label { margin:0 0 4px; font:inherit; }
        .kpmg-nav-all-item {
          display:flex; align-items:center; gap:8px; min-height:24px;
          margin:0; padding:0; font:inherit;
        }
        .kpmg-nav-all-dot {
          width:14px; height:14px; flex:0 0 14px; box-sizing:border-box;
          border:1px solid #CDD4DE; border-radius:50%; background:#F0F2F6;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def main() -> None:
    """应用入口：登录门禁 + 全流程页面编排。"""
    _initialize_login_state()
    if not st.session_state.logged_in:
        _render_login_page()
        st.stop()

    _render_app_styles()
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
            on_click=_logout,
            width="content",
        )

    if not is_project_member:
        st.info(
            "普通用户通道仅开放 STEP6 自定义分析与 STEP7 公司报告。",
            icon=":material/lock:",
        )
    profiles = read_profile_registry()
    active_profile = _render_profile_settings(profiles, is_project_member)
    taxonomy_path, rules_path = _resolve_profile_paths(active_profile)
    _render_page_heading(active_profile)
    visible_workflow_labels = (
        WORKFLOW_TAB_LABELS
        if is_project_member
        else WORKFLOW_TAB_LABELS[6:8]
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
        visible_workflow_labels[0],
    )
    analysis_tabs_active = active_workflow_tab in ANALYSIS_TAB_LABELS
    if analysis_tabs_active:
        _render_analysis_tab_styles()

    if is_project_member:
        # 指标字典在 STEP3/STEP4/STEP5 之间共用，只解析一次。
        taxonomy = resolve_profile_taxonomy(active_profile, taxonomy_path)
        with tabs[0]:
            monitor_companies = render_monitor_tab(active_profile)
        with tabs[1]:
            render_locator_tab(active_profile)
        with tabs[2]:
            render_extraction_tab(active_profile)
        with tabs[3]:
            render_standardization_tab(active_profile, taxonomy, is_project_member)
        with tabs[4]:
            render_validation_tab(active_profile, rules_path, taxonomy)
        with tabs[5]:
            integration_mode = render_integration_tab(
                active_profile, monitor_companies, taxonomy
            )
        preview = st.session_state.integration_preview
        step5_is_open = bool(tabs[5].open)
        _render_step5_review(preview, integration_mode, step5_is_open)

    raw_analysis_source = (
        st.session_state.integrated_data
        if not st.session_state.integrated_data.empty
        else st.session_state.standard_data
    )
    shared_analysis_source, _ = prepare_visualization_source(
        raw_analysis_source,
        active_profile.profile_id,
    )

    with tabs[6]:
        render_analysis_tab(
            active_profile, is_project_member, analysis_tabs_active, active_workflow_tab
        )
    with tabs[7]:
        if active_workflow_tab == WORKFLOW_TAB_LABELS[7]:
            company_report_source = st.session_state.step6_analysis_source
            if company_report_source.empty:
                company_report_source = shared_analysis_source
            render_company_report_workspace(company_report_source)
    if is_project_member:
        with tabs[8]:
            if active_workflow_tab == WORKFLOW_TAB_LABELS[8]:
                render_industry_report_workspace(shared_analysis_source)


if __name__ == "__main__":
    main()

