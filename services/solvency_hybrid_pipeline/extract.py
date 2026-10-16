"""STEP2 提取主入口 extract_tables_hybrid（并发逐页 + 确定性合并）。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from typing import Callable

from ..solvency_ai_table_extractor import (
    AIExtractionBundle,
    ExtractionLog,
    ExtractionQualityError,
    extract_tables_with_llm,
    PageGrid,
    _render_images,
    _source_completeness_profile,
    _source_item_labels,
    _source_section_titles,
    _validate,
    build_pdf_grids,
    extract_unit_records,
    merge_unit_records,
)
from ..solvency_table_boundaries import TableBoundaryError, enforce_output_boundaries
from ..solvency_pdf_locator import PageMatch
from ..solvency_table_extractor import TABLE_SIGNATURES, ExtractedTable, UnitRecord
from ..table_strategy_handlers import BoundaryRequest, CompletenessRequest
from ..table_strategy_registry import resolve_table_strategy

from .config import (
    CROSS_PAGE_MERGE_MODE,
    FULL_TABLE_RECONSTRUCTION_MODE,
    PAGE_TEXT_MODE,
    TABLE_EXTRACTION_MAX_WORKERS,
)
from .page_extract import _extract_one_page, _merge_page_rows


def extract_tables_hybrid(
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
    _parallel_tables: bool = True,
    _shared_grid_cache: dict[int, PageGrid] | None = None,
    _shared_image_cache: dict[tuple[int, float, int], tuple[int, str]] | None = None,
    _cache_lock: Lock | None = None,
    _page_worker_limit: int | None = None,
) -> AIExtractionBundle:
    """Per-page structured extraction followed by deterministic cross-page merge."""
    if not pdf_bytes:
        raise ValueError("请先上传PDF。")
    if not matches:
        raise ValueError("请先完成STEP1页码定位。")
    if not api_key.strip() or not base_url.strip() or not model.strip():
        raise ValueError("请完整填写模型接口地址、模型名称和API Key。")

    logs: list[ExtractionLog] = []
    tables: list[ExtractedTable] = []
    grid_cache = _shared_grid_cache if _shared_grid_cache is not None else {}
    image_cache = _shared_image_cache if _shared_image_cache is not None else {}
    cache_lock = _cache_lock or Lock()

    def add_log(log: ExtractionLog) -> None:
        logs.append(log)
        if progress_callback:
            progress_callback(log)

    def cached_grids(pages: list[int]) -> list[PageGrid]:
        ordered_pages = sorted(set(pages))
        with cache_lock:
            missing = [page for page in ordered_pages if page not in grid_cache]
            if missing:
                for grid in build_pdf_grids(pdf_bytes, missing):
                    grid_cache.setdefault(grid.page_number, grid)
            return [grid_cache[page] for page in ordered_pages]

    def cached_render_images(
        source_pdf_bytes: bytes,
        pages: list[int],
        *,
        zoom: float = 1.8,
        jpeg_quality: int = 84,
    ) -> list[tuple[int, str]]:
        ordered_pages = list(dict.fromkeys(pages))
        keys = [(page, float(zoom), int(jpeg_quality)) for page in ordered_pages]
        with cache_lock:
            missing_pages = [
                page for page, key in zip(ordered_pages, keys) if key not in image_cache
            ]
            if missing_pages:
                rendered = _render_images(
                    source_pdf_bytes,
                    missing_pages,
                    zoom=zoom,
                    jpeg_quality=jpeg_quality,
                )
                for page, image_url in rendered:
                    image_cache.setdefault(
                        (page, float(zoom), int(jpeg_quality)),
                        (page, image_url),
                    )
            return [image_cache[key] for key in keys]

    def cached_grid_builder(
        _source_pdf_bytes: bytes,
        pages: list[int],
    ) -> list[PageGrid]:
        return cached_grids(pages)

    active_matches = [match for match in matches if sorted(set(match.pages))]
    if _parallel_tables and len(active_matches) > 1:
        completed: dict[int, AIExtractionBundle] = {}
        table_workers = min(TABLE_EXTRACTION_MAX_WORKERS, len(active_matches))
        with ThreadPoolExecutor(max_workers=table_workers) as executor:
            future_map = {
                executor.submit(
                    extract_tables_hybrid,
                    pdf_bytes,
                    [match],
                    api_key=api_key,
                    base_url=base_url,
                    model=model,
                    auto_vision_retry=auto_vision_retry,
                    force_vision=force_vision,
                    timeout=timeout,
                    post_func=post_func,
                    progress_callback=None,
                    _parallel_tables=False,
                    _shared_grid_cache=grid_cache,
                    _shared_image_cache=image_cache,
                    _cache_lock=cache_lock,
                    _page_worker_limit=2,
                ): index
                for index, match in enumerate(active_matches)
            }
            for future in as_completed(future_map):
                index = future_map[future]
                bundle = future.result()
                completed[index] = bundle
                if progress_callback:
                    for log in bundle.logs:
                        progress_callback(log)
        for index in range(len(active_matches)):
            bundle = completed[index]
            tables.extend(bundle.tables)
            logs.extend(bundle.logs)
        return AIExtractionBundle(tables=tables, logs=logs)

    def run_full_table_reconstruction(match: PageMatch, reason: str) -> bool:
        pages = sorted(set(match.pages))
        add_log(
            ExtractionLog(
                match.table_id,
                match.table_name,
                pages,
                FULL_TABLE_RECONSTRUCTION_MODE,
                "启动整表重构",
                reason,
            )
        )
        try:
            fallback = extract_tables_with_llm(
                pdf_bytes,
                [match],
                api_key=api_key,
                base_url=base_url,
                model=model,
                auto_vision_retry=auto_vision_retry,
                force_vision=False,
                timeout=timeout,
                post_func=post_func,
                _grid_builder=cached_grid_builder,
                _render_func=cached_render_images,
            )
        except Exception as exc:
            add_log(
                ExtractionLog(
                    match.table_id,
                    match.table_name,
                    pages,
                    FULL_TABLE_RECONSTRUCTION_MODE,
                    "失败",
                    f"整表结构化重构异常：{exc}",
                )
            )
            return False
        for fallback_log in fallback.logs:
            add_log(fallback_log)
        if not fallback.tables:
            detail = fallback.logs[-1].message if fallback.logs else "未生成可用表格"
            add_log(
                ExtractionLog(
                    match.table_id,
                    match.table_name,
                    pages,
                    FULL_TABLE_RECONSTRUCTION_MODE,
                    "失败",
                    f"整表结构化重构未通过：{detail}",
                )
            )
            return False
        table = fallback.tables[0]
        table.strategy = f"{FULL_TABLE_RECONSTRUCTION_MODE}（{table.strategy}）"
        table.evidence = f"逐页处理未通过后完成整表重构；{table.evidence}"
        tables.append(table)
        add_log(
            ExtractionLog(
                match.table_id,
                match.table_name,
                pages,
                FULL_TABLE_RECONSTRUCTION_MODE,
                "成功",
                f"已从全部物理页联合重构，生成{len(table.rows) - 1}条数据行。",
            )
        )
        return True

    for match in matches:
        pages = sorted(set(match.pages))
        if not pages:
            continue
        table_strategy = resolve_table_strategy(
            match.table_id,
            match.strategy_id or None,
            match.table_config,
        )
        grids = cached_grids(pages)
        grid_by_page = {item.page_number: item for item in grids}
        page_rows: dict[int, list[list[str]]] = {}
        page_modes: dict[int, str] = {}
        page_scores: dict[int, float] = {}
        page_unit_records: dict[int, list[UnitRecord]] = {}
        default_workers = 2 if force_vision else min(5, max(1, len(pages)))
        workers = (
            min(default_workers, max(1, _page_worker_limit))
            if _page_worker_limit is not None
            else default_workers
        )
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {
                executor.submit(
                    table_strategy.run,
                    "extract_page",
                    _extract_one_page,
                    pdf_bytes=pdf_bytes,
                    table_name=match.table_name,
                    page_number=page,
                    grid=grid_by_page[page],
                    api_key=api_key,
                    base_url=base_url,
                    model=model,
                    auto_vision_retry=auto_vision_retry,
                    force_vision=force_vision,
                    timeout=timeout,
                    post_func=post_func,
                    render_func=cached_render_images,
                    table_config=match.table_config,
                ): page
                for page in pages
            }
            for future in as_completed(future_map):
                page = future_map[future]
                try:
                    rows, mode, score, page_logs, units = future.result()
                except Exception as exc:
                    rows, mode, score, units = None, "", 0.0, []
                    page_logs = [
                        ExtractionLog(
                            match.table_id,
                            match.table_name,
                            [page],
                            PAGE_TEXT_MODE,
                            "失败",
                            f"逐页任务异常：{exc}",
                        )
                    ]
                for log in page_logs:
                    add_log(log)
                if rows:
                    page_rows[page] = rows
                    page_modes[page] = mode
                    page_scores[page] = score
                    page_unit_records[page] = units

        missing_pages = [page for page in pages if page not in page_rows]
        if missing_pages:
            reason = f"逐页提取未完整覆盖物理页{missing_pages}，切换至多页联合重构。"
            if run_full_table_reconstruction(match, reason):
                continue
            add_log(
                ExtractionLog(
                    match.table_id,
                    match.table_name,
                    pages,
                    CROSS_PAGE_MERGE_MODE,
                    "失败",
                    f"以下物理页未成功提取，整表重构亦未通过：{missing_pages}",
                )
            )
            continue
        try:
            merged_rows = table_strategy.run(
                "merge_pages",
                _merge_page_rows,
                match.table_name,
                pages,
                page_rows,
                inject_table_id="positional",
            )
            try:
                merged_rows, boundary_note = table_strategy.enforce_boundaries(
                    BoundaryRequest(
                        mode="output_rows",
                        table_id=match.table_id,
                        rows=merged_rows,
                        require_complete=True,
                        engine=enforce_output_boundaries,
                    )
                )
            except TableBoundaryError as boundary_exc:
                raise ExtractionQualityError(str(boundary_exc)) from boundary_exc
            expected_rows, source_terms = _source_completeness_profile(
                match.table_id, grids, match.table_config
            )
            configured_terms = tuple(match.table_config.get("completeness_terms", ()))
            if configured_terms:
                source_terms = configured_terms
            source_labels = _source_item_labels(match.table_id, grids, match.table_config)
            source_sections = _source_section_titles(match.table_id, grids, match.table_config)
            quality, evidence = table_strategy.validate_completeness(
                CompletenessRequest(
                    mode="full_table",
                    table_id=match.table_id,
                    rows=merged_rows,
                    ragged_ratio=0.0,
                    signatures=tuple(
                        match.table_config.get("content_terms")
                        or TABLE_SIGNATURES.get(match.table_id, ())
                    ),
                    validator=_validate,
                    validator_kwargs={
                        "expected_source_rows": expected_rows,
                        "source_required_terms": source_terms,
                        "source_item_labels": source_labels,
                        "source_section_titles": source_sections,
                        "configured_required": tuple(match.table_config.get("content_terms", ())),
                        "configured_exclusions": tuple(
                            dict.fromkeys(
                                [
                                    *match.table_config.get("exclude_item_terms", ()),
                                    *match.table_config.get("profile_exclude_items", ()),
                                    *match.table_config.get("stop_terms", ()),
                                ]
                            )
                        ),
                        "configured_minimum_rows": int(
                            match.table_config.get("minimum_rows", 0) or 0
                        ),
                    },
                )
            )
            modes = [page_modes[page] for page in pages]
            strategy = CROSS_PAGE_MERGE_MODE + "（" + " / ".join(dict.fromkeys(modes)) + "）"
            merged_units = merge_unit_records(
                extract_unit_records(match.table_id, merged_rows, grids),
                *[page_unit_records.get(page, []) for page in pages],
            )
            tables.append(
                ExtractedTable(
                    table_id=match.table_id,
                    table_name=match.table_name,
                    page=pages[0],
                    table_index=1,
                    rows=merged_rows,
                    strategy=strategy,
                    quality_score=min(quality, sum(page_scores.values()) / len(page_scores)),
                    evidence=(
                        f"{evidence}；{boundary_note}；跨页列结构一致性校验通过；"
                        f"逐页提取成功后按物理页码{pages}拼接"
                    ),
                    source_pages=pages,
                    unit_records=merged_units,
                    profile_strategy_id=table_strategy.strategy_id,
                )
            )
            add_log(
                ExtractionLog(
                    match.table_id,
                    match.table_name,
                    pages,
                    CROSS_PAGE_MERGE_MODE,
                    "成功",
                    f"{len(pages)}页均提取成功并按页码拼接；合并后{len(merged_rows)-1}条数据行。",
                )
            )
        except Exception as exc:
            reason = f"逐页拼接结果未通过整表校验，切换至多页联合重构：{exc}"
            if run_full_table_reconstruction(match, reason):
                continue
            add_log(
                ExtractionLog(
                    match.table_id,
                    match.table_name,
                    pages,
                    CROSS_PAGE_MERGE_MODE,
                    "失败",
                    f"跨页拼接及整表重构均未通过：{exc}",
                )
            )
    return AIExtractionBundle(tables=tables, logs=logs)
