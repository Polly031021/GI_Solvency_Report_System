"""平台级 CSS 注入（含打印样式与 STEP6-STEP8 侧边栏样式）。"""

from __future__ import annotations

import streamlit as st


def render_app_styles() -> None:
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
def render_analysis_tab_styles() -> None:
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
