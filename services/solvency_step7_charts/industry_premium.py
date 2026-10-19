"""行业整体保费规模与结构：年度堆叠柱 + 车险/非车险/合计趋势线。"""

from __future__ import annotations

import re

import altair as alt
import pandas as pd

from .primitives import apply_report_theme


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


def _annual_rows(
    frame: pd.DataFrame,
    code: str,
    years: list[int],
    scopes: tuple[str, ...] = ANNUAL_SCOPE_ALIASES,
) -> pd.DataFrame:
    """Pick one annual-cumulative value per company and year."""
    if frame.empty or code not in set(frame["指标编码"].astype(str)):
        return pd.DataFrame(columns=["公司", "年度", "数值"])
    work = frame[frame["指标编码"].astype(str).eq(code)].copy()
    work["年度"] = work["报告期"].map(_year_from_period)
    work["数值"] = pd.to_numeric(work["数值"], errors="coerce")
    work = work.dropna(subset=["年度", "数值"])
    work["年度"] = work["年度"].astype(int)
    work = work[work["年度"].isin(years)]
    if work.empty:
        return pd.DataFrame(columns=["公司", "年度", "数值"])

    scope = work["期间口径"].fillna("").astype(str).str.strip()
    is_annual = scope.isin(scopes)
    if not is_annual.any():
        return pd.DataFrame(columns=["公司", "年度", "数值"])
    work = work[is_annual].copy()
    work["_季度"] = work["报告期"].map(_quarter_from_period)
    # Annual figures must come from Q4.  Keep undated year-level rows as a
    # compatibility fallback, but never treat Q1-Q3 cumulative data as annual.
    work = work[work["_季度"].isin((0, 4))].copy()
    if work.empty:
        return pd.DataFrame(columns=["公司", "年度", "数值"])
    # Q4 is the annual-cumulative anchor.  If a workbook contains Q1-Q3
    # cumulative rows as well, keep Q4; if Q4 is absent, fall back to the
    # latest available quarter for that company/year.
    work = work.sort_values(["公司", "年度", "_季度"]).drop_duplicates(
        ["公司", "年度"],
        keep="last",
    )
    return work[["公司", "年度", "数值"]]


def _scale_anomaly_mask(values: pd.Series) -> pd.Series:
    """Flag values that are likely a unit-scale mistake versus neighbouring years."""
    numeric = pd.to_numeric(values, errors="coerce")
    valid = numeric[numeric > 0]
    mask = pd.Series(False, index=values.index)
    if len(valid) < 2:
        return mask
    for index, value in valid.items():
        others = valid.drop(index)
        if others.empty:
            continue
        reference = float(others.median())
        if reference <= 0:
            continue
        ratio = float(value) / reference
        if ratio < 0.05 or ratio > 20:
            mask.loc[index] = True
    return mask


def prepare_industry_premium_annual(
    frame: pd.DataFrame | None,
    *,
    years: int = 5,
    annual_scope: str = "",
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Aggregate annual industry premium and car/non-car split.

    Only ``本年累计数`` (or equivalent annual cumulative) records are used.
    Quarter-only records are deliberately excluded so a Q4 single-quarter
    number is never presented as a full-year total.
    """
    columns = [
        "年度",
        "车险",
        "非车险",
        "合计",
        "公司数",
        "车险公司数",
        "完整",
        "异常数",
    ]
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return pd.DataFrame(columns=columns), pd.DataFrame(), []

    work = frame.copy()
    for column in ("公司", "报告期", "指标编码", "期间口径", "数值"):
        if column not in work.columns:
            work[column] = ""
    work["公司"] = work["公司"].fillna("").astype(str).str.strip()
    work = work[work["公司"].ne("")]
    company_type = work.get("公司类型", pd.Series("", index=work.index))
    work = work[
        ~work["公司"].eq("行业合计")
        & ~company_type.fillna("").astype(str).str.strip().eq("行业合计")
    ].copy()

    premium_rows = work[
        work["指标编码"].astype(str).isin((TOTAL_PREMIUM_CODE, MOTOR_PREMIUM_CODE))
    ]
    annual_scopes = (
        (annual_scope,)
        if str(annual_scope or "").strip()
        else ANNUAL_SCOPE_ALIASES
    )
    annual_premium_rows = premium_rows[
        premium_rows["期间口径"].fillna("").astype(str).str.strip().isin(annual_scopes)
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
    if not available_years:
        return pd.DataFrame(columns=columns), pd.DataFrame(), []
    selected_years = available_years[-max(1, int(years or 5)) :]

    total = _annual_rows(work, TOTAL_PREMIUM_CODE, selected_years, annual_scopes)
    motor = _annual_rows(work, MOTOR_PREMIUM_CODE, selected_years, annual_scopes)
    total_map = (
        total.set_index(["公司", "年度"])["数值"].to_dict() if not total.empty else {}
    )
    motor_map = (
        motor.set_index(["公司", "年度"])["数值"].to_dict() if not motor.empty else {}
    )

    companies = sorted(work["公司"].unique().tolist())
    annual_rows: list[dict[str, object]] = []
    detail_rows: list[dict[str, object]] = []
    for year in selected_years:
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
            anomaly_mask = _scale_anomaly_mask(detail_frame["合计"])
            detail_frame.loc[anomaly_mask, "状态"] = "异常（量级偏小）"

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
            }
        )
        if not detail_frame.empty:
            detail_rows.extend(detail_frame.to_dict("records"))

    annual = pd.DataFrame(annual_rows, columns=columns)
    detail = pd.DataFrame(detail_rows)
    return annual, detail, companies


def build_industry_premium_structure_chart(
    annual: pd.DataFrame,
    *,
    unit_mode: str = DEFAULT_DISPLAY_UNIT,
) -> alt.Chart:
    """Build the annual stacked-bar plus three-line industry premium chart."""
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
    all_years = available.loc[
        available["合计"].notna() | available["车险"].notna(),
        "年度",
    ].astype(str).tolist()
    available["年度"] = available["年度"].astype(str)
    available = available.sort_values("年度").reset_index(drop=True)
    unit = unit_mode if unit_mode in DISPLAY_UNITS else DEFAULT_DISPLAY_UNIT
    divisor = UNIT_TO_WAN.get(unit, UNIT_TO_WAN[DEFAULT_DISPLAY_UNIT])
    value_format = ",.1f" if unit == "十亿元" else ",.0f"
    years = all_years

    bar_rows: list[dict[str, object]] = []
    total_rows: list[dict[str, object]] = []
    line_rows: list[dict[str, object]] = []
    for row in available.itertuples(index=False):
        year = str(row.年度)
        car = None if pd.isna(row.车险) else float(row.车险) / divisor
        non_car = None if pd.isna(row.非车险) else float(row.非车险) / divisor
        total = None if pd.isna(row.合计) else float(row.合计) / divisor
        if total is None and car is not None and non_car is not None:
            total = car + non_car
        if total is None:
            continue
        car = 0.0 if car is None else car
        non_car = max(total - car, 0.0) if non_car is None else non_car
        if car is not None:
            bar_rows.append({"年度": year, "类别": "车险", "金额": car})
        if non_car is not None:
            bar_rows.append({"年度": year, "类别": "非车险", "金额": non_car})
        total_rows.append({"年度": year, "金额": total})
        line_rows.extend(
            [
                {"年度": year, "类别": "车险", "金额": car},
                {"年度": year, "类别": "非车险", "金额": non_car},
                {"年度": year, "类别": "合计", "金额": total},
            ]
        )

    if not total_rows:
        raise ValueError("没有可绘制的行业年度保费合计。")
    bar_frame = pd.DataFrame(bar_rows)
    line_frame = pd.DataFrame(line_rows)
    total_frame = pd.DataFrame(total_rows)
    max_value = float(total_frame["金额"].max()) if not total_frame.empty else 0.0
    y_scale = alt.Scale(
        domain=[0, max_value * 1.16 if max_value > 0 else 1],
        zero=True,
        nice=False,
    )
    bar_color_scale = alt.Scale(
        domain=["车险", "非车险", "未拆分"],
        range=["#1E49E2", "#00B8F5", "#D9E2EC"],
    )
    line_color_scale = alt.Scale(
        domain=["车险", "非车险", "合计"],
        range=["#7213EA", "#FD349C", "#00338D"],
    )

    bars = (
        alt.Chart(bar_frame)
        .mark_bar(size=54, cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
        .encode(
            x=alt.X(
                "年度:O",
                sort=years,
                title=None,
                axis=alt.Axis(labelAngle=0, labelFontSize=11, labelPadding=7),
            ),
            y=alt.Y(
                "金额:Q",
                title=f"保费（{unit}）",
                scale=y_scale,
                axis=alt.Axis(format=value_format, tickCount=5),
            ),
            color=alt.Color("类别:N", scale=bar_color_scale, legend=None),
            order=alt.Order("类别:N", sort="ascending"),
            tooltip=[
                alt.Tooltip("年度:N", title="年度"),
                alt.Tooltip("类别:N", title="构成"),
                alt.Tooltip("金额:Q", title=f"金额（{unit}）", format=",.1f"),
            ],
        )
    )
    total_labels = (
        alt.Chart(total_frame)
        .mark_text(
            dy=-10,
            fontSize=10,
            fontWeight="bold",
            color="#00338D",
        )
        .encode(
            x=alt.X("年度:O", sort=years),
            y=alt.Y("金额:Q", scale=y_scale),
            text=alt.Text("金额:Q", format=value_format),
        )
    )
    lines = (
        alt.Chart(line_frame)
        .mark_line(
            strokeWidth=2.5,
            point=alt.OverlayMarkDef(
                size=62,
                filled=True,
                stroke="#FFFFFF",
                strokeWidth=1.3,
            ),
        )
        .encode(
            x=alt.X("年度:O", sort=years),
            y=alt.Y("金额:Q", scale=y_scale),
            color=alt.Color("类别:N", scale=line_color_scale, legend=None),
            tooltip=[
                alt.Tooltip("年度:N", title="年度"),
                alt.Tooltip("类别:N", title="趋势线"),
                alt.Tooltip("金额:Q", title=f"金额（{unit}）", format=",.1f"),
            ],
        )
    )

    chart = (
        alt.layer(bars, total_labels, lines)
        .resolve_scale(color="independent")
        .properties(width="container", height=390)
        .configure_axisY(
            grid=True,
            gridColor="#E6EAF0",
            gridOpacity=0.8,
        )
    )
    return apply_report_theme(chart)
