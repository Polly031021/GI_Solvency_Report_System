"""STEP1 页码定位与 STEP2 逐页提取的混合管线。

config 配置、llm 模型调用、locator_vision 视觉定位、locator_rule 规则定位、
locator 定位入口、page_extract 逐页提取与跨页合并、extract 提取入口；
本模块重新导出全部符号，保持原导入路径可用。
"""

from __future__ import annotations

from .config import (
    PAGE_TEXT_MODE,
    PAGE_VISION_MODE,
    PAGE_HIGH_RES_VISION_MODE,
    PAGE_RETRY_MODE,
    CROSS_PAGE_MERGE_MODE,
    FULL_TABLE_RECONSTRUCTION_MODE,
    LOCATOR_VISION_BATCH_SIZE,
    LOW_TEXT_MIN_CHARACTERS,
    TABLE_EXTRACTION_MAX_WORKERS,
)

from .llm import (
    _completion_url,
    _response_error,
    _call_chat,
    _parse_json_object,
)

from .locator_vision import (
    _physical_pages,
    _low_text_page_numbers,
    _render_locator_images,
    _profile_prompt_values,
    _profile_instruction_block,
    _vision_locator_messages,
    _vision_directory_messages,
    _locate_scan_directory_with_vision,
    _batched,
    _run_vision_locator_stage,
    _locate_low_text_pages_with_vision,
)

from .locator_rule import (
    _radar_content_anchor,
    _solvency_radar,
    _merge_locator_pages,
    _locator_conflict,
    _locator_prompt,
    _confidence_from_sources,
)

from .locator import (
    locate_tables_hybrid,
)

from .page_extract import (
    _pipe_prompt,
    _text_page_messages,
    _vision_page_messages,
    _parse_pipe_rows_with_shape,
    _row_text,
    _numeric_output_rows,
    _trim_page_rows,
    _prepare_page_rows,
    _validate_single_page_core,
    _validate_single_page,
    _header_score,
    _is_same_header,
    _ROW_NUMBER_CELL_RE,
    _strip_orphan_row_number_column,
    _meaningful_width,
    _value_column_profile,
    _validate_cross_page_columns,
    _merge_page_rows,
    _extract_one_page,
)

from .extract import (
    extract_tables_hybrid,
)

__all__ = [
    "CROSS_PAGE_MERGE_MODE",
    "FULL_TABLE_RECONSTRUCTION_MODE",
    "LOCATOR_VISION_BATCH_SIZE",
    "LOW_TEXT_MIN_CHARACTERS",
    "PAGE_HIGH_RES_VISION_MODE",
    "PAGE_RETRY_MODE",
    "PAGE_TEXT_MODE",
    "PAGE_VISION_MODE",
    "TABLE_EXTRACTION_MAX_WORKERS",
    "_ROW_NUMBER_CELL_RE",
    "_batched",
    "_call_chat",
    "_completion_url",
    "_confidence_from_sources",
    "_extract_one_page",
    "_header_score",
    "_is_same_header",
    "_locate_low_text_pages_with_vision",
    "_locate_scan_directory_with_vision",
    "_locator_conflict",
    "_locator_prompt",
    "_low_text_page_numbers",
    "_meaningful_width",
    "_merge_locator_pages",
    "_merge_page_rows",
    "_numeric_output_rows",
    "_parse_json_object",
    "_parse_pipe_rows_with_shape",
    "_physical_pages",
    "_pipe_prompt",
    "_prepare_page_rows",
    "_profile_instruction_block",
    "_profile_prompt_values",
    "_radar_content_anchor",
    "_render_locator_images",
    "_response_error",
    "_row_text",
    "_run_vision_locator_stage",
    "_solvency_radar",
    "_strip_orphan_row_number_column",
    "_text_page_messages",
    "_trim_page_rows",
    "_validate_cross_page_columns",
    "_validate_single_page",
    "_validate_single_page_core",
    "_value_column_profile",
    "_vision_directory_messages",
    "_vision_locator_messages",
    "_vision_page_messages",
    "extract_tables_hybrid",
    "locate_tables_hybrid",
]
