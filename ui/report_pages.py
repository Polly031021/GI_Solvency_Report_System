"""STEP7 公司报告 / STEP8 行业报告的入口封装（转发到对应 step 模块）。"""

from __future__ import annotations

import pandas as pd
import streamlit as st
from step7_solvency import show_step_7_solvency
from step8_solvency import show_step_8_solvency


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
