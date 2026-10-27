"""行业整体保费规模与结构：签单保费柱线图 + 车险/非车险 100% 占比柱线图。"""

from __future__ import annotations

import math
import re
import unicodedata

import altair as alt
import pandas as pd

from ..report_style import COBALT_BLUE, PACIFIC_BLUE, PINK, PRIMARY_BLUE, PURPLE
from .primitives import apply_industry_chart_theme


TOTAL_PREMIUM_CODE = "SIGNED_PREMIUM"
MOTOR_PREMIUM_CODE = "SIGNED_PREMIUM_MOTOR"
ANNUAL_SCOPE_ALIASES = ("本年累计数", "年度累计数", "全年累计数")
UNIT_TO_WAN = {
    "元": 0.0001,
    "十万元": 10.0,
    "百万元": 100.0,
    "千万元": 1_000.0,
    "万元": 1.0,
    "亿元": 10_000.0,
    "十亿元": 100_000.0,
}
DISPLAY_UNITS = ("十亿元", "亿元", "百万元", "十万元")
DEFAULT_DISPLAY_UNIT = "亿元"
# 同一家公司在不同披露批次里可能带“（偿付能力）”等后缀；行业汇总前
# 必须去掉后缀，否则同一年度的合计保费与车险拆分会被拆成两行。
_DISCLOSURE_SUFFIX = re.compile(r"[（(](?:偿付能力|合并|母公司|本部)[）)]$")
HISTORICAL_SOURCE = "固定行业数据"
UPLOAD_SOURCE = "上传数据"
INDUSTRY_PREMIUM_WINDOWS = {
    "近5年": 5,
    "近10年": 10,
    "全部": None,
}

# 2017-2024 行业签单保费历史基准（保费单位：亿元；车险百分比用于拆分）。
# 上传数据在相同年度出现时优先，历史基准只补齐上传数据未覆盖的年度。
HISTORICAL_INDUSTRY_PREMIUM = (
    (2017, 10_541.0, 0.71),
    (2018, 11_756.0, 0.67),
    (2019, 13_016.0, 0.63),
    (2020, 13_584.0, 0.61),
    (2021, 13_676.0, 0.57),
    (2022, 14_867.0, 0.55),
    (2023, 15_868.0, 0.54),
    (2024, 16_907.0, 0.54),
)
ANNUAL_COLUMNS = [
    "年度",
    "车险",
    "非车险",
    "合计",
    "公司数",
    "车险公司数",
    "完整",
    "异常数",
    "来源",
]


def _canonical_company_name(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    text = re.sub(r"\s+", "", text)
    return _DISCLOSURE_SUFFIX.sub("", text)


def _year_from_period(value: object) -> int | None:
    match = re.search(r"(20\d{2})", str(value or ""))
    return int(match.group(1)) if match else None


def _quarter_from_period(value: object) -> int:
    """Return 1-4 for Q1-Q4 labels, or 0 when the period has no quarter."""
    text = str(value or "").strip()
    match = re.search(r"(?:Q|第)\s*([1-4])(?:\s*季度)?", text, flags=re.IGNORECASE)
    if not match:
        match = re.search(r"([1-4])\s*季度", text)
    if match:
        return int(match.group(1))
    chinese = {"一": 1, "二": 2, "三": 3, "四": 4}
    match = re.search(r"第?([一二三四])季度", text)
    return chinese.get(match.group(1), 0) if match else 0


def _repair_scale_anomalies(
    annual: pd.DataFrame,
    quarter_map: dict[tuple[str, int], float],
) -> pd.DataFrame:
    """Repair per-company annual amounts that carry a unit-scale mistake.

    Uploaded workbooks mix 万元, 亿元 and 元 values under the same ``万元``
    label, so a single record can be 10,000x off while its neighbours are
    fine.  Corrections are made per company:

    1. Compare the annual total with that year's Q4 single-quarter amount.
       The annual figure should be roughly 1-10x the quarter.
    2. Cluster the company's remaining years to their dominant order of
       magnitude, so a lone 亿元 record beside 万元 records is repaired.

    A value is only rewritten when the adjusted amount lands in a plausible
    range; genuine growth is left untouched.
    """
    result = annual.copy()
    result["量级异常"] = False
    result["校正说明"] = ""
    for company, group in result.groupby("公司", sort=False):
        indices = list(group.index)
        raw = [float(value) for value in group["数值"]]
        years = [int(year) for year in group["年度"]]
        corrected = list(raw)
        reasons: list[tuple[str, float] | None] = [None] * len(raw)

        # 1) Annual vs. Q4 single-quarter sanity check.
        for position, (year, value) in enumerate(zip(years, raw)):
            quarter = quarter_map.get((str(company), year))
            if quarter is None or not math.isfinite(quarter) or quarter <= 0:
                continue
            if value <= 0 or not math.isfinite(value):
                continue
            ratio = value / quarter
            if ratio > 100:
                for factor in (10_000, 1_000, 100, 10):
                    if 0.5 <= (value / factor) / quarter <= 10:
                        corrected[position] = value / factor
                        reasons[position] = ("偏大", float(factor))
                        break
            elif ratio < 0.01:
                others = [
                    other for other_position, other in enumerate(raw)
                    if other_position != position and other > 0
                ]
                reference = float(pd.Series(others).median()) if others else None
                # If the annual figure agrees with the company's other years,
                # the quarter value is the bad record; leave it alone.
                if reference is None or value < reference / 100:
                    for factor in (10, 100, 1_000, 10_000):
                        if 0.5 <= (value * factor) / quarter <= 10:
                            corrected[position] = value * factor
                            reasons[position] = ("偏小", float(factor))
                            break

        # 2) Cluster the company's annual history to its dominant scale.
        positive = [value for value in corrected if value > 0]
        if len(positive) >= 3:
            reference_log = float(
                pd.Series([math.log10(value) for value in positive]).median()
            )
            for position, value in enumerate(corrected):
                if value <= 0 or reasons[position] is not None:
                    continue
                difference = math.log10(value) - reference_log
                if abs(difference) >= 1.5:
                    exponent = int(round(difference))
                    corrected[position] = value / (10**exponent)
                    if reasons[position] is None:
                        reasons[position] = (
                            "偏大" if exponent > 0 else "偏小",
                            float(10 ** abs(exponent)),
                        )
        elif len(positive) == 2:
            small, large = sorted(positive)
            if small > 0 and large / small >= 100:
                exponent = int(round(math.log10(large / small)))
                for position, value in enumerate(corrected):
                    if reasons[position] is not None:
                        continue
                    if math.isclose(value, small, rel_tol=1e-12):
                        corrected[position] = value * (10**exponent)
                        if reasons[position] is None:
                            reasons[position] = ("偏小", float(10**exponent))

        for position, index in enumerate(indices):
            reason = reasons[position]
            if reason is None:
                continue
            direction, magnitude = reason
            result.at[index, "数值"] = corrected[position]
            result.at[index, "量级异常"] = True
            result.at[index, "校正说明"] = (
                f"检测到约 {magnitude:,.0f} 倍量级{direction}，已自动校正"
            )
    return result


def _annual_rows(
    frame: pd.DataFrame,
    code: str,
    years: list[int],
    scopes: tuple[str, ...] = ANNUAL_SCOPE_ALIASES,
) -> pd.DataFrame:
    """Pick one annual-cumulative value per company and year.

    Values are converted to the internal 万元 unit from the record's own
    ``单位`` column, and company names are canonicalised before aggregation.
    Both are required for the industry total: a single 2025 record stored in
    元 instead of 万元, or a ``（偿付能力）`` suffix that keeps the total and
    motor split on two rows, would otherwise multiply the chart axis by
    10,000x.
    """
    empty = pd.DataFrame(columns=["公司", "年度", "数值", "单位"])
    if frame.empty or code not in set(frame["指标编码"].astype(str)):
        return empty
    work = frame[frame["指标编码"].astype(str).eq(code)].copy()
    work["公司"] = work["公司"].map(_canonical_company_name)
    work["年度"] = work["报告期"].map(_year_from_period)
    work["数值"] = pd.to_numeric(work["数值"], errors="coerce")
    if "单位" in work.columns:
        units = work["单位"].fillna("").astype(str).str.strip()
    else:
        units = pd.Series("", index=work.index)
        work["单位"] = "万元"
    # Convert each record from its own unit to 万元.  Unknown/non-amount
    # units (%, 倍, empty) keep their value unchanged for ratio charts.
    factors = units.map(UNIT_TO_WAN).fillna(1.0)
    work["数值"] = work["数值"] * factors
    work = work.dropna(subset=["年度", "数值"])
    work["年度"] = work["年度"].astype(int)
    work = work[work["年度"].isin(years)]
    if work.empty:
        return empty

    work["_季度"] = work["报告期"].map(_quarter_from_period)
    scope = work["期间口径"].fillna("").astype(str).str.strip()
    # Q4 single-quarter amounts are a reliable anchor for spotting annual
    # totals that are orders of magnitude too large.
    quarter_rows = work[scope.eq("本季度数") & work["_季度"].eq(4)]
    quarter_map: dict[tuple[str, int], float] = {}
    if not quarter_rows.empty:
        quarter_rows = quarter_rows.sort_values(["公司", "年度"]).drop_duplicates(
            ["公司", "年度"],
            keep="last",
        )
        quarter_map = {
            (str(row.公司), int(row.年度)): float(row.数值)
            for row in quarter_rows.itertuples(index=False)
        }

    is_annual = scope.isin(scopes)
    if not is_annual.any():
        return empty
    work = work[is_annual].copy()
    # Annual figures must come from Q4.  Keep undated year-level rows as a
    # compatibility fallback, but never treat Q1-Q3 cumulative data as annual.
    work = work[work["_季度"].isin((0, 4))].copy()
    if work.empty:
        return empty
    # Q4 is the annual-cumulative anchor.  If a workbook contains Q1-Q3
    # cumulative rows as well, keep Q4; if Q4 is absent, fall back to the
    # latest available quarter for that company/year.
    work = work.sort_values(["公司", "年度", "_季度"]).drop_duplicates(
        ["公司", "年度"],
        keep="last",
    )
    work = _repair_scale_anomalies(work, quarter_map)
    columns = ["公司", "年度", "数值", "单位", "量级异常", "校正说明"]
    return work[columns]


def _historical_annual() -> pd.DataFrame:
    """Return the fixed industry baseline in the internal 万元 unit."""
    rows: list[dict[str, object]] = []
    for year, premium_yi, motor_share in HISTORICAL_INDUSTRY_PREMIUM:
        total = float(premium_yi) * 10_000.0
        motor = total * float(motor_share)
        rows.append(
            {
                "年度": str(year),
                "车险": motor,
                "非车险": total - motor,
                "合计": total,
                "公司数": 0,
                "车险公司数": 0,
                "完整": True,
                "异常数": 0,
                "来源": HISTORICAL_SOURCE,
            }
        )
    return pd.DataFrame(rows, columns=ANNUAL_COLUMNS)


def prepare_industry_premium_annual(
    frame: pd.DataFrame | None,
    *,
    years: int | None = 5,
    annual_scope: str = "",
    include_historical: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Aggregate annual industry premium and car/non-car split.

    Only ``本年累计数`` (or equivalent annual cumulative) records are used.
    Quarter-only records are deliberately excluded so a Q4 single-quarter
    number is never presented as a full-year total.  When ``include_historical``
    is enabled, 2017-2024 industry history is appended to the uploaded annual
    series; uploaded records replace the fixed value for the same year.
    """
    uploaded = pd.DataFrame(columns=ANNUAL_COLUMNS)
    detail = pd.DataFrame()
    companies: list[str] = []

    work = frame.copy() if isinstance(frame, pd.DataFrame) else pd.DataFrame()
    if not work.empty:
        for column in ("公司", "报告期", "指标编码", "期间口径", "数值"):
            if column not in work.columns:
                work[column] = ""
        work["公司"] = work["公司"].fillna("").astype(str).str.strip()
        work = work[work["公司"].ne("")]
        work["公司"] = work["公司"].map(_canonical_company_name)
        company_type = work.get("公司类型", pd.Series("", index=work.index))
        work = work[
            ~work["公司"].eq("行业合计")
            & ~company_type.fillna("").astype(str).str.strip().eq("行业合计")
        ].copy()

    annual_scopes = (
        (annual_scope,)
        if str(annual_scope or "").strip()
        else ANNUAL_SCOPE_ALIASES
    )
    available_years: list[int] = []
    total_map: dict[tuple[str, int], float] = {}
    motor_map: dict[tuple[str, int], float] = {}
    total_anomaly: dict[tuple[str, int], bool] = {}
    motor_anomaly: dict[tuple[str, int], bool] = {}
    if not work.empty:
        premium_rows = work[
            work["指标编码"].astype(str).isin((TOTAL_PREMIUM_CODE, MOTOR_PREMIUM_CODE))
        ]
        annual_premium_rows = premium_rows[
            premium_rows["期间口径"]
            .fillna("")
            .astype(str)
            .str.strip()
            .isin(annual_scopes)
        ]
        annual_premium_rows = annual_premium_rows[
            annual_premium_rows["报告期"].map(_quarter_from_period).isin((0, 4))
        ]
        available_years = sorted(
            {
                year
                for year in annual_premium_rows["报告期"]
                .map(_year_from_period)
                .dropna()
                .astype(int)
            }
        )
        if available_years:
            total = _annual_rows(
                work,
                TOTAL_PREMIUM_CODE,
                available_years,
                annual_scopes,
            )
            motor = _annual_rows(
                work,
                MOTOR_PREMIUM_CODE,
                available_years,
                annual_scopes,
            )
            total_map = (
                total.set_index(["公司", "年度"])["数值"].to_dict()
                if not total.empty
                else {}
            )
            motor_map = (
                motor.set_index(["公司", "年度"])["数值"].to_dict()
                if not motor.empty
                else {}
            )
            total_anomaly = (
                total.set_index(["公司", "年度"])["量级异常"].to_dict()
                if not total.empty
                else {}
            )
            motor_anomaly = (
                motor.set_index(["公司", "年度"])["量级异常"].to_dict()
                if not motor.empty
                else {}
            )
            companies = sorted(work["公司"].unique().tolist())

    annual_rows: list[dict[str, object]] = []
    detail_rows: list[dict[str, object]] = []
    for year in available_years:
        year_detail: list[dict[str, object]] = []
        for company in companies:
            total_value = total_map.get((company, year))
            motor_value = motor_map.get((company, year))
            non_motor_value = None
            status = ""
            if (
                total_value is not None
                and motor_value is not None
                and float(total_value) >= float(motor_value)
            ):
                non_motor_value = float(total_value) - float(motor_value)
            elif total_value is not None and motor_value is not None:
                status = "异常（车险大于合计）"
            elif total_value is not None:
                status = "缺车险拆分"
            elif motor_value is not None:
                status = "缺合计保费"
            else:
                status = "缺年度累计"
            if total_anomaly.get((company, year), False) or motor_anomaly.get(
                (company, year), False
            ):
                status = "异常（量级异常，已自动校正）"
            year_detail.append(
                {
                    "年度": str(year),
                    "公司": company,
                    "车险": motor_value,
                    "非车险": non_motor_value,
                    "合计": total_value,
                    "状态": status,
                }
            )

        detail_frame = pd.DataFrame(year_detail)
        if not detail_frame.empty:
            for column in ("车险", "非车险", "合计"):
                detail_frame[column] = pd.to_numeric(
                    detail_frame[column], errors="coerce"
                )

        total_count = sum(
            1 for row in year_detail if row["合计"] is not None
        )
        motor_count = sum(
            1 for row in year_detail if row["车险"] is not None
        )
        anomaly_count = int(
            detail_frame["状态"].astype(str).str.startswith("异常").sum()
        ) if not detail_frame.empty else 0
        total_sum = sum(
            float(row["合计"]) for row in year_detail if row["合计"] is not None
        )
        motor_sum = sum(
            float(row["车险"]) for row in year_detail if row["车险"] is not None
        )
        # Non-motor premium is derived at the aggregate level so that
        # car + non-car always reconciles to the reported total even when a
        # few companies are missing their motor split.
        non_motor_sum = (
            max(total_sum - motor_sum, 0.0)
            if total_count and motor_count
            else None
        )
        coverage = total_count / max(len(companies), 1)
        motor_coverage = motor_count / max(len(companies), 1)
        complete = (
            total_count > 0
            and motor_count > 0
            and coverage >= 0.8
            and motor_coverage >= 0.8
        )
        annual_rows.append(
            {
                "年度": str(year),
                "车险": motor_sum if motor_count else None,
                "非车险": non_motor_sum,
                "合计": total_sum if total_count else None,
                "公司数": total_count,
                "车险公司数": motor_count,
                "完整": complete,
                "异常数": anomaly_count,
                "来源": UPLOAD_SOURCE,
            }
        )
        if not detail_frame.empty:
            detail_rows.extend(detail_frame.to_dict("records"))

    uploaded = pd.DataFrame(annual_rows, columns=ANNUAL_COLUMNS)
    detail = pd.DataFrame(detail_rows)

    if include_historical:
        historical = _historical_annual()
        if not uploaded.empty:
            historical = historical[
                ~historical["年度"].astype(str).isin(uploaded["年度"].astype(str))
            ]
        annual = pd.concat([uploaded, historical], ignore_index=True)
    else:
        annual = uploaded.copy()

    if annual.empty:
        return pd.DataFrame(columns=ANNUAL_COLUMNS), detail, companies

    annual["_年度排序"] = pd.to_numeric(annual["年度"], errors="coerce")
    annual = annual.sort_values("_年度排序").drop(columns="_年度排序")
    if years is not None:
        annual = annual.tail(max(1, int(years)))
    annual = annual.reset_index(drop=True)

    if not detail.empty and "年度" in detail.columns:
        selected_years = set(annual["年度"].astype(str))
        detail = detail[detail["年度"].astype(str).isin(selected_years)].reset_index(
            drop=True
        )
    return annual, detail, companies


def _prepare_premium_series(
    annual: pd.DataFrame,
    unit_mode: str,
) -> tuple[pd.DataFrame, list[str], str, str]:
    """Normalise annual industry rows into display units for both charts."""
    if not isinstance(annual, pd.DataFrame) or annual.empty:
        raise ValueError("没有可绘制的行业年度保费数据。")
    available = annual.copy()
    available = available[
        available["合计"].notna()
        | available["车险"].notna()
        | available["非车险"].notna()
    ].copy()
    if available.empty:
        raise ValueError("没有可绘制的行业年度保费数据。")

    available["_年度排序"] = pd.to_numeric(available["年度"], errors="coerce")
    available = available.dropna(subset=["_年度排序"]).sort_values("_年度排序")

    unit = unit_mode if unit_mode in DISPLAY_UNITS else DEFAULT_DISPLAY_UNIT
    divisor = UNIT_TO_WAN.get(unit, UNIT_TO_WAN[DEFAULT_DISPLAY_UNIT])
    value_format = ",.1f" if unit == "十亿元" else ",.0f"

    rows: list[dict[str, object]] = []
    for row in available.itertuples(index=False):
        year = str(row.年度)
        car = None if pd.isna(row.车险) else float(row.车险) / divisor
        non_car = None if pd.isna(row.非车险) else float(row.非车险) / divisor
        total = None if pd.isna(row.合计) else float(row.合计) / divisor
        source = str(getattr(row, "来源", UPLOAD_SOURCE) or UPLOAD_SOURCE)
        if total is None:
            if car is not None and non_car is not None:
                total = car + non_car
            else:
                continue
        if total <= 0:
            continue
        car = 0.0 if car is None else float(car)
        non_car = max(float(total) - car, 0.0) if non_car is None else float(non_car)
        rows.append(
            {
                "年度": year,
                "车险": car,
                "非车险": non_car,
                "合计": float(total),
                "车险占比": car / float(total),
                "非车险占比": non_car / float(total),
                "来源": source,
            }
        )

    if not rows:
        raise ValueError("没有可绘制的行业年度保费合计。")
    frame = pd.DataFrame(rows)
    years = frame["年度"].astype(str).tolist()
    return frame, years, unit, value_format


def _implied_2016_prior(frame: pd.DataFrame) -> dict[str, float]:
    """Back out the 2016 base for 2017 so the first year has a YoY point."""
    matched = frame[frame["年度"].astype(str).eq("2017")]
    if matched.empty:
        return {}
    row = matched.iloc[0]
    growth_2017 = {"车险": 10.04, "非车险": 24.22}
    prior: dict[str, float] = {}
    for column, rate in growth_2017.items():
        value = row.get(column)
        if value is None or pd.isna(value) or float(value) <= 0 or rate <= -100:
            continue
        prior[column] = float(value) / (1.0 + rate / 100.0)
    return prior


def _growth_rows(
    values_by_year: dict[str, float],
    years: list[str],
    prior_2016: float | None,
) -> pd.DataFrame:
    """Compute year-on-year growth without resetting the base across gaps."""
    rows: list[dict[str, object]] = []
    previous_year: str | None = "2016" if prior_2016 is not None else None
    previous_value: float | None = prior_2016
    for year in years:
        key = str(year)
        if key not in values_by_year:
            continue
        current_value = values_by_year[key]
        if (
            previous_year is not None
            and previous_value is not None
            and previous_value != 0
        ):
            rows.append(
                {
                    "年度": key,
                    "增长率": (current_value / previous_value - 1.0) * 100.0,
                    "当年金额": current_value,
                    "上年金额": previous_value,
                    "基期年度": previous_year,
                }
            )
        previous_year = key
        previous_value = current_value
    return pd.DataFrame(rows)


def _total_growth_frame(frame: pd.DataFrame, years: list[str]) -> pd.DataFrame:
    prior = _implied_2016_prior(frame)
    prior_total = sum(prior.values()) if len(prior) == 2 else None
    values = {
        str(row.年度): float(row.合计)
        for row in frame.itertuples(index=False)
    }
    return _growth_rows(values, years, prior_total)


def _category_growth_frame(frame: pd.DataFrame, years: list[str]) -> pd.DataFrame:
    prior = _implied_2016_prior(frame)
    parts: list[pd.DataFrame] = []
    for category in ("车险", "非车险"):
        values = {
            str(row.年度): float(getattr(row, category))
            for row in frame.itertuples(index=False)
        }
        growth = _growth_rows(values, years, prior.get(category))
        if growth.empty:
            continue
        growth["类别"] = category
        parts.append(growth)
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


def _year_axis(years: list[str]) -> alt.X:
    return alt.X(
        "年度:O",
        sort=years,
        title=None,
        scale=alt.Scale(paddingInner=0.12, paddingOuter=0.30),
        axis=alt.Axis(
            labelAngle=0,
            labelFontSize=11,
            labelPadding=9,
            # 显式打开横轴轴线与刻度，避免主题配置把横轴整体收成一条无轴线的图。
            domain=True,
            ticks=True,
            tickSize=4,
            grid=False,
        ),
    )


def _growth_axis() -> alt.Axis:
    return alt.Axis(
        orient="right",
        format=".0f",
        labelExpr="datum.label + '%'",
        titlePadding=8,
        labelFontSize=10,
        tickCount=5,
        grid=False,
        labelPadding=6,
        offset=12,
    )


def _growth_scale(growth_frame: pd.DataFrame) -> alt.Scale:
    growth_min = min(0.0, float(growth_frame["增长率"].min()))
    growth_max = max(0.0, float(growth_frame["增长率"].max()))
    growth_pad = max(5.0, (growth_max - growth_min) * 0.15)
    return alt.Scale(
        domain=[growth_min - growth_pad, growth_max + growth_pad],
        zero=False,
        nice=False,
    )


def _zero_growth_rule(growth_scale: alt.Scale) -> alt.Chart:
    return (
        alt.Chart(pd.DataFrame({"增长率": [0.0]}))
        .mark_rule(color="#94A3B8", strokeDash=[4, 4], strokeWidth=1)
        .encode(y=alt.Y("增长率:Q", scale=growth_scale, axis=None))
    )


def _base_year_layers(
    years: list[str],
    growth_frame: pd.DataFrame,
    growth_scale: alt.Scale,
) -> list[alt.Chart]:
    growth_years = set(growth_frame["年度"].astype(str))
    base_year = next(
        (str(year) for year in years if str(year) not in growth_years),
        None,
    )
    if not base_year:
        return []
    base_frame = pd.DataFrame([{"年度": base_year, "增长率": 0.0}])
    return [
        alt.Chart(base_frame)
        .mark_point(
            size=72,
            filled=False,
            stroke="#94A3B8",
            strokeWidth=1.6,
        )
        .encode(
            x=alt.X("年度:O", sort=years),
            y=alt.Y("增长率:Q", scale=growth_scale, axis=None),
            tooltip=[
                alt.Tooltip("年度:N", title="年度"),
                alt.Tooltip("增长率:Q", title="同比基期（无上年数据）", format=".0f"),
            ],
        ),
        alt.Chart(base_frame)
        .mark_text(text="基期", dy=-12, fontSize=9, color="#64748B")
        .encode(
            x=alt.X("年度:O", sort=years),
            y=alt.Y("增长率:Q", scale=growth_scale, axis=None),
        ),
    ]


def build_industry_premium_total_chart(
    annual: pd.DataFrame,
    *,
    unit_mode: str = DEFAULT_DISPLAY_UNIT,
    width: int | str = "container",
) -> alt.Chart:
    """Annual signed-premium bars with a total YoY growth line."""
    frame, years, unit, value_format = _prepare_premium_series(annual, unit_mode)
    total_growth = _total_growth_frame(frame, years)
    max_value = float(frame["合计"].max())
    y_scale = alt.Scale(
        domain=[0, max_value * 1.16 if max_value > 0 else 1],
        zero=True,
        nice=False,
    )

    bars = (
        alt.Chart(frame)
        .mark_bar(
            size=28,
            cornerRadiusTopLeft=4,
            cornerRadiusTopRight=4,
            color=COBALT_BLUE,
        )
        .encode(
            x=_year_axis(years),
            y=alt.Y(
                "合计:Q",
                title=f"签单保费（{unit}）",
                scale=y_scale,
                axis=alt.Axis(
                    orient="left",
                    format=value_format,
                    tickCount=5,
                    domain=True,
                    ticks=True,
                    tickSize=4,
                    grid=True,
                ),
            ),
            tooltip=[
                alt.Tooltip("年度:N", title="年度"),
                alt.Tooltip("合计:Q", title=f"签单保费（{unit}）", format=",.1f"),
                alt.Tooltip("车险:Q", title=f"其中：车险（{unit}）", format=",.1f"),
                alt.Tooltip("非车险:Q", title=f"其中：非车险（{unit}）", format=",.1f"),
                alt.Tooltip("来源:N", title="数据来源"),
            ],
        )
    )
    total_labels = (
        alt.Chart(frame)
        .mark_text(
            dy=-9,
            fontSize=10,
            fontWeight="bold",
            color=PRIMARY_BLUE,
        )
        .encode(
            x=alt.X("年度:O", sort=years, axis=None),
            y=alt.Y("合计:Q", scale=y_scale, axis=None),
            text=alt.Text("合计:Q", format=value_format),
        )
    )

    layers: list[alt.Chart] = [bars, total_labels]
    if not total_growth.empty:
        growth_scale = _growth_scale(total_growth)
        growth_lines = (
            alt.Chart(total_growth)
            .mark_line(
                strokeWidth=2.2,
                color=PINK,
                point=alt.OverlayMarkDef(
                    size=58,
                    filled=True,
                    stroke="#FFFFFF",
                    strokeWidth=1.3,
                ),
            )
            .encode(
                x=alt.X("年度:O", sort=years, axis=None),
                y=alt.Y(
                    "增长率:Q",
                    title="同比增速",
                    scale=growth_scale,
                    axis=_growth_axis(),
                ),
                tooltip=[
                    alt.Tooltip("年度:N", title="年度"),
                    alt.Tooltip("增长率:Q", title="签单保费同比增速", format="+.1f"),
                    alt.Tooltip("当年金额:Q", title=f"当年签单保费（{unit}）", format=",.1f"),
                    alt.Tooltip("上年金额:Q", title=f"上年签单保费（{unit}）", format=",.1f"),
                    alt.Tooltip("基期年度:N", title="基期年度"),
                ],
            )
        )
        layers.extend(
            [_zero_growth_rule(growth_scale), *_base_year_layers(years, total_growth, growth_scale), growth_lines]
        )

    chart = alt.layer(*layers).resolve_scale(y="independent")
    chart = chart.properties(width=width)
    return apply_industry_chart_theme(chart, height=340)


def build_industry_premium_mix_chart(
    annual: pd.DataFrame,
    *,
    unit_mode: str = DEFAULT_DISPLAY_UNIT,
    width: int | str = "container",
) -> alt.Chart:
    """100% stacked motor/non-motor share bars with category YoY lines."""
    frame, years, unit, _ = _prepare_premium_series(annual, unit_mode)
    growth_frame = _category_growth_frame(frame, years)

    share_rows: list[dict[str, object]] = []
    label_rows: list[dict[str, object]] = []
    for row in frame.itertuples(index=False):
        car_share = float(row.车险占比)
        non_car_share = float(row.非车险占比)
        for category, share, amount in (
            ("车险", car_share, float(row.车险)),
            ("非车险", non_car_share, float(row.非车险)),
        ):
            share_rows.append(
                {
                    "年度": str(row.年度),
                    "类别": category,
                    "占比": share,
                    "金额": amount,
                    "来源": str(row.来源),
                }
            )
            if share >= 0.08:
                bottom = 0.0 if category == "车险" else car_share
                label_rows.append(
                    {
                        "年度": str(row.年度),
                        "类别": category,
                        "占比": share,
                        "标签位置": bottom + share / 2.0,
                    }
                )
    share_frame = pd.DataFrame(share_rows)
    label_frame = pd.DataFrame(label_rows)

    share_scale = alt.Scale(domain=[0, 1], zero=True, nice=False)
    bar_color_scale = alt.Scale(
        domain=["车险", "非车险"],
        range=[COBALT_BLUE, PACIFIC_BLUE],
    )
    line_color_scale = alt.Scale(
        domain=["车险", "非车险"],
        range=[PURPLE, PINK],
    )

    bars = (
        alt.Chart(share_frame)
        .mark_bar(size=30)
        .encode(
            x=_year_axis(years),
            y=alt.Y(
                "占比:Q",
                title="占比",
                scale=share_scale,
                axis=alt.Axis(
                    orient="left",
                    format=".0%",
                    tickCount=5,
                    domain=True,
                    ticks=True,
                    tickSize=4,
                    grid=True,
                ),
            ),
            color=alt.Color("类别:N", scale=bar_color_scale, legend=None),
            order=alt.Order("类别:N", sort="ascending"),
            tooltip=[
                alt.Tooltip("年度:N", title="年度"),
                alt.Tooltip("类别:N", title="构成"),
                alt.Tooltip("占比:Q", title="占签单保费", format=".1%"),
                alt.Tooltip("金额:Q", title=f"金额（{unit}）", format=",.1f"),
                alt.Tooltip("来源:N", title="数据来源"),
            ],
        )
    )
    share_labels = (
        alt.Chart(label_frame)
        .mark_text(fontSize=9, fontWeight="bold")
        .encode(
            x=alt.X("年度:O", sort=years, axis=None),
            y=alt.Y("标签位置:Q", scale=share_scale, axis=None),
            text=alt.Text("占比:Q", format=".0%"),
            color=alt.Color(
                "类别:N",
                scale=alt.Scale(
                    domain=["车险", "非车险"],
                    range=["#FFFFFF", "#00338D"],
                ),
                legend=None,
            ),
        )
    )

    layers: list[alt.Chart] = [bars, share_labels]
    if not growth_frame.empty:
        growth_scale = _growth_scale(growth_frame)
        growth_lines = (
            alt.Chart(growth_frame)
            .mark_line(
                strokeWidth=2.2,
                point=alt.OverlayMarkDef(
                    size=58,
                    filled=True,
                    stroke="#FFFFFF",
                    strokeWidth=1.3,
                ),
            )
            .encode(
                x=alt.X("年度:O", sort=years, axis=None),
                y=alt.Y(
                    "增长率:Q",
                    title="同比增速",
                    scale=growth_scale,
                    axis=_growth_axis(),
                ),
                color=alt.Color("类别:N", scale=line_color_scale, legend=None),
                tooltip=[
                    alt.Tooltip("年度:N", title="年度"),
                    alt.Tooltip("类别:N", title="同比类别"),
                    alt.Tooltip("增长率:Q", title="同比增速", format="+.1f"),
                    alt.Tooltip("当年金额:Q", title=f"当年金额（{unit}）", format=",.1f"),
                    alt.Tooltip("上年金额:Q", title=f"上年金额（{unit}）", format=",.1f"),
                    alt.Tooltip("基期年度:N", title="基期年度"),
                ],
            )
        )
        layers.extend(
            [_zero_growth_rule(growth_scale), *_base_year_layers(years, growth_frame, growth_scale), growth_lines]
        )

    chart = alt.layer(*layers).resolve_scale(y="independent", color="independent")
    chart = chart.properties(width=width)
    return apply_industry_chart_theme(chart, height=340)


def build_industry_premium_structure_chart(
    annual: pd.DataFrame,
    *,
    unit_mode: str = DEFAULT_DISPLAY_UNIT,
    width: int = 520,
) -> alt.Chart:
    """Backward-compatible side-by-side view of scale and mix."""
    total_chart = build_industry_premium_total_chart(
        annual,
        unit_mode=unit_mode,
        width=width,
    )
    mix_chart = build_industry_premium_mix_chart(
        annual,
        unit_mode=unit_mode,
        width=width,
    )
    # Child views cannot carry their own background/config inside hconcat;
    # lift them onto the combined chart instead.
    config = total_chart.config
    total_chart = total_chart.copy()
    total_chart.background = alt.Undefined
    total_chart.config = alt.Undefined
    mix_chart = mix_chart.copy()
    mix_chart.background = alt.Undefined
    mix_chart.config = alt.Undefined
    combined = alt.hconcat(total_chart, mix_chart, spacing=24)
    combined.background = "#FFFFFF"
    combined.config = config
    return combined
