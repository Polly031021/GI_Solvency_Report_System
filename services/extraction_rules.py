"""公司级提取规则：一家公司一份 YAML，跨年度复用。

规则模型（对应 plans/refactor-plan-track-and-rule-config.md 批次 2）：

* 路由键是封面识别出的公司名（别名归一）+ 报告年度（仅用于选结构版本）；
* 页码用 ``expected_pages`` + ``tolerance`` 形成搜索窗口，再由表标题锚点验证；
* 只有表结构大改时才在 ``structure_versions`` 追加一条，不按年维护；
* 未找到规则返回 ``None``，调用方沿用 Profile 默认配置（零回归）。
"""

from __future__ import annotations

import hashlib
import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .extraction_config import (
    ExtractionConfigError,
    build_table_configs,
    load_table_maps,
)

RULES_DIR = (
    Path(__file__).resolve().parent.parent / "config" / "extraction" / "companies"
)
DEFAULT_TOLERANCE = 2
PAGE_MATCH_MODES = {"anchor_only", "prefer", "strict"}

# 规则字段 → Profile table_config 字段（定位/提取直接读取的键）。
_RULE_TO_CONFIG_FIELDS = {
    "anchor_terms": "title_terms",
    "header_terms": "header_terms",
    "content_terms": "content_terms",
    "start_item": "start_item_terms",
    "end_item": "end_item_terms",
    "exclude_terms": "exclude_item_terms",
    # 公司可覆盖 tables.yaml 的全局签名，仍按“全部命中”判断。
    "signature_terms": "signature_terms",
}
# 原样写入 table_config 的字段。
_DIRECT_FIELDS = {
    "expected_pages",
    "tolerance",
    "page_match",
    "required_items",
    "unit",
    "notes",
    "columns",
    "defer_completeness_to_extractor",
    "not_disclosed",
    # 续页相关：不止一次出现“YAML 里写了却不生效”，这里补进白名单。
    "include_continuation",
    "max_pages",
    "continuation_min_structure_hits",
    "continuation_min_numeric_lines",
    # 与 _RULE_TO_CONFIG_FIELDS 同步放行；最终值统一由 _as_terms 规范。
    "signature_terms",
}

_CACHE: dict[str, Any] = {"signature": None, "rules": None}


@dataclass(frozen=True)
class StructureVersion:
    since: int
    notes: str
    tables: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    layout_contracts: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class CompanyRule:
    canonical: str
    aliases: tuple[str, ...]
    profile_id: str
    rule_version: str
    notes: str
    tables: Mapping[str, Mapping[str, Any]]
    structure_versions: tuple[StructureVersion, ...] = ()
    source: str = ""
    # 公司专属提取器名称（如 pingan）；空表示只用通用规则。
    extractor: str = ""
    layout_contracts: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedRule:
    canonical: str
    source: str
    rule_version: str
    structure_version: int | None
    tables: Mapping[str, Mapping[str, Any]]
    warnings: tuple[str, ...] = ()
    extractor: str = ""
    layout_contracts: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    def table(self, table_id: str) -> Mapping[str, Any] | None:
        return self.tables.get(table_id)

    @property
    def label(self) -> str:
        version = (
            f"v{self.structure_version}"
            if self.structure_version is not None
            else f"rule v{self.rule_version}"
        )
        return f"{self.canonical}（{version}）"


def _normalize_company(value: object) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\.pdf$", "", text, flags=re.IGNORECASE)
    # 去掉“（偿付能力）”这类文件名说明和所有空白。
    text = re.sub(r"[（(][^）)]*[）)]", "", text)
    return re.sub(r"\s+", "", text)


def _as_terms(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(
        dict.fromkeys(
            str(item).strip() for item in value if str(item).strip()
        )
    )


def _as_pages(value: Any, *, table_id: str) -> tuple[int, ...]:
    if value is None:
        return ()
    if isinstance(value, (int, str)):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise ExtractionConfigError(f"{table_id}.expected_pages 必须是整数列表。")
    pages: list[int] = []
    for item in value:
        try:
            page = int(item)
        except (TypeError, ValueError) as exc:
            raise ExtractionConfigError(
                f"{table_id}.expected_pages 含非整数值：{item!r}"
            ) from exc
        if page < 1:
            raise ExtractionConfigError(
                f"{table_id}.expected_pages 必须从 1 开始：{item!r}"
            )
        pages.append(page)
    return tuple(sorted(set(pages)))


def _as_tolerance(value: Any, *, table_id: str) -> int:
    if value is None:
        return DEFAULT_TOLERANCE
    try:
        tolerance = int(value)
    except (TypeError, ValueError) as exc:
        raise ExtractionConfigError(
            f"{table_id}.tolerance 必须是整数：{value!r}"
        ) from exc
    if tolerance < 0:
        raise ExtractionConfigError(f"{table_id}.tolerance 不能为负数。")
    return tolerance


def _parse_tables(
    raw: Any,
    *,
    source: str,
    known_tables: set[str],
) -> dict[str, dict[str, Any]]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ExtractionConfigError(f"{source} 的 tables 必须是映射。")
    tables: dict[str, dict[str, Any]] = {}
    for raw_id, raw_config in raw.items():
        table_id = str(raw_id or "").strip()
        if not table_id:
            raise ExtractionConfigError(f"{source} 存在空 table_id。")
        if table_id not in known_tables:
            raise ExtractionConfigError(
                f"{source} 引用了未知 table_id：{table_id}（请先在 tables.yaml 注册）"
            )
        if raw_config is None:
            raw_config = {}
        if not isinstance(raw_config, Mapping):
            raise ExtractionConfigError(f"{source} 的 {table_id} 必须是映射。")
        config = dict(raw_config)
        pages = _as_pages(config.pop("expected_pages", None), table_id=table_id)
        if pages:
            config["expected_pages"] = list(pages)
        config["tolerance"] = _as_tolerance(
            config.get("tolerance"), table_id=table_id
        )
        mode = str(config.get("page_match") or "prefer").strip()
        if mode not in PAGE_MATCH_MODES:
            raise ExtractionConfigError(
                f"{source} 的 {table_id}.page_match 非法：{mode}"
            )
        config["page_match"] = mode
        not_disclosed = config.get("not_disclosed", False)
        if not isinstance(not_disclosed, bool):
            raise ExtractionConfigError(
                f"{source} 的 {table_id}.not_disclosed 必须是布尔值。"
            )
        config["not_disclosed"] = not_disclosed
        tables[table_id] = config
    return tables


def _parse_layout_contracts(
    raw: Any,
    *,
    source: str,
    known_tables: set[str],
) -> dict[str, dict[str, Any]]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ExtractionConfigError(f"{source} 的 layout_contracts 必须是映射。")
    contracts: dict[str, dict[str, Any]] = {}
    for raw_id, raw_contract in raw.items():
        table_id = str(raw_id or "").strip()
        if table_id not in known_tables:
            raise ExtractionConfigError(
                f"{source}.layout_contracts 引用未知 table_id：{table_id}"
            )
        if not isinstance(raw_contract, Mapping):
            raise ExtractionConfigError(
                f"{source}.layout_contracts.{table_id} 必须是映射。"
            )
        try:
            min_rows = int(raw_contract.get("min_rows", 1))
        except (TypeError, ValueError) as exc:
            raise ExtractionConfigError(
                f"{source}.layout_contracts.{table_id}.min_rows 必须是整数。"
            ) from exc
        if min_rows < 1:
            raise ExtractionConfigError(
                f"{source}.layout_contracts.{table_id}.min_rows 必须大于 0。"
            )
        max_rows_raw = raw_contract.get("max_rows")
        max_rows: int | None = None
        if max_rows_raw is not None:
            try:
                max_rows = int(max_rows_raw)
            except (TypeError, ValueError) as exc:
                raise ExtractionConfigError(
                    f"{source}.layout_contracts.{table_id}.max_rows 必须是整数。"
                ) from exc
            if max_rows < min_rows:
                raise ExtractionConfigError(
                    f"{source}.layout_contracts.{table_id}.max_rows 不能小于 min_rows。"
                )
        unique_rows = raw_contract.get("unique_rows", False)
        if not isinstance(unique_rows, bool):
            raise ExtractionConfigError(
                f"{source}.layout_contracts.{table_id}.unique_rows 必须是布尔值。"
            )
        allow_mixed_widths = raw_contract.get("allow_mixed_widths", False)
        if not isinstance(allow_mixed_widths, bool):
            raise ExtractionConfigError(
                f"{source}.layout_contracts.{table_id}.allow_mixed_widths 必须是布尔值。"
            )
        raw_header_aliases = raw_contract.get("header_aliases") or {}
        if not isinstance(raw_header_aliases, Mapping):
            raise ExtractionConfigError(
                f"{source}.layout_contracts.{table_id}.header_aliases 必须是映射。"
            )
        header_aliases = {
            str(header).strip(): list(_as_terms(aliases))
            for header, aliases in raw_header_aliases.items()
            if str(header).strip()
        }
        raw_variants = raw_contract.get("header_variants") or []
        if not isinstance(raw_variants, (list, tuple)):
            raise ExtractionConfigError(
                f"{source}.layout_contracts.{table_id}.header_variants 必须是列表。"
            )
        header_variants = [
            list(_as_terms(variant))
            for variant in raw_variants
            if isinstance(variant, (list, tuple))
        ]
        contracts[table_id] = {
            "headers": list(_as_terms(raw_contract.get("headers"))),
            "required_rows": list(_as_terms(raw_contract.get("required_rows"))),
            "min_rows": min_rows,
            "max_rows": max_rows,
            "forbidden_rows": list(_as_terms(raw_contract.get("forbidden_rows"))),
            "unique_rows": unique_rows,
            "allow_mixed_widths": allow_mixed_widths,
            "header_aliases": header_aliases,
            "header_variants": header_variants,
        }
    return contracts


def _parse_rule(path: Path, known_tables: set[str]) -> CompanyRule:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - requirements 已包含 pyyaml
        raise ExtractionConfigError("缺少 PyYAML 依赖，无法读取公司规则。") from exc
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise ExtractionConfigError(f"无法解析 {path.name}：{exc}") from exc
    if not isinstance(payload, Mapping):
        raise ExtractionConfigError(f"{path.name} 顶层必须是映射。")
    company = payload.get("company") or {}
    if not isinstance(company, Mapping):
        raise ExtractionConfigError(f"{path.name} 的 company 必须是映射。")
    canonical = str(company.get("canonical") or path.stem).strip()
    if not canonical:
        raise ExtractionConfigError(f"{path.name} 缺少 canonical。")
    if canonical != path.stem:
        raise ExtractionConfigError(
            f"{path.name} 的 canonical（{canonical}）应等于文件名。"
        )
    aliases = _as_terms(company.get("aliases"))
    if _normalize_company(canonical) in {
        _normalize_company(alias) for alias in aliases
    }:
        raise ExtractionConfigError(
            f"{path.name} 的 aliases 不应重复 canonical。"
        )

    tables = _parse_tables(
        payload.get("tables"),
        source=path.name,
        known_tables=known_tables,
    )
    layout_contracts = _parse_layout_contracts(
        company.get("layout_contracts"),
        source=path.name,
        known_tables=known_tables,
    )

    versions_raw = payload.get("structure_versions") or []
    if not isinstance(versions_raw, (list, tuple)):
        raise ExtractionConfigError(f"{path.name} 的 structure_versions 必须是列表。")
    versions: list[StructureVersion] = []
    last_since = -1
    for item in versions_raw:
        if not isinstance(item, Mapping):
            raise ExtractionConfigError(
                f"{path.name} 的 structure_versions 项必须是映射。"
            )
        try:
            since = int(item.get("since"))
        except (TypeError, ValueError) as exc:
            raise ExtractionConfigError(
                f"{path.name} 的 structure_versions.since 必须是年份。"
            ) from exc
        if since <= last_since:
            raise ExtractionConfigError(
                f"{path.name} 的 structure_versions.since 必须递增。"
            )
        last_since = since
        versions.append(
            StructureVersion(
                since=since,
                notes=str(item.get("notes") or "").strip(),
                tables=_parse_tables(
                    item.get("tables"),
                    source=f"{path.name}（since {since}）",
                    known_tables=known_tables,
                ),
                layout_contracts=_parse_layout_contracts(
                    item.get("layout_contracts"),
                    source=f"{path.name}（since {since}）",
                    known_tables=known_tables,
                ),
            )
        )

    return CompanyRule(
        canonical=canonical,
        aliases=aliases,
        profile_id=str(company.get("profile_id") or "").strip(),
        rule_version=str(company.get("rule_version") or "1").strip(),
        notes=str(company.get("notes") or "").strip(),
        tables=tables,
        structure_versions=tuple(versions),
        source=path.name,
        extractor=str(company.get("extractor") or "").strip(),
        layout_contracts=layout_contracts,
    )


def _rules_signature(directory: Path | None = None) -> tuple | None:
    directory = directory or RULES_DIR
    if not directory.is_dir():
        return None
    entries: list[tuple[str, int, int]] = []
    for path in sorted(directory.glob("*.yaml")):
        try:
            stat = path.stat()
        except OSError:
            continue
        entries.append((path.name, int(stat.st_mtime_ns), int(stat.st_size)))
    return tuple(entries)


_LOAD_WARNINGS: list[str] = []


def get_load_warnings() -> list[str]:
    """最近一次非严格加载中被跳过的规则文件/条目说明。"""
    return list(_LOAD_WARNINGS)


def load_company_rules(
    directory: str | Path | None = None,
    *,
    force: bool = False,
    strict: bool = True,
) -> dict[str, CompanyRule]:
    """加载 ``config/extraction/companies/*.yaml``，按公司名建索引。

    ``strict=True``（默认）遇到坏文件直接抛 ``ExtractionConfigError``，
    供测试与显式校验使用；``strict=False`` 跳过坏文件/冲突别名并记入
    ``get_load_warnings()``，避免单个 YAML 错误拖垮批量提取。
    """
    target = Path(directory) if directory is not None else RULES_DIR
    signature = _rules_signature(target)
    cache_key = str(target.resolve())
    if (
        not force
        and _CACHE["rules"] is not None
        and _CACHE["signature"] == (cache_key, signature)
    ):
        return _CACHE["rules"]

    maps = load_table_maps()
    known_tables = set(maps["names"]) | set(maps["aliases"])
    rules: dict[str, CompanyRule] = {}
    seen_names: dict[str, str] = {}
    _LOAD_WARNINGS.clear()
    if target.is_dir():
        for path in sorted(target.glob("*.yaml")):
            try:
                rule = _parse_rule(path, known_tables)
            except ExtractionConfigError as exc:
                if strict:
                    raise
                _LOAD_WARNINGS.append(f"{path.name}: {exc}")
                continue
            if rule.canonical in rules:
                message = f"公司规则重复：{rule.canonical}（{path.name}）"
                if strict:
                    raise ExtractionConfigError(message)
                _LOAD_WARNINGS.append(message)
                continue
            names = [rule.canonical, *rule.aliases]
            conflicts = [
                name
                for name in names
                if (normalized := _normalize_company(name))
                and normalized in seen_names
                and seen_names[normalized] != rule.canonical
            ]
            if conflicts and strict:
                raise ExtractionConfigError(
                    f"公司别名冲突：{conflicts[0]} 同时属于 "
                    f"{seen_names[_normalize_company(conflicts[0])]} 和 {rule.canonical}"
                )
            for name in names:
                normalized = _normalize_company(name)
                if not normalized:
                    continue
                if normalized in seen_names and seen_names[normalized] != rule.canonical:
                    _LOAD_WARNINGS.append(
                        f"别名冲突跳过：{name} 属于 {seen_names[normalized]}，"
                        f"忽略 {rule.canonical} 的该别名"
                    )
                    continue
                seen_names[normalized] = rule.canonical
            rules[rule.canonical] = rule
    _CACHE["signature"] = (cache_key, signature)
    _CACHE["rules"] = rules
    return rules


def clear_cache() -> None:
    _CACHE["signature"] = None
    _CACHE["rules"] = None


def rule_signature(
    company: object,
    year: object = None,
    *,
    profile_id: str = "",
    directory: str | Path | None = None,
) -> str:
    """Stable fingerprint for a company rule and its extractor implementation."""
    try:
        resolved = resolve_rule(
            company,
            year,
            profile_id=profile_id,
            directory=directory,
        )
    except Exception:
        resolved = None
    if resolved is None:
        return "generic"
    target_dir = Path(directory) if directory is not None else RULES_DIR
    parts: list[str] = [
        resolved.canonical,
        resolved.rule_version,
        resolved.source,
        resolved.extractor,
    ]
    rule_path = target_dir / resolved.source if resolved.source else None
    if rule_path is not None and rule_path.is_file():
        stat = rule_path.stat()
        parts.extend((str(stat.st_mtime_ns), str(stat.st_size)))
    if resolved.extractor:
        module_path = (
            Path(__file__).resolve().parent
            / "company_extractors"
            / f"{resolved.extractor}.py"
        )
        if module_path.is_file():
            stat = module_path.stat()
            parts.extend((str(stat.st_mtime_ns), str(stat.st_size)))
        for helper in sorted(module_path.parent.glob("_*.py")):
            stat = helper.stat()
            parts.extend((helper.name, str(stat.st_mtime_ns), str(stat.st_size)))
    normalizer_path = Path(__file__).resolve().parent / "solvency_normalizer.py"
    if normalizer_path.is_file():
        stat = normalizer_path.stat()
        parts.extend((normalizer_path.name, str(stat.st_mtime_ns), str(stat.st_size)))
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


def resolve_rule(
    company: object,
    year: object = None,
    *,
    profile_id: str = "",
    directory: str | Path | None = None,
) -> ResolvedRule | None:
    """按封面公司名解析规则；未配置的公司返回 ``None``（沿用默认）。"""
    normalized = _normalize_company(company)
    if not normalized:
        return None
    try:
        catalog = load_company_rules(directory)
    except ExtractionConfigError as exc:  # 单个坏规则不应拖垮整批
        warnings.warn(f"公司规则加载失败，已降级跳过坏文件：{exc}", RuntimeWarning)
        catalog = load_company_rules(directory, strict=False, force=True)
    matched: CompanyRule | None = None
    for rule in catalog.values():
        if profile_id and rule.profile_id and rule.profile_id != profile_id:
            continue
        candidates = {_normalize_company(rule.canonical)}
        candidates.update(_normalize_company(alias) for alias in rule.aliases)
        if normalized in candidates:
            matched = rule
            break
    if matched is None:
        return None

    try:
        year_value = int(year) if year not in (None, "") else None
    except (TypeError, ValueError):
        year_value = None

    warnings: list[str] = []
    active_version: StructureVersion | None = None
    if year_value is None:
        warnings.append("YEAR_UNKNOWN：封面未识别报告年度，仅应用公司级主体规则。")
    else:
        for version in matched.structure_versions:
            if version.since <= year_value:
                if active_version is None or version.since > active_version.since:
                    active_version = version

    tables: dict[str, dict[str, Any]] = {
        table_id: dict(config) for table_id, config in matched.tables.items()
    }
    contracts: dict[str, dict[str, Any]] = {
        table_id: dict(config)
        for table_id, config in matched.layout_contracts.items()
    }
    if active_version is not None:
        for table_id, config in active_version.tables.items():
            merged = dict(tables.get(table_id) or {})
            merged.update(config)
            tables[table_id] = merged
        for table_id, config in active_version.layout_contracts.items():
            merged_contract = dict(contracts.get(table_id) or {})
            merged_contract.update(config)
            contracts[table_id] = merged_contract

    return ResolvedRule(
        canonical=matched.canonical,
        source=matched.source,
        rule_version=matched.rule_version,
        structure_version=(
            active_version.since if active_version is not None else None
        ),
        tables=tables,
        warnings=tuple(warnings),
        extractor=matched.extractor,
        layout_contracts=contracts,
    )


def _rule_table_for(
    table_id: str,
    resolved: ResolvedRule,
    aliases: Mapping[str, str],
) -> Mapping[str, Any] | None:
    if table_id in resolved.tables:
        return resolved.tables[table_id]
    primary = aliases.get(table_id, table_id)
    if primary in resolved.tables:
        return resolved.tables[primary]
    for key, config in resolved.tables.items():
        if aliases.get(key, key) == primary:
            return config
    return None


def _layout_contract_for(
    table_id: str,
    resolved: ResolvedRule,
    aliases: Mapping[str, str],
) -> Mapping[str, Any] | None:
    contracts = resolved.layout_contracts or {}
    if table_id in contracts:
        return contracts[table_id]
    primary = aliases.get(table_id, table_id)
    if primary in contracts:
        return contracts[primary]
    for key, contract in contracts.items():
        if aliases.get(key, key) == primary:
            return contract
    return None


def apply_rule_to_tables(
    table_configs: Sequence[Mapping[str, Any]],
    resolved: ResolvedRule | None,
) -> list[dict[str, Any]]:
    """把公司规则合并进 Profile 表配置，返回新列表（不修改原对象）。"""
    if resolved is None:
        return [dict(config) for config in table_configs]
    table_maps = load_table_maps()
    aliases = table_maps["aliases"]
    out: list[dict[str, Any]] = []
    for config in table_configs:
        merged = dict(config)
        table_id = str(config.get("table_id") or "").strip()
        rule_config = _rule_table_for(table_id, resolved, aliases) if table_id else None
        if rule_config:
            # 先回填全局 signature_terms，规则里的显式值随后覆盖。
            merged = build_table_configs([merged], maps=table_maps)[0]
            for field_name in _DIRECT_FIELDS:
                if field_name in rule_config:
                    merged[field_name] = rule_config[field_name]
            for rule_field, config_field in _RULE_TO_CONFIG_FIELDS.items():
                if rule_field in rule_config:
                    merged[config_field] = list(
                        _as_terms(rule_config[rule_field])
                    )
            merged["_extraction_rule"] = {
                "source": resolved.source,
                "canonical": resolved.canonical,
                "structure_version": resolved.structure_version,
                "page_match": rule_config.get("page_match", "prefer"),
                "extractor": resolved.extractor,
            }
        contract = (
            _layout_contract_for(table_id, resolved, aliases)
            if table_id
            else None
        )
        if contract:
            merged["_layout_contract"] = dict(contract)
        out.append(merged)
    return out


def rule_summary(resolved: ResolvedRule | None) -> str:
    if resolved is None:
        return ""
    parts = [resolved.label]
    if resolved.warnings:
        parts.extend(resolved.warnings)
    return "；".join(parts)


def resolve_rule_for_pdf(
    pdf_bytes: bytes,
    *,
    company_hint: str = "",
    profile_id: str = "",
    directory: str | Path | None = None,
    text_context: Any = None,
) -> tuple[ResolvedRule | None, dict[str, Any]]:
    """按 PDF 封面解析公司规则，返回 (rule, metadata)。

    批量提取的 matches 里不带报告年度，这里统一从封面补一次；
    解析失败不阻断，返回 (None, {})。
    """
    try:
        from .solvency_pdf_locator import extract_report_metadata

        metadata = extract_report_metadata(
            pdf_bytes,
            text_context=text_context,
        )
    except Exception:
        metadata = {}
    rule = resolve_rule(
        metadata.get("公司") or company_hint,
        metadata.get("报告年度"),
        profile_id=profile_id,
        directory=directory,
    )
    return rule, metadata


@dataclass(frozen=True)
class PageCheck:
    """STEP1 定位结果与公司 YAML 页码的比对结论。"""

    table_id: str
    table_name: str
    actual_pages: tuple[int, ...]
    expected_pages: tuple[int, ...]
    window: tuple[int, int] | None
    status: str
    message: str

    @property
    def passed(self) -> bool:
        return self.status in {"通过", "偏移"}

    def to_row(self) -> dict[str, object]:
        return {
            "目标表": self.table_name or self.table_id,
            "实际页码": "、".join(str(page) for page in self.actual_pages) or "—",
            "预期页码": "、".join(str(page) for page in self.expected_pages) or "未配置",
            "容差窗口": (
                f"{self.window[0]}-{self.window[1]}"
                if self.window is not None
                else "—"
            ),
            "对比结果": self.status,
            "说明": self.message,
        }


def _rule_config_for(table_id: str, resolved_rule: ResolvedRule | None) -> dict[str, Any]:
    if resolved_rule is None:
        return {}
    tables = getattr(resolved_rule, "tables", {}) or {}
    if table_id in tables:
        return dict(tables[table_id])
    aliases = load_table_maps()["aliases"]
    primary = aliases.get(table_id, table_id)
    if primary in tables:
        return dict(tables[primary])
    for key, config in tables.items():
        if aliases.get(key, key) == primary:
            return dict(config)
    return {}


def compare_pages_with_rule(
    matches: Sequence[Any] | None,
    resolved_rule: ResolvedRule | None,
) -> list[PageCheck]:
    """把定位页码与公司 YAML 的 expected_pages 逐表比对。

    状态：通过 / 偏移（窗口内） / 超出窗口 / 未定位 / 未配置。
    只有“通过/偏移”算 passed；未配置的表需要人工确认。
    """
    checks: list[PageCheck] = []
    for match in matches or []:
        table_id = str(getattr(match, "table_id", "") or "")
        table_name = str(getattr(match, "table_name", "") or table_id)
        actual = tuple(
            int(page) for page in (getattr(match, "pages", []) or []) if int(page) > 0
        )
        config = _rule_config_for(table_id, resolved_rule)
        expected = tuple(
            int(page) for page in (config.get("expected_pages") or []) if int(page) > 0
        )
        try:
            tolerance = max(0, int(config.get("tolerance", DEFAULT_TOLERANCE)))
        except (TypeError, ValueError):
            tolerance = DEFAULT_TOLERANCE

        if not expected:
            checks.append(
                PageCheck(
                    table_id,
                    table_name,
                    actual,
                    (),
                    None,
                    "未配置",
                    "公司 YAML 未配置 expected_pages，需人工确认",
                )
            )
            continue

        window = (max(1, min(expected) - tolerance), max(expected) + tolerance)
        if not actual:
            status = "未定位"
            message = f"预期第 {'、'.join(map(str, expected))} 页，但未定位到目标表"
        elif any(window[0] <= page <= window[1] for page in actual):
            if set(actual) == set(expected):
                status = "通过"
                message = f"实际 {actual} 与预期 {expected} 完全一致"
            else:
                status = "偏移"
                message = (
                    f"实际 {actual} 在窗口 {window[0]}-{window[1]} 内"
                    f"（预期 {expected}）"
                )
        else:
            status = "超出窗口"
            message = (
                f"实际 {actual} 超出窗口 {window[0]}-{window[1]}"
                f"（预期 {expected}）"
            )
        checks.append(
            PageCheck(table_id, table_name, actual, expected, window, status, message)
        )
    return checks


def page_check_passed(
    checks: Sequence[PageCheck],
    matches: Sequence[Any] | None = None,
) -> bool:
    """所有表都通过（允许窗口内偏移）且无定位冲突时才自动确认。"""
    if not checks:
        return False
    if any(getattr(match, "review_required", False) for match in (matches or [])):
        return False
    return all(check.passed for check in checks)
