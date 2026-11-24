"""前海财险专属定位、提取和未披露判定。"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from . import register
from ._document import (
    HEADERS,
    STANDARD_LAYOUT_CONTRACTS,
    extract_document,
    locate_document_with_rules,
)

_TABLE_NAMES = {
    "SOLVENCY_MAIN": "偿付能力充足率指标",
    "NON_LIFE_OPERATING_METRICS": "主要经营指标（财险）",
    "ACTUAL_CAPITAL": "S02-实际资本明细表",
    "THREE_YEAR_INVESTMENT_RETURN": "近三年（综合）投资收益率",
    "MINIMUM_CAPITAL": "S05-最低资本表",
    "LIQUIDITY_MONITORING": "流动性风险监测指标",
}
_UNDISCLOSED = {
    "THREE_YEAR_INVESTMENT_RETURN": (
        "原文没有独立的近三年平均投资收益率目标表，按未披露处理"
    ),
}


def locate_company_tables(
    pdf_bytes: bytes,
    table_configs: Sequence[Mapping[str, Any]] = (),
) -> dict[str, list[int]]:
    """Keep company pages authoritative and make the absent return table explicit."""
    located = locate_document_with_rules(pdf_bytes, table_configs)
    for supplied in table_configs:
        table_id = str(supplied.get("table_id") or "")
        if table_id in _UNDISCLOSED:
            located[table_id] = []
    return located


def _prepare_configs(
    table_configs: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    configs: list[dict[str, Any]] = []
    layout: dict[str, dict[str, Any]] = {}
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
        config["table_id"] = primary
        config.setdefault("table_name", _TABLE_NAMES[primary])
        if primary in _UNDISCLOSED:
            config["_confirmed_pages"] = []
        else:
            config["_confirmed_pages"] = [
                int(page)
                for page in (config.get("_confirmed_pages") or [])
                if int(page) > 0
            ]
        if not config.get("_layout_contract"):
            config["_layout_contract"] = dict(
                STANDARD_LAYOUT_CONTRACTS[primary]
            )
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
    return configs, layout


def _mark_undisclosed(
    result: Mapping[str, Any],
    configs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    tables = [
        table
        for table in (result.get("tables") or [])
        if str(getattr(table, "table_id", "")) not in _UNDISCLOSED
    ]
    logs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for supplied in result.get("logs") or []:
        log = dict(supplied)
        table_id = str(log.get("表编码") or "")
        if table_id in _UNDISCLOSED:
            log["状态"] = "未披露"
            log["说明"] = _UNDISCLOSED[table_id]
            log["物理页码"] = ""
            seen.add(table_id)
        logs.append(log)
    for config in configs:
        table_id = str(config.get("table_id") or "")
        if table_id not in _UNDISCLOSED or table_id in seen:
            continue
        logs.append(
            {
                "目标表": config.get("table_name"),
                "表编码": table_id,
                "物理页码": "",
                "识别方式": "公司专属规则:qianhai",
                "状态": "未披露",
                "说明": _UNDISCLOSED[table_id],
            }
        )
    pages_by_table = dict(result.get("pages_by_table") or {})
    for table_id in _UNDISCLOSED:
        pages_by_table[table_id] = []
    return {
        **dict(result),
        "tables": tables,
        "logs": logs,
        "pages_by_table": pages_by_table,
    }


def extract_company_tables(
    pdf_bytes: bytes,
    table_configs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    configs, layout = _prepare_configs(table_configs)
    result = extract_document(
        pdf_bytes,
        configs,
        layout,
        postprocess,
        "qianhai",
        generic=True,
        enforce_contract=True,
    )
    return _mark_undisclosed(result, configs)


@register("qianhai")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    del pdf_bytes
    return tables
