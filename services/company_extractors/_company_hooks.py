"""Shared execution primitives for explicitly declared company hooks."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


def locate_for_company(
    company: str,
    pdf_bytes: bytes,
    table_configs: Sequence[Mapping[str, Any]] = (),
) -> dict[str, list[int]]:
    from ._document import locate_document_with_rules

    return locate_document_with_rules(pdf_bytes, table_configs)


def _extract_legacy(
    company: str,
    pdf_bytes: bytes,
    table_configs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    from ..batch_workflow import extract_document_tables
    from ..solvency_pdf_locator import PageMatch
    from ._document import STANDARD_LAYOUT_CONTRACTS

    matches = []
    not_disclosed_logs = []
    for supplied in table_configs:
        config = dict(supplied)
        table_id = str(config.get("table_id") or "")
        primary = (
            "NON_LIFE_OPERATING_METRICS"
            if table_id == "OPERATING_METRICS"
            else table_id
        )
        if not primary:
            continue
        config["table_id"] = primary
        config.setdefault(
            "table_name",
            {
                "SOLVENCY_MAIN": "偿付能力充足率指标",
                "NON_LIFE_OPERATING_METRICS": "主要经营指标（财险）",
                "ACTUAL_CAPITAL": "S02-实际资本明细表",
                "MINIMUM_CAPITAL": "S05-最低资本表",
                "LIQUIDITY_MONITORING": "流动性风险监测指标",
                "THREE_YEAR_INVESTMENT_RETURN": "近三年（综合）投资收益率",
            }.get(primary, primary),
        )
        if config.get("not_disclosed"):
            not_disclosed_logs.append(
                {
                    "公司": company,
                    "目标表": str(config["table_name"]),
                    "物理页码": "",
                    "识别方式": "公司规则",
                    "状态": "未披露",
                    "说明": str(
                        config.get("notes")
                        or "公司规则标记该表原文未披露"
                    ),
                }
            )
            continue
        if not config.get("_layout_contract"):
            config["_layout_contract"] = dict(
                STANDARD_LAYOUT_CONTRACTS[primary]
            )
        pages = list(config.get("_confirmed_pages", []) or [])
        raw_config = {
            name: value
            for name, value in config.items()
            if name != "_extraction_rule"
        }
        matches.append(
            PageMatch(
                table_id=primary,
                table_name=str(config["table_name"]),
                pages=pages,
                score=100.0,
                evidence="公司专属页码",
                review_required=False,
                review_reason="",
                sources={},
                strategy_id=None,
                table_config=raw_config,
                confidence=100,
                confidence_basis="公司专属页码",
            )
        )
    outcome = extract_document_tables(
        company=company,
        pdf_bytes=pdf_bytes,
        matches=matches,
        allow_llm_fallback=False,
        force_legacy=True,
        company_extractor_override=company,
    )
    return {
        "tables": outcome.tables,
        "logs": [*not_disclosed_logs, *outcome.logs],
        "pages_by_table": {
            match.table_id: list(match.pages)
            for match in matches
        },
    }


def _extract_document(
    company: str,
    pdf_bytes: bytes,
    table_configs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    from . import get_postprocessor
    from ._document import (
        HEADERS,
        STANDARD_LAYOUT_CONTRACTS,
        extract_document,
    )

    postprocessor = get_postprocessor(company)
    if postprocessor is None:
        def postprocessor(tables, *, pdf_bytes):
            return tables

    configs = []
    layout = {}
    not_disclosed_logs = []
    table_names = {
        "SOLVENCY_MAIN": "偿付能力充足率指标",
        "NON_LIFE_OPERATING_METRICS": "主要经营指标（财险）",
        "ACTUAL_CAPITAL": "S02-实际资本明细表",
        "MINIMUM_CAPITAL": "S05-最低资本表",
        "LIQUIDITY_MONITORING": "流动性风险监测指标",
        "THREE_YEAR_INVESTMENT_RETURN": "近三年（综合）投资收益率",
    }
    for supplied in table_configs:
        config = dict(supplied)
        table_id = str(config.get("table_id") or "")
        primary = (
            "NON_LIFE_OPERATING_METRICS"
            if table_id == "OPERATING_METRICS"
            else table_id
        )
        if primary not in HEADERS:
            continue
        pages = list(config.get("_confirmed_pages", []) or [])
        config["table_id"] = primary
        config.setdefault("table_name", table_names[primary])
        if config.get("not_disclosed"):
            not_disclosed_logs.append(
                {
                    "公司": company,
                    "目标表": str(config["table_name"]),
                    "物理页码": "",
                    "识别方式": "公司规则",
                    "状态": "未披露",
                    "说明": str(
                        config.get("notes")
                        or "公司规则标记该表原文未披露"
                    ),
                }
            )
            continue
        if not config.get("_layout_contract"):
            config["_layout_contract"] = dict(
                STANDARD_LAYOUT_CONTRACTS[primary]
            )
        config["_confirmed_pages"] = pages
        configs.append(config)
        strong_terms = (
            config.get("signature_terms")
            or config.get("content_terms")
            or config.get("required_items")
            or []
        )
        layout[primary] = {
            "title": r"(?!)",
            "required": [],
            "unit": config.get("unit", "万元"),
            "signature_terms": list(strong_terms),
        }
    result = extract_document(
        pdf_bytes,
        configs,
        layout,
        postprocessor,
        company,
        generic=True,
        enforce_contract=True,
    )
    if not_disclosed_logs:
        result["logs"] = [
            *not_disclosed_logs,
            *list(result.get("logs") or []),
        ]
    return result


def extract_for_company(
    company: str,
    pdf_bytes: bytes,
    table_configs: Sequence[Mapping[str, Any]],
    *,
    mode: str = "document",
) -> dict[str, Any]:
    if mode == "legacy":
        return _extract_legacy(company, pdf_bytes, table_configs)
    if mode != "document":
        raise ValueError(f"不支持的公司提取模式：{mode}")
    return _extract_document(company, pdf_bytes, table_configs)
