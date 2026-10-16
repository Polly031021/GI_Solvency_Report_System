"""模型调用：提示词组装、HTTP 调用、JSON 解析与表格规整。"""

from __future__ import annotations

import json
import re
from typing import Callable, Mapping

import base64
import fitz
import requests
from ..llm_config import model_request_parameters, normalize_model_id
from ..llm_http import post_json_with_retry

from .grid import _instructions, _slice_grid_for_target
from .models import ExtractionQualityError, PageGrid, _clean, _compact


def _grid_messages(
    table_id: str,
    table_name: str,
    pages: list[int],
    grids: list[PageGrid],
    table_config: Mapping[str, object] | None = None,
) -> list[dict]:
    blocks = "\n\n".join(
        f"===== PDF物理第 {item.page_number} 页 =====\n"
        f"{_slice_grid_for_target(table_id, item.grid_text, table_config)}"
        for item in grids
    )
    config = dict(table_config or {})
    prompt_role = str(config.get("prompt_role") or "保险公司偿付能力报告表格重构专家")
    return [
        {
            "role": "system",
            "content": f"你是{prompt_role}。必须依据页面网格恢复表格，不能编造披露数据。",
        },
        {
            "role": "user",
            "content": f"{_instructions(table_id, table_name, pages, table_config)}\n\n{blocks}",
        },
    ]


def _grid_repair_messages(
    table_id: str,
    table_name: str,
    pages: list[int],
    grids: list[PageGrid],
    previous_error: str,
    table_config: Mapping[str, object] | None = None,
) -> list[dict]:
    messages = _grid_messages(table_id, table_name, pages, grids, table_config)
    messages[-1][
        "content"
    ] += f"""

上一轮重构未通过本地质量检查，失败原因：{previous_error}
请重新检查列对齐并返回完整JSON。特别注意：
- “行次/序号”列只是行编号，不是金额列；不得把它判作数值错位。
- 实际资本明细表应保留“行次、项目、期末数、期初数”等完整列。
- 实际资本的首个业务行可能是“财务报表资产总额”“认可资产”或“核心一级资本”；
  若原文表头后直接从“核心一级资本”开始，应从该行提取到“实际资本合计”，
  不得要求或补造原文未披露的“认可资产”等汇总行。
- 若原文从“实际资本”开始，随后另有“核心一级资本调整表”，两张子表都必须保留，
  并在“银保监会规定的其他调整项目”或同义项目结束；后续“认可资产表”和“认可负债表”
  明细不属于本目标，不得提取。
- 若实际资本先披露“实际资本汇总”、后披露“核心资本”，必须同时保留两张子表，
  从汇总首行“财务报表资产总额”或“认可资产”连续提取到核心资本末行“实际资本合计”；
  汇总表的“本季度数/上季度可比数”与明细表的“期末数/期初数”按本期、上期一一对应，
  统一输出为“期末数/期初数”，不得把第二张子表表头当作数据行。
- 组合经营指标按原文实际披露范围提取：后续类别缺失时允许以“综合投资收益率”结束；若出现品质类指标，则必须继续保留至最后的“营销员脱落率”（或同义名称），不得补造缺失项目。
- 偿付能力充足率指标不得只返回核心/综合充足率两行，必须保留实际资本、最低资本等整张表的所有数据行。
"""
    return messages


def _is_unsupported_vision_error(error: Exception | str) -> bool:
    message = str(error).lower().replace("`", "")
    signals = (
        "unknown variant image_url",
        "expected text",
        "image input is not supported",
        "does not support image",
        "unsupported image",
        "unsupported content type",
    )
    return any(signal in message for signal in signals)


def _render_images(
    pdf_bytes: bytes,
    pages: list[int],
    *,
    zoom: float = 1.8,
    jpeg_quality: int = 84,
) -> list[tuple[int, str]]:
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    result: list[tuple[int, str]] = []
    try:
        for page_number in pages:
            if page_number < 1 or page_number > len(document):
                raise ValueError(f"页码 {page_number} 超出PDF范围。")
            pixmap = document.load_page(page_number - 1).get_pixmap(
                matrix=fitz.Matrix(zoom, zoom), alpha=False
            )
            try:
                image = pixmap.tobytes("jpeg", jpg_quality=jpeg_quality)
                mime = "image/jpeg"
            except (TypeError, ValueError):
                image = pixmap.tobytes("png")
                mime = "image/png"
            result.append(
                (page_number, f"data:{mime};base64,{base64.b64encode(image).decode('ascii')}")
            )
    finally:
        document.close()
    return result


def _vision_messages(
    table_id: str,
    table_name: str,
    pages: list[int],
    images: list[tuple[int, str]],
    table_config: Mapping[str, object] | None = None,
) -> list[dict]:
    content: list[dict] = [
        {
            "type": "text",
            "text": _instructions(table_id, table_name, pages, table_config)
            + "\n以下图片按物理页码顺序排列，请直接读取图片网格。",
        }
    ]
    for page_number, data_url in images:
        content.extend(
            [
                {"type": "text", "text": f"PDF物理第 {page_number} 页"},
                {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
            ]
        )
    return [
        {
            "role": "system",
            "content": "你是偿付能力报告图片表格识别专家。严格按图片网格恢复跨页表格，不得编造数据。",
        },
        {"role": "user", "content": content},
    ]


def _completion_url(base_url: str) -> str:
    value = base_url.strip().rstrip("/")
    if not value:
        raise ValueError("模型接口地址不能为空。")
    return value if value.endswith("/chat/completions") else f"{value}/chat/completions"


def _response_error(response) -> str:
    try:
        error = response.json().get("error", {})
        if isinstance(error, dict):
            return _clean(error.get("message", ""))
    except Exception:
        pass
    return _clean(getattr(response, "text", ""))[:500]


def _call_model(
    *,
    api_key: str,
    base_url: str,
    model: str,
    messages: list[dict],
    timeout: int,
    post_func: Callable | None,
    source_grids: list[PageGrid] | None = None,
) -> str:
    post = post_func or requests.post
    payload = {
        "model": normalize_model_id(base_url, model),
        "messages": messages,
        "response_format": {"type": "json_object"},
    }
    payload.update(model_request_parameters(base_url, model))
    headers = {"Authorization": f"Bearer {api_key.strip()}", "Content-Type": "application/json"}
    response = post_json_with_retry(
        post,
        _completion_url(base_url),
        headers=headers,
        json=payload,
        timeout=timeout,
    )
    if not getattr(response, "ok", False) and int(getattr(response, "status_code", 0) or 0) in {
        400,
        422,
    }:
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
            str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in content
        )
    return str(content)


def _parse_json(content: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ExtractionQualityError("模型没有返回可解析的JSON对象。")
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ExtractionQualityError(f"模型JSON解析失败：{exc}") from exc
    if not isinstance(payload, dict):
        raise ExtractionQualityError("模型返回结果不是JSON对象。")
    for key in ("result", "table", "data"):
        if isinstance(payload.get(key), dict):
            payload = payload[key]
            break
    if isinstance(payload.get("tables"), list) and payload["tables"]:
        payload = payload["tables"][0]
    return payload


def _header_similarity(row: list[str], columns: list[str]) -> float:
    width = max(len(row), len(columns))
    if not width:
        return 0.0
    left = row + [""] * (width - len(row))
    right = columns + [""] * (width - len(columns))
    matches = sum(bool(_compact(a)) and _compact(a) == _compact(b) for a, b in zip(left, right))
    return matches / width


def _coerce_table(payload: dict) -> tuple[list[list[str]], float, str]:
    columns = payload.get("columns") or payload.get("headers") or payload.get("header")
    raw_rows = payload.get("rows") or payload.get("records")
    if not isinstance(raw_rows, list):
        raise ExtractionQualityError("模型结果缺少rows数组。")
    if not isinstance(columns, list) or not columns:
        first_dict = next((row for row in raw_rows if isinstance(row, dict)), None)
        if first_dict is None:
            raise ExtractionQualityError("模型结果缺少columns数组。")
        columns = list(first_dict)
    columns = [_clean(value) or f"列{index + 1}" for index, value in enumerate(columns)]
    width = len(columns)
    normalized: list[list[str]] = []
    ragged = 0
    for raw_row in raw_rows:
        if isinstance(raw_row, dict) and isinstance(raw_row.get("cells"), list):
            cells = raw_row["cells"]
        elif isinstance(raw_row, dict):
            cells = [raw_row.get(column, "") for column in columns]
        elif isinstance(raw_row, list):
            cells = raw_row
        else:
            continue
        cleaned = [_clean(value) for value in cells]
        if not any(cleaned):
            continue
        ragged += len(cleaned) != width
        cleaned = (cleaned + [""] * width)[:width]
        if _header_similarity(cleaned, columns) >= 0.6:
            continue
        if normalized and cleaned == normalized[-1]:
            continue
        normalized.append(cleaned)
    if not normalized:
        raise ExtractionQualityError("模型结果没有有效数据行。")
    return (
        [columns, *normalized],
        ragged / max(len(normalized), 1),
        _clean(payload.get("notes", "")),
    )
