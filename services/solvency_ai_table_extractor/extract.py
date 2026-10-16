"""逐页提取主流程：调用模型、校验、重建表格与提取日志。"""

from __future__ import annotations

import math
import re
from typing import Callable, Mapping

import pandas as pd
from ..solvency_disclosure_normalizer import normalize_three_year_return_rows_core
from ..solvency_pdf_locator import PageMatch
from ..solvency_table_boundaries import (
    TableBoundaryError,
    boundary_items,
    enforce_output_boundaries,
    item_hits_in_rows,
)
from ..solvency_table_extractor import (
    TABLE_EXCLUSIONS,
    TABLE_HEADERS,
    TABLE_SIGNATURES,
    ExtractedTable,
)
from ..table_strategy_handlers import (
    BoundaryRequest,
    CompletenessRequest,
    PostprocessRequest,
)
from ..table_strategy_registry import active_table_strategy, resolve_table_strategy

from .grid import _group_matches, build_pdf_grids
from .llm import (
    _call_model,
    _coerce_table,
    _grid_messages,
    _grid_repair_messages,
    _is_unsupported_vision_error,
    _parse_json,
    _render_images,
    _vision_messages,
)
from .models import (
    AIExtractionBundle,
    ExtractionLog,
    ExtractionQualityError,
    PageGrid,
    SOURCE_ITEM_RECALL_RATIO,
    SOURCE_ROW_COVERAGE_RATIO,
    TABLE_COMPLETENESS_TERMS,
    TABLE_MIN_NUMERIC_ROWS,
    TEXT_MODE,
    TEXT_RETRY_MODE,
    VISION_HIGH_RES_MODE,
    VISION_MODE,
    _compact,
)
from .source_profile import (
    _cell_has_disclosed_value,
    _item_recall_profile,
    _source_completeness_profile,
    _source_item_labels,
    _source_section_titles,
    _trim_adjacent_rows,
    _validate_source_section_order,
    recover_minimum_capital_terminal_rows_core,
)
from .units import (
    extract_unit_records,
    merge_unit_records,
    recover_unit_records_from_images,
    unit_records_from_payload,
    unit_recovery_needed,
)


def _validate(
    table_id: str,
    rows: list[list[str]],
    ragged_ratio: float,
    *,
    expected_source_rows: int = 0,
    source_required_terms: tuple[str, ...] = (),
    source_item_labels: tuple[str, ...] = (),
    source_section_titles: tuple[str, ...] = (),
    required_signature_hits: int | None = None,
    source_item_recall_ratio: float = SOURCE_ITEM_RECALL_RATIO,
    configured_required: tuple[str, ...] = (),
    configured_exclusions: tuple[str, ...] = (),
    configured_minimum_rows: int = 0,
    table_config: Mapping[str, object] | None = None,
) -> tuple[float, str]:
    config = dict(table_config or {})
    if len(rows) < 2 or len(rows[0]) < 2:
        raise ExtractionQualityError("重构结果少于2行或2列。")
    if ragged_ratio > 0.2:
        raise ExtractionQualityError(f"超过20%的数据行列数与表头不一致（{ragged_ratio:.0%}）。")
    flat = _compact("".join(cell for row in rows for cell in row))
    required = (
        configured_required
        or tuple(config.get("content_terms", ()))
        or TABLE_SIGNATURES.get(table_id, ())
    )
    required_hits = [term for term in required if _compact(term) in flat]
    minimum = (
        required_signature_hits
        if required_signature_hits is not None
        else (len(required) if table_id == "SOLVENCY_MAIN" else min(1, len(required)))
    )
    if required and len(required_hits) < minimum:
        raise ExtractionQualityError(f"缺少目标表核心内容：{'、'.join(required)}。")
    exclusions = (
        tuple(configured_exclusions)
        or tuple(config.get("stop_terms", ()))
        or TABLE_EXCLUSIONS.get(table_id, ())
    )
    exclusion_hits = [term for term in exclusions if _compact(term) in flat]
    if exclusion_hits:
        raise ExtractionQualityError(f"疑似混入相邻表内容：{'、'.join(exclusion_hits)}。")

    completeness_terms = (
        source_required_terms
        or tuple(config.get("completeness_terms", ()))
        or TABLE_COMPLETENESS_TERMS.get(table_id, ())
    )
    semantic_items = set(boundary_items(table_id, config))
    exact_hits = set(item_hits_in_rows(rows, semantic_items))
    missing_completeness = [
        term
        for term in completeness_terms
        if (term not in exact_hits if term in semantic_items else _compact(term) not in flat)
    ]
    if missing_completeness:
        raise ExtractionQualityError(
            f"整表提取不完整，缺少原页面项目：{'、'.join(missing_completeness)}。"
        )

    data_rows = rows[1:]
    header_text = _compact("".join(rows[0][:2]))
    row_number_table = any(term in header_text for term in ("行次", "序号", "编号"))
    numeric_rows = 0
    shifted_rows = 0
    for row in data_rows:
        value_start = 2 if row_number_table and len(row) > 2 else 1
        has_number = any(_cell_has_disclosed_value(cell) for cell in row[value_start:])
        numeric_rows += bool(has_number)
        if not row_number_table:
            shifted_rows += bool(re.search(r"\d", row[0]) and not has_number)
    configured_minimum = (
        int(config.get("minimum_rows", 0) or 0)
        if "minimum_rows" in config
        else max(
            int(TABLE_MIN_NUMERIC_ROWS.get(table_id, 1)),
            int(configured_minimum_rows or 0),
        )
    )
    minimum_numeric_rows = (
        min(configured_minimum, expected_source_rows)
        if expected_source_rows > 0
        else configured_minimum
    )
    if numeric_rows < minimum_numeric_rows:
        raise ExtractionQualityError(
            f"整表提取不完整：仅识别到{numeric_rows}条数值行，至少应有{minimum_numeric_rows}条。"
        )
    if expected_source_rows >= 3:
        coverage_minimum = max(
            minimum_numeric_rows,
            min(expected_source_rows, math.ceil(expected_source_rows * SOURCE_ROW_COVERAGE_RATIO)),
        )
        if numeric_rows < coverage_minimum:
            raise ExtractionQualityError(
                f"整表覆盖不足：原PDF网格约有{expected_source_rows}条数值行，"
                f"模型仅返回{numeric_rows}条，至少应返回{coverage_minimum}条。"
            )
    item_recall, missing_items = _item_recall_profile(rows, source_item_labels)
    _validate_source_section_order(rows, source_section_titles)
    if len(source_item_labels) >= 4 and item_recall < source_item_recall_ratio:
        preview = "、".join(missing_items[:8])
        raise ExtractionQualityError(
            f"项目行召回率不足：{item_recall:.0%}，要求至少"
            f"{source_item_recall_ratio:.0%}；疑似遗漏：{preview}。"
        )
    if len(data_rows) >= 3 and numeric_rows / len(data_rows) < 0.25:
        raise ExtractionQualityError("有效数值行比例过低，疑似列错位。")
    if shifted_rows >= 2 and shifted_rows / len(data_rows) > 0.35:
        raise ExtractionQualityError("数值集中在首列，疑似网格列错位。")

    header_hits = [
        term
        for term in (config.get("header_terms") or TABLE_HEADERS.get(table_id, ()))
        if _compact(term) in flat
    ]
    score = min(
        99.0, 65 + len(required_hits) * 8 + len(header_hits) * 3 + min(numeric_rows, 15) * 0.6
    )
    evidence_parts = [*required_hits, *header_hits]
    if source_item_labels:
        evidence_parts.append(f"项目行召回率{item_recall:.0%}")
    if row_number_table:
        evidence_parts.append("首列已识别为行次，不参与数值错位判断")
    evidence = "、".join(dict.fromkeys(evidence_parts))
    return round(score, 2), evidence or "跨页结构与数值列校验通过"


def _reconstruct(
    *,
    table_id: str,
    table_name: str,
    pages: list[int],
    mode: str,
    messages: list[dict],
    api_key: str,
    base_url: str,
    model: str,
    timeout: int,
    post_func: Callable | None,
    source_grids: list[PageGrid] | None = None,
    table_config: Mapping[str, object] | None = None,
) -> ExtractedTable:
    config = dict(table_config or {})
    strategy = active_table_strategy(table_id)
    payload = _parse_json(
        _call_model(
            api_key=api_key,
            base_url=base_url,
            model=model,
            messages=messages,
            timeout=timeout,
            post_func=post_func,
        )
    )
    rows, ragged_ratio, notes = _coerce_table(payload)
    rows, postprocess_notes = strategy.postprocess(
        PostprocessRequest(
            table_id=table_id,
            rows=rows,
            source_grids=source_grids,
            normalize_three_year=normalize_three_year_return_rows_core,
            recover_minimum_capital=recover_minimum_capital_terminal_rows_core,
            trim_adjacent=lambda target_id, target_rows: _trim_adjacent_rows(
                target_id,
                target_rows,
                config,
            ),
            table_config=config,
        )
    )
    if any("归一化" in note for note in postprocess_notes):
        ragged_ratio = 0.0
    try:
        rows, item_boundary_note = strategy.enforce_boundaries(
            BoundaryRequest(
                mode="output_rows",
                table_id=table_id,
                rows=rows,
                require_complete=True,
                engine=enforce_output_boundaries,
                table_config=config,
            )
        )
    except TableBoundaryError as exc:
        raise ExtractionQualityError(str(exc)) from exc
    boundary_note = "；".join(
        item
        for item in (
            *postprocess_notes,
            item_boundary_note,
        )
        if item
    )
    expected_source_rows, source_required_terms = _source_completeness_profile(
        table_id, source_grids, config
    )
    configured_completeness = tuple(config.get("completeness_terms", ()))
    if configured_completeness:
        source_required_terms = configured_completeness
    source_item_labels = _source_item_labels(table_id, source_grids, config)
    source_section_titles = _source_section_titles(table_id, source_grids, config)
    score, evidence = strategy.validate_completeness(
        CompletenessRequest(
            mode="full_table",
            table_id=table_id,
            rows=rows,
            ragged_ratio=ragged_ratio,
            signatures=tuple(config.get("content_terms") or TABLE_SIGNATURES.get(table_id, ())),
            validator=_validate,
            validator_kwargs={
                "expected_source_rows": expected_source_rows,
                "source_required_terms": source_required_terms,
                "source_item_labels": source_item_labels,
                "source_section_titles": source_section_titles,
                "configured_required": tuple(config.get("content_terms", ())),
                "configured_exclusions": tuple(
                    dict.fromkeys(
                        [
                            *config.get("exclude_item_terms", ()),
                            *config.get("profile_exclude_items", ()),
                            *config.get("stop_terms", ()),
                        ]
                    )
                ),
                "configured_minimum_rows": int(config.get("minimum_rows", 0) or 0),
            },
            table_config=config,
        )
    )
    if boundary_note:
        evidence = f"{evidence}；{boundary_note}"
    if notes:
        evidence = f"{evidence}；{notes}"
    payload_units = unit_records_from_payload(
        payload,
        rows,
        pages,
        source_type="\u8868\u683c\u7ed3\u6784\u5316\u540c\u6b65\u5355\u4f4d",
    )
    return ExtractedTable(
        table_id=table_id,
        table_name=table_name,
        page=pages[0],
        table_index=1,
        rows=rows,
        strategy=mode,
        quality_score=score,
        evidence=evidence,
        source_pages=pages,
        unit_records=merge_unit_records(
            extract_unit_records(table_id, rows, source_grids),
            payload_units,
        ),
    )


def extract_tables_with_llm(
    pdf_bytes: bytes,
    matches: list[PageMatch],
    *,
    api_key: str,
    base_url: str,
    model: str,
    auto_vision_retry: bool = True,
    force_vision: bool = False,
    timeout: int = 180,
    post_func: Callable | None = None,
    progress_callback: Callable[[ExtractionLog], None] | None = None,
    _grid_builder: Callable[[bytes, list[int]], list[PageGrid]] | None = None,
    _render_func: Callable[..., list[tuple[int, str]]] | None = None,
) -> AIExtractionBundle:
    if not pdf_bytes:
        raise ValueError("请先上传PDF。")
    if not matches:
        raise ValueError("请先完成STEP1页码定位。")
    if not api_key.strip() or not base_url.strip() or not model.strip():
        raise ValueError("请完整填写模型接口地址、模型名称和API Key。")

    tables: list[ExtractedTable] = []
    logs: list[ExtractionLog] = []
    build_grids = _grid_builder or build_pdf_grids
    render_images = _render_func or _render_images

    def add_log(log: ExtractionLog) -> None:
        logs.append(log)
        if progress_callback:
            progress_callback(log)

    for table_id, table_name, pages, strategy_id, table_config in _group_matches(matches):
        table_strategy = resolve_table_strategy(
            table_id,
            strategy_id or None,
            table_config,
        )
        grids: list[PageGrid] = []
        text_error = ""
        if not force_vision:
            try:
                grids = build_grids(pdf_bytes, pages)
                if sum(item.word_count for item in grids) < max(12, len(pages) * 6):
                    raise ExtractionQualityError("页面可检索文字不足，判断为扫描件或文字层异常。")
                table = table_strategy.run(
                    "reconstruct_table",
                    _reconstruct,
                    table_name=table_name,
                    pages=pages,
                    mode=TEXT_MODE,
                    messages=_grid_messages(
                        table_id,
                        table_name,
                        pages,
                        grids,
                        table_config,
                    ),
                    source_grids=grids,
                    table_config=table_config,
                    api_key=api_key,
                    base_url=base_url,
                    model=model,
                    timeout=timeout,
                    post_func=post_func,
                )
                tables.append(table)
                add_log(
                    ExtractionLog(
                        table_id,
                        table_name,
                        pages,
                        TEXT_MODE,
                        "成功",
                        f"已完成多页网格重构和自动拼接，质量评分 {table.quality_score:.1f}。",
                    )
                )
                continue
            except Exception as exc:
                text_error = str(exc)
                add_log(
                    ExtractionLog(
                        table_id,
                        table_name,
                        pages,
                        TEXT_MODE,
                        "触发图片重试" if auto_vision_retry else "失败",
                        text_error,
                    )
                )

        if not (force_vision or auto_vision_retry):
            continue

        try:
            images = render_images(pdf_bytes, pages)
            table = table_strategy.run(
                "reconstruct_table",
                _reconstruct,
                table_name=table_name,
                pages=pages,
                mode=VISION_MODE,
                messages=_vision_messages(
                    table_id,
                    table_name,
                    pages,
                    images,
                    table_config,
                ),
                source_grids=grids,
                table_config=table_config,
                api_key=api_key,
                base_url=base_url,
                model=model,
                timeout=timeout,
                post_func=post_func,
            )
            unit_note = ""
            if unit_recovery_needed(table.rows, table.unit_records):
                try:
                    recovered_units = recover_unit_records_from_images(
                        table_id=table_id,
                        table_name=table_name,
                        pages=pages,
                        rows=table.rows,
                        images=images,
                        api_key=api_key,
                        base_url=base_url,
                        model=model,
                        timeout=timeout,
                        post_func=post_func,
                    )
                    table.unit_records = merge_unit_records(
                        table.unit_records,
                        recovered_units,
                    )
                    if recovered_units:
                        unit_note = f"；单位专用补提取获得{len(recovered_units)}条明确单位"
                    else:
                        unit_note = "；单位专用补提取未发现明确单位，已保留待核对状态"
                except Exception as unit_exc:
                    unit_note = f"；单位专用补提取失败，已保留待核对状态：{unit_exc}"
            tables.append(table)
            add_log(
                ExtractionLog(
                    table_id,
                    table_name,
                    pages,
                    VISION_MODE,
                    "成功",
                    f"图片扫描重试成功，质量评分 {table.quality_score:.1f}{unit_note}。",
                )
            )
            continue
        except Exception as vision_exc:
            vision_error = str(vision_exc)
            vision_unsupported = _is_unsupported_vision_error(vision_exc)
            if vision_unsupported:
                add_log(
                    ExtractionLog(
                        table_id,
                        table_name,
                        pages,
                        VISION_MODE,
                        "接口不支持图片",
                        "当前模型接口仅接受文本消息，已自动改用加强版网格纠错重试。",
                    )
                )
            else:
                prefix = f"网格模式失败：{text_error}；" if text_error else ""
                add_log(
                    ExtractionLog(
                        table_id,
                        table_name,
                        pages,
                        VISION_MODE,
                        "失败，转3倍高清图片",
                        f"{prefix}图片扫描重试未通过：{vision_error}",
                    )
                )

            if not vision_unsupported:
                try:
                    high_res_images = render_images(
                        pdf_bytes,
                        pages,
                        zoom=3.0,
                        jpeg_quality=92,
                    )
                    table = table_strategy.run(
                        "reconstruct_table",
                        _reconstruct,
                        table_name=table_name,
                        pages=pages,
                        mode=VISION_HIGH_RES_MODE,
                        messages=_vision_messages(
                            table_id,
                            table_name,
                            pages,
                            high_res_images,
                            table_config,
                        ),
                        source_grids=grids,
                        table_config=table_config,
                        api_key=api_key,
                        base_url=base_url,
                        model=model,
                        timeout=timeout,
                        post_func=post_func,
                    )
                    tables.append(table)
                    add_log(
                        ExtractionLog(
                            table_id,
                            table_name,
                            pages,
                            VISION_HIGH_RES_MODE,
                            "成功",
                            f"3倍高清图片重试成功，质量评分{table.quality_score:.1f}。",
                        )
                    )
                    continue
                except Exception as high_res_exc:
                    vision_error = f"{vision_error}；3倍高清图片重试未通过：{high_res_exc}"
                    add_log(
                        ExtractionLog(
                            table_id,
                            table_name,
                            pages,
                            VISION_HIGH_RES_MODE,
                            "失败，转网格纠错",
                            str(high_res_exc),
                        )
                    )

            if not grids:
                try:
                    grids = build_grids(pdf_bytes, pages)
                except Exception:
                    grids = []
            searchable_words = sum(item.word_count for item in grids)
            if searchable_words < max(12, len(pages) * 6):
                add_log(
                    ExtractionLog(
                        table_id,
                        table_name,
                        pages,
                        TEXT_RETRY_MODE,
                        "失败",
                        "PDF没有足够文字层，图片重试也未成功。请改用支持图片的视觉模型，或人工核对页码后重试。",
                    )
                )
                continue

            try:
                repair_reason = "；".join(item for item in (text_error, vision_error) if item)
                table = table_strategy.run(
                    "reconstruct_table",
                    _reconstruct,
                    table_name=table_name,
                    pages=pages,
                    mode=TEXT_RETRY_MODE,
                    messages=_grid_repair_messages(
                        table_id,
                        table_name,
                        pages,
                        grids,
                        repair_reason,
                        table_config,
                    ),
                    source_grids=grids,
                    table_config=table_config,
                    api_key=api_key,
                    base_url=base_url,
                    model=model,
                    timeout=timeout,
                    post_func=post_func,
                )
                tables.append(table)
                add_log(
                    ExtractionLog(
                        table_id,
                        table_name,
                        pages,
                        TEXT_RETRY_MODE,
                        "成功",
                        f"加强版网格纠错成功，质量评分 {table.quality_score:.1f}。",
                    )
                )
            except Exception as repair_exc:
                add_log(
                    ExtractionLog(
                        table_id,
                        table_name,
                        pages,
                        TEXT_RETRY_MODE,
                        "失败",
                        f"图片重试和网格纠错均未通过完整性检查：{repair_exc}",
                    )
                )
    return AIExtractionBundle(tables=tables, logs=logs)


def extraction_logs_frame(logs: list[ExtractionLog]) -> pd.DataFrame:
    columns = ["目标表", "物理页码", "识别方式", "状态", "说明"]
    return pd.DataFrame([item.to_dict() for item in logs], columns=columns)
