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
from .extraction_rules import rule_signature
from .solvency_normalizer import STANDARD_COLUMNS
from .solvency_pdf_locator import (
    PageMatch,
    PdfTextContext,
    build_pdf_text_context,
    extract_report_metadata,
)
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
    missing_tables: list[dict[str, Any]] = field(default_factory=list)
    traces: list[dict[str, str]] = field(default_factory=list)
    resolved_rule: Any = None


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
    text_context = ensure_document_pdf_context(document)
    if text_context is None:
        return document
    document["metadata"] = extract_report_metadata(
        document_pdf_bytes(document),
        text_context=text_context,
    )
    document["metadata_loaded"] = True
    return document


def ensure_document_pdf_context(document: dict[str, Any]) -> PdfTextContext | None:
    """Build or reuse the page-text cache for one uploaded PDF."""
    existing = document.get("pdf_context")
    if isinstance(existing, PdfTextContext):
        return existing
    pdf_bytes = document_pdf_bytes(document)
    if not pdf_bytes:
        return None
    context = build_pdf_text_context(pdf_bytes)
    document["pdf_context"] = context
    return context


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
    config_signatures = config.get("signature_terms")
    if isinstance(config_signatures, str):
        config_signatures = re.split(r"[|\n\r]+", config_signatures)
    if config_signatures:
        terms.extend(str(value).strip() for value in config_signatures if str(value).strip())
    else:
        terms.extend(TABLE_SIGNATURES.get(str(match.table_id), ()))
    terms.append(str(match.table_name or "").strip())
    return tuple(dict.fromkeys(term for term in terms if term))


def _flat_rows(rows: Iterable[Iterable[Any]]) -> str:
    return re.sub(
        r"\s+",
        "",
        "".join(str(cell or "") for row in rows for cell in row),
    )


def _signature_terms_for_match(match: PageMatch) -> tuple[str, ...]:
    """优先使用公司/表配置里的签名；未配置时回退全局 tables.yaml 签名。"""
    config = getattr(match, "table_config", None) or {}
    if not isinstance(config, dict):
        config = {}
    value = config.get("signature_terms")
    if isinstance(value, str):
        value = re.split(r"[|\n\r]+", value)
    terms = tuple(str(item).strip() for item in (value or ()) if str(item).strip())
    if terms:
        return terms
    return tuple(TABLE_SIGNATURES.get(str(getattr(match, "table_id", "")), ()))


def _select_target_frames(
    frames: Sequence[Sequence[Sequence[Any]]],
    terms: Sequence[str],
    table_id: str,
    signatures: Sequence[str] | None = None,
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
    effective_signatures = (
        tuple(signatures) if signatures is not None
        else TABLE_SIGNATURES.get(str(table_id), ())
    )
    signature_terms = [
        term.replace(" ", "")
        for term in effective_signatures
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
    frames_by_target: Mapping[tuple[str, int], list[list[list[str]]]],
    page_engines: Mapping[int, str],
    page_units: Mapping[int, str],
) -> tuple[list[FlexibleTable], list[dict[str, Any]]]:
    tables: list[FlexibleTable] = []
    missing: list[dict[str, Any]] = []
    for match in matches:
        pages = [int(page) for page in (match.pages or [])]
        blocks: list[dict[str, Any]] = []
        rows: list[list[str]] = []
        width = 0
        for page in pages:
            # 每张表只使用“用本表配置提取出来”的帧，避免同页相邻表互相串数据。
            frames = frames_by_target.get((str(match.table_id), page)) or []
            selected = _select_target_frames(
                frames,
                _table_terms(match),
                str(match.table_id),
                _signature_terms_for_match(match),
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
            missing.append(
                {
                    "table_id": str(match.table_id),
                    "table_name": str(match.table_name),
                    "pages": pages,
                    "reason": "本表所在页未提取到可用表格行（解析失败或该页确无表格）",
                }
            )
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
    return tables, missing


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
    text_context: PdfTextContext | None = None,
    metadata: Mapping[str, Any] | None = None,
    profile_id: str = "",
) -> BatchExtractionOutcome:
    """Extract confirmed pages for one PDF without touching Streamlit state."""
    outcome = BatchExtractionOutcome()
    located = [match for match in matches if match.pages]
    if not located:
        outcome.status = "失败"
        outcome.note = "没有已确认页码的目标表。"
        return outcome

    from .company_extractors import (
        extractor_from_matches,
        get_company_document_hooks,
    )

    company_extractor_name = extractor_from_matches(matches)
    company_document_extractor = None
    if company_extractor_name:
        _company_locator, company_document_extractor = get_company_document_hooks(
            company_extractor_name
        )
    if company_document_extractor is not None:
        from .extraction_rules import resolve_rule

        resolved_for_scope = resolve_rule(
            (metadata or {}).get("公司") or company,
            (metadata or {}).get("报告年度"),
            profile_id=profile_id,
        )
        configured_tables = set((getattr(resolved_for_scope, "tables", {}) or {}).keys())
        match_aliases = {"OPERATING_METRICS": "NON_LIFE_OPERATING_METRICS"}
        scoped_matches = [
            match for match in matches
            if not configured_tables
            or match.table_id in configured_tables
            or match_aliases.get(match.table_id, match.table_id) in configured_tables
        ]
        company_result = company_document_extractor(
            pdf_bytes,
            [
                {**match.table_config, "_confirmed_pages": list(match.pages)}
                for match in scoped_matches
                if isinstance(match.table_config, dict)
            ],
        )
        outcome.tables = list(company_result.get("tables") or [])
        outcome.logs = list(company_result.get("logs") or [])

        from .extraction_rules import resolve_rule, resolve_rule_for_pdf
        from .extraction_trace import build_traces

        if metadata:
            resolved_rule = resolve_rule(
                metadata.get("公司") or company,
                metadata.get("报告年度"),
                profile_id=profile_id,
            )
        else:
            resolved_rule, metadata = resolve_rule_for_pdf(
                pdf_bytes,
                company_hint=company,
                profile_id=profile_id,
                text_context=text_context,
            )
        outcome.resolved_rule = resolved_rule
        outcome.traces = build_traces(
            resolved_rule=resolved_rule,
            matches=scoped_matches,
            logs=outcome.logs,
            tables=outcome.tables,
        )
        failed = [log for log in outcome.logs if log.get("状态") not in {"成功", "未披露"}]
        if not outcome.tables:
            outcome.status = "失败"
            outcome.note = "公司专属提取未生成任何目标表。"
        elif failed:
            outcome.status = "部分成功"
            outcome.note = (
                f"公司专属提取 {len(outcome.tables)} 张表，"
                f"{len(failed)} 张表失败。"
            )
        else:
            outcome.status = "成功"
            outcome.note = (
                f"公司专属提取 {len(outcome.tables)} 张表，"
                "未调用通用表格提取。"
            )
        return outcome

    page_to_matches: dict[int, list[PageMatch]] = {}
    for match in located:
        for page in match.pages:
            page_to_matches.setdefault(int(page), []).append(match)

    tasks: list[tuple[int, PageMatch]] = []
    if whole_page:
        # 整页模式下同一页可能承载多张目标表（经营/流动性/近三年同页）。
        # 以前每页只取第一个 match，其余表要么拿错帧、要么直接消失；
        # 现在按 (页, 表) 各提一次，各表只用自己的配置与帧。
        seen: set[tuple[int, str]] = set()
        for page, page_matches in sorted(page_to_matches.items()):
            for match in page_matches:
                key = (page, str(match.table_id))
                if key in seen:
                    continue
                seen.add(key)
                tasks.append((page, match))
    else:
        tasks = [
            (int(page), match)
            for match in located
            for page in match.pages
        ]

    if text_context is None:
        try:
            text_context = build_pdf_text_context(pdf_bytes)
        except Exception:
            text_context = None

    def _cached_page_text(page: int) -> str:
        if (
            text_context is not None
            and 1 <= page <= len(text_context.page_texts)
        ):
            return text_context.page_texts[page - 1]
        return page_source_text(pdf_bytes, page)

    def run_task(page: int, match: PageMatch):
        page_text = _cached_page_text(page)
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

    frames_by_target: dict[tuple[str, int], list[list[list[str]]]] = {}
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
                label = str(match.table_name)
                if result.succeeded:
                    frames = [
                        frame.fillna("").astype(str).values.tolist()
                        for frame in (result.frames or (result.frame,))
                        if frame is not None and not frame.empty
                    ]
                    frames_by_target[(str(match.table_id), page)] = frames
                    page_engines[page] = result.engine
                    page_units[page] = (
                        sniff_unit_from_text(_cached_page_text(page))
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

    outcome.tables, outcome.missing_tables = _assemble_tables(
        matches=located,
        frames_by_target=frames_by_target,
        page_engines=page_engines,
        page_units=page_units,
    )
    for item in outcome.missing_tables:
        outcome.logs.append(
            {
                "公司": company,
                "目标表": str(item["table_name"]),
                "物理页码": "、".join(map(str, item["pages"])),
                "识别方式": "",
                "状态": "缺失",
                "说明": str(item["reason"]),
            }
        )

    # 公司专属后处理：单文件与批量共用同一入口。
    from .company_extractors import apply_company_extractor_for_tables

    outcome.tables, extractor_result = apply_company_extractor_for_tables(
        outcome.tables,
        located,
        pdf_bytes=pdf_bytes,
    )
    # 通用经营表补全：所有公司都执行，补齐 pdfplumber 漏掉的效益/规模/品质类指标。
    from .company_extractors._operating_table import complete_operating_tables

    outcome.tables = list(
        complete_operating_tables(
            outcome.tables,
            pdf_bytes,
            page_texts=(
                text_context.fitz_page_texts
                if text_context is not None
                else None
            ),
        )
    )
    if extractor_result:
        outcome.logs.append(
            {
                "公司": company,
                "目标表": "公司专属规则",
                "物理页码": "",
                "识别方式": f"extractor:{extractor_result['extractor']}",
                "状态": extractor_result["status"],
                "说明": extractor_result["message"],
            }
        )

    # R4：每张目标表的提取状态与未命中原因。
    from .extraction_rules import resolve_rule, resolve_rule_for_pdf
    from .extraction_trace import build_traces

    if metadata:
        resolved_rule = resolve_rule(
            metadata.get("公司") or company,
            metadata.get("报告年度"),
            profile_id=profile_id,
        )
    else:
        resolved_rule, metadata = resolve_rule_for_pdf(
            pdf_bytes,
            company_hint=company,
            profile_id=profile_id,
            text_context=text_context,
        )
    outcome.resolved_rule = resolved_rule
    outcome.traces = build_traces(
        resolved_rule=resolved_rule,
        matches=located,
        logs=outcome.logs,
        tables=outcome.tables,
    )
    failed_pages = [log for log in outcome.logs if log["状态"] == "失败"]
    if not outcome.tables:
        outcome.status = "失败"
        outcome.note = "未提取到任何目标表。"
    elif failed_pages or outcome.missing_tables:
        outcome.status = "部分成功"
        parts = [f"提取 {len(outcome.tables)} 张表"]
        if failed_pages:
            parts.append(f"{len(failed_pages)} 页失败")
        if outcome.missing_tables:
            parts.append(f"{len(outcome.missing_tables)} 张表缺帧")
        outcome.note = "；".join(parts) + "。"
    else:
        outcome.status = "成功"
        outcome.note = f"提取 {len(outcome.tables)} 张表，共 {len(page_engines)} 页。"
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
    keep_unmapped: bool = False,
    extraction_traces: Sequence[Mapping[str, Any]] | None = None,
    metadata: Mapping[str, Any] | None = None,
    text_context: PdfTextContext | None = None,
    build_workbook: bool = True,
) -> dict[str, Any]:
    """Run STEP3 for one PDF and return its data, workbook and diagnostics."""
    try:
        metadata_source = dict(metadata or {})
        if not metadata_source:
            metadata_source = extract_report_metadata(
                pdf_bytes,
                text_context=text_context,
            )
        metadata = sanitize_tree(
            {
                **metadata_source,
                "来源文件": pdf_name,
                "公司": company,
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
            keep_unmapped=keep_unmapped,
            extraction_traces=extraction_traces,
        )
        report_period = str(metadata.get("报告期", "")).strip() or "2025Q4"
        data = result.data
        has_registered_capital = (
            not data.empty
            and "指标编码" in data.columns
            and data["指标编码"].astype(str).eq("REGISTERED_CAPITAL").any()
        )
        if not has_registered_capital:
            pdf_text = (
                "\n".join(text_context.fitz_page_texts)
                if text_context is not None
                else _full_pdf_text(pdf_bytes)
            )
            data = append_registered_capital(
                data,
                pdf_text=pdf_text,
                pdf_bytes=pdf_bytes if not pdf_text else None,
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
            "workbook_bytes": (
                result_workbook_bytes(result)
                if build_workbook
                else b""
            ),
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
        current_rule_signature = rule_signature(
            company,
            profile_id=profile_id,
        )
        if (
            previous
            and previous.get("profile_id") == profile_id
            and previous.get("upload_signature") == signature
            and previous.get("rule_signature") == current_rule_signature
        ):
            previous["uploaded_file"] = uploaded
            continue
        updated[company] = {
            "company": company,
            "pdf_name": name,
            "uploaded_file": uploaded,
            "upload_signature": signature,
            "rule_signature": current_rule_signature,
            "pdf_bytes": None,
            "pdf_context": None,
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
