"""STEP1 视觉定位：扫描版目录识别、低文字页批量视觉定位。"""

from __future__ import annotations

import json
import re
from typing import Callable, Mapping

import base64
import fitz
from ..solvency_ai_table_extractor import _compact, _is_unsupported_vision_error
from ..solvency_table_boundaries import boundary_instruction_for_table

from .config import LOCATOR_VISION_BATCH_SIZE, LOW_TEXT_MIN_CHARACTERS
from .llm import _call_chat, _parse_json_object


def _physical_pages(value, total_pages: int) -> list[int]:
    if isinstance(value, int):
        values = [value]
    elif isinstance(value, str):
        values = [int(item) for item in re.findall(r"\d+", value)]
    elif isinstance(value, list):
        values = []
        for item in value:
            if isinstance(item, int):
                values.append(item)
            elif isinstance(item, str) and item.strip().isdigit():
                values.append(int(item.strip()))
    else:
        values = []
    return sorted({item for item in values if 1 <= item <= total_pages})


def _low_text_page_numbers(
    page_texts: list[str],
    minimum_characters: int = LOW_TEXT_MIN_CHARACTERS,
) -> list[int]:
    """Return physical pages whose searchable text layer is absent or unusable."""
    return [
        index
        for index, text in enumerate(page_texts, start=1)
        if len(_compact(text)) < minimum_characters
    ]


def _render_locator_images(
    pdf_bytes: bytes,
    pages: list[int],
    *,
    zoom: float,
    quality: int,
) -> list[tuple[int, str]]:
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    result: list[tuple[int, str]] = []
    try:
        for page_number in pages:
            if page_number < 1 or page_number > len(document):
                continue
            pixmap = document.load_page(page_number - 1).get_pixmap(
                matrix=fitz.Matrix(zoom, zoom),
                alpha=False,
            )
            try:
                image = pixmap.tobytes("jpeg", jpg_quality=quality)
                mime = "image/jpeg"
            except (TypeError, ValueError):
                image = pixmap.tobytes("png")
                mime = "image/png"
            encoded = base64.b64encode(image).decode("ascii")
            result.append((page_number, f"data:{mime};base64,{encoded}"))
    finally:
        document.close()
    return result


def _profile_prompt_values(
    profile_context: Mapping[str, object] | None,
) -> tuple[str, str, list[str]]:
    context = dict(profile_context or {})
    profile_name = str(context.get("profile_name") or "保险公司偿付能力季度报告").strip()
    prompt_role = str(context.get("prompt_role") or f"{profile_name}审阅专家").strip()
    raw_instructions = context.get("instructions", [])
    instructions = [
        str(item).strip()
        for item in (
            raw_instructions if isinstance(raw_instructions, (list, tuple)) else [raw_instructions]
        )
        if str(item).strip()
    ]
    return profile_name, prompt_role, instructions


def _profile_instruction_block(
    profile_context: Mapping[str, object] | None,
) -> str:
    _, _, instructions = _profile_prompt_values(profile_context)
    if not instructions:
        return "- 无额外 profile 专用规则，以目标表配置和项目边界为准。"
    return "\n".join(f"- {instruction}" for instruction in instructions)


def _vision_locator_messages(
    tables: list[dict],
    pages: list[int],
    images: list[tuple[int, str]],
    *,
    stage: str,
    candidate_hints: dict[str, list[int]] | None = None,
    profile_context: Mapping[str, object] | None = None,
) -> list[dict]:
    target_spec = [
        {
            "table_id": str(item["table_id"]),
            "table_name": str(item["table_name"]),
            "max_pages": max(1, int(item.get("max_pages", 1))),
            "boundary": boundary_instruction_for_table(item),
        }
        for item in tables
    ]
    hint_text = json.dumps(candidate_hints or {}, ensure_ascii=False)
    profile_name, prompt_role, _ = _profile_prompt_values(profile_context)
    profile_rules = _profile_instruction_block(profile_context)
    prompt = f"""你是{prompt_role}，负责图片页码定位。
当前报告 profile：{profile_name}
当前阶段：{stage}
本次只检查PDF物理页码：{pages}

目标表及边界：
{json.dumps(target_spec, ensure_ascii=False)}

上一阶段候选页（仅供复核，不得盲从）：
{hint_text}

profile 专用规则：
{profile_rules}

要求：
1. 直接阅读随后提供的页面图片，页码以每张图片前标注的PDF物理页码为准。
2. 同时依据标题、表头、首尾数据项目判断；跨页表必须返回本批次中属于该表的全部页。
3. 只能返回本次提供的物理页码。未在本批次出现的目标返回空数组。
4. 只输出JSON对象；键使用table_name，值为物理页码数组，并包含全部目标。
"""
    content: list[dict] = [{"type": "text", "text": prompt}]
    for page_number, image_url in images:
        content.extend(
            [
                {"type": "text", "text": f"PDF物理第 {page_number} 页"},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": image_url,
                        "detail": "low" if stage.startswith("低分辨率") else "high",
                    },
                },
            ]
        )
    return [
        {
            "role": "system",
            "content": (f"你负责从{profile_name}页面图片中定位目标披露，" "不得根据常见页码猜测。"),
        },
        {"role": "user", "content": content},
    ]


def _vision_directory_messages(
    tables: list[dict],
    pages: list[int],
    images: list[tuple[int, str]],
    *,
    profile_context: Mapping[str, object] | None = None,
) -> list[dict]:
    targets = [
        {
            "table_id": str(item["table_id"]),
            "table_name": str(item["table_name"]),
            "aliases": [
                str(item.get("table_name", "")),
                *[str(term) for term in item.get("title_terms", [])],
            ],
        }
        for item in tables
    ]
    profile_name, _, _ = _profile_prompt_values(profile_context)
    prompt = f"""你负责读取扫描版{profile_name}前部页面中的目录。
本次图片对应PDF物理页码：{pages}

目标披露：
{json.dumps(targets, ensure_ascii=False)}

要求：
1. 先确认图片中确实存在“目录”页，并读取目标披露对应的报告印刷页码。
2. 利用同批图片中可见的正文标题或页脚印刷页码，计算“PDF物理页码-报告印刷页码”的固定偏移。
3. 只有偏移有可靠视觉证据时才换算；无法可靠换算的目标返回空数组。
4. 返回的是换算后的PDF物理页码，可以超出本批图片范围；不得把目录页本身当成目标页。
5. 目录只是候选证据，不要求图片中已经出现目标表正文。
6. 只输出JSON对象；键使用table_name，值为物理页码数组，并包含全部目标。
"""
    content: list[dict] = [{"type": "text", "text": prompt}]
    for page_number, image_url in images:
        content.extend(
            [
                {"type": "text", "text": f"PDF物理第 {page_number} 页"},
                {
                    "type": "image_url",
                    "image_url": {"url": image_url, "detail": "low"},
                },
            ]
        )
    return [
        {
            "role": "system",
            "content": "你只解析目录及印刷页码到PDF物理页码的可证实换算，不猜测页码。",
        },
        {"role": "user", "content": content},
    ]


def _locate_scan_directory_with_vision(
    pdf_bytes: bytes,
    tables: list[dict],
    low_text_pages: list[int],
    total_pages: int,
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout: int,
    post_func: Callable | None,
    max_front_pages: int = 10,
    profile_context: Mapping[str, object] | None = None,
) -> tuple[dict[str, list[int]], str]:
    """Parse directory candidates when the report front matter is image-only."""
    result = {str(item["table_id"]): [] for item in tables}
    front_pages = [
        page for page in sorted(set(low_text_pages)) if page <= min(total_pages, max_front_pages)
    ]
    if not front_pages:
        return result, ""
    try:
        images = _render_locator_images(
            pdf_bytes,
            front_pages,
            zoom=0.9,
            quality=72,
        )
        content = _call_chat(
            api_key=api_key,
            base_url=base_url,
            model=model,
            messages=_vision_directory_messages(
                tables,
                front_pages,
                images,
                profile_context=profile_context,
            ),
            timeout=timeout,
            post_func=post_func,
            json_mode=True,
        )
        payload = _parse_json_object(content)
        for table in tables:
            table_id = str(table["table_id"])
            table_name = str(table["table_name"])
            raw_pages = payload.get(table_name, payload.get(table_id, []))
            result[table_id] = _physical_pages(raw_pages, total_pages)
        located = sum(bool(pages) for pages in result.values())
        return result, f"扫描目录视觉解析生成{located}类候选"
    except Exception as exc:
        return result, f"扫描目录视觉解析失败：{exc}"


def _batched(values: list[int], size: int) -> list[list[int]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _run_vision_locator_stage(
    pdf_bytes: bytes,
    tables: list[dict],
    pages: list[int],
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout: int,
    post_func: Callable | None,
    stage: str,
    zoom: float,
    quality: int,
    candidate_hints: dict[str, list[int]] | None = None,
    profile_context: Mapping[str, object] | None = None,
) -> tuple[dict[str, list[int]], list[str]]:
    result = {str(item["table_id"]): [] for item in tables}
    errors: list[str] = []
    for batch in _batched(sorted(set(pages)), LOCATOR_VISION_BATCH_SIZE):
        try:
            images = _render_locator_images(
                pdf_bytes,
                batch,
                zoom=zoom,
                quality=quality,
            )
            content = _call_chat(
                api_key=api_key,
                base_url=base_url,
                model=model,
                messages=_vision_locator_messages(
                    tables,
                    batch,
                    images,
                    stage=stage,
                    candidate_hints=candidate_hints,
                    profile_context=profile_context,
                ),
                timeout=timeout,
                post_func=post_func,
                json_mode=True,
            )
            payload = _parse_json_object(content)
            for table in tables:
                table_id = str(table["table_id"])
                table_name = str(table["table_name"])
                raw_pages = payload.get(table_name, payload.get(table_id, []))
                located = _physical_pages(raw_pages, max(batch))
                result[table_id].extend(page for page in located if page in batch)
        except Exception as exc:
            errors.append(f"{stage}物理页{batch}失败：{exc}")
            if _is_unsupported_vision_error(exc):
                break
    return {
        table_id: sorted(set(located_pages)) for table_id, located_pages in result.items()
    }, errors


def _locate_low_text_pages_with_vision(
    pdf_bytes: bytes,
    tables: list[dict],
    low_text_pages: list[int],
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout: int,
    post_func: Callable | None,
    profile_context: Mapping[str, object] | None = None,
) -> tuple[dict[str, list[int]], dict[str, list[int]], list[str]]:
    low_candidates, errors = _run_vision_locator_stage(
        pdf_bytes,
        tables,
        low_text_pages,
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout=timeout,
        post_func=post_func,
        stage="低分辨率候选扫描",
        zoom=0.9,
        quality=72,
        profile_context=profile_context,
    )
    candidate_pages = sorted({page for pages in low_candidates.values() for page in pages})
    if not candidate_pages:
        return low_candidates, low_candidates, errors

    low_text_set = set(low_text_pages)
    confirmation_pages = sorted(
        {
            nearby
            for page in candidate_pages
            for nearby in (page - 1, page, page + 1)
            if nearby in low_text_set
        }
    )
    confirmed, confirmation_errors = _run_vision_locator_stage(
        pdf_bytes,
        tables,
        confirmation_pages,
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout=timeout,
        post_func=post_func,
        stage="高分辨率候选确认",
        zoom=1.8,
        quality=86,
        candidate_hints=low_candidates,
        profile_context=profile_context,
    )
    needs_high_resolution = bool(confirmation_errors) or any(
        low_candidates.get(table_id) and not confirmed.get(table_id) for table_id in low_candidates
    )
    high_resolution: dict[str, list[int]] = {}
    high_resolution_errors: list[str] = []
    if needs_high_resolution:
        high_resolution, high_resolution_errors = _run_vision_locator_stage(
            pdf_bytes,
            tables,
            confirmation_pages,
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout=timeout,
            post_func=post_func,
            stage="3倍高清候选复核",
            zoom=3.0,
            quality=92,
            candidate_hints={
                table_id: confirmed.get(table_id) or low_candidates.get(table_id, [])
                for table_id in low_candidates
            },
            profile_context=profile_context,
        )
    final = {
        table_id: (
            high_resolution.get(table_id)
            or confirmed.get(table_id)
            or low_candidates.get(table_id, [])
        )
        for table_id in low_candidates
    }
    return (
        low_candidates,
        final,
        [
            *errors,
            *confirmation_errors,
            *high_resolution_errors,
        ],
    )
