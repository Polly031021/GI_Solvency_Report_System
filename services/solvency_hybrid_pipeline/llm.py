"""模型调用薄封装：地址拼接、错误信息与 JSON 解析。"""

from __future__ import annotations

import json
import re
from typing import Callable

import requests
from ..llm_config import model_request_parameters, normalize_model_id
from ..llm_http import post_json_with_retry


def _completion_url(base_url: str) -> str:
    value = str(base_url or "").strip().rstrip("/")
    if not value:
        raise ValueError("模型接口地址不能为空。")
    return value if value.endswith("/chat/completions") else f"{value}/chat/completions"


def _response_error(response) -> str:
    try:
        error = response.json().get("error", {})
        if isinstance(error, dict):
            return str(error.get("message", "")).strip()
    except Exception:
        pass
    return str(getattr(response, "text", "") or "")[:500]


def _call_chat(
    *,
    api_key: str,
    base_url: str,
    model: str,
    messages: list[dict],
    timeout: int,
    post_func: Callable | None = None,
    json_mode: bool = False,
) -> str:
    post = post_func or requests.post
    payload = {
        "model": normalize_model_id(base_url, model),
        "messages": messages,
    }
    payload.update(model_request_parameters(base_url, model))
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json",
    }
    response = post_json_with_retry(
        post,
        _completion_url(base_url),
        headers=headers,
        json=payload,
        timeout=timeout,
    )
    if (
        json_mode
        and not getattr(response, "ok", False)
        and int(getattr(response, "status_code", 0) or 0) in {400, 422}
    ):
        payload.pop("response_format", None)
        response = post_json_with_retry(
            post,
            _completion_url(base_url),
            headers=headers,
            json=payload,
            timeout=timeout,
        )
    if not getattr(response, "ok", False):
        raise RuntimeError(f"模型接口调用失败：{_response_error(response) or 'HTTP错误'}")
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("模型接口返回结构中没有choices/message/content。") from exc
    if isinstance(content, list):
        content = "".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item) for item in content
        )
    return str(content)


def _parse_json_object(content: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(content).strip(), flags=re.I)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise RuntimeError("页码定位模型没有返回JSON对象。")
        value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise RuntimeError("页码定位模型结果不是JSON对象。")
    return value
