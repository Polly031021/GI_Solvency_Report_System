"""Recompute STEP7 metrics that the integrated workbook marks as “系统计算”.

The integrated workbook (root ``偿付能力行业集成数据_公司名称与分类规范化.xlsx``,
sheet ``标准数据``) stores a ``来源类型`` column.  Rows whose type is
**系统计算** are calculated results; when the raw disclosed inputs are
corrected those rows keep the old Excel value, so STEP7 recalculates them from
the current base indicators before drawing charts.

Only the metric codes below and only rows explicitly marked 系统计算 are
touched.  Everything else (披露 / 报告提取 / 人工补录 values, including ratios
such as 综合偿付能力充足率, 综合成本率 and 净资产收益率 that this workbook
stores as disclosed figures) is left exactly as uploaded.

The formulas mirror what the workbook itself uses:

* 比率 = 分子指标 / 分母指标（倍，不做百分比换算）
* 保单未来盈余/核心资本分布 = 按 <=0、(0,20%]、(20,35%]、>35% 分档
* 考虑特征系数影响 = 量化风险最低资本 - 各项风险最低资本合计
* 注册资本/核心资本率 = 1/(核心资本/注册资本)
"""

from __future__ import annotations

import pandas as pd

#: ``来源类型`` 取值：只有这一类的行才会被重算。
RECOMPUTE_SOURCE_TYPE = "系统计算"

#: 允许重算的指标编码 —— 与集成表中“来源类型=系统计算”的指标完全一致。
SYSTEM_CALC_METRIC_CODES: frozenset[str] = frozenset(
    {
        "ACTUAL_CAPITAL_TO_RECOGNIZED_ASSETS",
        "CORE_T1_TO_ACTUAL_CAPITAL",
        "CORE_T2_TO_ACTUAL_CAPITAL",
        "ANC_T1_TO_ACTUAL_CAPITAL",
        "ANC_T2_TO_ACTUAL_CAPITAL",
        "MINIMUM_CAPITAL_TO_RECOGNIZED_LIABILITIES",
        "RECOGNIZED_ASSETS_TO_ACTUAL_CAPITAL",
        "RECOGNIZED_ASSETS_TO_MINIMUM_CAPITAL",
        "RECOGNIZED_ASSETS_TO_REGISTERED_CAPITAL",
        "ACTUAL_CAPITAL_TO_REGISTERED_CAPITAL",
        "NON_LIFE_INSURANCE_RISK_TO_LIABILITIES",
        "NON_LIFE_INSURANCE_RISK_TO_QUANT_CAPITAL",
        "MARKET_RISK_TO_QUANT_CAPITAL",
        "CREDIT_RISK_TO_QUANT_CAPITAL",
        "DIVERSIFICATION_EFFECT_TO_QUANT_CAPITAL",
        "MARKET_RISK_TO_ASSETS",
        "CREDIT_RISK_TO_ASSETS",
        "CORE_CAPITAL_TO_REGISTERED_CAPITAL",
        "REGISTERED_CAPITAL_TO_CORE_CAPITAL",
        "INTEREST_RATE_RISK_TO_ASSETS",
        "EQUITY_RISK_TO_ASSETS",
        "SPREAD_RISK_TO_ASSETS",
        "COUNTERPARTY_RISK_TO_ASSETS",
        "TOTAL_ASSETS_TO_REGISTERED_CAPITAL",
        "LOSS_ABSORPTION_TO_QUANT_CAPITAL",
        "POLICY_SURPLUS_CORE_TO_ACTUAL_CAPITAL",
        "POLICY_SURPLUS_CORE_BAND",
        "FEATURE_FACTOR_IMPACT",
        "POLICY_SURPLUS_CORE_TO_CORE_CAPITAL",
        "FEATURE_FACTOR_CHECK",
        "LIFE_INSURANCE_RISK_TO_LIABILITIES",
        "LIFE_INSURANCE_RISK_TO_QUANT_CAPITAL",
        "CORE_T1_POLICY_SURPLUS_SHARE",
        "ANC_T1_POLICY_SURPLUS_SHARE",
    }
)

#: 简单比率：目标编码 -> (分子编码, 分母编码)；分子/分母各自求和。
_SIMPLE_RATIOS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "ACTUAL_CAPITAL_TO_RECOGNIZED_ASSETS": (("ACTUAL_CAPITAL",), ("RECOGNIZED_ASSETS",)),
    "CORE_T1_TO_ACTUAL_CAPITAL": (("CORE_T1_CAPITAL",), ("ACTUAL_CAPITAL",)),
    "CORE_T2_TO_ACTUAL_CAPITAL": (("CORE_T2_CAPITAL",), ("ACTUAL_CAPITAL",)),
    "ANC_T1_TO_ACTUAL_CAPITAL": (("ANC_T1_CAPITAL",), ("ACTUAL_CAPITAL",)),
    "ANC_T2_TO_ACTUAL_CAPITAL": (("ANC_T2_CAPITAL",), ("ACTUAL_CAPITAL",)),
    "MINIMUM_CAPITAL_TO_RECOGNIZED_LIABILITIES": (
        ("MINIMUM_CAPITAL",),
        ("RECOGNIZED_LIABILITIES",),
    ),
    "RECOGNIZED_ASSETS_TO_ACTUAL_CAPITAL": (("RECOGNIZED_ASSETS",), ("ACTUAL_CAPITAL",)),
    "RECOGNIZED_ASSETS_TO_MINIMUM_CAPITAL": (
        ("RECOGNIZED_ASSETS",),
        ("MINIMUM_CAPITAL",),
    ),
    "RECOGNIZED_ASSETS_TO_REGISTERED_CAPITAL": (
        ("RECOGNIZED_ASSETS",),
        ("REGISTERED_CAPITAL",),
    ),
    "ACTUAL_CAPITAL_TO_REGISTERED_CAPITAL": (("ACTUAL_CAPITAL",), ("REGISTERED_CAPITAL",)),
    "NON_LIFE_INSURANCE_RISK_TO_LIABILITIES": (
        ("NON_LIFE_INSURANCE_RISK_CAPITAL",),
        ("RECOGNIZED_LIABILITIES",),
    ),
    "NON_LIFE_INSURANCE_RISK_TO_QUANT_CAPITAL": (
        ("NON_LIFE_INSURANCE_RISK_CAPITAL",),
        ("QUANT_RISK_CAPITAL",),
    ),
    "MARKET_RISK_TO_QUANT_CAPITAL": (("MARKET_RISK_CAPITAL",), ("QUANT_RISK_CAPITAL",)),
    "CREDIT_RISK_TO_QUANT_CAPITAL": (("CREDIT_RISK_CAPITAL",), ("QUANT_RISK_CAPITAL",)),
    "DIVERSIFICATION_EFFECT_TO_QUANT_CAPITAL": (
        ("QUANT_RISK_DIVERSIFICATION_EFFECT",),
        ("QUANT_RISK_CAPITAL",),
    ),
    "MARKET_RISK_TO_ASSETS": (("MARKET_RISK_CAPITAL",), ("RECOGNIZED_ASSETS",)),
    "CREDIT_RISK_TO_ASSETS": (("CREDIT_RISK_CAPITAL",), ("RECOGNIZED_ASSETS",)),
    "CORE_CAPITAL_TO_REGISTERED_CAPITAL": (
        ("CORE_T1_CAPITAL", "CORE_T2_CAPITAL"),
        ("REGISTERED_CAPITAL",),
    ),
    "INTEREST_RATE_RISK_TO_ASSETS": (("INTEREST_RATE_RISK_CAPITAL",), ("RECOGNIZED_ASSETS",)),
    "EQUITY_RISK_TO_ASSETS": (("EQUITY_RISK_CAPITAL",), ("RECOGNIZED_ASSETS",)),
    "SPREAD_RISK_TO_ASSETS": (("SPREAD_RISK_CAPITAL",), ("RECOGNIZED_ASSETS",)),
    "COUNTERPARTY_RISK_TO_ASSETS": (("COUNTERPARTY_RISK_CAPITAL",), ("RECOGNIZED_ASSETS",)),
    "TOTAL_ASSETS_TO_REGISTERED_CAPITAL": (("TOTAL_ASSETS",), ("REGISTERED_CAPITAL",)),
    "LOSS_ABSORPTION_TO_QUANT_CAPITAL": (
        ("CONTRACT_LOSS_ABSORPTION_EFFECT",),
        ("QUANT_RISK_CAPITAL",),
    ),
    "POLICY_SURPLUS_CORE_TO_ACTUAL_CAPITAL": (
        ("POLICY_SURPLUS_CORE_T1", "POLICY_SURPLUS_CORE_T2"),
        ("ACTUAL_CAPITAL",),
    ),
    "POLICY_SURPLUS_CORE_TO_CORE_CAPITAL": (
        ("POLICY_SURPLUS_CORE_T1", "POLICY_SURPLUS_CORE_T2"),
        ("CORE_T1_CAPITAL", "CORE_T2_CAPITAL"),
    ),
    "LIFE_INSURANCE_RISK_TO_LIABILITIES": (
        ("INSURANCE_RISK_CAPITAL",),
        ("RECOGNIZED_LIABILITIES",),
    ),
    "LIFE_INSURANCE_RISK_TO_QUANT_CAPITAL": (
        ("INSURANCE_RISK_CAPITAL",),
        ("QUANT_RISK_CAPITAL",),
    ),
    "CORE_T1_POLICY_SURPLUS_SHARE": (("POLICY_SURPLUS_CORE_T1",), ("CORE_T1_CAPITAL",)),
    "ANC_T1_POLICY_SURPLUS_SHARE": (("POLICY_SURPLUS_ANC_T1",), ("ANC_T1_CAPITAL",)),
    "FEATURE_FACTOR_CHECK": (
        ("FEATURE_FACTOR_IMPACT",),
        (
            "INSURANCE_RISK_CAPITAL",
            "NON_LIFE_INSURANCE_RISK_CAPITAL",
            "MARKET_RISK_CAPITAL",
            "CREDIT_RISK_CAPITAL",
            "QUANT_RISK_DIVERSIFICATION_EFFECT",
            "CONTRACT_LOSS_ABSORPTION_EFFECT",
        ),
    ),
}

#: 考虑特征系数影响 = 量化风险最低资本 - 六项风险最低资本之和（万元）。
_FEATURE_FACTOR_COMPONENTS = (
    "INSURANCE_RISK_CAPITAL",
    "NON_LIFE_INSURANCE_RISK_CAPITAL",
    "MARKET_RISK_CAPITAL",
    "CREDIT_RISK_CAPITAL",
    "QUANT_RISK_DIVERSIFICATION_EFFECT",
    "CONTRACT_LOSS_ABSORPTION_EFFECT",
)

#: 集成表存在同一公司同期同口径的多份记录（如 merged 主值与附注分保前口径），
#: 因此先按现有计算值建立“记录指纹”把每一行系统计算记录对应到她所属的那份
#: 口径；只有无法唯一对应时才退回按编码取首条。
_GROUP_KEYS = ["报告类型", "公司", "报告期", "期间口径"]
_FALLBACK_GROUP_KEYS = ["公司", "报告期", "期间口径"]
_ROW_FINGERPRINT = "_记录指纹"


def _base_codes() -> set[str]:
    codes: set[str] = {"QUANT_RISK_CAPITAL", "FEATURE_FACTOR_IMPACT", "FEATURE_FACTOR_CHECK"}
    for numerators, denominators in _SIMPLE_RATIOS.values():
        codes.update(numerators)
        codes.update(denominators)
    return codes


def _ensure_columns(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    for column in (*_GROUP_KEYS, "指标编码", "数值", "来源类型"):
        if column not in work.columns:
            work[column] = ""
    for column in ("公司", "报告期", "期间口径", "指标编码", "来源类型", "报告类型"):
        work[column] = work[column].fillna("").astype(str).str.strip()
    work["来源类型"] = work["来源类型"].str.replace(r"\s+", "", regex=True)
    return work


def _band_label(value: float) -> str:
    if pd.isna(value):
        return ""
    if value <= 0:
        return "小于等于0%"
    if value <= 0.20:
        return "(0,20%]"
    if value <= 0.35:
        return "(20%,35%]"
    return "大于35%"


def _wide_values(work: pd.DataFrame, group_keys: list[str], codes: set[str]) -> pd.DataFrame:
    subset = work[work["指标编码"].isin(codes) & work["数值"].notna()].copy()
    if subset.empty:
        return pd.DataFrame()
    subset = subset.drop_duplicates(subset=[*group_keys, "指标编码"], keep="first")
    return subset.set_index([*group_keys, "指标编码"])["数值"].unstack("指标编码")


def _computed_values(wide: pd.DataFrame) -> pd.DataFrame:
    """Apply the workbook formulas to the wide base-indicator frame."""
    computed: dict[str, pd.Series] = {}
    for target, (numerators, denominators) in _SIMPLE_RATIOS.items():
        available = [code for code in numerators if code in wide.columns]
        if not available:
            continue
        numerator = wide[available].sum(axis=1, min_count=len(available))
        finite_denominators = [code for code in denominators if code in wide.columns]
        if not finite_denominators:
            continue
        denominator = wide[finite_denominators].sum(
            axis=1, min_count=len(finite_denominators)
        )
        value = numerator / denominator
        computed[target] = value.where(denominator.ne(0), other=pd.NA)

    # 注册资本/核心资本率 = 1/(核心资本/注册资本)
    if "CORE_CAPITAL_TO_REGISTERED_CAPITAL" in computed:
        base_ratio = computed["CORE_CAPITAL_TO_REGISTERED_CAPITAL"]
        computed["REGISTERED_CAPITAL_TO_CORE_CAPITAL"] = 1.0 / base_ratio.where(
            base_ratio.ne(0)
        )

    # 保单未来盈余/核心资本分布（文本分档，只在对象列中计算）
    if "POLICY_SURPLUS_CORE_TO_ACTUAL_CAPITAL" in computed and not isinstance(
        computed["POLICY_SURPLUS_CORE_TO_ACTUAL_CAPITAL"].dtype, pd.ArrowDtype | pd.StringDtype
    ) and computed["POLICY_SURPLUS_CORE_TO_ACTUAL_CAPITAL"].dtype.kind == "f":
        computed["POLICY_SURPLUS_CORE_BAND"] = computed[
            "POLICY_SURPLUS_CORE_TO_ACTUAL_CAPITAL"
        ].map(lambda value: _band_label(value) if pd.notna(value) else pd.NA)

    # 考虑特征系数影响 = 量化风险最低资本 - 六项之和
    components = [code for code in _FEATURE_FACTOR_COMPONENTS if code in wide.columns]
    if "QUANT_RISK_CAPITAL" in wide.columns and components:
        before_factor = wide[components].sum(axis=1, min_count=len(components))
        impact = wide["QUANT_RISK_CAPITAL"] - before_factor
        computed["FEATURE_FACTOR_IMPACT"] = impact.where(before_factor.notna())

    return pd.DataFrame(computed)


def _fingerprint_ids(rows: pd.DataFrame, group_keys: list[str]) -> pd.Series:
    """Stable per-record id: row order inside each company/period/scope group.

    Ties are broken by metric code so the mapping stays deterministic when some
    calculated values are missing.
    """
    if rows.empty:
        return pd.Series(dtype="int64")
    ordered = rows.assign(
        _原有数值=pd.to_numeric(rows["数值"], errors="coerce").astype(float)
    ).sort_values(
        [*group_keys, "_原有数值", "指标编码"],
        ascending=True,
        kind="stable",
        na_position="last",
    )
    counts = ordered.groupby(group_keys, sort=False).cumcount()
    counts.index = ordered.index
    return counts.sort_index()  # back to the original row order


def _group_key_frame(work: pd.DataFrame, group_keys: list[str]) -> pd.DataFrame:
    """Hashable string keys so reindexing is stable regardless of dtypes."""
    return pd.DataFrame(
        {
            key: work[key].astype(str)
            for key in group_keys
        }
    )


def _arrow_text(values) -> list[str]:
    """Convert (possibly pyarrow-backed) index levels to plain Python strings."""
    if hasattr(values, "to_pylist"):
        return [str(value) for value in values.to_pylist()]
    return values.astype(str).tolist()


def _bucket_index(stacked: pd.Series, group_keys: list[str]) -> dict[tuple, list[int]]:
    """Computed-value positions grouped by (company/period/scope, code)."""
    buckets: dict[tuple, list[int]] = {}
    key_columns = [
        _arrow_text(stacked.index.get_level_values(level)) for level in group_keys
    ]
    code_column = _arrow_text(stacked.index.get_level_values("指标编码"))
    for position in range(len(code_column)):
        key = (*[column[position] for column in key_columns], code_column[position])
        buckets.setdefault(key, []).append(position)
    return buckets


def _map_rows(
    stacked: pd.Series,
    work: pd.DataFrame,
    group_keys: list[str],
    fingerprints: pd.Series,
    buckets: dict[tuple, list[int]],
) -> pd.Series:
    """Return the computed value for each row, honouring the record fingerprint."""
    values = [float(value) for value in stacked.to_numpy()]
    keys = list(
        _group_key_frame(work, group_keys).itertuples(index=False, name=None)
    )
    target_codes = work["指标编码"].tolist()
    prints = fingerprints.reindex(work.index).fillna(0).to_numpy().tolist()
    result: list[float] = []
    for key, code, fingerprint in zip(keys, target_codes, prints):
        bucket = buckets.get((*key, code))
        if not bucket:
            result.append(float("nan"))
            continue
        fingerprint = int(fingerprint) if fingerprint == fingerprint else 1
        if fingerprint <= 0 or fingerprint > len(bucket):
            fingerprint = 1
        result.append(values[bucket[fingerprint - 1]])
    return pd.Series(result, index=work.index)


def _assign_fingerprints(
    work: pd.DataFrame,
    target_mask: pd.Series,
    fingerprints: pd.Series,
    mapped: pd.Series,
    stacked: pd.Series,
    group_keys: list[str],
    buckets: dict[tuple, list[int]],
) -> None:
    """Align fingerprints to the computed values (fixes rows with missing values).

    Only rows whose current value uniquely matches one computed record are
    corrected, so rows that were blank keep their position in the group.
    """
    values = [float(value) for value in stacked.to_numpy()]
    keys = list(
        _group_key_frame(work, group_keys).itertuples(index=False, name=None)
    )
    target_codes = work["指标编码"].tolist()
    mapped_values = mapped.to_numpy()
    target_positions = target_mask.to_numpy().nonzero()[0]
    print_values = fingerprints.to_numpy(copy=True)
    for local_index, position in enumerate(target_positions):
        current = mapped_values[position]
        if pd.isna(current):
            continue
        bucket = buckets.get((*keys[position], target_codes[position]))
        if not bucket:
            continue
        matches = [
            order
            for order, candidate in enumerate(bucket, start=1)
            if abs(values[candidate] - float(current)) <= 1e-9
        ]
        if len(matches) == 1:
            print_values[local_index] = matches[0]
    fingerprints.loc[:] = print_values


def recompute_step7_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    """Recalculate rows marked 系统计算 for the supported calculated metrics.

    Values are replaced in place only for rows whose ``指标编码`` is one of
    :data:`SYSTEM_CALC_METRIC_CODES` **and** whose ``来源类型`` equals
    系统计算.  Rows without a ``来源类型`` column are left untouched, so charts
    built from synthetic or partial frames keep their supplied values.
    """
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return frame
    if "来源类型" not in frame.columns:
        return frame

    work = _ensure_columns(frame)
    work["数值"] = pd.to_numeric(frame["数值"], errors="coerce")
    target_mask = work["指标编码"].isin(SYSTEM_CALC_METRIC_CODES) & work[
        "来源类型"
    ].eq(RECOMPUTE_SOURCE_TYPE)
    if not target_mask.any():
        return frame

    has_report_type = "报告类型" in frame.columns
    group_keys = _GROUP_KEYS if has_report_type else _FALLBACK_GROUP_KEYS
    wide = _wide_values(work, group_keys, _base_codes())
    if wide.empty:
        return frame
    computed = _computed_values(wide)
    if computed.empty:
        return frame

    stacked = computed.stack().dropna()
    stacked.index.names = [*group_keys, "指标编码"]
    # 只映射数值型结果；文本分档（保单未来盈余分布）不在 STEP7 图表中使用。
    stacked = stacked[pd.to_numeric(stacked, errors="coerce").notna()]
    if stacked.empty:
        return frame

    # 同一公司/期次/口径可能存在多份记录（主值与附注口径），用“记录指纹”
    # 让第 k 份记录对齐到第 k 份重算结果，而不是全部套用首条记录。
    buckets = _bucket_index(stacked, group_keys)
    fingerprints = _fingerprint_ids(work[target_mask], group_keys)
    mapped = _map_rows(stacked, work, group_keys, fingerprints, buckets)
    _assign_fingerprints(
        work, target_mask, fingerprints, mapped, stacked, group_keys, buckets
    )
    mapped = _map_rows(stacked, work, group_keys, fingerprints, buckets)

    mask = mapped.notna().to_numpy() & target_mask.to_numpy()
    if not mask.any():
        return frame

    result = frame.copy()
    numeric = pd.to_numeric(result["数值"], errors="coerce").to_numpy(dtype=float).copy()
    numeric[mask] = mapped.to_numpy(dtype=float)[mask]
    result["数值"] = numeric
    return result
