"""STEP1 定位主入口 locate_tables_hybrid（规则法 / 大模型法 / 复核）。"""

from __future__ import annotations

from typing import Callable, Mapping

from ..solvency_table_boundaries import boundary_instruction_for_table
from ..solvency_pdf_locator import (
    PageMatch,
    _excluded_detail_page,
    _three_year_page_has_explicit_value,
    locate_directory_candidates,
    locate_tables,
)

from .llm import _call_chat, _parse_json_object
from .locator_rule import (
    _confidence_from_sources,
    _locator_conflict,
    _locator_prompt,
    _merge_locator_pages,
    _solvency_radar,
)
from .locator_vision import (
    _locate_low_text_pages_with_vision,
    _locate_scan_directory_with_vision,
    _low_text_page_numbers,
    _physical_pages,
)


def locate_tables_hybrid(
    pdf_bytes: bytes,
    feature_config: dict,
    *,
    api_key: str = "",
    base_url: str = "",
    model: str = "",
    timeout: int = 180,
    post_func: Callable | None = None,
    profile_context: Mapping[str, object] | None = None,
    mode: str = "hybrid",
    llm_verify: bool = False,
) -> tuple[list[PageMatch], str]:
    """Locate target disclosures through rules, optionally with LLM support.

    mode="hybrid" keeps the historical primary-LLM behaviour. mode="rule"
    runs the offline path only (boundary analysis + radar + directory); when
    llm_verify=True the text model is called once afterwards to cross-check
    the rule result instead of contributing pages itself.
    """
    mode = (mode or "hybrid").lower()
    if mode not in {"hybrid", "rule"}:
        raise ValueError("mode must be 'hybrid' or 'rule'")
    rule_mode = mode == "rule"
    llm_configured = all(str(value or "").strip() for value in (api_key, base_url, model))
    verify_with_llm = rule_mode and llm_verify and llm_configured

    local_matches = locate_tables(pdf_bytes, feature_config)
    local_by_id = {item.table_id: item for item in local_matches}
    page_texts, radar_hints, hint_text = _solvency_radar(pdf_bytes, feature_config)
    tables = feature_config.get("tables", [])
    directory_hints, directory_message = locate_directory_candidates(
        page_texts,
        tables,
    )
    low_text_pages = _low_text_page_numbers(page_texts)
    if low_text_pages and not any(directory_hints.values()) and not rule_mode:
        visual_directory_hints, visual_directory_message = _locate_scan_directory_with_vision(
            pdf_bytes,
            tables,
            low_text_pages,
            len(page_texts),
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout=timeout,
            post_func=post_func,
            profile_context=profile_context,
        )
        if any(visual_directory_hints.values()):
            directory_hints = visual_directory_hints
        if visual_directory_message:
            directory_message += f"；{visual_directory_message}"
    low_text_set = set(low_text_pages)
    searchable_pages = [page for page in range(1, len(page_texts) + 1) if page not in low_text_set]
    target_names = [str(item["table_name"]) for item in tables]
    boundary_hints = "\n".join(
        f"- {item['table_name']}：{boundary_instruction_for_table(item)}" for item in tables
    )
    scan_text = "\n\n".join(
        f"---PDF物理第{index + 1}页---\n{' '.join(text.split())[:1600]}"
        for index, text in enumerate(page_texts)
        if index + 1 in searchable_pages
    )
    prompt = _locator_prompt(
        target_names=target_names,
        boundary_hints=boundary_hints,
        radar_hints=hint_text,
        directory_hints=directory_hints,
        scan_text=scan_text,
        profile_context=profile_context,
    )
    ai_error = ""
    ai_result: dict = {}
    run_text_llm = (not rule_mode) or verify_with_llm
    if run_text_llm and searchable_pages and llm_configured:
        try:
            content = _call_chat(
                api_key=api_key,
                base_url=base_url,
                model=model,
                messages=[{"role": "user", "content": prompt}],
                timeout=timeout,
                post_func=post_func,
                json_mode=True,
            )
            ai_result = _parse_json_object(content)
        except Exception as exc:
            ai_error = str(exc)

    vision_candidates = {str(item["table_id"]): [] for item in tables}
    vision_pages = {str(item["table_id"]): [] for item in tables}
    vision_errors: list[str] = []
    if low_text_pages and not rule_mode:
        vision_candidates, vision_pages, vision_errors = _locate_low_text_pages_with_vision(
            pdf_bytes,
            tables,
            low_text_pages,
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout=timeout,
            post_func=post_func,
            profile_context=profile_context,
        )

    total_pages = len(page_texts)
    results: list[PageMatch] = []
    for table in tables:
        table_id = str(table["table_id"])
        table_name = str(table["table_name"])
        local = local_by_id.get(table_id)
        local_pages = list(local.pages) if local else []
        raw_ai_pages = _physical_pages(ai_result.get(table_name, []), total_pages)
        detail_filtered_ai_pages = [
            page for page in raw_ai_pages if not _excluded_detail_page(page_texts[page - 1], table)
        ]
        semantic_lookalike_pages: list[int] = []
        ai_pages = detail_filtered_ai_pages
        local_boundary_closed = bool(
            local and "起始项目：" in str(local.evidence) and "终止项目：" in str(local.evidence)
        )
        if table_id == "THREE_YEAR_INVESTMENT_RETURN" and local_boundary_closed:
            semantic_lookalike_pages = [
                page
                for page in detail_filtered_ai_pages
                if (
                    page not in local_pages
                    and not _three_year_page_has_explicit_value(page_texts[page - 1])
                )
            ]
            ai_pages = [
                page for page in detail_filtered_ai_pages if page not in semantic_lookalike_pages
            ]
        visual_candidates = [
            page
            for page in vision_candidates.get(table_id, [])
            if not _excluded_detail_page(page_texts[page - 1], table)
        ]
        visual_pages = [
            page
            for page in vision_pages.get(table_id, [])
            if not _excluded_detail_page(page_texts[page - 1], table)
        ]
        excluded_detail_pages = sorted(set(raw_ai_pages) - set(detail_filtered_ai_pages))
        radar_pages = sorted(set(radar_hints.get(table_id, [])))
        directory_pages = sorted(set(directory_hints.get(table_id, [])))
        boundary_closed = local_boundary_closed
        # In rule mode the LLM output is verification-only: it must never
        # contribute pages itself, otherwise this stops being an offline path.
        merge_ai_pages = [] if rule_mode else ai_pages
        pages = (
            local_pages
            if boundary_closed
            else _merge_locator_pages(
                local_pages,
                sorted(set(merge_ai_pages + visual_pages)),
                radar_pages,
                max(1, int(table.get("max_pages", 1))),
                directory_pages,
            )
        )
        review_required, review_reason = _locator_conflict(
            local_pages=local_pages,
            ai_pages=merge_ai_pages,
            visual_pages=visual_pages,
            directory_pages=directory_pages,
            boundary_closed=boundary_closed,
        )
        confidence, confidence_basis = _confidence_from_sources(
            boundary_closed=boundary_closed,
            local_pages=local_pages,
            radar_pages=radar_pages,
            directory_pages=directory_pages,
            ai_pages=merge_ai_pages,
            visual_pages=visual_pages,
        )
        if verify_with_llm and ai_pages:
            if set(ai_pages) == set(pages):
                confidence = min(confidence + 5, 98)
                confidence_basis += "；大模型复核一致（+5）"
            else:
                confidence = min(confidence, 55)
                review_required = True
                review_reason = (
                    "规则法与大模型复核不一致，请人工确认（规则："
                    f"{','.join(map(str, pages))}；大模型："
                    f"{','.join(map(str, ai_pages))}）"
                )
        elif verify_with_llm and not ai_error:
            confidence_basis += "；大模型复核未返回该表页码"
        evidence_parts = []
        if local and local.evidence:
            evidence_parts.append(local.evidence)
        if boundary_closed:
            evidence_parts.append("首尾项目已闭合，页码范围以项目边界为准")
        if ai_pages and merge_ai_pages:
            evidence_parts.append(f"大模型页码推断：{','.join(map(str, ai_pages))}")
        elif ai_pages:
            evidence_parts.append(f"大模型复核页码：{','.join(map(str, ai_pages))}")
        if excluded_detail_pages:
            evidence_parts.append(
                "已隔离非目标明细页：" + ",".join(map(str, excluded_detail_pages))
            )
        if semantic_lookalike_pages:
            evidence_parts.append(
                "已排除仅含季度收益率或脚注提及的近三年伪候选页："
                + ",".join(map(str, semantic_lookalike_pages))
            )
        if visual_candidates:
            evidence_parts.append(f"图片候选扫描：{','.join(map(str, visual_candidates))}")
        if visual_pages:
            evidence_parts.append(f"图片候选确认：{','.join(map(str, visual_pages))}")
        if radar_pages:
            evidence_parts.append(
                f"Python表格雷达候选（仅补洞或兜底）：{','.join(map(str, radar_pages))}"
            )
        if directory_pages:
            evidence_parts.append(f"目录换算候选：{','.join(map(str, directory_pages))}")
        if review_required:
            evidence_parts.append(f"证据冲突，必须人工确认：{review_reason}")
        if ai_error:
            evidence_parts.append("大模型定位失败，已使用Python雷达兜底")
        results.append(
            PageMatch(
                table_id=table_id,
                table_name=table_name,
                pages=pages,
                score=max(
                    float(local.score) if local else 0.0,
                    10.0 if merge_ai_pages else 0.0,
                    12.0 if visual_pages else 0.0,
                ),
                evidence="；".join(evidence_parts) or "未找到明确页码证据",
                review_required=review_required,
                review_reason=review_reason,
                sources={
                    "local": local_pages,
                    "semantic": ai_pages,
                    "vision": visual_pages,
                    "directory": directory_pages,
                    "radar": radar_pages,
                },
                strategy_id=str(table.get("strategy_id", "")),
                table_config=dict(table),
                confidence=int(confidence),
                confidence_basis=confidence_basis,
            )
        )
    status_parts: list[str] = []
    if rule_mode:
        status_parts.append(
            "已完成规则法定位：本地边界分析＋表格雷达＋目录换算"
            + ("＋大模型复核" if verify_with_llm else "（未调用大模型）")
        )
    else:
        if low_text_pages:
            status_parts.append(
                f"检测到{len(low_text_pages)}/{total_pages}个低文本页，已启用两阶段图片页码定位"
            )
        if searchable_pages:
            status_parts.append("已完成可检索页面语义推断与表格结构雷达复核")
    if low_text_pages and rule_mode:
        status_parts.append(
            f"检测到{len(low_text_pages)}/{total_pages}个低文本页；规则法不读取图片页，"
            "如结果为空请切换大模型法或人工填写页码"
        )
    status_parts.append(directory_message)
    if ai_error:
        if verify_with_llm:
            status_parts.append(f"大模型复核失败，已保留规则法结果：{ai_error}")
        else:
            status_parts.append(f"文本语义定位失败，已保留其他定位结果：{ai_error}")
    if vision_errors:
        status_parts.append("图片页码定位部分失败：" + "；".join(vision_errors))
    conflict_count = sum(item.review_required for item in results)
    if conflict_count:
        status_parts.append(f"{conflict_count}类目标存在定位证据冲突，已隔离等待人工确认")
    if not status_parts:
        status_parts.append("已完成混合智能定位")
    return results, "；".join(status_parts) + "。"
