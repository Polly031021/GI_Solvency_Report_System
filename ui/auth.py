"""登录门禁：AI 提供商选择、安全码校验与登录页渲染。"""

from __future__ import annotations

import hmac
import os

import streamlit as st

from .config import (
    DEFAULT_LOGIN_AI_PROVIDER,
    LOGIN_AI_PROVIDERS,
    NORMAL_USER_ROLE,
    PROJECT_MEMBER_ROLE,
)


def initialize_login_state() -> None:
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


def logout() -> None:
    st.session_state.logged_in = False
    st.session_state.user_role = None
    st.session_state.llm_api_key = ""
    st.session_state.login_api_key_input = ""
    st.session_state.login_security_code = ""
    st.session_state.pop("workflow_main_tab", None)
    st.session_state.pop("profile_settings_tabs", None)


def render_login_page() -> None:
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
                st.session_state.llm_base_url = str(st.session_state.login_base_url_input).strip()
                st.session_state.llm_provider = _provider_name_from_base_url(
                    st.session_state.llm_base_url
                )
                st.session_state.llm_model = str(st.session_state.login_model_input).strip()
                st.session_state.llm_api_key = str(st.session_state.login_api_key_input).strip()
                st.session_state.pop("workflow_main_tab", None)
                st.session_state.pop("profile_settings_tabs", None)
                st.rerun()

        st.markdown(
            "<div style='text-align:center; color:#94A3B8; font-size:11px; "
            "margin-top:30px; letter-spacing:1px;'>系统版本：v0.3.1 © 2026<br>"
            "保险报告处理与分析平台</div>",
            unsafe_allow_html=True,
        )
