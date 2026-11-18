"""规则法提取的目标表定义：内置默认 + `config/extraction/tables.yaml` 覆盖。

设计约定（对应 plans/refactor-plan-track-and-rule-config.md 批次 1）：

* 内置默认值即迁移自 ``solvency_table_extractor`` 的原始常量，改造前后行为一致；
* YAML 只写需要改的字段，缺失字段回退内置默认；
* ``table_id`` 支持别名（例如 Profile 的 ``NON_LIFE_OPERATING_METRICS``
  指向内置 ``OPERATING_METRICS``），返回的映射同时包含主键与别名；
* 配置错误在加载时直接抛 ``ExtractionConfigError``，不静默降级；
* 提供 ``config_signature`` 供调用方判断配置是否变更，实现无需重启的热更新。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "extraction" / "tables.yaml"
)


class ExtractionConfigError(ValueError):
    """Raised when the extraction table config is invalid."""


# ---------- 内置默认（迁移自 solvency_table_extractor.py 原始常量） ----------
_BUILTIN_NAMES: dict[str, str] = {
    "SOLVENCY_MAIN": "偿付能力充足率指标",
    "LIQUIDITY_RISK": "流动性风险指标",
    "LIQUIDITY_MONITORING": "流动性风险监测指标",
    "OPERATING_METRICS": "主要经营指标",
    "THREE_YEAR_INVESTMENT_RETURN": "近三年投资收益率",
    "ACTUAL_CAPITAL": "实际资本表",
    "RECOGNIZED_ASSETS": "认可资产表",
    "RECOGNIZED_LIABILITIES": "认可负债表",
    "MINIMUM_CAPITAL": "最低资本表",
}

_BUILTIN_SIGNATURES: dict[str, tuple[str, ...]] = {
    "SOLVENCY_MAIN": ("认可资产", "综合偿付能力充足率"),
    "LIQUIDITY_RISK": ("LCR1", "未来3个月"),
    "LIQUIDITY_MONITORING": ("综合退保率", "融资杠杆比例"),
    "OPERATING_METRICS": ("保险业务收入",),
    "THREE_YEAR_INVESTMENT_RETURN": ("投资收益率", "综合投资收益率"),
    "ACTUAL_CAPITAL": ("核心一级资本", "实际资本合计"),
    "RECOGNIZED_ASSETS": ("投资资产", "认可资产合计"),
    "RECOGNIZED_LIABILITIES": ("准备金负债", "认可负债合计"),
    "MINIMUM_CAPITAL": ("量化风险最低资本", "最低资本"),
}

_BUILTIN_EXCLUSIONS: dict[str, tuple[str, ...]] = {
    "SOLVENCY_MAIN": ("监管指标名称", "流动性覆盖率", "LCR1", "LCR2", "LCR3"),
    "OPERATING_METRICS": (
        "流动性风险监测指标",
        "流动性风险监管指标",
        "LCR1",
        "前五大产品的信息",
        "报告期内签单保费占前五位的产品",
        "签单保费占前五位的产品",
    ),
    "ACTUAL_CAPITAL": ("认可资产表", "认可负债表", "最低资本表"),
    "THREE_YEAR_INVESTMENT_RETURN": ("流动性覆盖率", "最低资本表"),
    "MINIMUM_CAPITAL": ("认可资产表", "认可负债表"),
}

_BUILTIN_HEADERS: dict[str, tuple[str, ...]] = {
    "SOLVENCY_MAIN": (
        "项目",
        "指标名称",
        "本季度数",
        "本季度末数",
        "上季度可比数",
        "上季度末数",
        "基本情景下的下季度预测数",
        "下季度预测数",
        "下季度末预测数",
    ),
    "OPERATING_METRICS": ("指标名称", "本季度", "本年累计"),
    "ACTUAL_CAPITAL": ("行次", "期末数", "期初数"),
    "THREE_YEAR_INVESTMENT_RETURN": ("投资收益率", "近三年"),
    "MINIMUM_CAPITAL": ("行次", "期末数", "期初数"),
}

_BUILTIN_START_MARKERS: dict[str, tuple[str, ...]] = {
    "SOLVENCY_MAIN": ("指标名称", "认可资产"),
    "OPERATING_METRICS": (
        "主要经营指标",
        "效益类指标",
        "规模类指标",
        "品质类指标",
        "保险业务收入",
    ),
    "ACTUAL_CAPITAL": (
        "实际资本表",
        "实际资本明细表",
        "实际资本汇总",
        "核心资本",
        "财务报表资产总额",
        "认可资产",
        "核心一级资本",
    ),
    "THREE_YEAR_INVESTMENT_RETURN": (
        "近三年（综合）投资收益率",
        "近三年平均投资收益率",
    ),
    "MINIMUM_CAPITAL": (
        "最低资本表",
        "最低资本",
        "量化风险最低资本",
        "保险风险最低资本汇总",
        "市场风险最低资本汇总",
        "信用风险最低资本汇总",
    ),
}

_BUILTIN_END_MARKERS: dict[str, tuple[str, ...]] = {
    "SOLVENCY_MAIN": ("监管指标名称", "流动性覆盖率", "LCR1"),
    "OPERATING_METRICS": (
        "流动性风险监测指标",
        "流动性风险监管指标",
        "近三年综合投资收益率",
        "近三年（综合）投资收益率",
    ),
    "ACTUAL_CAPITAL": (
        "（三）认可资产",
        "(三)认可资产",
        "认可资产表",
        "认可负债表",
        "最低资本表",
    ),
    "THREE_YEAR_INVESTMENT_RETURN": ("实际资本表", "认可资产表", "最低资本表"),
    "MINIMUM_CAPITAL": (),
}

_BUILTIN_ALIASES: dict[str, str] = {
    "NON_LIFE_OPERATING_METRICS": "OPERATING_METRICS",
}

_BUILTIN_STRATEGIES: tuple[tuple[str, dict[str, Any]], ...] = (
    (
        "线框识别",
        {
            "vertical_strategy": "lines",
            "horizontal_strategy": "lines",
            "snap_tolerance": 4,
            "join_tolerance": 4,
            "intersection_tolerance": 5,
        },
    ),
    (
        "文字对齐识别",
        {
            "vertical_strategy": "text",
            "horizontal_strategy": "text",
            "min_words_vertical": 2,
            "min_words_horizontal": 1,
        },
    ),
)

_TERM_FIELDS = {
    "signature_terms": _BUILTIN_SIGNATURES,
    "exclude_terms": _BUILTIN_EXCLUSIONS,
    "header_terms": _BUILTIN_HEADERS,
    "start_markers": _BUILTIN_START_MARKERS,
    "end_markers": _BUILTIN_END_MARKERS,
}

_CACHE: dict[str, Any] = {"key": None, "signature": None, "maps": None}


def config_path(path: str | Path | None = None) -> Path:
    return Path(path) if path is not None else CONFIG_PATH


def config_signature(path: str | Path | None = None) -> tuple[int, int] | None:
    """Return ``(mtime_ns, size)`` for change detection, or ``None`` if absent."""
    target = config_path(path)
    try:
        stat = target.stat()
    except OSError:
        return None
    return int(stat.st_mtime_ns), int(stat.st_size)


def _as_terms(value: Any, *, field: str, table_id: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise ExtractionConfigError(
            f"{table_id}.{field} 必须是字符串或字符串列表。"
        )
    terms: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            terms.append(text)
    return tuple(dict.fromkeys(terms))


def _load_yaml(path: Path) -> Mapping[str, Any]:
    if not path.exists():
        return {}
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - requirements 已包含 pyyaml
        raise ExtractionConfigError("缺少 PyYAML 依赖，无法读取提取配置。") from exc
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ExtractionConfigError(f"无法解析 {path.name}：{exc}") from exc
    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        raise ExtractionConfigError("tables.yaml 顶层必须是映射。")
    return payload


def _build_maps(path: Path) -> dict[str, Any]:
    payload = _load_yaml(path)
    raw_tables = payload.get("tables") or {}
    if not isinstance(raw_tables, Mapping):
        raise ExtractionConfigError("tables.yaml 的 tables 必须是映射。")
    raw_aliases = payload.get("aliases") or {}
    if not isinstance(raw_aliases, Mapping):
        raise ExtractionConfigError("tables.yaml 的 aliases 必须是映射。")

    names = dict(_BUILTIN_NAMES)
    aliases: dict[str, str] = dict(_BUILTIN_ALIASES)
    term_maps: dict[str, dict[str, tuple[str, ...]]] = {
        field: dict(source) for field, source in _TERM_FIELDS.items()
    }

    for raw_id, raw_entry in raw_tables.items():
        table_id = str(raw_id or "").strip()
        if not table_id:
            raise ExtractionConfigError("tables.yaml 存在空 table_id。")
        if raw_entry is None:
            raw_entry = {}
        if not isinstance(raw_entry, Mapping):
            raise ExtractionConfigError(f"{table_id} 的定义必须是映射。")
        entry = dict(raw_entry)
        names[table_id] = str(entry.get("table_name") or names.get(table_id, table_id))
        for field, mapping in term_maps.items():
            if field in entry:
                mapping[table_id] = _as_terms(
                    entry.get(field), field=field, table_id=table_id
                )
        # 表内 aliases 表示“该表还叫这些 ID”，映射到自身。
        for alias in _as_terms(
            entry.get("aliases"), field="aliases", table_id=table_id
        ):
            aliases.setdefault(alias, table_id)

    aliases_for_targets: dict[str, str] = dict(_BUILTIN_ALIASES)
    for alias, target in raw_aliases.items():
        alias_text = str(alias or "").strip()
        target_text = str(target or "").strip()
        if alias_text and target_text:
            aliases_for_targets[alias_text] = target_text
    aliases.update(aliases_for_targets)
    unknown_targets = sorted(
        {target for target in aliases.values() if target not in names}
    )
    if unknown_targets:
        raise ExtractionConfigError(
            "aliases 指向了未知 table_id：" + "、".join(unknown_targets)
        )

    # 别名与主键共享同一份定义，保证 Profile 的 table_id 也能取到内置字段。
    for alias, target in aliases.items():
        if alias in names:
            continue
        names[alias] = names[target]
        for mapping in term_maps.values():
            if target in mapping:
                mapping[alias] = mapping[target]

    strategies_raw = payload.get("extraction_strategies")
    if strategies_raw is None:
        strategies = _BUILTIN_STRATEGIES
    else:
        if not isinstance(strategies_raw, (list, tuple)) or not strategies_raw:
            raise ExtractionConfigError("extraction_strategies 必须是非空列表。")
        parsed: list[tuple[str, dict[str, Any]]] = []
        for index, item in enumerate(strategies_raw, start=1):
            if not isinstance(item, Mapping):
                raise ExtractionConfigError(
                    f"extraction_strategies 第 {index} 项必须是映射。"
                )
            name = str(item.get("name") or "").strip()
            settings = item.get("settings")
            if not name or not isinstance(settings, Mapping):
                raise ExtractionConfigError(
                    f"extraction_strategies 第 {index} 项缺少 name 或 settings。"
                )
            parsed.append((name, dict(settings)))
        strategies = tuple(parsed)

    return {
        "names": names,
        "aliases": aliases,
        "signatures": term_maps["signature_terms"],
        "exclusions": term_maps["exclude_terms"],
        "headers": term_maps["header_terms"],
        "start_markers": term_maps["start_markers"],
        "end_markers": term_maps["end_markers"],
        "strategies": strategies,
    }


def load_table_maps(
    path: str | Path | None = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """加载并缓存表级定义。返回的字典可直接原地更新（热更新用）。"""
    target = config_path(path)
    signature = config_signature(target)
    cache_key = str(target.resolve())
    if (
        not force
        and _CACHE["maps"] is not None
        and _CACHE["key"] == cache_key
        and _CACHE["signature"] == signature
    ):
        return _CACHE["maps"]
    maps = _build_maps(target)
    _CACHE["key"] = cache_key
    _CACHE["signature"] = signature
    _CACHE["maps"] = maps
    return maps


def clear_cache() -> None:
    _CACHE["key"] = None
    _CACHE["signature"] = None
    _CACHE["maps"] = None
