"""公司专属提取后处理。

通用规则负责"大多数公司的常见版式"，这里处理通用规则无法覆盖的
版式特例（合并单元格、跨章节指标、无表头续页等）。

约定：
* 只处理 raw_tables（``rows``），不进入标准化；
* 提取器必须容错：任何异常都不能阻断通用提取流程；
* 通过公司 YAML 的 ``company.extractor`` 启用，例如 ``extractor: pingan``。
"""

from __future__ import annotations

import importlib
from typing import Any, Callable, Sequence

POSTPROCESSORS: dict[str, Callable[..., Sequence[Any]]] = {}
LEGACY_PAGE_ADAPTERS: set[str] = {
    "ancheng",
    "bohai",
    "dongjing",
    "donghai",
    "ansheng",
    "fude",
    "haixia",
    "jingdong",
    "jintai",
    "ruizai",
    "sanxing",
    "sumitomo",
    "taishan",
    "yatai",
    "zhongcheng",
}


def register(name: str) -> Callable[[Callable[..., Sequence[Any]]], Callable[..., Sequence[Any]]]:
    def decorator(func: Callable[..., Sequence[Any]]) -> Callable[..., Sequence[Any]]:
        POSTPROCESSORS[str(name).strip().lower()] = func
        return func

    return decorator


def get_postprocessor(name: str) -> Callable[..., Sequence[Any]] | None:
    key = str(name or "").strip().lower()
    if not key:
        return None
    if key not in POSTPROCESSORS:
        # 延迟导入，避免启动时加载 fitz 之外的解析逻辑；
        # 模块名与 extractor 名一致，例如 pingan → company_extractors/pingan.py。
        module = "".join(ch for ch in key if ch.isalnum() or ch == "_")
        if module:
            try:
                importlib.import_module(f".{module}", __name__)
            except ImportError:
                pass
    return POSTPROCESSORS.get(key)


def get_company_document_hooks(
    name: str,
) -> tuple[Callable[..., Any] | None, Callable[..., Any] | None]:
    """Return optional company-specific locator and document extractor hooks."""
    key = str(name or "").strip().lower()
    if not key:
        return None, None
    module_name = "".join(ch for ch in key if ch.isalnum() or ch == "_")
    if not module_name:
        return None, None
    try:
        module = importlib.import_module(f".{module_name}", __name__)
    except ImportError:
        return None, None
    locator = getattr(module, "locate_company_tables", None)
    extractor = getattr(module, "extract_company_tables", None)
    if locator is None:
        from ._document import locate_document_with_rules

        def locator(pdf_bytes, table_configs=()):
            return locate_document_with_rules(pdf_bytes, table_configs)

        locator.__name__ = f"{key}_document_locator"
    if extractor is None:
        postprocessor = POSTPROCESSORS.get(key)
        if postprocessor is None:
            def postprocessor(tables, *, pdf_bytes):
                return tables

        def extractor(pdf_bytes, table_configs):
            if key in LEGACY_PAGE_ADAPTERS:
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
                                "公司": key,
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
                    company=key,
                    pdf_bytes=pdf_bytes,
                    matches=matches,
                    allow_llm_fallback=False,
                    force_legacy=True,
                    company_extractor_override=key,
                )
                return {
                    "tables": outcome.tables,
                    "logs": [*not_disclosed_logs, *outcome.logs],
                    "pages_by_table": {
                        match.table_id: list(match.pages)
                        for match in matches
                    },
                }

            from ._document import (
                HEADERS,
                STANDARD_LAYOUT_CONTRACTS,
                extract_document,
            )

            configs = []
            not_disclosed_logs = []
            layout = {}
            for supplied in table_configs:
                config = dict(supplied)
                table_id = str(config.get("table_id") or "")
                primary = "NON_LIFE_OPERATING_METRICS" if table_id == "OPERATING_METRICS" else table_id
                if primary not in HEADERS:
                    continue
                pages = list(config.get("_confirmed_pages", []) or [])
                config["table_id"] = primary
                table_names = {"SOLVENCY_MAIN": "偿付能力充足率指标",
                               "NON_LIFE_OPERATING_METRICS": "主要经营指标（财险）",
                               "ACTUAL_CAPITAL": "S02-实际资本明细表",
                               "MINIMUM_CAPITAL": "S05-最低资本表",
                               "LIQUIDITY_MONITORING": "流动性风险监测指标",
                               "THREE_YEAR_INVESTMENT_RETURN": "近三年（综合）投资收益率"}
                config.setdefault("table_name", table_names[primary])
                if config.get("not_disclosed"):
                    not_disclosed_logs.append(
                        {
                            "公司": key,
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
                configs.append(config)
                strong_terms = (config.get("signature_terms") or config.get("content_terms")
                                or config.get("required_items") or [])
                layout[primary] = {"title": r"(?!)", "required": [], "unit": config.get("unit", "万元"),
                                   "signature_terms": list(strong_terms)}
                config["_confirmed_pages"] = pages
            result = extract_document(
                pdf_bytes,
                configs,
                layout,
                postprocessor,
                key,
                generic=True,
                enforce_contract=True,
            )
            if not_disclosed_logs:
                result["logs"] = [
                    *not_disclosed_logs,
                    *list(result.get("logs") or []),
                ]
            return result

        extractor.__name__ = f"{key}_document_extractor"
    return locator, extractor


def apply_company_extractor(
    name: str,
    tables: Sequence[Any],
    *,
    pdf_bytes: bytes,
) -> Sequence[Any]:
    postprocessor = get_postprocessor(name)
    if postprocessor is None or not tables:
        return tables
    return postprocessor(tables, pdf_bytes=pdf_bytes)


def extractor_from_matches(matches: Sequence[Any] | None) -> str:
    """从页定位结果的 table_config 里取公司专属 extractor 名称。

    STEP1 应用公司规则时会把 ``_extraction_rule.extractor`` 写进表配置，
    单文件与批量路径都依赖这个标记，保证两边行为一致。
    """
    for match in matches or []:
        config = getattr(match, "table_config", None)
        if not isinstance(config, dict):
            continue
        info = config.get("_extraction_rule")
        if isinstance(info, dict):
            name = str(info.get("extractor") or "").strip()
            if name:
                return name
    return ""


def apply_company_extractor_for_tables(
    tables: Sequence[Any],
    matches: Sequence[Any] | None = None,
    *,
    pdf_bytes: bytes,
    extractor_name: str = "",
) -> tuple[Sequence[Any], dict[str, str] | None]:
    """单文件与批量共用的入口；返回 (tables, 日志信息或 None)。"""
    name = str(extractor_name or "").strip() or extractor_from_matches(matches)
    if not name or not tables or not pdf_bytes:
        return tables, None
    if get_postprocessor(name) is None:
        return tables, None
    try:
        updated = list(apply_company_extractor(name, tables, pdf_bytes=pdf_bytes))
        contract_failures = []
        for table in updated:
            config = getattr(table, "table_config", None)
            if not isinstance(config, dict):
                continue
            failure = str(config.get("_layout_contract_failure") or "").strip()
            if failure:
                contract_failures.append(
                    f"{getattr(table, 'table_name', '')}: {failure}"
                )
        if contract_failures:
            return updated, {
                "extractor": name,
                "status": "失败",
                "message": "；".join(contract_failures)[:500],
            }
        return updated, {
            "extractor": name,
            "status": "成功",
            "message": "已应用公司专属提取后处理。",
        }
    except Exception as exc:  # 专属逻辑失败不能阻断通用提取
        return tables, {
            "extractor": name,
            "status": "失败",
            "message": f"专属后处理异常，已保留通用提取结果：{exc}",
        }
