from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import pandas as pd


@dataclass(frozen=True)
class CompanyAliasDefinition:
    code: str
    standard_name: str
    company_type: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompanyIdentity:
    original_name: str
    standard_name: str
    company_code: str
    company_type: str
    matched_by: str


@dataclass(frozen=True)
class CompanyMaster:
    """公司分类主数据：同业分类与公司类型。"""

    peer_group_map: Mapping[str, str]
    company_type_map: Mapping[str, str]
    default_peer_group: str = "其他"
    default_company_type: str = "财险"


COMPANY_ALIASES = (
    CompanyAliasDefinition(
        code="LIFE_ORIENTAL_JIAFU",
        standard_name="东方嘉富人寿",
        company_type="寿险",
        aliases=("中韩人寿",),
    ),
)

# 财险清单使用短名称，PDF 文件名/正文常用这些简称或全称片段。
_NON_LIFE_SHORT_ALIASES: dict[str, tuple[str, ...]] = {
    "人保财险": ("中国人民财产保险", "人保", "人保财险", "人保产险"),
    "平安产险": ("中国平安财产保险", "平安财险", "平安产险"),
    "太保财险": ("中国太平洋财产保险", "太平洋财险", "太保财险", "太保产险"),
    "国寿财产": ("中国人寿财产保险", "国寿财险", "国寿财产"),
    "众安财产": ("众安在线财产保险", "众安保险", "众安财产", "众安产险"),
    "英大财产": ("英大泰和财产保险", "英大财险", "英大财产"),
    "大地财产": ("中国大地财产保险", "大地财险", "大地财产"),
    "中华联合": ("中华联合财产保险", "中华财险", "中华联合"),
    "太平财险": ("太平财产保险", "太平财险"),
    "阳光财产": ("阳光财产保险", "阳光财险", "阳光财产", "阳光产险"),
}


def canonical_company_name(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    return re.sub(r"\s+", "", text)


def _company_code(standard_name: str) -> str:
    digest = hashlib.sha1(standard_name.encode("utf-8")).hexdigest()[:12].upper()
    return f"COMPANY_{digest}"


def _alias_lookup() -> dict[str, CompanyAliasDefinition]:
    lookup: dict[str, CompanyAliasDefinition] = {}
    for definition in COMPANY_ALIASES:
        for name in (definition.standard_name, *definition.aliases):
            lookup[canonical_company_name(name)] = definition
    return lookup


COMPANY_ALIAS_LOOKUP = _alias_lookup()


def load_peer_group_config(path: str | Path) -> tuple[dict[str, str], str]:
    """Load the auditable company-to-peer-group master mapping."""
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    groups = payload.get("groups", {})
    if not isinstance(groups, dict):
        raise ValueError("同业分类配置中的 groups 必须是对象。")

    mapping: dict[str, str] = {}
    owners: dict[str, str] = {}
    for peer_group, company_names in groups.items():
        label = str(peer_group or "").strip()
        if not label or not isinstance(company_names, list):
            raise ValueError("同业分类配置必须使用非空分类名称和公司名称列表。")
        for company_name in company_names:
            name = str(company_name or "").strip()
            key = canonical_company_name(name)
            if not key:
                continue
            if key in mapping and mapping[key] != label:
                raise ValueError(
                    f"公司“{name}”同时属于“{owners[key]}”和“{label}”，请修正同业分类配置。"
                )
            mapping[key] = label
            owners[key] = label

    default_peer_group = str(payload.get("default_peer_group", "其他") or "其他").strip()
    return mapping, default_peer_group


def resolve_peer_group(
    value: Any,
    peer_group_map: Mapping[str, str] | None,
    fallback_peer_group: str = "其他",
) -> str:
    """Resolve a peer group by standard name, historical alias, or legal-name containment."""
    fallback = str(fallback_peer_group or "").strip()
    if not peer_group_map:
        return fallback

    normalized_map = {
        canonical_company_name(name): str(peer_group or "").strip()
        for name, peer_group in peer_group_map.items()
        if canonical_company_name(name) and str(peer_group or "").strip()
    }
    original_name = str(value or "").strip()
    identity = resolve_company_identity(original_name)
    definition = COMPANY_ALIAS_LOOKUP.get(canonical_company_name(original_name))
    candidate_names = [original_name, identity.standard_name]
    if definition:
        candidate_names.extend(definition.aliases)

    candidate_keys = [canonical_company_name(name) for name in candidate_names if name]
    for key in candidate_keys:
        if normalized_map.get(key):
            return normalized_map[key]

    matches: list[tuple[int, str]] = []
    for candidate_key in candidate_keys:
        for mapped_key, peer_group in normalized_map.items():
            if len(mapped_key) >= 4 and (
                mapped_key in candidate_key or candidate_key in mapped_key
            ):
                matches.append((len(mapped_key), peer_group))
    if not matches:
        return fallback
    longest = max(length for length, _ in matches)
    longest_groups = {group for length, group in matches if length == longest}
    return next(iter(longest_groups)) if len(longest_groups) == 1 else fallback


def _normalize_url(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text.rstrip("/")


def _normalize_company_type(value: Any, default: str = "财险") -> str:
    text = str(value or "").strip()
    if not text:
        return default
    if any(token in text for token in ("财险", "财产保险", "财产险", "产险")):
        return "财险"
    if "健康" in text:
        return "健康险"
    if "养老" in text:
        return "养老险"
    if "寿险" in text or "人寿" in text:
        return "寿险"
    return text


def load_company_master_workbook(
    path: str | Path,
    *,
    company_column: str = "公司",
    group_column: str = "公司类别",
    type_column: str = "业务类型",
    default_peer_group: str = "其他",
    default_company_type: str = "财险",
    alias_workbook: str | Path | None = None,
) -> CompanyMaster:
    """读取公司分类工作簿，并可选合并全称/别名工作簿。

    ``财险公司列表.xlsx`` 使用短名称（如“人保财险”），而 PDF 文件名或
    报告正文可能使用全称。``alias_workbook`` 通过披露地址把全称关联到
    同一家公司，避免短名称与全称被分到“其他”。
    """
    frame = pd.read_excel(path).fillna("")
    if company_column not in frame.columns or group_column not in frame.columns:
        raise ValueError(
            f"公司分类工作簿缺少列：{company_column} / {group_column}"
        )

    peer_group_map: dict[str, str] = {}
    company_type_map: dict[str, str] = {}
    group_by_url: dict[str, str] = {}
    for _, row in frame.iterrows():
        company_name = str(row.get(company_column, "") or "").strip()
        group = str(row.get(group_column, "") or "").strip()
        if not company_name or not group:
            continue
        peer_group_map[company_name] = group
        company_type_map[company_name] = _normalize_company_type(
            row.get(type_column, ""),
            default=default_company_type,
        )
        url = _normalize_url(
            row.get("报告披露地址", row.get("偿付能力披露地址", ""))
        )
        if url:
            group_by_url[url] = group

    alias_path = Path(alias_workbook) if alias_workbook else None
    if alias_path is not None and alias_path.exists():
        alias_frame = pd.read_excel(alias_path).fillna("")
        for _, row in alias_frame.iterrows():
            company_name = str(row.get("公司", "") or "").strip()
            url = _normalize_url(
                row.get(
                    "偿付能力披露地址",
                    row.get("报告披露地址", ""),
                )
            )
            group = group_by_url.get(url)
            if not company_name or not group:
                continue
            peer_group_map.setdefault(company_name, group)
            company_type_map.setdefault(company_name, default_company_type)

    for short_name, aliases in _NON_LIFE_SHORT_ALIASES.items():
        group = peer_group_map.get(short_name)
        if not group:
            continue
        for alias in aliases:
            peer_group_map.setdefault(alias, group)
            company_type_map.setdefault(alias, default_company_type)

    if not peer_group_map:
        raise ValueError(f"公司分类工作簿没有可用记录：{path}")
    return CompanyMaster(
        peer_group_map=peer_group_map,
        company_type_map=company_type_map,
        default_peer_group=str(default_peer_group or "其他").strip(),
        default_company_type=str(default_company_type or "财险").strip(),
    )


def resolve_company_identity(
    value: Any,
    company_type_map: Mapping[str, str] | None = None,
    fallback_company_type: str = "未分类",
) -> CompanyIdentity:
    original_name = str(value or "").strip()
    key = canonical_company_name(original_name)
    definition = COMPANY_ALIAS_LOOKUP.get(key)
    standard_name = definition.standard_name if definition else original_name
    company_code = definition.code if definition else _company_code(standard_name)

    normalized_type_map = {
        canonical_company_name(name): str(company_type or "").strip()
        for name, company_type in (company_type_map or {}).items()
        if canonical_company_name(name)
    }
    candidate_names = [original_name, standard_name]
    if definition:
        candidate_names.extend(definition.aliases)
    mapped_type = next(
        (
            normalized_type_map[canonical_company_name(name)]
            for name in candidate_names
            if normalized_type_map.get(canonical_company_name(name))
        ),
        "",
    )
    fallback = str(fallback_company_type or "").strip()
    company_type = (
        mapped_type
        or (fallback if fallback and fallback != "未分类" else "")
        or (definition.company_type if definition else "")
        or "未分类"
    )
    return CompanyIdentity(
        original_name=original_name,
        standard_name=standard_name,
        company_code=company_code,
        company_type=company_type,
        matched_by="历史名称映射" if definition and key != canonical_company_name(standard_name) else "标准名称",
    )


def apply_company_identities(
    frame: pd.DataFrame,
    company_type_map: Mapping[str, str] | None = None,
    peer_group_map: Mapping[str, str] | None = None,
    default_peer_group: str = "",
) -> pd.DataFrame:
    result = frame.copy()
    if result.empty or "公司" not in result.columns:
        return result
    company = result.get("公司", pd.Series("", index=result.index)).fillna("").astype(str).str.strip()
    company_type = (
        result.get("公司类型", pd.Series("", index=result.index))
        .fillna("")
        .astype(str)
        .str.strip()
    )
    company_code = (
        result.get("公司统一编码", pd.Series("", index=result.index))
        .fillna("")
        .astype(str)
        .str.strip()
    )
    industry_mask = (
        company.eq("行业合计")
        | company_type.eq("行业合计")
        | company_code.str.startswith("INDUSTRY_")
    )
    if industry_mask.any():
        result.loc[industry_mask, "原始公司名称"] = "行业合计"
        result.loc[industry_mask, "标准公司名称"] = "行业合计"
        result.loc[industry_mask, "公司"] = "行业合计"
        result.loc[industry_mask, "公司统一编码"] = company_code.loc[industry_mask].where(
            company_code.loc[industry_mask].ne(""),
            "INDUSTRY_TOTAL",
        )
        result.loc[industry_mask, "公司类型"] = "行业合计"

    original = (
        result.get("原始公司名称", pd.Series("", index=result.index))
        .fillna("")
        .astype(str)
        .str.strip()
    )
    original = original.where(original.ne(""), company)
    fallback_type = company_type.where(company_type.ne(""), "未分类")

    # Resolve each distinct (name, fallback type) once instead of once per row.
    key_frame = pd.DataFrame(
        {"original": original, "fallback": fallback_type},
        index=result.index,
    )
    unique_keys = key_frame.drop_duplicates().itertuples(index=False, name=None)
    identity_map = {
        (str(name), str(fallback)): resolve_company_identity(
            name,
            company_type_map,
            fallback_company_type=fallback,
        )
        for name, fallback in unique_keys
    }
    identities = [
        identity_map.get((str(name), str(fallback)))
        for name, fallback in zip(original, fallback_type)
    ]
    non_industry = ~industry_mask
    if non_industry.any():
        non_industry_values = [
            item for item, keep in zip(identities, non_industry) if keep
        ]
        result.loc[non_industry, "原始公司名称"] = [
            item.original_name for item in non_industry_values
        ]
        result.loc[non_industry, "标准公司名称"] = [
            item.standard_name for item in non_industry_values
        ]
        result.loc[non_industry, "公司统一编码"] = [
            item.company_code for item in non_industry_values
        ]
        result.loc[non_industry, "公司"] = [
            item.standard_name for item in non_industry_values
        ]
        result.loc[non_industry, "公司类型"] = [
            item.company_type for item in non_industry_values
        ]

    if peer_group_map:
        peer_group = (
            result.get("同业分类", pd.Series("", index=result.index))
            .fillna("")
            .astype(str)
            .str.strip()
        )
        missing_peer = peer_group.eq("") & ~industry_mask
        if missing_peer.any():
            peer_names = original.loc[missing_peer].drop_duplicates()
            peer_lookup = {
                str(name): resolve_peer_group(
                    name,
                    peer_group_map,
                    fallback_peer_group=default_peer_group,
                )
                for name in peer_names
            }
            result.loc[missing_peer, "同业分类"] = original.loc[missing_peer].map(
                lambda value: peer_lookup.get(str(value), default_peer_group)
            )
    return result
