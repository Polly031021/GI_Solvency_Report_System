"""公司级提取规则：一家公司一份 YAML，跨年度复用。

规则模型（对应 plans/refactor-plan-track-and-rule-config.md 批次 2）：

* 路由键是封面识别出的公司名（别名归一）+ 报告年度（仅用于选结构版本）；
* 页码用 ``expected_pages`` + ``tolerance`` 形成搜索窗口，再由表标题锚点验证；
* 只有表结构大改时才在 ``structure_versions`` 追加一条，不按年维护；
* 未找到规则返回 ``None``，调用方沿用 Profile 默认配置（零回归）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .extraction_config import ExtractionConfigError, load_table_maps

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
}

_CACHE: dict[str, Any] = {"signature": None, "rules": None}


@dataclass(frozen=True)
class StructureVersion:
    since: int
    notes: str
    tables: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)


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


@dataclass(frozen=True)
class ResolvedRule:
    canonical: str
    source: str
    rule_version: str
    structure_version: int | None
    tables: Mapping[str, Mapping[str, Any]]
    warnings: tuple[str, ...] = ()
    extractor: str = ""

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
        tables[table_id] = config
    return tables


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


def load_company_rules(
    directory: str | Path | None = None,
    *,
    force: bool = False,
) -> dict[str, CompanyRule]:
    """加载 ``config/extraction/companies/*.yaml``，按公司名建索引。"""
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
    if target.is_dir():
        for path in sorted(target.glob("*.yaml")):
            rule = _parse_rule(path, known_tables)
            if rule.canonical in rules:
                raise ExtractionConfigError(
                    f"公司规则重复：{rule.canonical}"
                )
            names = [rule.canonical, *rule.aliases]
            for name in names:
                normalized = _normalize_company(name)
                if not normalized:
                    continue
                if normalized in seen_names and seen_names[normalized] != rule.canonical:
                    raise ExtractionConfigError(
                        f"公司别名冲突：{name} 同时属于 "
                        f"{seen_names[normalized]} 和 {rule.canonical}"
                    )
                seen_names[normalized] = rule.canonical
            rules[rule.canonical] = rule
    _CACHE["signature"] = (cache_key, signature)
    _CACHE["rules"] = rules
    return rules


def clear_cache() -> None:
    _CACHE["signature"] = None
    _CACHE["rules"] = None


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
    catalog = load_company_rules(directory)
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
    if active_version is not None:
        for table_id, config in active_version.tables.items():
            merged = dict(tables.get(table_id) or {})
            merged.update(config)
            tables[table_id] = merged

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


def apply_rule_to_tables(
    table_configs: Sequence[Mapping[str, Any]],
    resolved: ResolvedRule | None,
) -> list[dict[str, Any]]:
    """把公司规则合并进 Profile 表配置，返回新列表（不修改原对象）。"""
    if resolved is None:
        return [dict(config) for config in table_configs]
    aliases = load_table_maps()["aliases"]
    out: list[dict[str, Any]] = []
    for config in table_configs:
        merged = dict(config)
        table_id = str(config.get("table_id") or "").strip()
        rule_config = _rule_table_for(table_id, resolved, aliases) if table_id else None
        if rule_config:
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
        out.append(merged)
    return out


def rule_summary(resolved: ResolvedRule | None) -> str:
    if resolved is None:
        return ""
    parts = [resolved.label]
    if resolved.warnings:
        parts.extend(resolved.warnings)
    return "；".join(parts)
