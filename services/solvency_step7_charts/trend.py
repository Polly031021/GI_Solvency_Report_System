"""趋势类图表：单指标折线、公司柱状趋势、公司分期柱。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

import altair as alt
import pandas as pd

from .primitives import (
    COMPACT_TREND_SCALE_CODES,
    REGULATORY_LIMITS,
    TREND_AXIS_FORMATS,
    TREND_LABEL_FORMATS,
    TREND_LABEL_THRESHOLDS,
    _axis_title,
    compact_period_scale,
    company_scale,
    facet_layout,
    hidden_value_axis,
    metric_rows,
    panel_typography,
    responsive_bar_width,
    apply_report_theme,
    convert_multiple_units_to_percent,
    report_period_color_map,
)


def trend_y_domain(metric: pd.DataFrame, code: str) -> list[float]:
    values = pd.to_numeric(metric["数值"], errors="coerce").dropna().tolist()
    limit = REGULATORY_LIMITS.get(code)
    if limit is not None:
        values.append(float(limit))
    if not values:
        return [0.0, 1.0]
    low, high = min(values), max(values)
    span = high - low
    minimum_padding = 0.0000001 if code in COMPACT_TREND_SCALE_CODES else 0.01
    padding = max(span * 0.08, max(abs(low), abs(high)) * 0.025, minimum_padding)
    return [low - padding, high + padding]


def build_metric_trend_group(
    metric: pd.DataFrame,
    periods: list[str],
    name: str,
    code: str,
    company_colors: Mapping[str, str],
    highlight_company: str,
    y_domain: list[float],
    group_index: int = 1,
    group_count: int = 1,
) -> alt.Chart:
    companies = list(dict.fromkeys(metric["公司"].astype(str)))
    scale = company_scale(companies, company_colors)
    label_format = TREND_LABEL_FORMATS.get(code, ",.2f")
    if code in COMPACT_TREND_SCALE_CODES:
        y_axis = alt.Axis(format=".5f", tickCount=6)
    elif code in TREND_AXIS_FORMATS:
        y_axis = alt.Axis(format=TREND_AXIS_FORMATS[code])
    else:
        y_axis = alt.Axis()
    company_legend = alt.Legend(
        title="公司",
        orient="top-right",
        direction="vertical",
        columns=1,
        symbolSize=80,
    )
    highlight = str(highlight_company or "").strip()
    line_size = (
        alt.condition(alt.datum["公司"] == highlight, alt.value(4.0), alt.value(1.8))
        if highlight in companies
        else alt.value(2.0)
    )
    line_opacity = (
        alt.condition(alt.datum["公司"] == highlight, alt.value(1.0), alt.value(0.68))
        if highlight in companies
        else alt.value(0.72)
    )
    value_tooltip = alt.Tooltip("数值:Q", format=label_format)
    base = alt.Chart(metric)
    lines = base.mark_line().encode(
        x=alt.X("报告期:N", sort=periods, title="报告期"),
        y=alt.Y(
            "数值:Q",
            title=_axis_title(metric),
            axis=y_axis,
            scale=alt.Scale(domain=y_domain, zero=False, nice=False),
        ),
        color=alt.Color("公司:N", scale=scale, legend=company_legend),
        detail="公司:N",
        size=line_size,
        opacity=line_opacity,
        tooltip=["公司:N", "报告期:N", "指标名称:N", "披露状态:N", value_tooltip, "单位:N"],
    )
    point_size = (
        alt.condition(alt.datum["公司"] == highlight, alt.value(88), alt.value(58))
        if highlight in companies
        else alt.value(58)
    )
    points = base.mark_point(filled=True, size=58).encode(
        x=alt.X("报告期:N", sort=periods),
        y=alt.Y("数值:Q"),
        color=alt.Color("公司:N", scale=scale, legend=None),
        size=point_size,
        opacity=line_opacity,
        tooltip=["公司:N", "报告期:N", "指标名称:N", "披露状态:N", value_tooltip, "单位:N"],
    )
    highlight_labels = (
        base.transform_filter(
            alt.datum["显示标签"] & ~alt.datum["是否全局最大"] & ~alt.datum["是否全局最小"]
        )
        .mark_text(tooltip=False, dy=-10, fontSize=10, fontWeight="bold", color="#0C233C")
        .encode(
            x=alt.X("报告期:N", sort=periods),
            y=alt.Y("数值:Q"),
            text=alt.Text("数值标签:N"),
        )
    )
    max_labels = (
        base.transform_filter(alt.datum["是否全局最大"])
        .mark_text(
            tooltip=False,
            dy=-11,
            fontSize=10,
            fontWeight="bold",
            color="#0C233C",
        )
        .encode(x=alt.X("报告期:N", sort=periods), y="数值:Q", text=alt.Text("数值标签:N"))
    )
    min_labels = (
        base.transform_filter(alt.datum["是否全局最小"])
        .mark_text(
            tooltip=False,
            dy=11,
            baseline="top",
            fontSize=10,
            fontWeight="bold",
            color="#0C233C",
        )
        .encode(x=alt.X("报告期:N", sort=periods), y="数值:Q", text=alt.Text("数值标签:N"))
    )
    trend_layers: list[alt.Chart] = [lines, points, highlight_labels, max_labels, min_labels]
    limit = REGULATORY_LIMITS.get(code)
    if limit is not None:
        rule_data = pd.DataFrame({"监管下限": [limit]})
        trend_layers.append(
            alt.Chart(rule_data)
            .mark_rule(color="#ED2124", strokeDash=[5, 4], strokeWidth=2)
            .encode(
                y="监管下限:Q",
                tooltip=[alt.Tooltip("监管下限:Q", title="监管下限")],
            )
        )
    title = f"{name}跨期趋势"
    height = 420
    if group_count > 1:
        title = f"{title} · 第 {group_index}/{group_count} 组（{len(companies)} 家公司）"
        height = 350
    return apply_report_theme(alt.layer(*trend_layers).properties(title=title, height=height))


def prepare_metric_trend(
    frame: pd.DataFrame,
    code: str,
    period_order: Iterable[str],
    highlight_company: str,
) -> tuple[pd.DataFrame, list[str], str, list[float]]:
    metric, periods, name = metric_rows(frame, code, period_order)
    highlight = str(highlight_company or "").strip()
    metric["是否追踪"] = metric["公司"].astype(str).eq(highlight)
    label_threshold = TREND_LABEL_THRESHOLDS.get(code)
    if label_threshold is not None:
        metric["是否全局最大"] = False
        metric["是否全局最小"] = False
        metric["显示标签"] = metric["数值"].gt(label_threshold).fillna(False)
    else:
        metric["是否全局最大"] = metric["数值"].eq(metric["数值"].max())
        metric["是否全局最小"] = metric["数值"].eq(metric["数值"].min())
        metric["显示标签"] = metric[["是否追踪", "是否全局最大", "是否全局最小"]].any(axis=1)
    return metric, periods, name, trend_y_domain(metric, code)


def build_single_metric_trend_chart(
    frame: pd.DataFrame,
    code: str,
    period_order: Iterable[str],
    company_colors: Mapping[str, str],
    highlight_company: str = "",
) -> alt.Chart:
    """Return one point-line trend chart (kept for small selections and callers)."""
    metric, periods, name, y_domain = prepare_metric_trend(
        frame, code, period_order, highlight_company
    )
    return build_metric_trend_group(
        metric,
        periods,
        name,
        code,
        company_colors,
        highlight_company,
        y_domain,
    )


def build_single_metric_trend_charts(
    frame: pd.DataFrame,
    code: str,
    period_order: Iterable[str],
    company_colors: Mapping[str, str],
    highlight_company: str = "",
) -> list[alt.Chart]:
    """Return one trend chart containing every selected company."""
    return [
        build_single_metric_trend_chart(
            frame,
            code,
            period_order,
            company_colors,
            highlight_company,
        )
    ]


def build_company_bar_trend_chart(
    frame: pd.DataFrame,
    code: str,
    period_order: Iterable[str],
    highlight_company: str = "",
) -> alt.Chart:
    """Return one compact bar + point-line panel per company."""
    metric, periods, name = metric_rows(frame, code, period_order)
    highlight = str(highlight_company or "").strip()
    metric["是否追踪"] = metric["公司"].astype(str).eq(highlight)
    period_colors = report_period_color_map(periods)
    company_count = metric["公司"].nunique()
    columns, panel_width, facet_spacing = facet_layout(company_count)
    panel_height = 285
    dense_layout = company_count >= 10
    period_font_size, value_font_size, company_font_size = panel_typography(
        company_count,
        dense_layout,
    )
    period_scale = compact_period_scale(company_count, dense_layout)
    shared_domain = metric_bar_axis_domain(metric, code, periods)
    y_scale = alt.Scale(domain=list(shared_domain), zero=True, nice=False)
    base = alt.Chart()
    bars = base.mark_bar(
        width=responsive_bar_width(company_count, dense_layout),
        opacity=0.92,
        cornerRadiusTopLeft=2,
        cornerRadiusTopRight=2,
    ).encode(
        x=alt.X(
            "报告期:N",
            sort=periods,
            title=None,
            axis=alt.Axis(title=None, labelAngle=0, labelFontSize=period_font_size, labelLimit=55),
            scale=period_scale,
        ),
        y=alt.Y(
            "数值:Q",
            title=None,
            axis=hidden_value_axis(),
            scale=y_scale,
        ),
        color=alt.Color(
            "报告期:N",
            scale=alt.Scale(
                domain=periods,
                range=[period_colors[period] for period in periods],
            ),
            legend=None,
        ),
        tooltip=[
            "公司:N",
            "报告期:N",
            "指标名称:N",
            "披露状态:N",
            alt.Tooltip("数值:Q", format=",.2f"),
            "单位:N",
        ],
    )
    line = base.mark_line(color="#0C233C", strokeWidth=2.6).encode(
        x=alt.X("报告期:N", sort=periods, scale=period_scale),
        y=alt.Y("数值:Q", scale=y_scale),
    )
    points = base.mark_point(
        filled=True,
        size=62,
        color="#0C233C",
    ).encode(
        x=alt.X("报告期:N", sort=periods, scale=period_scale),
        y=alt.Y("数值:Q", scale=y_scale),
    )
    labels = base.mark_text(
        tooltip=False,
        dy=-12,
        fontSize=value_font_size,
        fontWeight="bold",
        color="#0C233C",
    ).encode(
        x=alt.X("报告期:N", sort=periods, scale=period_scale),
        y=alt.Y("数值:Q", scale=y_scale),
        text=alt.Text("数值标签:N"),
    )
    layers: list[alt.Chart] = [bars, line, points, labels]
    limit = REGULATORY_LIMITS.get(code)
    if limit is not None:
        layers.append(
            alt.Chart(pd.DataFrame({"监管下限": [limit]}))
            .mark_rule(color="#ED2124", strokeDash=[5, 4], strokeWidth=1.2)
            .encode(y=alt.Y("监管下限:Q", scale=y_scale))
        )
    tracked_frame = (
        base.transform_filter(alt.datum["是否追踪"])
        .transform_aggregate(追踪记录="count()", groupby=["公司"])
        .mark_rect(
            fill="#00338D",
            fillOpacity=0.03,
            stroke="#00338D",
            strokeOpacity=0.35,
            strokeWidth=1.5,
            cornerRadius=5,
        )
        .encode(
            x=alt.value(-13),
            x2=alt.value(panel_width + 13),
            y=alt.value(-18),
            y2=alt.value(panel_height + 13),
        )
    )
    layers.append(tracked_frame)
    chart = alt.layer(*layers, data=metric).properties(width=panel_width, height=panel_height)
    return apply_report_theme(
        chart.facet(
            facet=alt.Facet(
                "公司:N",
                title="公司",
                header=alt.Header(labelFontSize=company_font_size, labelPadding=20),
            ),
            columns=columns,
            spacing=facet_spacing,
        )
        .resolve_scale(y="shared")
        .properties(title=f"{name}公司跨期对标")
    )


def build_company_period_bar_chart(
    frame: pd.DataFrame,
    code: str,
    period_order: Iterable[str],
    *,
    shared_y_domain: tuple[float, float] | None = None,
    dense_layout: bool = False,
    panel_count: int = 1,
) -> alt.Chart:
    """Return one company's metric bars with report period on x."""
    metric, periods, name = metric_rows(frame, code, period_order)
    metric = metric.dropna(subset=["数值"]).copy()
    if metric.empty:
        raise ValueError(f"{name}缺少可绘制的数据。")
    companies = list(dict.fromkeys(metric["公司"].dropna().astype(str)))
    if len(companies) != 1:
        raise ValueError(f"{name}需要按公司分别绘制。")
    company = companies[0]
    period_font_size, value_font_size, _ = panel_typography(
        panel_count,
        dense_layout,
    )
    period_scale = compact_period_scale(panel_count, dense_layout)
    period_colors = report_period_color_map(periods)
    value_format = TREND_LABEL_FORMATS.get(code, ",.2f")
    y_scale = (
        alt.Scale(domain=list(shared_y_domain), zero=True, nice=False)
        if shared_y_domain is not None
        else alt.Scale(zero=True)
    )
    bars = (
        alt.Chart(metric)
        .mark_bar(
            width=responsive_bar_width(panel_count, dense_layout),
            opacity=0.94,
            cornerRadiusTopLeft=2,
            cornerRadiusTopRight=2,
        )
        .encode(
            x=alt.X(
                "报告期:N",
                title=None,
                sort=periods,
                axis=alt.Axis(
                    title=None,
                    labelAngle=0,
                    labelFontSize=period_font_size,
                    labelLimit=55,
                ),
                scale=period_scale,
            ),
            y=alt.Y(
                "数值:Q",
                title=None,
                axis=hidden_value_axis(),
                scale=y_scale,
            ),
            color=alt.Color(
                "报告期:N",
                scale=alt.Scale(
                    domain=periods, range=[period_colors[period] for period in periods]
                ),
                legend=None,
            ),
            tooltip=[
                "公司:N",
                "报告期:N",
                "指标名称:N",
                alt.Tooltip("数值:Q", format=value_format),
                "单位:N",
            ],
        )
    )
    labels = (
        alt.Chart(metric)
        .mark_text(
            tooltip=False,
            dy=-7,
            fontSize=value_font_size,
            fontWeight="bold",
            color="#0C233C",
        )
        .encode(
            x=alt.X("报告期:N", sort=periods, scale=period_scale),
            y=alt.Y("数值:Q", scale=y_scale),
            text=alt.Text("数值标签:N"),
        )
    )
    layers: list[alt.Chart] = [bars, labels]
    limit = REGULATORY_LIMITS.get(code)
    if limit is not None:
        layers.append(
            alt.Chart(pd.DataFrame({"监管下限": [limit]}))
            .mark_rule(color="#ED2124", strokeDash=[5, 4], strokeWidth=2)
            .encode(y="监管下限:Q")
        )
    return apply_report_theme(alt.layer(*layers).properties(title=company, height=250))


def metric_bar_axis_domain(
    frame: pd.DataFrame,
    code: str,
    period_order: Iterable[str],
) -> tuple[float, float]:
    """Return one padded bar domain shared by every displayed company."""
    periods = {str(period) for period in period_order}
    rows = convert_multiple_units_to_percent(
        frame[
            frame["指标编码"].astype(str).eq(str(code)) & frame["报告期"].astype(str).isin(periods)
        ]
    )
    values = pd.to_numeric(rows.get("数值", pd.Series(dtype=float)), errors="coerce").dropna()
    if values.empty:
        return 0.0, 1.0
    lower = min(0.0, float(values.min()))
    upper = max(0.0, float(values.max()))
    padded_lower = lower * 1.08 if lower < 0 else 0.0
    padded_upper = upper * 1.12 if upper > 0 else 1.0
    return float(padded_lower), float(padded_upper)


def build_company_line_trend_chart(
    frame: pd.DataFrame,
    code: str,
    period_order: Iterable[str],
    highlight_company: str = "",
) -> alt.Chart:
    """每家公司一个年度折线小图（facet 网格换行），用于核心指标速览类展示。

    - x 轴用年份（从报告期 2021Q4 提取 2021），按 (公司, 年份) 取均值，
      同年多批次/多口径记录合并成一个点，消除竖线；
    - facet 按列数换行成网格；y 轴显示刻度，首尾年份标数值；
    - 监管下限红色虚线；数值 hover tooltip。
    """
    metric, periods, name = metric_rows(frame, code, period_order)
    metric = metric.dropna(subset=["数值"]).copy()
    if metric.empty:
        raise ValueError(f"{name}缺少可绘制的数据。")

    metric["年份"] = metric["报告期"].astype(str).str.extract(r"(\d{4})")[0]
    metric = metric.dropna(subset=["年份"]).copy()
    metric["年份"] = metric["年份"].astype(str)
    metric["数值"] = pd.to_numeric(metric["数值"], errors="coerce")
    metric = metric.dropna(subset=["数值"])
    # 同年多记录取均值，确保每个 (公司, 年份) 只有一个点
    metric = metric.groupby(["公司", "年份"], as_index=False)["数值"].mean()
    year_order = sorted(metric["年份"].unique().tolist())
    if not year_order:
        raise ValueError(f"{name}缺少可绘制的年度数据。")

    company_count = metric["公司"].nunique()
    if company_count <= 4:
        columns = 2
    elif company_count <= 9:
        columns = 3
    else:
        columns = 4
    panel_width = 300
    panel_height = 220

    vals = metric["数值"].astype(float)
    lo, hi = float(vals.min()), float(vals.max())
    limit = REGULATORY_LIMITS.get(code)
    if limit is not None:
        lo = min(lo, float(limit))
        hi = max(hi, float(limit))
    pad = max((hi - lo) * 0.1, 1.0)
    y_scale = alt.Scale(domain=[lo - pad, hi + pad], zero=False, nice=False)

    first_year, last_year = year_order[0], year_order[-1]
    metric["_首末年"] = metric["年份"].isin([first_year, last_year])

    line_color = "#00338D"
    base = alt.Chart()

    # 单位自适应：比率类（%）用整数百分比格式；金额类（元等）用 ~s 自动缩写
    unit = ""
    if "单位" in metric.columns:
        unit_series = metric["单位"].dropna().astype(str)
        if not unit_series.empty:
            unit = unit_series.iloc[0].strip()
    is_pct = unit == "%"
    y_format = ",.0f" if is_pct else "~s"
    title_suffix = f"（{unit}）" if unit else ""

    line = base.mark_line(color=line_color, strokeWidth=2.2, opacity=0.95).encode(
        x=alt.X(
            "年份:N",
            sort=year_order,
            axis=alt.Axis(title=None, labelAngle=0, labelFontSize=9, labelLimit=40),
        ),
        y=alt.Y(
            "数值:Q",
            title=None,
            axis=alt.Axis(title=None, labelFontSize=8, format=y_format),
            scale=y_scale,
        ),
    )
    points = base.mark_point(filled=True, size=50, color=line_color).encode(
        x=alt.X("年份:N", sort=year_order),
        y=alt.Y("数值:Q", scale=y_scale),
        tooltip=["公司:N", "年份:N", alt.Tooltip("数值:Q", format=",.1f"), "单位:N"],
    )
    labels = (
        base.transform_filter(alt.datum["_首末年"])
        .mark_text(dy=-11, fontSize=9, fontWeight="bold", color="#0C233C")
        .encode(
            x=alt.X("年份:N", sort=year_order),
            y=alt.Y("数值:Q", scale=y_scale),
            text=alt.Text("数值:Q", format=y_format),
        )
    )
    layers: list[alt.Chart] = [line, points, labels]

    if limit is not None:
        layers.append(
            alt.Chart(pd.DataFrame({"监管下限": [limit]}))
            .mark_rule(color="#ED2124", strokeDash=[5, 4], strokeWidth=1.5)
            .encode(y=alt.Y("监管下限:Q", scale=y_scale))
        )

    chart = alt.layer(*layers, data=metric).properties(width=panel_width, height=panel_height)
    return apply_report_theme(
        chart.facet(
            facet=alt.Facet(
                "公司:N",
                title="",
                header=alt.Header(labelFontSize=10, labelPadding=4),
            ),
            columns=columns,
            spacing=16,
        )
        .resolve_scale(y="shared")
        .properties(title=f"{name}公司年度走势{title_suffix}")
    )
