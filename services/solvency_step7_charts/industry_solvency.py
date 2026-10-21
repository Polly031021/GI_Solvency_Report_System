"""行业概览：偿付能力披露整体情况（合计口径）。"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ..solvency_step6_analysis import sort_report_periods

ACTUAL_CAPITAL_CODE = "ACTUAL_CAPITAL"
MINIMUM_CAPITAL_CODE = "MINIMUM_CAPITAL"
CORE_T1_CODE = "CORE_T1_CAPITAL"
CORE_T2_CODE = "CORE_T2_CAPITAL"
CAPITAL_CODES = (
    ACTUAL_CAPITAL_CODE,
    MINIMUM_CAPITAL_CODE,
    CORE_T1_CODE,
    CORE_T2_CODE,
)


@dataclass(frozen=True)
class IndustrySolvencyOverview:
    """Aggregate solvency disclosure for the latest reporting period."""

    period: str
    company_count: int
    coverage: float
    actual_capital: float | None
    minimum_capital: float | None
    core_capital: float | None
    combined_ratio: float | None
    core_ratio: float | None
    core_share: float | None
    previous_period: str = ""
    previous_combined_ratio: float | None = None
    previous_core_ratio: float | None = None
    detail: pd.DataFrame | None = None


def _pivot_capital(work: pd.DataFrame, period: str) -> pd.DataFrame:
    rows = work[
        work["报告期"].astype(str).eq(str(period))
        & work["指标编码"].astype(str).isin(CAPITAL_CODES)
    ].copy()
    if rows.empty:
        return pd.DataFrame()
    rows["数值"] = pd.to_numeric(rows["数值"], errors="coerce")
    pivot = (
        rows.pivot_table(
            index="公司",
            columns="指标编码",
            values="数值",
            aggfunc="last",
        )
        .reset_index()
    )
    for code in CAPITAL_CODES:
        if code not in pivot.columns:
            pivot[code] = pd.NA
    return pivot


def _period_metrics(work: pd.DataFrame, period: str) -> dict | None:
    pivot = _pivot_capital(work, period)
    if pivot.empty:
        return None
    actual = pd.to_numeric(pivot[ACTUAL_CAPITAL_CODE], errors="coerce")
    minimum = pd.to_numeric(pivot[MINIMUM_CAPITAL_CODE], errors="coerce")
    core_t1 = pd.to_numeric(pivot[CORE_T1_CODE], errors="coerce")
    core_t2 = pd.to_numeric(pivot[CORE_T2_CODE], errors="coerce")

    combined_valid = actual.notna() & minimum.notna() & minimum.gt(0)
    core_series = core_t1.fillna(0.0) + core_t2.fillna(0.0)
    core_valid = (
        combined_valid
        & (core_t1.notna() | core_t2.notna())
        & core_series.notna()
    )

    actual_sum = float(actual[combined_valid].sum()) if combined_valid.any() else None
    minimum_sum = float(minimum[combined_valid].sum()) if combined_valid.any() else None
    core_sum = float(core_series[core_valid].sum()) if core_valid.any() else None
    core_minimum_sum = (
        float(minimum[core_valid].sum()) if core_valid.any() else None
    )

    combined_ratio = (
        actual_sum / minimum_sum * 100.0
        if actual_sum is not None and minimum_sum not in (None, 0)
        else None
    )
    core_ratio = (
        core_sum / core_minimum_sum * 100.0
        if core_sum is not None and core_minimum_sum not in (None, 0)
        else None
    )
    core_share = (
        core_sum / actual_sum * 100.0
        if core_sum is not None and actual_sum not in (None, 0)
        else None
    )

    detail = pd.DataFrame(
        {
            "公司": pivot["公司"].astype(str).str.strip(),
            "实际资本": actual,
            "最低资本": minimum,
            "核心资本": core_series,
            "纳入综合口径": combined_valid,
            "纳入核心口径": core_valid,
        }
    )
    detail["综合偿付能力充足率"] = (
        detail["实际资本"] / detail["最低资本"] * 100.0
    )
    detail.loc[~combined_valid, "综合偿付能力充足率"] = pd.NA
    detail["核心偿付能力充足率"] = (
        detail["核心资本"] / detail["最低资本"] * 100.0
    )
    detail.loc[~core_valid, "核心偿付能力充足率"] = pd.NA
    detail = detail.sort_values(
        ["纳入综合口径", "实际资本"],
        ascending=[False, False],
    ).reset_index(drop=True)

    any_capital = (
        actual.notna() | minimum.notna() | core_t1.notna() | core_t2.notna()
    )
    company_count = int(combined_valid.sum())
    return {
        "period": str(period),
        "company_count": company_count,
        "coverage": (
            company_count / int(any_capital.sum())
            if int(any_capital.sum()) > 0
            else 0.0
        ),
        "actual_capital": actual_sum,
        "minimum_capital": minimum_sum,
        "core_capital": core_sum,
        "combined_ratio": combined_ratio,
        "core_ratio": core_ratio,
        "core_share": core_share,
        "detail": detail,
    }


def prepare_industry_solvency_overview(
    frame: pd.DataFrame | None,
    *,
    period: str = "",
) -> IndustrySolvencyOverview | None:
    """Aggregate capital and compute industry-level solvency ratios.

    Comprehensive ratio = sum(actual capital) / sum(minimum capital).
    Core ratio = sum(core tier-1 + core tier-2 capital) / sum(minimum capital).
    Only companies with non-missing capital and a positive minimum capital are
    included in each ratio denominator so the aggregate is comparable.
    """
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return None
    work = frame.copy()
    for column in ("公司", "报告期", "指标编码", "数值"):
        if column not in work.columns:
            work[column] = ""
    work["公司"] = work["公司"].fillna("").astype(str).str.strip()
    work = work[
        work["公司"].ne("")
        & ~work["公司"].eq("行业合计")
        & work["报告期"].astype(str).str.strip().ne("")
    ].copy()
    work["指标编码"] = work["指标编码"].fillna("").astype(str).str.strip()
    work["报告期"] = work["报告期"].astype(str).str.strip()
    work["数值"] = pd.to_numeric(work["数值"], errors="coerce")
    if work.empty:
        return None

    periods = sort_report_periods(work["报告期"])
    if not periods:
        return None
    requested = str(period or "").strip()
    if requested and requested in periods:
        periods = [requested]

    latest: dict | None = None
    previous: dict | None = None
    for candidate in reversed(periods):
        metrics = _period_metrics(work, candidate)
        if metrics is None:
            continue
        if (
            metrics.get("combined_ratio") is None
            and metrics.get("core_ratio") is None
        ):
            continue
        if latest is None:
            latest = metrics
            continue
        previous = metrics
        break
    if latest is None:
        return None

    return IndustrySolvencyOverview(
        period=str(latest["period"]),
        company_count=int(latest["company_count"]),
        coverage=float(latest["coverage"]),
        actual_capital=latest["actual_capital"],
        minimum_capital=latest["minimum_capital"],
        core_capital=latest["core_capital"],
        combined_ratio=latest["combined_ratio"],
        core_ratio=latest["core_ratio"],
        core_share=latest["core_share"],
        previous_period=str(previous["period"]) if previous else "",
        previous_combined_ratio=(
            previous["combined_ratio"] if previous else None
        ),
        previous_core_ratio=previous["core_ratio"] if previous else None,
        detail=latest["detail"],
    )
