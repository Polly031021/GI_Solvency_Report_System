"""Shared multi-PDF workflow for STEP1-STEP4.

The Streamlit pages keep their single-file editors, while this module provides
pure helpers for the batch workspace: page extraction, assembly of per-target
intermediate tables, standardization, and a one-sheet merged workbook.
"""

from __future__ import annotations

import io
import re
import traceback
import dataclasses
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import fitz
import pandas as pd

from .batch_extraction import (
    FlexibleTable,
    detect_table_unit,
    sanitize_tree,
    sniff_unit_from_text,
)
from .registered_capital_extractor import append_registered_capital
from .solvency_normalizer import STANDARD_COLUMNS
from .solvency_pdf_locator import PageMatch, extract_report_metadata
from .solvency_rule_table_extractor import (
    ENGINE_LABELS,
    MODE_RULE,
    MODE_VISION,
    extract_page_with_method,
    page_source_text,
)
from .solvency_step3_standardizer import (
    _target_summary,
    result_workbook_bytes,
    standardize_to_target,
)
from .solvency_table_extractor import TABLE_SIGNATURES, header_compare_text


@dataclass
class BatchExtractionOutcome:
    """Result of extracting all confirmed target pages from one PDF."""

    tables: list[FlexibleTable] = field(default_factory=list)
    logs: list[dict[str, Any]] = field(default_factory=list)
    status: str = ""
    note: str = ""
    traceback: str = ""


def upload_signature(uploaded: Any) -> tuple[str, int | None, str]:
    """Stable identity used to avoid re-reading unchanged uploads every rerun."""
    return (
        str(getattr(uploaded, "name", "")),
        int(getattr(uploaded, "size", 0) or 0),
        str(getattr(uploaded, "file_id", "") or ""),
    )


def document_pdf_bytes(document: Mapping[str, Any]) -> bytes:
    """Read an uploaded PDF once and keep it in the shared batch document."""
    existing = document.get("pdf_bytes")
    if isinstance(existing, (bytes, bytearray)) and existing:
        return bytes(existing)
    uploaded = document.get("uploaded_file")
    data = uploaded.getvalue() if uploaded is not None else b""
    data = bytes(data or b"")
    if isinstance(document, dict):
        document["pdf_bytes"] = data
    return data


def ensure_document_metadata(document: dict[str, Any]) -> dict[str, Any]:
    """Parse page count and report identity only when a file is actually used."""
    metadata = document.get("metadata")
    if document.get("metadata_loaded") and isinstance(metadata, dict):
        return document
    pdf_bytes = document_pdf_bytes(document)
    if not pdf_bytes:
        return document
    document["metadata"] = extract_report_metadata(pdf_bytes)
    document["metadata_loaded"] = True
    return document


def _table_terms(match: PageMatch) -> tuple[str, ...]:
    config = (
        dict(match.table_config)
        if isinstance(getattr(match, "table_config", None), dict)
        else {}
    )
    terms: list[str] = []
    for key in ("title_terms", "content_terms", "completeness_terms"):
        values = config.get(key) or ()
        if isinstance(values, str):
            values = re.split(r"[|\n\r]+", values)
        terms.extend(
            str(value).strip()
            for value in values
            if str(value).strip()
        )
    terms.extend(TABLE_SIGNATURES.get(str(match.table_id), ()))
    terms.append(str(match.table_name or "").strip())
    return tuple(dict.fromkeys(term for term in terms if term))


def _flat_rows(rows: Iterable[Iterable[Any]]) -> str:
    return re.sub(
        r"\s+",
        "",
        "".join(str(cell or "") for row in rows for cell in row),
    )


def _select_target_frames(
    frames: Sequence[Sequence[Sequence[Any]]],
    terms: Sequence[str],
    table_id: str,
) -> list[list[list[str]]]:
    normalized = [
        [
            [str(cell).strip() for cell in row]
            for row in frame_rows
        ]
        for frame_rows in frames
        if frame_rows
    ]
    if len(normalized) <= 1 or not terms:
        return normalized

    exact: list[list[list[str]]] = []
    matched: list[list[list[str]]] = []
    signature_terms = [
        term.replace(" ", "")
        for term in TABLE_SIGNATURES.get(str(table_id), ())
    ]
    normalized_terms = [term.replace(" ", "") for term in terms]
    for rows in normalized:
        text = _flat_rows(rows)
        if signature_terms and all(term in text for term in signature_terms):
            exact.append(rows)
        elif (
            len(normalized_terms) >= 2
            and all(term in text for term in normalized_terms[:2])
        ):
            exact.append(rows)
        elif any(term in text for term in normalized_terms):
            matched.append(rows)
    return exact or matched or normalized


def _rows_have_metric_header(rows: Sequence[Sequence[str]]) -> bool:
    terms = (
        "指标名称",
        "项目",
        "行次",
        "本季度数",
        "本年累计数",
        "本季度末数",
        "上季度末数",
    )
    for row in rows[:3]:
        text = header_compare_text("".join(str(cell) for cell in row))
        if sum(1 for term in terms if header_compare_text(term) in text) >= 2:
            return True
    return False


def _assemble_tables(
    *,
    matches: Sequence[PageMatch],
    page_frames: Mapping[int, list[list[list[str]]]],
    page_engines: Mapping[int, str],
    page_units: Mapping[int, str],
) -> list[FlexibleTable]:
    tables: list[FlexibleTable] = []
    for match in matches:
        pages = [int(page) for page in (match.pages or [])]
        blocks: list[dict[str, Any]] = []
        rows: list[list[str]] = []
        width = 0
        for page in pages:
            frames = page_frames.get(page) or []
            selected = _select_target_frames(
                frames,
                _table_terms(match),
                str(match.table_id),
            )
            for frame_rows in selected:
                if not frame_rows:
                    continue
                block_width = max(len(row) for row in frame_rows)
                width = max(width, block_width)
                if rows and block_width:
                    rows.append([f"【第{page}页表格分块】"] + [""] * (block_width - 1))
                rows.extend(frame_rows)
                blocks.append({"page": page, "rows": frame_rows})
        if not rows:
            continue
        canonical_headers = [
            str(item).strip()
            for item in ((match.table_config or {}).get("canonical_headers") or ())
            if str(item).strip()
        ]
        if canonical_headers and not _rows_have_metric_header(rows):
            width = max(len(row) for row in rows)
            header = [
                canonical_headers[index]
                if index < len(canonical_headers)
                else ""
                for index in range(width)
            ]
            rows = [header, *rows]

        unit = detect_table_unit(rows)
        if not unit:
            unit = next(
                (
                    str(page_units.get(page, "")).strip()
                    for page in pages
                    if str(page_units.get(page, "")).strip()
                ),
                "",
            )
        if not unit:
            unit = "未识别"

        frame = pd.DataFrame(
            [
                [str(cell).strip() for cell in row]
                + [""] * (width - len(row))
                for row in rows
            ]
        )
        engines = [
            ENGINE_LABELS.get(str(page_engines.get(page, "")), str(page_engines.get(page, "")))
            for page in pages
            if page_engines.get(page)
        ]
        strategy = "批量·" + "+".join(dict.fromkeys(engine for engine in engines if engine))
        if strategy == "批量·":
            strategy = "批量"

        def to_frame(
            *_args,
            _frame: pd.DataFrame = frame,
            **_kwargs,
        ) -> pd.DataFrame:
            return _frame.copy()

        table_config = (
            sanitize_tree(match.table_config)
            if isinstance(match.table_config, dict)
            else None
        )
        if isinstance(table_config, dict) and canonical_headers:
            # sanitize_tree turns every list into a delimited string; restore
            # the header list so downstream table-block expansion can use it.
            table_config = dict(table_config)
            table_config["canonical_headers"] = list(canonical_headers)
        tables.append(
            FlexibleTable(
                table_id=match.table_id,
                table_name=match.table_name,
                page=pages[0] if pages else 1,
                source_pages=pages,
                rows=rows,
                to_frame=to_frame,
                strategy=strategy,
                quality_score=1.0,
                evidence="",
                table_config=table_config,
                unit=unit,
                unit_source=unit,
                source_grid_raw=[list(row) for row in rows],
                table_blocks=blocks,
            )
        )
    return tables


def extract_document_tables(
    *,
    company: str,
    pdf_bytes: bytes,
    matches: Sequence[PageMatch],
    method: str = MODE_RULE,
    allow_llm_fallback: bool = False,
    vision_for_scanned_pages: bool = True,
    whole_page: bool = True,
    api_key: str = "",
    base_url: str = "",
    model: str = "",
    max_workers: int = 4,
) -> BatchExtractionOutcome:
    """Extract confirmed pages for one PDF without touching Streamlit state."""
    outcome = BatchExtractionOutcome()
    located = [match for match in matches if match.pages]
    if not located:
        outcome.status = "失败"
        outcome.note = "没有已确认页码的目标表。"
        return outcome

    page_to_matches: dict[int, list[PageMatch]] = {}
    for match in located:
        for page in match.pages:
            page_to_matches.setdefault(int(page), []).append(match)

    tasks: list[tuple[int, PageMatch]] = []
    if whole_page:
        tasks = [
            (page, page_matches[0])
            for page, page_matches in sorted(page_to_matches.items())
        ]
    else:
        tasks = [
            (int(page), match)
            for match in located
            for page in match.pages
        ]

    def run_task(page: int, match: PageMatch):
        page_text = page_source_text(pdf_bytes, page)
        has_text = len(re.sub(r"\s+", "", page_text or "")) >= 20
        effective_method = method
        auto_vision = (
            not has_text
            and vision_for_scanned_pages
            and method != MODE_VISION
            and all(
                str(value or "").strip()
                for value in (api_key, base_url, model)
            )
        )
        if auto_vision:
            effective_method = MODE_VISION
        result = extract_page_with_method(
            pdf_bytes=pdf_bytes,
            page_number=page,
            table_id=str(match.table_id),
            table_name=str(match.table_name),
            method=effective_method,
            has_text=has_text,
            table_config=(
                match.table_config
                if isinstance(match.table_config, dict)
                else {}
            ),
            strategy_id=getattr(match, "strategy_id", None),
            api_key=api_key,
            base_url=base_url,
            model=model,
            allow_llm_fallback=allow_llm_fallback,
            whole_page=whole_page,
        )
        if auto_vision and result.succeeded:
            result.note = f"扫描页自动转视觉大模型；{result.note}"
        return page, match, result

    page_frames: dict[int, list[list[list[str]]]] = {}
    page_engines: dict[int, str] = {}
    page_units: dict[int, str] = {}
    try:
        with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as pool:
            futures = {
                pool.submit(run_task, page, match): (page, match)
                for page, match in tasks
            }
            for future in as_completed(futures):
                page, match, result = future.result()
                label = str(match.table_name) if not whole_page else "整页全表"
                if result.succeeded:
                    frames = [
                        frame.fillna("").astype(str).values.tolist()
                        for frame in (result.frames or (result.frame,))
                        if frame is not None and not frame.empty
                    ]
                    page_frames[page] = frames
                    page_engines[page] = result.engine
                    page_units[page] = (
                        sniff_unit_from_text(page_source_text(pdf_bytes, page))
                        or ""
                    )
                    status = "成功"
                else:
                    status = "失败"
                outcome.logs.append(
                    {
                        "公司": company,
                        "目标表": label,
                        "物理页码": page,
                        "识别方式": ENGINE_LABELS.get(result.engine, result.engine or "未识别"),
                        "状态": status,
                        "说明": str(result.note or "")[:500],
                    }
                )
    except Exception as exc:
        outcome.status = "异常"
        outcome.note = str(exc)[:300]
        outcome.traceback = traceback.format_exc()
        return outcome

    outcome.tables = _assemble_tables(
        matches=located,
        page_frames=page_frames,
        page_engines=page_engines,
        page_units=page_units,
    )
    failed_pages = [log for log in outcome.logs if log["状态"] == "失败"]
    if not outcome.tables:
        outcome.status = "失败"
        outcome.note = "未提取到任何目标表。"
    elif failed_pages:
        outcome.status = "部分成功"
        outcome.note = f"提取 {len(outcome.tables)} 张表；{len(failed_pages)} 页失败。"
    else:
        outcome.status = "成功"
        outcome.note = f"提取 {len(outcome.tables)} 张表，共 {len(page_frames)} 页。"
    return outcome


def _full_pdf_text(pdf_bytes: bytes) -> str:
    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            return "\n".join(page.get_text() or "" for page in document)
        finally:
            document.close()
    except Exception:
        return ""


def standardize_document_tables(
    *,
    company: str,
    pdf_name: str,
    pdf_bytes: bytes,
    tables: Sequence[FlexibleTable],
    context: Any,
    period_overrides: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Run STEP3 for one PDF and return its data, workbook and diagnostics."""
    try:
        metadata = sanitize_tree(
            {
                **extract_report_metadata(pdf_bytes),
                "来源文件": pdf_name,
                "公司": company,
                "first_page_text": _full_pdf_text(pdf_bytes),
                "导入批次": f"{company}:batch",
            }
        )
        company_type = context.company_type_map.get(
            company,
            context.default_company_type,
        )
        result = standardize_to_target(
            list(tables),
            context.taxonomy,
            metadata,
            company_type,
            context.target_catalog,
            report_profile_id=context.profile.profile_id,
            allowed_company_types=context.profile.company_types,
            peer_group_map=context.peer_group_map,
            default_peer_group=context.default_peer_group,
            company_type_map=context.company_type_map,
            include_derived=context.include_derived,
            period_overrides=period_overrides,
        )
        report_period = str(metadata.get("报告期", "")).strip() or "2025Q4"
        data = append_registered_capital(
            result.data,
            pdf_text=str(metadata.get("first_page_text", "")),
            pdf_bytes=pdf_bytes,
            pdf_name=pdf_name,
            company_name=company,
            report_period=report_period,
            company_type=company_type,
        )
        summary = _target_summary(
            result.target_catalog,
            data,
            result.diagnostics,
        )
        result = dataclasses.replace(result, data=data, target_summary=summary)
        return {
            "状态": "成功" if not data.empty else "失败",
            "data": data,
            "summary": summary,
            "diagnostics": result.diagnostics,
            "warnings": list(result.warnings),
            "workbook_bytes": result_workbook_bytes(result),
            "metadata": metadata,
            "说明": f"标准窄表 {len(data)} 条。",
        }
    except Exception as exc:
        return {
            "状态": "异常",
            "data": pd.DataFrame(columns=STANDARD_COLUMNS),
            "summary": pd.DataFrame(),
            "diagnostics": pd.DataFrame(),
            "warnings": [],
            "workbook_bytes": b"",
            "metadata": {},
            "说明": str(exc)[:300],
            "traceback": traceback.format_exc(),
        }


def merge_standard_frames(frames: Iterable[pd.DataFrame]) -> pd.DataFrame:
    valid = [
        frame
        for frame in frames
        if isinstance(frame, pd.DataFrame) and not frame.empty
    ]
    if not valid:
        return pd.DataFrame(columns=STANDARD_COLUMNS)
    merged = pd.concat(valid, ignore_index=True)
    for column in STANDARD_COLUMNS:
        if column not in merged.columns:
            merged[column] = ""
    return merged.reindex(columns=STANDARD_COLUMNS)


def single_sheet_workbook_bytes(
    frame: pd.DataFrame,
    *,
    sheet_name: str = "合并指标",
) -> bytes:
    """Write all companies/metrics/provenance into exactly one worksheet."""
    output = io.BytesIO()
    data = (
        frame.copy()
        if isinstance(frame, pd.DataFrame)
        else pd.DataFrame(columns=STANDARD_COLUMNS)
    )
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        data.to_excel(writer, sheet_name=sheet_name, index=False)
        sheet = writer.book[sheet_name]
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for column_cells in sheet.iter_cols():
            values = [str(cell.value or "") for cell in column_cells[:200]]
            width = min(max(max((len(value) for value in values), default=0) + 2, 10), 36)
            sheet.column_dimensions[column_cells[0].column_letter].width = width
    return output.getvalue()


def reset_document_after_pages(document: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy of a batch document with downstream results cleared."""
    updated = dict(document)
    updated.update(
        {
            "raw_tables": [],
            "extraction_logs": [],
            "extraction_status": "",
            "extraction_note": "",
            "standard_data": pd.DataFrame(columns=STANDARD_COLUMNS),
            "standard_summary": pd.DataFrame(),
            "standard_diagnostics": pd.DataFrame(),
            "standard_workbook_bytes": b"",
            "standard_status": "",
            "standard_note": "",
        }
    )
    return updated


def sync_documents_from_uploads(
    existing: Mapping[str, dict[str, Any]],
    uploads: Sequence[Any],
    *,
    profile_id: str,
) -> dict[str, dict[str, Any]]:
    """Keep batch results while uploaded files remain in the uploader."""
    updated = dict(existing)
    retained: set[str] = set()
    for uploaded in uploads:
        name = str(getattr(uploaded, "name", "report.pdf"))
        company = Path(name).stem
        retained.add(company)
        previous = updated.get(company)
        signature = upload_signature(uploaded)
        if (
            previous
            and previous.get("profile_id") == profile_id
            and previous.get("upload_signature") == signature
        ):
            previous["uploaded_file"] = uploaded
            continue
        updated[company] = {
            "company": company,
            "pdf_name": name,
            "uploaded_file": uploaded,
            "upload_signature": signature,
            "pdf_bytes": None,
            "profile_id": profile_id,
            "metadata": {},
            "metadata_loaded": False,
            "matches": [],
            "edited_pages": {},
            "pages_confirmed": False,
            "locate_status": "",
            "locate_note": "",
            "raw_tables": [],
            "extraction_logs": [],
            "extraction_status": "",
            "extraction_note": "",
            "standard_data": pd.DataFrame(columns=STANDARD_COLUMNS),
            "standard_summary": pd.DataFrame(),
            "standard_diagnostics": pd.DataFrame(),
            "standard_workbook_bytes": b"",
            "standard_status": "",
            "standard_note": "",
        }
    return {
        company: document
        for company, document in updated.items()
        if company in retained
    }
