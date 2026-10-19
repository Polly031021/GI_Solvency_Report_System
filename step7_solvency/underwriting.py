"""承保效益：近五年综合成本率与保费排名的年度散点图。

页面口径：
- 每个年度一张图，横轴是当年保费收入排名，纵轴是综合成本率；
- 纵轴以 100% 为中心对称展开，100% 虚线代表盈亏平衡；
- 综合成本率低于 100% 计为盈利，高于 100% 计为亏损；
- 五张年度图之后补一张盈利/亏损公司数折线图。
"""

from __future__ import annotations

import math
import re

import altair as alt
import pandas as pd
import streamlit as st

from services.report_style import (
    BREAKEVEN_COLOR,
    LOSS_BG,
    LOSS_COLOR,
    NEUTRAL_COLOR,
    PROFIT_BG,
    PROFIT_COLOR,
    REPORT_AXIS_COLOR,
    REPORT_DOMAIN_COLOR,
    REPORT_GRID_COLOR,
)


DEFAULT_BREAKEVEN = 100.0

_REQUIRED_COLUMNS = ("公司", "指标编码", "报告期", "数值")
_SCOPE_PRIORITY = {
    "本年累计数": 0,
    "累计数": 0,
    "本季度数": 1,
    "本季度末数": 2,
    "期末数": 2,
    "上季度数": 8,
    "上季度末数": 9,
}
_CHINESE_QUARTERS = {"一": 1, "二": 2, "三": 3, "四": 4}


def _annual_year(value: object) -> int | None:
    """Return the calendar year for an annual (Q4 / year-end) period."""
    text = str(value or "").strip()
    year_match = re.search(r"(20\d{2})", text)
    if not year_match:
        return None
    year = int(year_match.group(1))

    quarter_match = re.search(r"(?:Q|q)\s*([1-4])", text)
    if not quarter_match:
        quarter_match = re.search(r"第?\s*([1-4])\s*季度?", text)
    if quarter_match:
        return year if int(quarter_match.group(1)) == 4 else None

    chinese_quarter = re.search(r"第?\s*([一二三四])\s*季度?", text)
    if chinese_quarter:
        return year if _CHINESE_QUARTERS.get(chinese_quarter.group(1)) == 4 else None

    month_match = re.search(r"(?:20\d{2})[-/年]\s*(\d{1,2})(?:[-/月]|$)", text)
    if month_match:
        month = int(month_match.group(1))
        return year if month in (10, 11, 12) else None
    return year


def _scope_priority(value: object) -> int:
    text = str(value or "").strip()
    return _SCOPE_PRIORITY.get(text, 5)


def _normalise_company(series: pd.Series) -> pd.Series:
    return (
        series.fillna("")
        .astype(str)
        .str.strip()
        .str.replace(
            r"[（(]\s*(?:偿付能力|财险|寿险|合并|母公司|集团)\s*[)）]\s*$",
            "",
            regex=True,
        )
        .str.strip()
    )


def _metric_rows(df: pd.DataFrame, codes: tuple[str, ...]) -> pd.DataFrame:
    """Pick one annual observation per company/metric, preferring cumulative data."""
    parts: list[pd.DataFrame] = []
    for code_priority, code in enumerate(codes):
        part = df[df["指标编码"].eq(code)].copy()
        if part.empty:
            continue
        part["_code_priority"] = code_priority
        part["_scope_priority"] = part["期间口径"].map(_scope_priority)
        part["年度"] = part["报告期"].map(_annual_year)
        part = part[part["年度"].notna()].copy()
        if part.empty:
            continue
        part["年度"] = part["年度"].astype(int)
        parts.append(part)

    if not parts:
        return pd.DataFrame(columns=["年度", "公司", "数值", "单位", "期间口径", "报告期"])

    combined = pd.concat(parts, ignore_index=True)
    combined = combined.dropna(subset=["数值"])
    combined = combined.sort_values(
        ["年度", "公司", "_scope_priority", "_code_priority", "报告期"],
        kind="mergesort",
    )
    combined = combined.drop_duplicates(["年度", "公司"], keep="first")
    return combined[
        ["年度", "公司", "数值", "单位", "期间口径", "报告期"]
    ].reset_index(drop=True)


def _ratio_to_percent(frame: pd.DataFrame) -> pd.DataFrame:
    """Convert combined-ratio values such as 0.98 / 99.0 to 98 / 99 percent."""
    result = frame.copy()
    result["数值"] = pd.to_numeric(result["数值"], errors="coerce")
    units = result["单位"].fillna("").astype(str).str.strip()
    percent_unit = units.str.contains(r"%|％", regex=True)

    decimal_percent = percent_unit & result["数值"].abs().le(2.0)
    result.loc[decimal_percent, "数值"] = result.loc[decimal_percent, "数值"] * 100.0

    unknown_unit = ~percent_unit
    for _, group in result[unknown_unit].groupby("年度"):
        values = group["数值"].dropna().abs()
        if not values.empty and float(values.median()) <= 1.5:
            result.loc[group.index, "数值"] = result.loc[group.index, "数值"] * 100.0
    return result


def _symmetric_axis_domain(
    values: pd.Series | list[float],
    center: float = DEFAULT_BREAKEVEN,
    *,
    max_span: float = 80.0,
) -> tuple[float, float]:
    """Return a robust y-axis domain centred on the break-even line.

    A plain min/max range is unusable for a 90-company industry pool because a
    handful of extreme combined ratios (e.g. 400%+) would compress almost every
    company into a narrow band around 100%.  Use the 5th–95th percentile to set
    the visible range, then place true outliers on the boundary with a distinct
    marker in the chart.  The midpoint remains exactly 100%.
    """
    numeric = pd.to_numeric(pd.Series(values), errors="coerce").dropna()
    numeric = numeric[numeric.map(lambda value: math.isfinite(float(value)))]
    if numeric.empty:
        return float(center) - 5.0, float(center) + 5.0

    if len(numeric) >= 8:
        lower_reference = float(numeric.quantile(0.05))
        upper_reference = float(numeric.quantile(0.95))
    else:
        lower_reference = float(numeric.min())
        upper_reference = float(numeric.max())

    max_deviation = max(
        abs(lower_reference - float(center)),
        abs(upper_reference - float(center)),
    )
    if not math.isfinite(max_deviation) or max_deviation <= 0:
        max_deviation = 5.0
    # Leave breathing room above/below the plotted values.
    max_deviation *= 1.10
    step = 5.0 if max_deviation <= 20 else 10.0 if max_deviation <= 60 else 20.0
    span = max(step, math.ceil(max_deviation / step) * step)
    span = min(float(max_span), span)
    return float(center) - span, float(center) + span


def prepare_underwriting_annual(
    data: pd.DataFrame,
    *,
    years: int = 5,
    breakeven: float = DEFAULT_BREAKEVEN,
    premium_codes: tuple[str, ...] = ("SIGNED_PREMIUM", "PREMIUM_INCOME"),
    combined_code: str = "COMBINED_RATIO",
) -> pd.DataFrame:
    """Merge annual combined ratio and premium data and rank companies by premium.

    The returned frame contains one row per company/year with:
    年度, 公司, 保费, 保费排名, 综合成本率, 盈亏, 综合成本率口径, 保费口径.
    """
    empty_columns = [
        "年度",
        "公司",
        "保费",
        "保费单位",
        "保费排名",
        "综合成本率",
        "盈亏",
        "综合成本率口径",
        "保费口径",
        "综合成本率报告期",
        "保费报告期",
    ]
    if not isinstance(data, pd.DataFrame) or data.empty:
        return pd.DataFrame(columns=empty_columns)
    if any(column not in data.columns for column in _REQUIRED_COLUMNS):
        return pd.DataFrame(columns=empty_columns)

    df = data.copy()
    df["公司"] = _normalise_company(df["公司"])
    df["指标编码"] = df["指标编码"].fillna("").astype(str).str.strip()
    df["报告期"] = df["报告期"].fillna("").astype(str).str.strip()
    for optional in ("单位", "期间口径"):
        if optional not in df.columns:
            df[optional] = ""
    df["期间口径"] = df["期间口径"].fillna("").astype(str).str.strip()
    df["单位"] = df["单位"].fillna("").astype(str).str.strip()
    df["数值"] = pd.to_numeric(
        df["数值"]
        .astype(str)
        .str.replace("%", "", regex=False)
        .str.replace("％", "", regex=False)
        .str.replace(",", "", regex=False)
        .str.replace("，", "", regex=False),
        errors="coerce",
    )
    df = df[
        df["公司"].ne("")
        & df["指标编码"].ne("")
        & df["数值"].notna()
    ].copy()

    ratio = _metric_rows(df, (str(combined_code or "COMBINED_RATIO").strip(),))
    if ratio.empty:
        return pd.DataFrame(columns=empty_columns)
    ratio = _ratio_to_percent(ratio)
    ratio = ratio.rename(
        columns={
            "数值": "综合成本率",
            "期间口径": "综合成本率口径",
            "报告期": "综合成本率报告期",
        }
    )

    premium_codes_clean = tuple(
        dict.fromkeys(
            str(code).strip()
            for code in (*premium_codes, "SIGNED_PREMIUM", "PREMIUM_INCOME")
            if str(code).strip()
        )
    )
    premium = _metric_rows(df, premium_codes_clean)
    if premium.empty:
        return pd.DataFrame(columns=empty_columns)
    premium = premium.rename(
        columns={
            "数值": "保费",
            "单位": "保费单位",
            "期间口径": "保费口径",
            "报告期": "保费报告期",
        }
    )

    annual = ratio.merge(
        premium[["年度", "公司", "保费", "保费单位", "保费口径", "保费报告期"]],
        on=["年度", "公司"],
        how="inner",
    )
    annual["综合成本率"] = pd.to_numeric(annual["综合成本率"], errors="coerce")
    annual["保费"] = pd.to_numeric(annual["保费"], errors="coerce")
    annual = annual[
        annual["综合成本率"].between(0.0, 1000.0, inclusive="neither")
        & annual["保费"].ge(0.0)
    ].copy()
    if annual.empty:
        return pd.DataFrame(columns=empty_columns)

    annual = annual.sort_values(
        ["年度", "保费", "公司"],
        ascending=[True, False, True],
        kind="mergesort",
    )
    annual["保费排名"] = annual.groupby("年度").cumcount() + 1

    annual["盈亏"] = "盈亏平衡"
    annual.loc[annual["综合成本率"] < float(breakeven), "盈亏"] = "盈利"
    annual.loc[annual["综合成本率"] > float(breakeven), "盈亏"] = "亏损"

    window = max(1, int(years or 5))
    available_years = sorted(annual["年度"].unique().tolist())
    selected_years = available_years[-window:]
    annual = annual[annual["年度"].isin(selected_years)].copy()
    return annual[empty_columns].reset_index(drop=True)


def annual_underwriting_counts(
    annual: pd.DataFrame,
    *,
    breakeven: float = DEFAULT_BREAKEVEN,
) -> pd.DataFrame:
    """Summarise profitable / loss-making company counts for every year."""
    columns = ["年度", "盈利", "亏损", "盈亏平衡", "有效公司数"]
    if not isinstance(annual, pd.DataFrame) or annual.empty:
        return pd.DataFrame(columns=columns)

    frame = annual.copy()
    frame["盈亏"] = frame["盈亏"].fillna("").astype(str).str.strip()
    counts = (
        frame.groupby(["年度", "盈亏"])
        .size()
        .unstack(fill_value=0)
        .reindex(columns=["盈利", "亏损", "盈亏平衡"], fill_value=0)
        .reset_index()
    )
    counts["有效公司数"] = counts[["盈利", "亏损", "盈亏平衡"]].sum(axis=1)
    return counts[columns].sort_values("年度").reset_index(drop=True)


def build_underwriting_year_chart(
    annual: pd.DataFrame,
    year: int,
    *,
    breakeven: float = DEFAULT_BREAKEVEN,
) -> alt.LayerChart | None:
    """Build one annual premium-rank / combined-ratio scatter chart."""
    if not isinstance(annual, pd.DataFrame) or annual.empty:
        return None
    frame = annual[annual["年度"].eq(int(year))].copy()
    if frame.empty:
        return None

    frame["综合成本率"] = pd.to_numeric(frame["综合成本率"], errors="coerce")
    frame["保费排名"] = pd.to_numeric(frame["保费排名"], errors="coerce")
    frame = frame.dropna(subset=["综合成本率", "保费排名", "公司"])
    if frame.empty:
        return None

    center = float(breakeven)
    lower, upper = _symmetric_axis_domain(frame["综合成本率"], center)
    max_rank = int(frame["保费排名"].max())
    x_domain = (0.4, max_rank + 0.6)
    point_count = len(frame)
    point_size = 150 if point_count <= 30 else 90 if point_count <= 60 else 48
    stroke_width = 1.8 if point_count <= 30 else 1.2 if point_count <= 60 else 0.7
    opacity = 0.96 if point_count <= 30 else 0.88
    color_scale = alt.Scale(
        domain=["盈利", "亏损", "盈亏平衡"],
        range=[PROFIT_COLOR, LOSS_COLOR, BREAKEVEN_COLOR],
    )
    y_scale = alt.Scale(domain=[lower, upper], zero=False, nice=False)

    in_range = frame[
        frame["综合成本率"].between(lower, upper, inclusive="both")
    ].copy()
    outliers = frame[
        (frame["综合成本率"] < lower) | (frame["综合成本率"] > upper)
    ].copy()
    if not outliers.empty:
        boundary_padding = (upper - lower) * 0.025
        outliers["_绘制值"] = lower + boundary_padding
        outliers.loc[
            outliers["综合成本率"] > upper, "_绘制值"
        ] = upper - boundary_padding
        outliers["_范围"] = "低于纵轴下限"
        outliers.loc[
            outliers["综合成本率"] > upper, "_范围"
        ] = "高于纵轴上限"

    # Keep the profit/loss background bands colour-fixed.  If they use a
    # colour encoding, Altair merges the layer colour scales and the company
    # points inherit the pale band palette instead of the profit/loss palette.
    profit_band = (
        alt.Chart(pd.DataFrame([{"y0": lower, "y1": center}]))
        .mark_rect(color=PROFIT_BG, opacity=1)
        .encode(
            y=alt.Y("y0:Q", title=None, scale=y_scale),
            y2=alt.Y2("y1:Q"),
        )
    )
    loss_band = (
        alt.Chart(pd.DataFrame([{"y0": center, "y1": upper}]))
        .mark_rect(color=LOSS_BG, opacity=1)
        .encode(
            y=alt.Y("y0:Q", title=None, scale=y_scale),
            y2=alt.Y2("y1:Q"),
        )
    )

    break_even_rule = (
        alt.Chart(pd.DataFrame({"盈亏平衡": [center]}))
        .mark_rule(color=NEUTRAL_COLOR, strokeDash=[6, 4], strokeWidth=1.6)
        .encode(y=alt.Y("盈亏平衡:Q", scale=y_scale))
    )
    break_even_label = (
        alt.Chart(
            pd.DataFrame(
                [{"x": 0.55, "y": center, "标签": "100%"}]
            )
        )
        .mark_text(
            align="left",
            dx=0,
            dy=-6,
            fontSize=9,
            fontWeight=600,
            color=NEUTRAL_COLOR,
        )
        .encode(
            x=alt.X("x:Q", scale=alt.Scale(domain=list(x_domain), nice=False)),
            y=alt.Y("y:Q", scale=y_scale),
            text=alt.Text("标签:N"),
        )
    )

    rank_axis = alt.Axis(
        tickMinStep=1,
        labelFontSize=9,
        titleFontSize=10,
        format=".0f",
        **(
            {"values": list(range(1, max_rank + 1))}
            if max_rank <= 15
            else {"tickCount": 10}
        ),
    )
    tooltip = [
        alt.Tooltip("公司:N"),
        alt.Tooltip("保费排名:Q", title="保费排名"),
        alt.Tooltip("综合成本率:Q", title="综合成本率", format=".2f"),
        alt.Tooltip("保费:Q", title="保费", format=",.0f"),
        alt.Tooltip("保费单位:N", title="保费单位"),
        alt.Tooltip("盈亏:N", title="盈亏状态"),
    ]
    points = (
        alt.Chart(in_range)
        .mark_circle(
            size=point_size,
            filled=True,
            opacity=opacity,
            stroke="#FFFFFF",
            strokeWidth=stroke_width,
        )
        .encode(
            x=alt.X(
                "保费排名:Q",
                title="保费收入排名（1 = 最高）",
                scale=alt.Scale(domain=list(x_domain), nice=False),
                axis=rank_axis,
            ),
            y=alt.Y(
                "综合成本率:Q",
                title="综合成本率（%）",
                scale=y_scale,
                axis=alt.Axis(
                    format=".0f",
                    tickCount=5,
                    labelFontSize=9,
                    titleFontSize=10,
                ),
            ),
            color=alt.Color("盈亏:N", scale=color_scale, legend=None),
            tooltip=tooltip,
        )
    )

    label_style = {
        "fontSize": 9,
        "fontWeight": 600,
        "color": REPORT_AXIS_COLOR,
    }
    above = in_range[in_range["综合成本率"] >= center]
    below = in_range[in_range["综合成本率"] < center]
    labels_above = (
        alt.Chart(above)
        .mark_text(dy=-13, **label_style)
        .encode(
            x=alt.X("保费排名:Q", scale=alt.Scale(domain=list(x_domain), nice=False)),
            y=alt.Y("综合成本率:Q", scale=y_scale),
            text=alt.Text("公司:N"),
        )
    )
    labels_below = (
        alt.Chart(below)
        .mark_text(dy=17, **label_style)
        .encode(
            x=alt.X("保费排名:Q", scale=alt.Scale(domain=list(x_domain), nice=False)),
            y=alt.Y("综合成本率:Q", scale=y_scale),
            text=alt.Text("公司:N"),
        )
    )

    layers: list[alt.Chart] = [profit_band, loss_band, break_even_rule, points]
    if not outliers.empty:
        outlier_tooltip = [
            *tooltip,
            alt.Tooltip("_范围:N", title="显示范围"),
        ]
        outlier_above = outliers[outliers["综合成本率"] > upper]
        outlier_below = outliers[outliers["综合成本率"] <= lower]
        for outlier_frame, shape in (
            (outlier_above, "triangle-up"),
            (outlier_below, "triangle-down"),
        ):
            if outlier_frame.empty:
                continue
            layers.append(
                alt.Chart(outlier_frame)
                .mark_point(
                    size=point_size + 18,
                    shape=shape,
                    filled=True,
                    opacity=0.95,
                    stroke="#FFFFFF",
                    strokeWidth=stroke_width,
                )
                .encode(
                    x=alt.X(
                        "保费排名:Q",
                        scale=alt.Scale(domain=list(x_domain), nice=False),
                    ),
                    y=alt.Y(
                        "_绘制值:Q",
                        title="综合成本率（%）",
                        scale=y_scale,
                    ),
                    color=alt.Color("盈亏:N", scale=color_scale, legend=None),
                    tooltip=outlier_tooltip,
                )
            )
    # With a very large company pool, point labels would overlap; keep the
    # company name in the tooltip and preserve the 100% reference label.
    if max_rank <= 20:
        layers.extend([labels_above, labels_below])
    layers.append(break_even_label)

    return (
        alt.layer(*layers)
        .properties(width=540, height=275, background="#FFFFFF")
        .configure_view(stroke=None)
        .configure_axis(
            gridColor=REPORT_GRID_COLOR,
            gridOpacity=0.65,
            domainColor=REPORT_DOMAIN_COLOR,
            labelColor=REPORT_AXIS_COLOR,
            titleColor=REPORT_AXIS_COLOR,
        )
    )


def build_underwriting_count_chart(
    counts: pd.DataFrame,
    *,
    years: list[int] | tuple[int, ...] | None = None,
) -> alt.LayerChart | None:
    """Build the profitable / loss-making company-count trend line chart."""
    if not isinstance(counts, pd.DataFrame) or counts.empty:
        return None
    frame = counts.copy()
    if years is not None:
        frame = frame[frame["年度"].isin(list(years))]
    if frame.empty:
        return None

    frame["年度"] = frame["年度"].astype(int).astype(str)
    year_order = (
        [str(year) for year in years]
        if years is not None
        else sorted(frame["年度"].unique().tolist())
    )
    long = frame.melt(
        id_vars=["年度"],
        value_vars=["盈利", "亏损"],
        var_name="类型",
        value_name="公司数",
    )
    max_count = int(pd.to_numeric(long["公司数"], errors="coerce").fillna(0).max())
    color_scale = alt.Scale(
        domain=["盈利", "亏损"],
        range=[PROFIT_COLOR, LOSS_COLOR],
    )
    x_scale = alt.Scale(domain=year_order, padding=0.35)
    y_scale = alt.Scale(domain=[0, max(1, max_count + 1)], nice=False)

    line = (
        alt.Chart(long)
        .mark_line(
            strokeWidth=2.5,
            point=alt.OverlayMarkDef(filled=True, size=85, stroke="#FFFFFF", strokeWidth=1.6),
        )
        .encode(
            x=alt.X(
                "年度:O",
                title="年度",
                sort=year_order,
                scale=x_scale,
                axis=alt.Axis(labelAngle=0, labelFontSize=10, titleFontSize=10),
            ),
            y=alt.Y(
                "公司数:Q",
                title="公司数（家）",
                scale=y_scale,
                axis=alt.Axis(format=".0f", tickMinStep=1, labelFontSize=9, titleFontSize=10),
            ),
            color=alt.Color(
                "类型:N",
                scale=color_scale,
                legend=alt.Legend(
                    title=None,
                    orient="top",
                    direction="horizontal",
                    symbolType="stroke",
                ),
            ),
            tooltip=[
                alt.Tooltip("年度:O", title="年度"),
                alt.Tooltip("类型:N"),
                alt.Tooltip("公司数:Q", title="公司数", format=".0f"),
            ],
        )
    )
    labels = (
        alt.Chart(long)
        .mark_text(dy=-11, fontSize=10, fontWeight=600)
        .encode(
            x=alt.X("年度:O", sort=year_order, scale=x_scale),
            y=alt.Y("公司数:Q", scale=y_scale),
            text=alt.Text("公司数:Q", format=".0f"),
            color=alt.Color("类型:N", scale=color_scale, legend=None),
            detail=alt.Detail("类型:N"),
        )
    )

    return (
        alt.layer(line, labels)
        .properties(width=540, height=275, background="#FFFFFF")
        .configure_view(stroke=None)
        .configure_axis(
            gridColor=REPORT_GRID_COLOR,
            gridOpacity=0.65,
            domainColor=REPORT_DOMAIN_COLOR,
            labelColor=REPORT_AXIS_COLOR,
            titleColor=REPORT_AXIS_COLOR,
        )
    )


def _counts_html(row: pd.Series) -> str:
    profit = int(row.get("盈利", 0) or 0)
    loss = int(row.get("亏损", 0) or 0)
    even = int(row.get("盈亏平衡", 0) or 0)
    valid = int(row.get("有效公司数", profit + loss + even) or 0)
    chips = [
        f"<span class='s7-uw-chip s7-uw-chip--profit'>盈利&nbsp;<b>{profit}</b>&nbsp;家</span>",
        f"<span class='s7-uw-chip s7-uw-chip--loss'>亏损&nbsp;<b>{loss}</b>&nbsp;家</span>",
    ]
    if even:
        chips.append(
            f"<span class='s7-uw-chip s7-uw-chip--even'>盈亏平衡&nbsp;<b>{even}</b>&nbsp;家</span>"
        )
    chips.append(f"<span class='s7-uw-muted'>有效样本 {valid} 家</span>")
    return "<div class='s7-uw-counts'>" + "".join(chips) + "</div>"


def _render_year_card(
    annual: pd.DataFrame,
    counts: pd.DataFrame,
    year: int,
    *,
    breakeven: float,
    key_prefix: str,
) -> None:
    st.markdown(
        f"<div class='s7-uw-year-title'>{int(year)} 年</div>"
        "<div class='s7-uw-year-sub'>综合成本率与保费收入排名</div>",
        unsafe_allow_html=True,
    )
    chart = build_underwriting_year_chart(annual, int(year), breakeven=breakeven)
    if chart is None:
        st.markdown(
            "<div class='s7-uw-empty'>该年度没有同时具备综合成本率与保费数据的公司。</div>",
            unsafe_allow_html=True,
        )
        return
    st.altair_chart(
        chart,
        width="stretch",
        key=f"{key_prefix}_underwriting_year_{int(year)}",
    )
    count_row = counts[counts["年度"].eq(int(year))]
    if count_row.empty:
        st.markdown(
            "<div class='s7-uw-counts'><span class='s7-uw-muted'>暂无盈亏统计</span></div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(_counts_html(count_row.iloc[0]), unsafe_allow_html=True)

    year_frame = annual[annual["年度"].eq(int(year))]
    if not year_frame.empty:
        lower, upper = _symmetric_axis_domain(
            year_frame["综合成本率"],
            float(breakeven),
        )
        outlier_count = int(
            (
                (year_frame["综合成本率"] < lower)
                | (year_frame["综合成本率"] > upper)
            ).sum()
        )
        if outlier_count:
            st.caption(
                f"▲ {outlier_count} 家超出纵轴显示范围，已用边界三角标记；"
                "实际值见悬停提示或下方明细。"
            )
        if len(year_frame) < 5:
            st.caption("该年度有效样本较少，请结合下方明细核对数据完整性。")


def render_underwriting_efficiency_scatter(
    data: pd.DataFrame,
    chart_config: dict | None = None,
    key_prefix: str = "",
) -> None:
    """Render five annual scatter charts plus a profitable/loss count trend."""
    # The shared s7-uw-* card/legend styles live in step7_solvency.theme.
    cfg = dict(chart_config or {})
    try:
        year_window = min(5, max(1, int(cfg.get("years", 5) or 5)))
    except (TypeError, ValueError):
        year_window = 5
    try:
        breakeven = float(cfg.get("breakeven", DEFAULT_BREAKEVEN) or DEFAULT_BREAKEVEN)
    except (TypeError, ValueError):
        breakeven = DEFAULT_BREAKEVEN
    premium_metric = str(cfg.get("x_metric") or "SIGNED_PREMIUM").strip()
    premium_codes = tuple(
        dict.fromkeys(
            code
            for code in (premium_metric, "SIGNED_PREMIUM", "PREMIUM_INCOME")
            if code
        )
    )

    annual = prepare_underwriting_annual(
        data,
        years=year_window,
        breakeven=breakeven,
        premium_codes=premium_codes,
    )
    if annual.empty:
        st.warning(
            "当前数据缺少可配对的年度综合成本率（COMBINED_RATIO）与保费收入"
            "（SIGNED_PREMIUM / PREMIUM_INCOME），无法绘制承保效益年度图。"
        )
        return

    counts = annual_underwriting_counts(annual, breakeven=breakeven)
    years = sorted(annual["年度"].dropna().astype(int).unique().tolist())
    count_chart = build_underwriting_count_chart(counts, years=years)

    year_label = f"{years[0]}–{years[-1]}" if years else "—"
    st.caption(
        f"展示 {year_label} 共 {len(years)} 个年度 ｜ "
        "横轴为当年保费收入排名（1 = 最高），纵轴为综合成本率；"
        "纵轴以 100% 为中心对称展开。"
    )
    st.markdown(
        "<div class='s7-uw-legend'>"
        f"<span><i class='s7-uw-dot' style='background:{PROFIT_COLOR}'></i>盈利（综合成本率 &lt; 100%）</span>"
        f"<span><i class='s7-uw-dot' style='background:{LOSS_COLOR}'></i>亏损（综合成本率 &gt; 100%）</span>"
        f"<span><i class='s7-uw-dot' style='background:{BREAKEVEN_COLOR}'></i>盈亏平衡（= 100%）</span>"
        "<span><i class='s7-uw-dash'></i>100% 盈亏平衡线</span>"
        "</div>",
        unsafe_allow_html=True,
    )
    outlier_total = 0
    for _year in years:
        _year_frame = annual[annual["年度"].eq(int(_year))]
        _lower, _upper = _symmetric_axis_domain(
            _year_frame["综合成本率"],
            float(breakeven),
        )
        outlier_total += int(
            (
                (_year_frame["综合成本率"] < _lower)
                | (_year_frame["综合成本率"] > _upper)
            ).sum()
        )
    if outlier_total:
        st.caption(
            "纵轴采用稳健显示范围（约 5%–95% 分位）；"
            "超出范围的极端值用三角形标记在边界处，实际值见悬停提示或下方明细。"
        )
    if len(years) < year_window:
        st.caption(
            f"当前数据仅覆盖 {len(years)} 个可用年度；"
            f"系统已按现有年度展示，目标窗口为最近 {year_window} 年。"
        )

    year_slots: list[int | None] = [*years, *([None] * max(0, 5 - len(years)))]
    cells: list[tuple[str, int | None]] = [
        *(("year", year) for year in year_slots),
        ("counts", None),
    ]
    st.markdown(
        "<div class='solvency-underwriting-grid' aria-hidden='true'></div>",
        unsafe_allow_html=True,
    )
    missing_notice_rendered = False
    for row_start in range(0, 6, 2):
        with st.container(
            key=f"underwriting_row_{key_prefix}_{row_start // 2}",
        ):
            columns = st.columns(2, gap="large", vertical_alignment="top")
            for column, (kind, payload) in zip(columns, cells[row_start : row_start + 2]):
                with column:
                    if kind == "year" and payload is not None:
                        _render_year_card(
                            annual,
                            counts,
                            int(payload),
                            breakeven=breakeven,
                            key_prefix=key_prefix,
                        )
                    elif kind == "year" and not missing_notice_rendered:
                        missing_notice_rendered = True
                        st.markdown(
                            "<div class='s7-uw-empty'>"
                            "当前数据未覆盖第 5 个年度。<br>"
                            "可用的年度图已在上方展示；"
                            "请检查 STEP5/STEP6 是否已集成完整的年度综合成本率与保费数据。"
                            "</div>",
                            unsafe_allow_html=True,
                        )
                    elif kind == "counts":
                        st.markdown(
                            "<div class='s7-uw-year-title'>盈利 / 亏损公司数趋势</div>"
                            "<div class='s7-uw-year-sub'>按年度统计（家）</div>",
                            unsafe_allow_html=True,
                        )
                        if count_chart is None:
                            st.markdown(
                                "<div class='s7-uw-empty'>暂无可用于统计的年度数据。</div>",
                                unsafe_allow_html=True,
                            )
                        else:
                            st.altair_chart(
                                count_chart,
                                width="stretch",
                                key=f"{key_prefix}_underwriting_count_trend",
                            )
                            st.caption(
                                "盈利公司数 = 综合成本率低于 100% 的公司数；"
                                "亏损公司数 = 综合成本率高于 100% 的公司数。"
                            )

    fallback_premium = annual["保费口径"].ne("本年累计数").sum()
    with st.expander("查看承保效益年度明细与口径", expanded=False):
        detail = annual[
            [
                "年度",
                "公司",
                "保费",
                "保费单位",
                "保费排名",
                "综合成本率",
                "盈亏",
                "综合成本率口径",
                "保费口径",
                "综合成本率报告期",
                "保费报告期",
            ]
        ].copy()
        detail = detail.rename(
            columns={
                "保费": "保费（原始单位）",
                "综合成本率": "综合成本率（%）",
            }
        )
        st.dataframe(
            detail.sort_values(["年度", "保费排名"]),
            width="stretch",
            hide_index=True,
        )
        st.caption(
            "综合成本率优先取“本年累计数”，保费优先取“本年累计数”；"
            "年度累计保费缺失时回退到“本季度数”用于排名。"
        )
        if fallback_premium:
            st.caption(
                f"注：{int(fallback_premium)} 条公司年度记录缺少本年累计保费，"
                "已使用本季度保费参与当年排名。"
            )
