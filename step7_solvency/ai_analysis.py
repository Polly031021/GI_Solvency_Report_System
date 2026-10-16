"""一键 AI 分析：调用登录页配置的模型接口，为最新报告期生成点评。"""

from __future__ import annotations

import pandas as pd
import requests
import streamlit as st
from services.llm_config import model_request_parameters
from services.llm_config import normalize_model_id
from services.llm_http import post_json_with_retry
from services.solvency_step6_analysis import format_chart_value


def _analysis_completion_url(base_url: str) -> str:
    value = str(base_url or "").strip().rstrip("/")
    if not value:
        raise ValueError("模型接口地址不能为空。")
    return value if value.endswith("/chat/completions") else f"{value}/chat/completions"


@st.cache_data(show_spinner=False, ttl="12h", max_entries=256)
def _call_ai_analysis_cached(
    data_text: str,
    metric_name: str,
    latest_period: str,
    api_key: str,
    base_url: str,
    model: str,
) -> str:
    prompt = (
        "你是资深四大保险精算顾问。请根据以下偿付能力同业对标数据，"
        "用一句中文给出专业点评，指出最高、最低、均值及值得关注的差异；"
        "不要编造数据，不超过80字。\n"
        f"报告期：{latest_period}\n指标：{metric_name}\n数据：{data_text}"
    )
    payload = {
        "model": normalize_model_id(base_url, model),
        "messages": [{"role": "user", "content": prompt}],
    }
    payload.update(model_request_parameters(base_url, model))
    response = post_json_with_retry(
        requests.post,
        _analysis_completion_url(base_url),
        headers={
            "Authorization": f"Bearer {api_key.strip()}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )
    if not response.ok:
        try:
            detail = response.json().get("error", {}).get("message", "")
        except Exception:
            detail = response.text
        raise RuntimeError(detail or f"HTTP {response.status_code}")
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("模型接口返回结构中没有 choices/message/content。") from exc
    if isinstance(content, list):
        content = "".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item) for item in content
        )
    return str(content).strip()


def ai_analysis_for_latest_period(
    latest: pd.DataFrame,
    metric_name: str,
    latest_period: str,
) -> tuple[str, bool]:
    settings = {
        "api_key": str(st.session_state.get("llm_api_key", "")).strip(),
        "base_url": str(st.session_state.get("llm_base_url", "")).strip(),
        "model": str(st.session_state.get("llm_model", "")).strip(),
    }
    if not all(settings.values()):
        return (
            "当前登录会话的 AI 配置不完整。请退出登录，在登录页填写 "
            "Base URL、模型名称和 API Key 后重新进入。"
        ), True
    company_values = "、".join(
        f"{row['公司']}={format_chart_value(row['数值'], row.get('单位', ''), row.get('数据类型', ''))}"
        for _, row in latest.sort_values("数值", ascending=False).iterrows()
    )
    sample = latest.iloc[0]
    average_text = format_chart_value(
        latest["数值"].mean(),
        sample.get("单位", ""),
        sample.get("数据类型", ""),
    )
    data_text = f"{company_values}；样本均值={average_text}"
    try:
        return (
            _call_ai_analysis_cached(
                data_text,
                metric_name,
                latest_period,
                settings["api_key"],
                settings["base_url"],
                settings["model"],
            ),
            False,
        )
    except Exception as exc:
        return f"AI 分析失败：{exc}", True
