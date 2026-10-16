"""STEP1 规则法定位：表格雷达、目录换算、证据合并与置信度。"""

from __future__ import annotations

import json
import re
from typing import Mapping

import fitz
from ..solvency_pdf_locator import (
    _excluded_detail_page,
    _numeric_table_lines,
    _term_hits,
)

from .locator_vision import _profile_instruction_block, _profile_prompt_values


def _radar_content_anchor(
    table_id: str,
    content_hits: list[str],
    header_hits: list[str],
    terminal_hits: list[str],
    table_like: bool,
) -> bool:
    if table_id == "OPERATING_METRICS":
        # Narrative sections often mention assets, premiums, or other generic
        # operating terms together. Require a real table header, or the
        # distinctive terminal row on a continuation page.
        return len(content_hits) >= 2 and (
            bool(header_hits) or (bool(terminal_hits) and table_like)
        )
    return len(content_hits) >= 2 and (bool(header_hits) or table_like)


def _solvency_radar(
    pdf_bytes: bytes,
    feature_config: dict,
) -> tuple[list[str], dict[str, list[int]], str]:
    tables = feature_config.get("tables", [])
    hints: dict[str, list[int]] = {str(item["table_id"]): [] for item in tables}
    summaries: list[str] = []
    page_texts: list[str] = []
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for index in range(len(document)):
            page = document.load_page(index)
            raw_text = page.get_text("text") or ""
            page_texts.append(raw_text)
            compressed = " ".join(raw_text.replace("\x00", " ").split())
            summaries.append(f"---PDF物理第{index + 1}页---\n{compressed[:1600]}")
            compact_text = re.sub(r"\s+", "", raw_text)
            try:
                has_table = bool(page.find_tables().tables)
            except Exception:
                has_table = False
            numeric_lines = _numeric_table_lines(raw_text)
            continued = any(term in compact_text for term in ("续表", "（续）", "(续)"))
            table_like = has_table or numeric_lines >= 2 or continued
            for table in tables:
                table_id = str(table["table_id"])
                if _excluded_detail_page(raw_text, table):
                    continue
                title_hits = _term_hits(raw_text, table.get("title_terms", []))
                content_hits = _term_hits(raw_text, table.get("content_terms", []))
                header_hits = _term_hits(raw_text, table.get("header_terms", []))
                continuation_hits = _term_hits(raw_text, table.get("continuation_terms", []))
                terminal_hits = _term_hits(raw_text, table.get("terminal_terms", []))
                strong_anchor = bool(title_hits) and (
                    len(content_hits) + len(header_hits) >= 1 or numeric_lines >= 2
                )
                content_anchor = _radar_content_anchor(
                    table_id,
                    content_hits,
                    header_hits,
                    terminal_hits,
                    table_like,
                )
                explicit_continuation = (
                    continued
                    and table_like
                    and bool(content_hits or header_hits or continuation_hits)
                )
                if strong_anchor or content_anchor or explicit_continuation:
                    hints[table_id].append(index + 1)
    finally:
        document.close()
    hint_lines = []
    by_id = {str(item["table_id"]): item for item in tables}
    for table_id, pages in hints.items():
        if pages:
            hint_lines.append(
                f"- {by_id[table_id]['table_name']}：候选物理页码 {sorted(set(pages))}"
            )
    return page_texts, hints, "\n".join(hint_lines) or "- Python雷达未找到强候选页"


def _merge_locator_pages(
    local_pages: list[int],
    ai_pages: list[int],
    radar_pages: list[int],
    max_pages: int,
    directory_pages: list[int] | None = None,
) -> list[int]:
    """Merge page evidence without allowing radar-only edge expansion.

    Local boundary analysis and model-confirmed pages are primary evidence.
    The Python radar is deliberately weaker: when primary evidence exists it
    may fill a missing page inside an already confirmed range, but it may not
    extend either edge. It remains a fallback when both primary sources fail.
    """
    directory_candidates = sorted(set(directory_pages or []))
    pages = sorted(set(local_pages))
    ai_candidates = sorted(set(ai_pages))
    if not pages:
        pages = ai_candidates or directory_candidates or sorted(set(radar_pages))

    # Model-confirmed pages may extend a local range when they are contiguous.
    changed = True
    while changed and len(pages) < max_pages:
        changed = False
        for page in ai_candidates:
            if page in pages:
                continue
            if not pages or any(abs(page - existing) == 1 for existing in pages):
                pages.append(page)
                pages.sort()
                changed = True
                if len(pages) >= max_pages:
                    break

    # Radar is candidate generation only. It can repair an internal hole but
    # cannot append a merely adjacent false-positive page.
    if pages and (local_pages or ai_pages):
        lower, upper = min(pages), max(pages)
        for page in sorted(set(radar_pages)):
            if len(pages) >= max_pages:
                break
            if lower < page < upper and page not in pages:
                pages.append(page)
        pages.sort()

    if len(pages) > max_pages:
        local_anchor = min(local_pages) if local_pages else min(pages)
        pages = sorted(pages, key=lambda value: (abs(value - local_anchor), value))[:max_pages]
        pages.sort()
    return pages


def _locator_conflict(
    *,
    local_pages: list[int],
    ai_pages: list[int],
    visual_pages: list[int],
    directory_pages: list[int],
    boundary_closed: bool,
) -> tuple[bool, str]:
    """Flag materially inconsistent locator evidence for human isolation."""
    sources = {
        "首尾项目闭合": sorted(set(local_pages)),
        "大模型语义": sorted(set(ai_pages)),
        "图片确认": sorted(set(visual_pages)),
    }
    nonempty = [(name, pages) for name, pages in sources.items() if pages]
    reasons: list[str] = []
    if boundary_closed and local_pages:
        local_set = set(local_pages)
        for name, pages in nonempty:
            if name == "首尾项目闭合":
                continue
            if set(pages) != local_set:
                reasons.append(f"首尾项目闭合页{sorted(local_set)}与{name}页{pages}不一致")
    elif len(nonempty) >= 2:
        for index, (left_name, left_pages) in enumerate(nonempty):
            for right_name, right_pages in nonempty[index + 1 :]:
                left, right = set(left_pages), set(right_pages)
                if left.isdisjoint(right):
                    reasons.append(f"{left_name}页{left_pages}与{right_name}页{right_pages}无交集")

    primary_pages = sorted({page for _, pages in nonempty for page in pages})
    if directory_pages and primary_pages:
        if all(
            min(abs(candidate - primary) for primary in primary_pages) > 1
            for candidate in directory_pages
        ):
            reasons.append(f"目录候选页{sorted(set(directory_pages))}与其他证据相距超过1页")
    elif directory_pages and not primary_pages:
        reasons.append(f"仅有目录候选页{sorted(set(directory_pages))}，尚无正文证据")
    return bool(reasons), "；".join(reasons)


def _locator_prompt(
    *,
    target_names: list[str],
    boundary_hints: str,
    radar_hints: str,
    directory_hints: Mapping[str, list[int]],
    scan_text: str,
    profile_context: Mapping[str, object] | None = None,
) -> str:
    profile_name, prompt_role, _ = _profile_prompt_values(profile_context)
    profile_rules = _profile_instruction_block(profile_context)
    example = {name: [14 + index] for index, name in enumerate(target_names[:3])}
    return f"""你是{prompt_role}。请定位下列目标表的PDF物理页码。
当前报告 profile：{profile_name}

【通用执行方法】
1. 页面标题、表头、首尾数据项目和明确的续表关系是主要证据；下方Python表格雷达可能误报，只能作为待复核候选，不能单独证明页面属于目标表。
2. 页码必须是PDF阅读器显示的物理页码，不是报告印刷页码。
3. 表格跨页时必须返回全部连续页；续页可能没有表名，也可能只有一条数据。
4. 如果某一页顶部仍有上一页表格的一条或数条数据、下方才开始新表，该页仍属于上一张表。
5. 对“近三年（综合）投资收益率”，必须找到近三年项目对应的真实平均值；主要经营指标中的季度
   “投资收益率/综合投资收益率”以及脚注中仅提及近三年项目名称，都不能作为该目标页。标题和
   平均值可能分居相邻行，此时仍属于一个完整项目。

【profile 专用规则】
{profile_rules}

【输出要求】
1. 输出前逐页自检：每个返回页必须有该目标的标题、表头、边界项目，或与前页表格连续的数据行；仅出现通用词、章节叙述或与候选页相邻都不算证据。
2. 只输出JSON对象，必须包含全部目标表名；找不到时返回空数组。

目标表：
{json.dumps(target_names, ensure_ascii=False)}

【首尾项目边界】
{boundary_hints}

【Python表格雷达候选页】
{radar_hints}

【目录换算候选页（仅作先验，不得单独证明）】
{json.dumps(directory_hints, ensure_ascii=False)}

【逐页文本摘要】
{scan_text}

输出示例：
{json.dumps(example, ensure_ascii=False)}"""


def _confidence_from_sources(
    *,
    boundary_closed: bool,
    local_pages: list[int],
    radar_pages: list[int],
    directory_pages: list[int],
    ai_pages: list[int],
    visual_pages: list[int],
) -> tuple[int, str]:
    """Score locator evidence deterministically (0-100).

    Boundary closure and multi-source agreement are the strongest signals.
    A single weak source (radar or directory only) stays below the review
    threshold so the UI can ask the user to confirm manually.
    """
    if boundary_closed and local_pages:
        return 92, "首尾项目闭合（确定性边界）"
    page_sets = [
        set(pages)
        for pages in (local_pages, directory_pages, radar_pages, ai_pages, visual_pages)
        if pages
    ]
    if len(page_sets) >= 2 and all(item == page_sets[0] for item in page_sets[1:]):
        return 88, "多路证据完全一致"
    if len(page_sets) >= 2:
        overlap = set.intersection(*page_sets)
        if overlap:
            return 80, "多路证据部分一致"
        return 62, "多路证据冲突，已按主证据合并"
    if local_pages:
        return 74, "关键词锚点定位（单路证据）"
    if directory_pages:
        return 60, "目录换算定位（单路证据）"
    if ai_pages:
        return 70, "大模型语义定位（单路证据）"
    if visual_pages:
        return 75, "视觉模型定位（单路证据）"
    if radar_pages:
        return 50, "结构雷达兜底（单路证据）"
    return 0, "未找到页码证据"
