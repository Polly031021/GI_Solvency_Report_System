"""逐页提取与跨页合并：提示词、单页校验、表头对齐与行合并。"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Callable, Mapping

from ..solvency_ai_table_extractor import (
    AI_TABLE_END_MARKERS,
    ExtractionLog,
    ExtractionQualityError,
    PageGrid,
    SOURCE_ITEM_RECALL_RATIO,
    _cell_has_disclosed_value,
    _compact,
    _item_recall_profile,
    _is_unsupported_vision_error,
    _render_images,
    _source_completeness_profile,
    _source_item_labels,
    _source_section_titles,
    _validate_source_section_order,
    _slice_grid_for_target,
    extract_unit_records,
    merge_unit_records,
    recover_unit_records_from_images,
    recover_minimum_capital_terminal_rows_core,
    unit_recovery_needed,
)
from ..solvency_table_boundaries import (
    boundary_instruction_for_table,
    boundary_items,
    item_hits_in_rows,
)
from ..solvency_disclosure_normalizer import normalize_three_year_return_rows_core
from ..table_number_repair import repair_thousand_separators
from ..solvency_table_extractor import (
    TABLE_EXCLUSIONS,
    TABLE_HEADERS,
    TABLE_SIGNATURES,
    UnitRecord,
)
from ..table_strategy_handlers import (
    CompletenessRequest,
    PostprocessRequest,
    PromptRequest,
)
from ..table_strategy_registry import active_table_strategy

from .config import (
    PAGE_HIGH_RES_VISION_MODE,
    PAGE_RETRY_MODE,
    PAGE_TEXT_MODE,
    PAGE_VISION_MODE,
)
from .llm import _call_chat


def _pipe_prompt(
    table_id: str,
    table_name: str,
    page_number: int,
    retry_reason: str = "",
    table_config: Mapping[str, object] | None = None,
) -> str:
    config = dict(table_config or {})
    signatures = tuple(
        config.get("content_terms")
        or config.get("completeness_terms")
        or TABLE_SIGNATURES.get(table_id, ())
    )
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
    strategy = active_table_strategy(table_id)
    return strategy.build_prompt(
        PromptRequest(
            mode="single_page",
            table_id=table_id,
            table_name=table_name,
            signatures=signatures,
            headers=headers,
            exclusions=exclusions,
            boundary_text=boundary_instruction_for_table(config or {"table_id": table_id}),
            page_number=page_number,
            retry_reason=retry_reason,
            table_config=config,
        )
    )


def _text_page_messages(
    table_id: str,
    table_name: str,
    page_number: int,
    grid: PageGrid,
    retry_reason: str = "",
    table_config: Mapping[str, object] | None = None,
) -> list[dict]:
    sliced = _slice_grid_for_target(
        table_id,
        grid.grid_text,
        table_config,
    )
    prompt = _pipe_prompt(
        table_id,
        table_name,
        page_number,
        retry_reason,
        table_config,
    )
    prompt += (
        "\n下面每行开头的三位数字和第一个竖线是页面坐标行号，不属于表格单元格；"
        "请忽略坐标行号后再重构。\n\n" + sliced
    )
    return [{"role": "user", "content": prompt}]


def _vision_page_messages(
    table_id: str,
    table_name: str,
    page_number: int,
    image_url: str,
    table_config: Mapping[str, object] | None = None,
) -> list[dict]:
    return [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": _pipe_prompt(
                        table_id,
                        table_name,
                        page_number,
                        table_config=table_config,
                    ),
                },
                {"type": "image_url", "image_url": {"url": image_url, "detail": "high"}},
            ],
        }
    ]


def _parse_pipe_rows_with_shape(content: str) -> tuple[list[list[str]], float]:
    text = re.sub(r"^```(?:csv|text)?\s*|\s*```$", "", str(content).strip(), flags=re.I)
    rows: list[list[str]] = []
    max_width = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.fullmatch(r"[\s|:\-]+", line) and "-" in line:
            continue
        if line.startswith("|"):
            line = line[1:]
        if line.endswith("|"):
            line = line[:-1]
        cells = [re.sub(r"\s+", " ", item).strip() for item in line.split("|")]
        if not any(cells):
            continue
        rows.append(cells)
        max_width = max(max_width, len(cells))
    if not rows:
        raise ExtractionQualityError("模型没有返回可解析的竖线分隔表格。")
    ragged_rows = sum(len(row) != max_width for row in rows)
    padded = [row + [""] * (max_width - len(row)) for row in rows]
    return padded, ragged_rows / max(len(rows), 1)


def _row_text(row: list[str]) -> str:
    return _compact("".join(row))


def _numeric_output_rows(rows: list[list[str]]) -> int:
    return sum(
        any(_cell_has_disclosed_value(cell) for cell in row[1:]) for row in rows if len(row) >= 2
    )


def _trim_page_rows(
    table_id: str,
    rows: list[list[str]],
    table_config: Mapping[str, object] | None = None,
) -> list[list[str]]:
    config = dict(table_config or active_table_strategy(table_id).table_config or {})
    markers = tuple(config.get("stop_terms") or AI_TABLE_END_MARKERS.get(table_id, ()))
    end = len(rows)
    for index, row in enumerate(rows):
        row_text = _row_text(row)
        if any(_compact(marker) in row_text for marker in markers):
            end = index
            break
    trimmed = rows[:end]
    if not trimmed:
        raise ExtractionQualityError("目标表在相邻表边界之前没有有效行。")
    return trimmed


def _prepare_page_rows(
    table_id: str,
    content: str,
    source_grids: list[PageGrid] | None = None,
) -> tuple[list[list[str]], str, float]:
    strategy = active_table_strategy(table_id)
    config = dict(strategy.table_config)
    rows, ragged_ratio = _parse_pipe_rows_with_shape(content)
    rows, postprocess_notes = strategy.postprocess(
        PostprocessRequest(
            table_id=table_id,
            rows=rows,
            source_grids=source_grids,
            normalize_three_year=normalize_three_year_return_rows_core,
            recover_minimum_capital=recover_minimum_capital_terminal_rows_core,
        )
    )
    # ★ 数值修复：模型常把千分位逗号写成小数点（1.234.567.89），
    #   这里用同一页的原文网格做佐证做一次确定性还原。
    repair = repair_thousand_separators(
        rows,
        "\n".join(
            str(getattr(grid, "grid_text", "") or "")
            for grid in (source_grids or [])
        ),
    )
    if repair.changed:
        rows = repair.rows
        postprocess_notes = (
            *postprocess_notes,
            f"已修复{len(repair.repairs)}处千分位误读",
        )
    if any("归一化" in note for note in postprocess_notes):
        ragged_ratio = 0.0
    note = "；".join(postprocess_notes)
    return _trim_page_rows(table_id, rows, config), note, ragged_ratio


def _validate_single_page_core(
    table_id: str,
    rows: list[list[str]],
    grid: PageGrid,
    ragged_ratio: float = 0.0,
    source_item_recall_ratio: float = SOURCE_ITEM_RECALL_RATIO,
    table_config: Mapping[str, object] | None = None,
) -> tuple[float, int, int]:
    config = dict(table_config or {})
    rows = _trim_page_rows(table_id, rows, config)
    if ragged_ratio > 0.20:
        raise ExtractionQualityError(f"本页超过20%的输出行列数不一致（{ragged_ratio:.0%}）。")
    expected_rows, source_terms = _source_completeness_profile(table_id, [grid], config)
    semantic_items = set(boundary_items(table_id, config))
    exact_hits = set(item_hits_in_rows(rows, semantic_items))
    flat = _compact("".join(cell for row in rows for cell in row))
    missing_terms = [
        term
        for term in source_terms
        if (term not in exact_hits if term in semantic_items else _compact(term) not in flat)
    ]
    if missing_terms:
        raise ExtractionQualityError(f"本页提取遗漏源PDF项目：{'、'.join(missing_terms)}。")
    actual_rows = _numeric_output_rows(rows)
    if expected_rows > 0:
        minimum = max(1, math.ceil(expected_rows * 0.55))
        if actual_rows < minimum:
            raise ExtractionQualityError(
                f"本页提取不完整：原PDF网格约有{expected_rows}条数值行，"
                f"模型仅返回{actual_rows}条，至少应返回{minimum}条。"
            )
    elif len(rows) < 1:
        raise ExtractionQualityError("本页未提取到有效表格行。")
    source_labels = _source_item_labels(table_id, [grid], config)
    source_sections = _source_section_titles(table_id, [grid], config)
    _validate_source_section_order(rows, source_sections)
    item_recall, missing_items = _item_recall_profile(rows, source_labels)
    if len(source_labels) >= 4 and item_recall < source_item_recall_ratio:
        raise ExtractionQualityError(
            f"本页项目行召回率不足：{item_recall:.0%}，要求至少"
            f"{source_item_recall_ratio:.0%}；疑似遗漏："
            f"{'、'.join(missing_items[:8])}。"
        )
    score = min(99.0, 70.0 + min(actual_rows, 20) * 1.2)
    return score, expected_rows, actual_rows


def _validate_single_page(
    table_id: str,
    rows: list[list[str]],
    grid: PageGrid,
    ragged_ratio: float = 0.0,
) -> tuple[float, int, int]:
    strategy = active_table_strategy(table_id)
    return strategy.validate_completeness(
        CompletenessRequest(
            mode="single_page",
            table_id=table_id,
            rows=rows,
            validator=_validate_single_page_core,
            validator_kwargs={
                "grid": grid,
                "ragged_ratio": ragged_ratio,
            },
        )
    )


def _header_score(table_id: str, row: list[str]) -> int:
    text = _row_text(row)
    config = active_table_strategy(table_id).table_config
    headers = tuple(config.get("header_terms") or TABLE_HEADERS.get(table_id, ()))
    return sum(_compact(term) in text for term in headers)


def _is_same_header(left: list[str], right: list[str]) -> bool:
    width = max(len(left), len(right))
    if not width:
        return False
    a = left + [""] * (width - len(left))
    b = right + [""] * (width - len(right))
    matches = sum(bool(_compact(x)) and _compact(x) == _compact(y) for x, y in zip(a, b))
    return matches / width >= 0.5


_ROW_NUMBER_CELL_RE = re.compile(r"^\d+(?:\.\d+)*(?:[、.)）])?$")


def _strip_orphan_row_number_column(
    table_id: str,
    header: list[str],
    rows: list[list[str]],
) -> list[list[str]]:
    """Remove a source row-number column omitted by the canonical header."""
    if table_id != "SOLVENCY_MAIN" or not header or not rows:
        return rows
    header_text = _compact("".join(header[:2]))
    if any(term in header_text for term in ("行次", "序号", "编号")):
        return rows

    source_width = len(header) + 1
    candidates = [row for row in rows if len(row) == source_width]
    if len(candidates) < 2:
        return rows
    row_number_hits = sum(
        bool(_ROW_NUMBER_CELL_RE.fullmatch(_compact(row[0]))) and bool(_compact(row[1]))
        for row in candidates
    )
    if row_number_hits / len(candidates) < 0.8:
        return rows

    return [row[1:] if len(row) == source_width else row for row in rows]


def _meaningful_width(rows: list[list[str]]) -> int:
    return max(
        (
            max(
                (index + 1 for index, cell in enumerate(row) if _compact(cell)),
                default=0,
            )
            for row in rows
        ),
        default=0,
    )


def _value_column_profile(rows: list[list[str]]) -> tuple[int, ...]:
    if not rows:
        return ()
    header_text = _compact("".join(rows[0][:2]))
    row_number_table = any(term in header_text for term in ("行次", "序号", "编号"))
    counts: Counter[int] = Counter()
    numeric_rows = 0
    for row in rows[1:]:
        value_start = 2 if row_number_table and len(row) > 2 else 1
        positions = {
            index
            for index, cell in enumerate(row[value_start:], start=value_start)
            if re.search(r"\d", cell)
        }
        if not positions:
            continue
        numeric_rows += 1
        counts.update(positions)
    minimum_hits = max(1, math.ceil(numeric_rows * 0.30))
    return tuple(sorted(index for index, hits in counts.items() if hits >= minimum_hits))


def _validate_cross_page_columns(
    pages: list[int],
    page_rows: dict[int, list[list[str]]],
) -> str:
    profiles = {
        page: (
            _meaningful_width(page_rows[page]),
            _value_column_profile(page_rows[page]),
        )
        for page in pages
        if page in page_rows
    }
    if len(profiles) <= 1:
        return "单页表无需跨页列一致性检查"
    widths = [width for width, _ in profiles.values() if width]
    if widths and max(widths) - min(widths) > 1:
        raise ExtractionQualityError(
            "跨页列数不一致："
            + "；".join(f"第{page}页{width}列" for page, (width, _) in profiles.items())
        )
    reference_page, (_, reference_columns) = next(iter(profiles.items()))
    for page, (_, columns) in list(profiles.items())[1:]:
        if not reference_columns or not columns:
            continue
        if len(set(reference_columns) ^ set(columns)) > 1:
            raise ExtractionQualityError(
                f"跨页数值列位置不一致：第{reference_page}页"
                f"{reference_columns}，第{page}页{columns}。"
            )
    return "跨页列数与数值列位置一致"


def _merge_page_rows(
    table_id: str,
    table_name: str,
    pages: list[int],
    page_rows: dict[int, list[list[str]]],
) -> list[list[str]]:
    ordered = [(page, page_rows[page]) for page in pages if page in page_rows]
    if not ordered:
        raise ExtractionQualityError("没有可拼接的逐页结果。")
    _validate_cross_page_columns(pages, page_rows)
    header_candidates = [
        (score, page_index, row_index, row)
        for page_index, (_, rows) in enumerate(ordered)
        for row_index, row in enumerate(rows)
        if (score := _header_score(table_id, row)) > 0 and len(row) >= 2
    ]
    if header_candidates:
        _, header_page_index, header_row_index, header = max(
            header_candidates,
            key=lambda item: (item[0], -item[1], -item[2]),
        )
    else:
        header_page_index, header_row_index = 0, -1
        width = max(len(row) for _, rows in ordered for row in rows)
        header = [f"列{index + 1}" for index in range(width)]

    merged_data: list[list[str]] = []
    category_titles = {"效益类指标", "规模类指标", "品质类指标"}
    for page_index, (_, rows) in enumerate(ordered):
        start = header_row_index + 1 if page_index == header_page_index else 0
        for row in rows[start:]:
            if _is_same_header(row, header) or _header_score(table_id, row) >= 2:
                continue
            nonempty = [cell for cell in row if _compact(cell)]
            row_text = _row_text(row)
            if (
                len(nonempty) <= 1
                and _compact(table_name) in row_text
                and not any(_compact(title) in row_text for title in category_titles)
            ):
                continue
            if not nonempty:
                continue
            if merged_data and row == merged_data[-1]:
                continue
            merged_data.append(row)
    if not merged_data:
        raise ExtractionQualityError("逐页结果去重后没有数据行。")
    merged_data = _strip_orphan_row_number_column(
        table_id,
        header,
        merged_data,
    )
    width = max(len(header), *(len(row) for row in merged_data))
    if len(header) < width:
        header = header + [f"列{index + 1}" for index in range(len(header), width)]
    return [
        header[:width],
        *[(row + [""] * (width - len(row)))[:width] for row in merged_data],
    ]


def _extract_one_page(
    *,
    pdf_bytes: bytes,
    table_id: str,
    table_name: str,
    page_number: int,
    grid: PageGrid,
    api_key: str,
    base_url: str,
    model: str,
    auto_vision_retry: bool,
    force_vision: bool,
    timeout: int,
    post_func: Callable | None,
    render_func: Callable | None = None,
    table_config: Mapping[str, object] | None = None,
) -> tuple[
    list[list[str]] | None,
    str,
    float,
    list[ExtractionLog],
    list[UnitRecord],
]:
    logs: list[ExtractionLog] = []
    render_images = render_func or _render_images
    text_error = ""
    if not force_vision:
        try:
            if grid.word_count < 6:
                raise ExtractionQualityError("本页可检索文字不足。")
            content = _call_chat(
                api_key=api_key,
                base_url=base_url,
                model=model,
                messages=_text_page_messages(
                    table_id,
                    table_name,
                    page_number,
                    grid,
                    table_config=table_config,
                ),
                timeout=timeout,
                post_func=post_func,
            )
            rows, normalization_note, ragged_ratio = _prepare_page_rows(table_id, content, [grid])
            score, expected, actual = _validate_single_page(table_id, rows, grid, ragged_ratio)
            detail = f"逐页提取完成；源网格约{expected}条数值行，返回{actual}条。"
            if normalization_note:
                detail += normalization_note + "。"
            logs.append(
                ExtractionLog(
                    table_id,
                    table_name,
                    [page_number],
                    PAGE_TEXT_MODE,
                    "成功",
                    detail,
                )
            )
            return (
                rows,
                PAGE_TEXT_MODE,
                score,
                logs,
                extract_unit_records(table_id, rows, [grid]),
            )
        except Exception as exc:
            text_error = str(exc)
            logs.append(
                ExtractionLog(
                    table_id,
                    table_name,
                    [page_number],
                    PAGE_TEXT_MODE,
                    "触发图片重试" if auto_vision_retry else "失败",
                    text_error,
                )
            )
    if not (force_vision or auto_vision_retry):
        return None, "", 0.0, logs, []

    vision_error = ""
    try:
        images = render_images(pdf_bytes, [page_number])
        content = _call_chat(
            api_key=api_key,
            base_url=base_url,
            model=model,
            messages=_vision_page_messages(
                table_id, table_name, page_number, images[0][1], table_config
            ),
            timeout=timeout,
            post_func=post_func,
        )
        rows, normalization_note, ragged_ratio = _prepare_page_rows(table_id, content, [grid])
        score, expected, actual = _validate_single_page(table_id, rows, grid, ragged_ratio)
        detail = f"逐页图片提取完成；源网格约{expected}条数值行，返回{actual}条。"
        if normalization_note:
            detail += normalization_note + "。"
        page_units = extract_unit_records(table_id, rows, [grid])
        if unit_recovery_needed(rows, page_units):
            try:
                recovered_units = recover_unit_records_from_images(
                    table_id=table_id,
                    table_name=table_name,
                    pages=[page_number],
                    rows=rows,
                    images=images,
                    api_key=api_key,
                    base_url=base_url,
                    model=model,
                    timeout=timeout,
                    post_func=post_func,
                )
                page_units = merge_unit_records(page_units, recovered_units)
                if recovered_units:
                    detail += f"单位专用补提取获得{len(recovered_units)}条明确单位。"
                else:
                    detail += "单位专用补提取未发现明确单位，已保留待核对状态。"
            except Exception as unit_exc:
                detail += f"单位专用补提取失败，已保留待核对状态：{unit_exc}。"
        logs.append(
            ExtractionLog(
                table_id,
                table_name,
                [page_number],
                PAGE_VISION_MODE,
                "成功",
                detail,
            )
        )
        return rows, PAGE_VISION_MODE, score, logs, page_units
    except Exception as exc:
        vision_error = str(exc)
        unsupported = _is_unsupported_vision_error(exc)
        logs.append(
            ExtractionLog(
                table_id,
                table_name,
                [page_number],
                PAGE_VISION_MODE,
                "接口不支持图片，转文本纠错" if unsupported else "失败，转3倍高清图片",
                vision_error,
            )
        )

    if not unsupported:
        try:
            high_res_images = render_images(
                pdf_bytes,
                [page_number],
                zoom=3.0,
                jpeg_quality=92,
            )
            content = _call_chat(
                api_key=api_key,
                base_url=base_url,
                model=model,
                messages=_vision_page_messages(
                    table_id,
                    table_name,
                    page_number,
                    high_res_images[0][1],
                    table_config,
                ),
                timeout=timeout,
                post_func=post_func,
            )
            rows, normalization_note, ragged_ratio = _prepare_page_rows(table_id, content, [grid])
            score, expected, actual = _validate_single_page(table_id, rows, grid, ragged_ratio)
            detail = f"3倍高清图片提取完成；源网格约{expected}条数值行，" f"返回{actual}条。"
            if normalization_note:
                detail += normalization_note + "。"
            page_units = extract_unit_records(table_id, rows, [grid])
            if unit_recovery_needed(rows, page_units):
                try:
                    recovered_units = recover_unit_records_from_images(
                        table_id=table_id,
                        table_name=table_name,
                        pages=[page_number],
                        rows=rows,
                        images=high_res_images,
                        api_key=api_key,
                        base_url=base_url,
                        model=model,
                        timeout=timeout,
                        post_func=post_func,
                    )
                    page_units = merge_unit_records(page_units, recovered_units)
                except Exception as unit_exc:
                    detail += f"单位补提取失败，保留待核对：{unit_exc}。"
            logs.append(
                ExtractionLog(
                    table_id,
                    table_name,
                    [page_number],
                    PAGE_HIGH_RES_VISION_MODE,
                    "成功",
                    detail,
                )
            )
            return rows, PAGE_HIGH_RES_VISION_MODE, score, logs, page_units
        except Exception as high_res_exc:
            vision_error = f"{vision_error}；3倍高清重试：{high_res_exc}"
            logs.append(
                ExtractionLog(
                    table_id,
                    table_name,
                    [page_number],
                    PAGE_HIGH_RES_VISION_MODE,
                    "失败，转文本纠错",
                    str(high_res_exc),
                )
            )

    if grid.word_count < 6:
        logs.append(
            ExtractionLog(
                table_id,
                table_name,
                [page_number],
                PAGE_RETRY_MODE,
                "失败",
                "本页没有足够文字层，图片模式也未成功。",
            )
        )
        return None, "", 0.0, logs, []
    try:
        reason = "；".join(item for item in (text_error, vision_error) if item)
        content = _call_chat(
            api_key=api_key,
            base_url=base_url,
            model=model,
            messages=_text_page_messages(
                table_id, table_name, page_number, grid, reason, table_config
            ),
            timeout=timeout,
            post_func=post_func,
        )
        rows, normalization_note, ragged_ratio = _prepare_page_rows(table_id, content, [grid])
        score, expected, actual = _validate_single_page(table_id, rows, grid, ragged_ratio)
        detail = f"逐页文本纠错完成；源网格约{expected}条数值行，返回{actual}条。"
        if normalization_note:
            detail += normalization_note + "。"
        logs.append(
            ExtractionLog(
                table_id,
                table_name,
                [page_number],
                PAGE_RETRY_MODE,
                "成功",
                detail,
            )
        )
        return (
            rows,
            PAGE_RETRY_MODE,
            score,
            logs,
            extract_unit_records(table_id, rows, [grid]),
        )
    except Exception as exc:
        logs.append(
            ExtractionLog(
                table_id,
                table_name,
                [page_number],
                PAGE_RETRY_MODE,
                "失败",
                str(exc),
            )
        )
        return None, "", 0.0, logs, []
