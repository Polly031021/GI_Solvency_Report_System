"""PDF 文本网格：文字层切列、按目标表裁剪与给模型的切片说明。"""

from __future__ import annotations

import io
import re
from typing import Mapping

import pdfplumber
from ..solvency_pdf_locator import PageMatch
from ..solvency_table_boundaries import (
    boundary_instruction_for_table,
    boundary_variants,
    end_item_groups,
    line_has_item,
)
from ..solvency_table_extractor import (
    TABLE_EXCLUSIONS,
    TABLE_HEADERS,
    TABLE_SIGNATURES,
    TABLE_START_MARKERS,
)
from ..table_strategy_handlers import BoundaryRequest, PromptRequest
from ..table_strategy_registry import active_table_strategy

from .models import (
    AI_TABLE_END_MARKERS,
    GRID_COLUMNS,
    OPERATING_SECTION_TITLES,
    PageGrid,
    TABLE_COMPLETENESS_TERMS,
    _clean,
    _compact,
)


def _group_matches(
    matches: list[PageMatch],
) -> list[tuple[str, str, list[int], str, dict]]:
    grouped: dict[str, dict] = {}
    order: list[str] = []
    for match in matches:
        if match.table_id not in grouped:
            grouped[match.table_id] = {
                "name": match.table_name,
                "pages": [],
                "strategy_id": match.strategy_id,
                "table_config": dict(match.table_config),
            }
            order.append(match.table_id)
        elif (
            match.strategy_id
            and grouped[match.table_id]["strategy_id"]
            and match.strategy_id != grouped[match.table_id]["strategy_id"]
        ):
            raise ValueError(f"目标表 {match.table_id} 同时引用了多个提取策略。")
        elif match.strategy_id:
            grouped[match.table_id]["strategy_id"] = match.strategy_id
        grouped[match.table_id]["pages"].extend(match.pages)
    return [
        (
            table_id,
            grouped[table_id]["name"],
            sorted(set(grouped[table_id]["pages"])),
            grouped[table_id]["strategy_id"],
            grouped[table_id]["table_config"],
        )
        for table_id in order
    ]


def _words_to_grid(page, columns: int = GRID_COLUMNS) -> tuple[str, int]:
    words = page.extract_words(
        x_tolerance=2, y_tolerance=3, keep_blank_chars=False, use_text_flow=False
    )
    if not words:
        return "", 0
    groups: list[dict] = []
    for word in sorted(words, key=lambda item: (float(item["top"]), float(item["x0"]))):
        top = float(word["top"])
        if not groups or abs(top - groups[-1]["top"]) > 3.5:
            groups.append({"top": top, "words": [word]})
        else:
            group = groups[-1]
            group["words"].append(word)
            group["top"] = (group["top"] * (len(group["words"]) - 1) + top) / len(group["words"])

    width = max(float(page.width), 1.0)
    lines: list[str] = []
    for row_number, group in enumerate(groups):
        canvas = [" "] * columns
        for word in sorted(group["words"], key=lambda item: float(item["x0"])):
            text = _clean(word.get("text", ""))
            column = min(columns - 1, max(0, int(float(word["x0"]) / width * (columns - 1))))
            while column < columns and canvas[column] != " ":
                column += 1
            for character in text:
                if column >= columns:
                    break
                canvas[column] = character
                column += 1
        line = "".join(canvas).rstrip()
        if line:
            lines.append(f"{row_number:03d}|{line}")
    return "\n".join(lines), len(words)


def build_pdf_grids(pdf_bytes: bytes, pages: list[int]) -> list[PageGrid]:
    result: list[PageGrid] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page_number in pages:
            if page_number < 1 or page_number > len(pdf.pages):
                raise ValueError(f"页码 {page_number} 超出PDF范围。")
            grid, word_count = _words_to_grid(pdf.pages[page_number - 1])
            result.append(PageGrid(page_number, grid, word_count))
    return result


def _instructions(
    table_id: str,
    table_name: str,
    pages: list[int],
    table_config: Mapping[str, object] | None = None,
) -> str:
    config = dict(table_config or {})
    signatures = tuple(config.get("content_terms") or TABLE_SIGNATURES.get(table_id, ()))
    headers = tuple(config.get("header_terms") or TABLE_HEADERS.get(table_id, ()))
    configured_exclusions = tuple(
        dict.fromkeys(
            [
                *config.get("exclude_item_terms", ()),
                *config.get("profile_exclude_items", ()),
                *config.get("stop_terms", ()),
            ]
        )
    )
    exclusions = configured_exclusions or TABLE_EXCLUSIONS.get(table_id, ())
    completeness = tuple(
        config.get("completeness_terms") or TABLE_COMPLETENESS_TERMS.get(table_id, ())
    )
    strategy = active_table_strategy(table_id)
    return strategy.build_prompt(
        PromptRequest(
            mode="full_table",
            table_id=table_id,
            table_name=table_name,
            signatures=signatures,
            headers=headers,
            exclusions=exclusions,
            completeness_terms=completeness,
            boundary_text=boundary_instruction_for_table(config or {"table_id": table_id}),
            pages=tuple(pages),
            table_config=config,
        )
    )


def _slice_grid_for_target_core(
    table_id: str,
    grid_text: str,
    *,
    operating_metrics: bool = False,
    respect_variant_activation: bool = False,
    require_value_for_boundary_items: bool = False,
    table_config: Mapping[str, object] | None = None,
) -> str:
    config = dict(table_config or {})
    lines = grid_text.splitlines()
    if not lines:
        return grid_text

    compact_lines = [_compact(line.partition("|")[2]) for line in lines]

    def boundary_line_has_item(line: str, item: str) -> bool:
        matched = line_has_item(
            line,
            item,
            require_value=require_value_for_boundary_items,
        )
        if matched or not require_value_for_boundary_items:
            return matched
        # ``compact_item`` intentionally removes punctuation, so a business
        # row whose disclosed values are only "-" placeholders cannot satisfy
        # ``require_value=True`` there. Preserve that legitimate row while
        # still rejecting a bare numbered section title such as
        # "十一、最低资本".
        if not line_has_item(line, item):
            return False
        content = str(line or "").partition("|")[2]
        return bool(
            re.search(
                r"(?:--|—|-|[<＜]不适用[>＞]|不适用|N/?A)\s*$",
                content,
                flags=re.I,
            )
        )

    variants = boundary_variants(table_id, table_config)
    boundary = variants[0] if variants else {}
    variant_profiles: list[tuple[int, int, dict, int | None, bool]] = []
    for variant_index, candidate in enumerate(variants):
        activation_terms = tuple(candidate.get("activation_terms", ()))
        if (
            respect_variant_activation
            and activation_terms
            and not any(
                any(_compact(term) in line for term in activation_terms) for line in compact_lines
            )
        ):
            continue
        candidate_starts = tuple(candidate.get("start_items", ()))
        candidate_end_groups = end_item_groups(table_id, candidate)
        candidate_ends = tuple(item for group in candidate_end_groups for item in group)
        start_hits = [
            index
            for index, line in enumerate(lines)
            if any(boundary_line_has_item(line, item) for item in candidate_starts)
        ]
        end_hits = [
            index
            for index, line in enumerate(lines)
            if any(boundary_line_has_item(line, item) for item in candidate_ends)
        ]
        preferred_end_items = candidate_end_groups[0] if candidate_end_groups else ()
        preferred_end_hits = [
            index
            for index, line in enumerate(lines)
            if any(boundary_line_has_item(line, item) for item in preferred_end_items)
        ]
        ordered = bool(
            start_hits
            and end_hits
            and any(end >= start for start in start_hits for end in end_hits)
        )
        end_only_continuation = bool(
            operating_metrics
            and preferred_end_hits
            and (not start_hits or min(preferred_end_hits) < min(start_hits))
        )
        if end_only_continuation:
            # A continuation tail can end before a footnote later repeats the
            # table's start item. Treat it as end-only evidence so the footnote
            # cannot replace the real page start.
            evidence_rank = 3
        elif ordered:
            evidence_rank = 4
        elif start_hits and end_hits:
            evidence_rank = 1
        elif end_hits:
            evidence_rank = 3
        elif start_hits:
            evidence_rank = 2
        else:
            evidence_rank = 0
        variant_profiles.append(
            (
                evidence_rank,
                -variant_index,
                candidate,
                (min(start_hits) if start_hits and not end_only_continuation else None),
                end_only_continuation,
            )
        )
    if variant_profiles:
        _, _, boundary, profiled_start_hit, end_only_continuation = max(
            variant_profiles,
            key=lambda item: (item[0], item[1]),
        )
    else:
        profiled_start_hit = None
        end_only_continuation = False

    start_items = tuple(boundary.get("start_items", ()))
    configured_end_groups = end_item_groups(table_id, boundary)
    title_terms = tuple(boundary.get("title_terms", ()))

    title_index = next(
        (
            index
            for index, line in enumerate(compact_lines)
            if any(_compact(term) in line for term in title_terms)
        ),
        None,
    )
    search_from = title_index + 1 if title_index is not None else 0

    start_hit = (
        profiled_start_hit
        if profiled_start_hit is not None and profiled_start_hit >= search_from
        else None
    )
    if start_hit is None and not end_only_continuation:
        for item in start_items:
            start_hit = next(
                (
                    index
                    for index, line in enumerate(
                        lines[search_from:],
                        start=search_from,
                    )
                    if boundary_line_has_item(line, item)
                ),
                None,
            )
            if start_hit is not None:
                break

    if operating_metrics and title_index is not None and start_hit is not None:
        # Keep the report's real table header. Some operating-metrics pages
        # contain a category row immediately before the first business item;
        # using only ``start_hit - 1`` would drop both the title and columns.
        start = title_index
    elif start_hit is not None:
        lower_bound = title_index if title_index is not None else 0
        start = max(lower_bound, start_hit - 1)
    else:
        start = title_index if title_index is not None else 0

    if operating_metrics and start_hit is None and title_index is None:
        continuation_titles = tuple(
            map(
                _compact,
                ("效益类指标", "规模类指标", "品质类指标"),
            )
        )
        continuation_start = next(
            (
                index
                for index, line in enumerate(compact_lines)
                if any(title in line for title in continuation_titles)
            ),
            None,
        )
        product_detail_markers = tuple(
            map(
                _compact,
                (
                    "前五大产品的信息",
                    "第一大产品的信息",
                    "第二大产品的信息",
                    "第三大产品的信息",
                    "第四大产品的信息",
                    "第五大产品的信息",
                ),
            )
        )
        product_details_before_continuation = continuation_start is not None and any(
            any(marker in line for marker in product_detail_markers)
            for line in compact_lines[:continuation_start]
        )
        if continuation_start is not None and product_details_before_continuation:
            start = continuation_start

    end_search_from = start_hit + 1 if start_hit is not None else start
    end_hit = None
    candidate_end_groups = configured_end_groups if operating_metrics else configured_end_groups[:1]
    for group_index, end_items in enumerate(candidate_end_groups):
        candidate_end_hit = next(
            (
                index
                for index, line in enumerate(lines[end_search_from:], start=end_search_from)
                if any(boundary_line_has_item(line, item) for item in end_items)
            ),
            None,
        )
        if candidate_end_hit is not None:
            if (
                operating_metrics
                and group_index > 0
                and any(
                    any(
                        line_has_item(lines[index], title)
                        for title in (config.get("section_titles") or OPERATING_SECTION_TITLES)
                    )
                    for index in range(candidate_end_hit + 1, len(lines))
                )
            ):
                # “综合投资收益率”只是未披露后续类别时的备用终点。
                # 同页已出现效益/规模/品质类标题，说明目标表仍在继续。
                continue
            end_hit = candidate_end_hit
            break
    if end_hit is not None:
        end = end_hit + 1
    else:
        if start_hit is None and title_index is None:
            for index, line in enumerate(compact_lines):
                if any(
                    _compact(term) in line
                    for term in (
                        config.get("start_item_terms")
                        or config.get("title_terms")
                        or TABLE_START_MARKERS.get(table_id, ())
                    )
                ):
                    start = index
                    break
        end = len(lines)
        for index in range(start + 1, len(lines)):
            if any(
                _compact(term) in compact_lines[index]
                for term in (config.get("stop_terms") or AI_TABLE_END_MARKERS.get(table_id, ()))
            ):
                end = index
                break
    return "\n".join(lines[start:end])


def _slice_grid_for_target(
    table_id: str,
    grid_text: str,
    table_config: Mapping[str, object] | None = None,
) -> str:
    strategy = active_table_strategy(table_id)
    return strategy.enforce_boundaries(
        BoundaryRequest(
            mode="grid_slice",
            table_id=table_id,
            grid_text=grid_text,
            engine=_slice_grid_for_target_core,
            table_config=dict(table_config or {}),
        )
    )
