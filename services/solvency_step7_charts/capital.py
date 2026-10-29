"""资本类图表：充足率组合、资本分级堆叠、构成占比堆叠与效应发散。"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping

import altair as alt
import pandas as pd
from ..report_i18n import category_name, is_english, translate_text
from ..solvency_navigation import KPMG_BRIGHT_CHART_COLORS, KPMG_CAPITAL_COMBO_COLORS

from .primitives import (
    CAPITAL_AMOUNT_CODES,
    CAPITAL_STRUCTURE_COLORS,
    COMPONENT_COLORS,
    COMPONENT_LABELS,
    MIN_INSIDE_LABEL_SHARE,
    _capital_stack_positions,
    clean_numeric,
    compact_period_scale,
    facet_layout,
    hidden_value_axis,
    label_color_for_fill,
    metric_title,
    panel_typography,
    responsive_bar_width,
    _responsive_grouped_bar_width,
    _signed_stack_positions,
    apply_report_theme,
    whole_number_label,
    whole_percent_label,
    with_metric_value_labels,
    convert_multiple_units_to_percent,
    report_period_color_map,
)
from .trend import metric_bar_axis_domain
from .structure import display_period_label


def _scope_priority(scope: pd.Series) -> pd.Series:
    """Rank period bases so the current-quarter value wins over prior quarter.

    The previous regex treated both 本季度末数 and 上季度末数 as "period-end" and
    then kept whichever row appeared first in the source, which could pair a
    prior-quarter quantitative-risk total with current-quarter components.
    """
    text = scope.fillna("").astype(str).str.strip()
    priority = pd.Series(9, index=text.index, dtype=int)
    priority[text.str.contains("本年累计|年度累计|全年累计", na=False)] = 0
    priority[
        text.str.contains("本季度末数|本季末|期末数|期末$|当期|时点", na=False)
    ] = 0
    priority[text.str.contains("本季度数", na=False)] = 1
    priority[text.str.contains("上季度", na=False)] = 20
    priority[text.str.contains("下季度|预测", na=False)] = 99
    return priority


def _capital_amount_label(value: object) -> str:
    """Keep small capital amounts readable instead of rounding them to zero."""
    number = pd.to_numeric(value, errors="coerce")
    if pd.isna(number):
        return ""
    number = float(number)
    if abs(number) < 1e-9:
        return ""
    if abs(number) >= 100:
        return f"{number:,.0f}"
    if abs(number) >= 10:
        return f"{number:,.1f}"
    if abs(number) >= 0.01:
        return f"{number:,.2f}"
    return f"{number:,.3f}"


def _period_year_quarter(value: object) -> tuple[int | None, int]:
    text = str(value or "").strip()
    year_match = re.search(r"(20\d{2})", text)
    if not year_match:
        return None, 0
    year = int(year_match.group(1))
    quarter_match = re.search(
        r"(?:Q|第)\s*([1-4])(?:\s*季度)?",
        text,
        flags=re.IGNORECASE,
    )
    if not quarter_match:
        quarter_match = re.search(r"([1-4])\s*季度", text)
    if quarter_match:
        return year, int(quarter_match.group(1))
    chinese_quarter = re.search(r"第?([一二三四])季度", text)
    if chinese_quarter:
        return year, {"一": 1, "二": 2, "三": 3, "四": 4}[chinese_quarter.group(1)]
    return year, 0


def annual_period_order(period_order: Iterable[str]) -> list[str]:
    """Keep one annual observation per year and prefer Q4 over partial quarters."""
    periods = list(dict.fromkeys(str(period) for period in period_order if str(period).strip()))
    grouped: dict[str, list[tuple[int, str]]] = {}
    unparsed: list[str] = []
    for period in periods:
        year, quarter = _period_year_quarter(period)
        if year is None:
            unparsed.append(period)
            continue
        grouped.setdefault(str(year), []).append((quarter, period))

    selected: list[str] = []
    for year in sorted(grouped):
        candidates = grouped[year]
        q4 = [period for quarter, period in candidates if quarter == 4]
        undated = [period for quarter, period in candidates if quarter == 0]
        if q4:
            selected.append(q4[-1])
        elif undated:
            selected.append(undated[-1])
        else:
            selected.append(max(candidates, key=lambda item: item[0])[1])
    return [*selected, *unparsed]


def solvency_ratio_axis_domain(
    frame: pd.DataFrame,
    period_order: Iterable[str],
) -> tuple[float, float]:
    """Return one padded percentage domain shared by all displayed companies."""
    periods = {str(period) for period in period_order}
    rows = convert_multiple_units_to_percent(
        frame[
            frame["指标编码"].astype(str).isin(("CORE_SOLVENCY_RATIO", "COMBINED_SOLVENCY_RATIO"))
            & frame["报告期"].astype(str).isin(periods)
        ]
    ).copy()
    values = pd.to_numeric(rows.get("数值", pd.Series(dtype=float)), errors="coerce")
    maximum = max(100.0, float(values.max())) if values.notna().any() else 100.0
    upper = max(125.0, math.ceil(maximum * 1.12 / 25.0) * 25.0)
    return 0.0, float(upper)


def build_solvency_ratio_combo_chart(
    frame: pd.DataFrame,
    period_order: Iterable[str],
    *,
    shared_y_domain: tuple[float, float] | None = None,
    dense_layout: bool = False,
    panel_count: int = 1,
) -> alt.Chart:
    """Return one company panel with combined-ratio bars and a core-ratio line."""
    periods = list(dict.fromkeys(str(period) for period in period_order))
    codes = ("CORE_SOLVENCY_RATIO", "COMBINED_SOLVENCY_RATIO")
    metric = convert_multiple_units_to_percent(
        clean_numeric(frame[frame["指标编码"].astype(str).isin(codes)])
    )
    metric = with_metric_value_labels(metric, "SOLVENCY_RATIO_COMBO")
    metric = metric[metric["报告期"].astype(str).isin(periods)].copy()
    companies = list(dict.fromkeys(metric["公司"].dropna().astype(str)))
    if len(companies) != 1:
        raise ValueError("核心及综合充足率需要按公司分别绘制。")
    company = companies[0]
    combined = metric[metric["指标编码"].astype(str).eq("COMBINED_SOLVENCY_RATIO")].copy()
    core = metric[metric["指标编码"].astype(str).eq("CORE_SOLVENCY_RATIO")].copy()
    if combined.empty or core.empty:
        raise ValueError(f"{company}缺少核心或综合偿付能力充足率数据。")

    domain = shared_y_domain or solvency_ratio_axis_domain(metric, periods)
    y_scale = alt.Scale(domain=list(domain), zero=True, nice=False)
    period_font_size, value_font_size, _ = panel_typography(
        panel_count,
        dense_layout,
    )
    period_scale = compact_period_scale(panel_count, dense_layout)
    period_colors = report_period_color_map(periods)
    x = alt.X(
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
    )
    value_tooltip = alt.Tooltip("数值:Q", format=",.0f")

    bars = (
        alt.Chart(combined)
        .mark_bar(
            width=responsive_bar_width(panel_count, dense_layout),
            opacity=0.9,
            cornerRadiusTopLeft=2,
            cornerRadiusTopRight=2,
        )
        .encode(
            x=x,
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
            tooltip=["公司:N", "报告期:N", "指标名称:N", value_tooltip, "单位:N"],
        )
    )
    bar_labels = (
        alt.Chart(combined)
        .mark_text(
            tooltip=False,
            dy=-7,
            fontSize=value_font_size,
            fontWeight="bold",
            color="#000000",
        )
        .encode(
            x=x,
            y=alt.Y("数值:Q", scale=y_scale),
            text=alt.Text("数值标签:N"),
        )
    )
    line = (
        alt.Chart(core)
        .mark_line(
            color="#FD349C",
            strokeWidth=2.6,
        )
        .encode(
            x=x,
            y=alt.Y("数值:Q", axis=None, scale=y_scale),
            tooltip=["公司:N", "报告期:N", "指标名称:N", value_tooltip, "单位:N"],
        )
    )
    points = (
        alt.Chart(core)
        .mark_point(
            color="#FD349C",
            filled=True,
            size=54,
        )
        .encode(
            x=x,
            y=alt.Y("数值:Q", axis=None, scale=y_scale),
            tooltip=["公司:N", "报告期:N", "指标名称:N", value_tooltip, "单位:N"],
        )
    )
    line_labels = (
        alt.Chart(core)
        .mark_text(
            tooltip=False,
            dy=-10,
            fontSize=value_font_size,
            fontWeight="bold",
            color="#000000",
        )
        .encode(
            x=x,
            y=alt.Y("数值:Q", axis=None, scale=y_scale),
            text=alt.Text("数值标签:N"),
        )
    )
    return apply_report_theme(
        alt.layer(
            bars,
            bar_labels,
            line,
            points,
            line_labels,
        ).properties(title=company, height=250)
    )


def build_capital_ratio_combo_chart(
    frame: pd.DataFrame,
    amount_groups: Mapping[str, tuple[str, ...]],
    period_order: Iterable[str],
    title: str,
    *,
    ratio_code: str = "",
    ratio_label: str = "",
    share_line: bool = False,
    shared_amount_domain: tuple[float, float] | None = None,
    dense_layout: bool = False,
    panel_count: int = 1,
    bar_layout: str = "stacked",
    annual_labels: bool = False,
) -> alt.Chart:
    """Build one company panel: ratio line above stacked or grouped capital bars."""
    if bar_layout not in {"stacked", "grouped"}:
        raise ValueError("bar_layout must be 'stacked' or 'grouped'.")
    periods = list(period_order)
    source_codes = tuple(code for codes in amount_groups.values() for code in codes)
    source = clean_numeric(frame[frame["指标编码"].astype(str).isin(source_codes)])
    source = source[source["报告期"].astype(str).isin(periods)].copy()
    if source.empty:
        raise ValueError(f"{title}缺少可绘制的年度资本金额。")
    pivot = source.pivot_table(
        index=["公司", "报告期"],
        columns="指标编码",
        values="数值",
        aggfunc="first",
    ).reset_index()
    component_rows: list[dict[str, object]] = []
    unit_values = [
        str(value).strip()
        for value in source.get("单位", pd.Series(dtype=str)).dropna()
        if str(value).strip()
    ]
    amount_unit = unit_values[0] if unit_values else ""
    stack_labels = list(amount_groups)
    if "最低资本" in stack_labels:
        stack_labels = ["最低资本", *[label for label in stack_labels if label != "最低资本"]]
    stack_order = {label: index for index, label in enumerate(stack_labels)}
    for row in pivot.to_dict("records"):
        for label, codes in amount_groups.items():
            values = [pd.to_numeric(row.get(code), errors="coerce") for code in codes]
            # ★ 只有全部明细都缺失才跳过整组；只要有一条存在，缺失项按 0 处理
            if all(pd.isna(value) for value in values):
                continue
            clean_values = [0.0 if pd.isna(value) else float(value) for value in values]
            component_rows.append(
                {
                    "公司": row["公司"],
                    "报告期": row["报告期"],
                    "记录类型": "资本构成",
                    "组成类别": label,
                    "组成顺序": stack_order[label],
                    "数值": float(sum(clean_values)),
                    "单位": amount_unit,
                }
            )
    components = pd.DataFrame(component_rows)
    if components.empty:
        raise ValueError(f"{title}缺少可绘制的资本金额。")
    companies = list(dict.fromkeys(components["公司"].dropna().astype(str)))
    if len(companies) != 1:
        raise ValueError(f"{title}需要按公司分别绘制。")
    company = companies[0]
    period_labels = [display_period_label(period) for period in periods]
    period_label_map = dict(zip(periods, period_labels))
    components["显示期"] = components["报告期"].astype(str).map(period_label_map)
    period_font_size, value_font_size, _ = panel_typography(
        panel_count,
        dense_layout,
    )
    components = _signed_stack_positions(components, "数值", "组成顺序")
    component_values = pd.to_numeric(components["数值"], errors="coerce")
    components["标签对齐"] = "middle"
    if bar_layout == "grouped":
        components["标签位置"] = component_values / 2
    else:
        shares = pd.to_numeric(components["构成占比"], errors="coerce").abs()
        # 小占比项目把数字放到柱体外侧，避免两个标签叠在一起或只显示一个。
        small = shares.lt(0.06) & component_values.abs().gt(1e-9)
        positive = component_values.gt(0)
        components.loc[small, "标签位置"] = components.loc[small, "堆叠终点"]
        components.loc[small & ~positive, "标签位置"] = components.loc[
            small & ~positive, "堆叠起点"
        ]
        components.loc[small & positive, "标签对齐"] = "bottom"
        components.loc[small & ~positive, "标签对齐"] = "top"
    components["数值标签"] = [
        _capital_amount_label(value) for value in component_values
    ]
    label_length = max((len(text) for text in components["数值标签"] if text), default=0)
    if label_length >= 10:
        label_font_size = max(6, value_font_size - 2)
    elif label_length >= 7:
        label_font_size = max(7, value_font_size - 1)
    else:
        label_font_size = value_font_size

    if share_line:
        wide = components.pivot_table(
            index=["公司", "报告期"], columns="组成类别", values="数值", aggfunc="first"
        ).reset_index()
        # ★ 补齐可能缺失的组名（如某公司未披露附属资本），缺失按 0 处理
        group_labels = list(amount_groups)
        for label in group_labels:
            if label not in wide.columns:
                wide[label] = 0.0
        wide[group_labels] = wide[group_labels].fillna(0.0)
        first_label = group_labels[0]
        total = wide[group_labels].sum(axis=1)
        wide["线值"] = wide[first_label] / total.where(total.ne(0)) * 100
        line_rows = wide[["公司", "报告期", "线值"]].dropna().copy()
    else:
        line_rows = convert_multiple_units_to_percent(
            clean_numeric(frame[frame["指标编码"].astype(str).eq(ratio_code)])
        )[["公司", "报告期", "数值"]].drop_duplicates(["公司", "报告期"], keep="last")
        line_rows = line_rows.rename(columns={"数值": "线值"})
    line_rows = line_rows[line_rows["报告期"].astype(str).isin(periods)].copy()
    line_rows["显示期"] = line_rows["报告期"].astype(str).map(period_label_map)
    line_rows["线标签"] = line_rows["线值"].map(
        lambda value: "" if pd.isna(value) else f"{float(value):,.1f}%"
    )
    colors = [
        KPMG_CAPITAL_COMBO_COLORS[index % len(KPMG_CAPITAL_COMBO_COLORS)]
        for index in range(len(amount_groups))
    ]
    labels = list(amount_groups)
    label_color_map = {label: label_color_for_fill(color) for label, color in zip(labels, colors)}
    components["标签颜色"] = components["组成类别"].map(label_color_map).fillna("#0C233C")
    period_label_font_size = (
        max(6, period_font_size - 1) if annual_labels else period_font_size
    )
    period_axis = alt.Axis(
        title=None,
        domain=False,
        ticks=False,
        labelAngle=0,
        labelPadding=8,
        labelColor="#7A8496",
        labelFontSize=period_label_font_size,
        labelLimit=80,
    )
    amount_axis = alt.Axis(
        title=None,
        domain=False,
        ticks=False,
        labels=False,
        grid=False,
    )
    ratio_axis = alt.Axis(
        title=None,
        domain=False,
        ticks=False,
        labels=False,
        grid=False,
    )
    amount_domain = shared_amount_domain or capital_ratio_amount_axis_domain(
        frame,
        amount_groups,
        periods,
        stacked=bar_layout == "stacked",
    )
    amount_scale = alt.Scale(domain=list(amount_domain), zero=True, nice=False)
    combo_period_scale = compact_period_scale(panel_count, dense_layout)
    period_x = alt.X(
        "显示期:N" if annual_labels else "报告期:N",
        sort=period_labels if annual_labels else periods,
        axis=period_axis,
        scale=combo_period_scale,
    )
    category_offset = alt.XOffset(
        "组成类别:N",
        sort=labels,
        scale=alt.Scale(paddingInner=0.0, paddingOuter=0.04),
    )
    bar_encoding: dict[str, object] = {
        "x": period_x,
        "y": alt.Y(
            "堆叠终点:Q" if bar_layout == "stacked" else "数值:Q",
            axis=amount_axis,
            scale=amount_scale,
            stack=None,
        ),
        "color": alt.Color(
            "组成类别:N",
            scale=alt.Scale(domain=labels, range=colors),
            legend=None,
        ),
        "tooltip": [
            "公司:N",
            "报告期:N",
            "组成类别:N",
            alt.Tooltip("数值:Q", format=",.2f"),
            "单位:N",
        ],
    }
    if bar_layout == "stacked":
        bar_encoding["y2"] = alt.Y2("堆叠起点:Q")
    else:
        bar_encoding["xOffset"] = category_offset
    bars = (
        alt.Chart(components)
        .mark_bar(
            width=(
                _responsive_grouped_bar_width(panel_count, dense_layout)
                if bar_layout == "grouped"
                else responsive_bar_width(panel_count, dense_layout)
            )
        )
        .encode(**bar_encoding)
    )
    label_encoding: dict[str, object] = {
        "x": alt.X(
            "显示期:N" if annual_labels else "报告期:N",
            sort=period_labels if annual_labels else periods,
            scale=combo_period_scale,
        ),
        "y": alt.Y("标签位置:Q", axis=amount_axis, scale=amount_scale),
        "text": "数值标签:N",
        "color": alt.Color("标签颜色:N", scale=None, legend=None),
    }
    if bar_layout == "grouped":
        label_encoding["xOffset"] = category_offset

    def _label_layer(alignment: str, baseline: str, dy: int, use_fill_color: bool) -> alt.Chart:
        layer_encoding = dict(label_encoding)
        if not use_fill_color:
            layer_encoding["color"] = alt.value("#0C233C")
        layer_rows = components[components["标签对齐"].astype(str).eq(alignment)]
        return (
            alt.Chart(layer_rows)
            .mark_text(
                tooltip=False,
                fontSize=label_font_size,
                fontWeight="bold",
                baseline=baseline,
                dy=dy,
            )
            .encode(**layer_encoding)
        )

    inside_labels = _label_layer("middle", "middle", 0, True)
    outside_top_labels = _label_layer("bottom", "bottom", -2, False)
    outside_bottom_labels = _label_layer("top", "top", 2, False)
    bar_panel = alt.layer(
        bars,
        inside_labels,
        outside_top_labels,
        outside_bottom_labels,
    ).properties(height=190)
    if line_rows.empty:
        return apply_report_theme(bar_panel.properties(title=company))

    line = (
        alt.Chart(line_rows)
        .mark_line(
            color="#FD349C",
            strokeWidth=2.6,
        )
        .encode(
            x=alt.X(
                "显示期:N" if annual_labels else "报告期:N",
                sort=period_labels if annual_labels else periods,
                axis=None,
                scale=combo_period_scale,
            ),
            y=alt.Y(
                "线值:Q",
                axis=ratio_axis,
                scale=alt.Scale(zero=False, padding=26),
            ),
            tooltip=[
                "公司:N",
                "报告期:N",
                alt.Tooltip("线值:Q", title=ratio_label or "比例", format=",.2f"),
            ],
        )
    )
    points = (
        alt.Chart(line_rows)
        .mark_point(
            color="#FD349C",
            filled=True,
            shape="triangle-up",
            size=68,
        )
        .encode(
            x=alt.X(
                "显示期:N" if annual_labels else "报告期:N",
                sort=period_labels if annual_labels else periods,
                axis=None,
                scale=combo_period_scale,
            ),
            y=alt.Y("线值:Q", axis=ratio_axis, scale=alt.Scale(zero=False, padding=26)),
            tooltip=[
                "公司:N",
                "报告期:N",
                alt.Tooltip("线值:Q", title=ratio_label or "比例", format=",.2f"),
            ],
        )
    )
    line_labels = (
        alt.Chart(line_rows)
        .mark_text(
            tooltip=False,
            dy=-12,
            color="#FD349C",
            fontSize=value_font_size,
            fontWeight="bold",
        )
        .encode(
            x=alt.X(
                "显示期:N" if annual_labels else "报告期:N",
                sort=period_labels if annual_labels else periods,
                axis=None,
                scale=combo_period_scale,
            ),
            y=alt.Y("线值:Q", axis=ratio_axis, scale=alt.Scale(zero=False, padding=26)),
            text="线标签:N",
        )
    )
    line_panel = alt.layer(line, points, line_labels).properties(height=84)
    return apply_report_theme(
        alt.vconcat(line_panel, bar_panel, spacing=4)
        .resolve_scale(x="shared")
        .properties(title=company)
    )


def capital_ratio_amount_axis_domain(
    frame: pd.DataFrame,
    amount_groups: Mapping[str, tuple[str, ...]],
    period_order: Iterable[str],
    *,
    stacked: bool = True,
) -> tuple[float, float]:
    """Return one shared amount domain for stacked totals or individual bars."""
    periods = {str(period) for period in period_order}
    source_codes = tuple(code for codes in amount_groups.values() for code in codes)
    source = clean_numeric(frame[frame["指标编码"].astype(str).isin(source_codes)])
    source = source[source["报告期"].astype(str).isin(periods)]
    pivot = source.pivot_table(
        index=["公司", "报告期"],
        columns="指标编码",
        values="数值",
        aggfunc="first",
    ).reset_index()
    positive_totals: list[float] = []
    negative_totals: list[float] = []
    for row in pivot.to_dict("records"):
        positive_total = 0.0
        negative_total = 0.0
        has_value = False
        for codes in amount_groups.values():
            values = [pd.to_numeric(row.get(code), errors="coerce") for code in codes]
            if any(pd.isna(value) for value in values):
                continue
            group_value = float(sum(values))
            if stacked:
                positive_total += max(group_value, 0.0)
                negative_total += min(group_value, 0.0)
            else:
                positive_total = max(positive_total, max(group_value, 0.0))
                negative_total = min(negative_total, min(group_value, 0.0))
            has_value = True
        if has_value:
            positive_totals.append(positive_total)
            negative_totals.append(negative_total)
    if not positive_totals:
        return 0.0, 1.0
    lower = min(0.0, min(negative_totals))
    upper = max(0.0, max(positive_totals))
    padded_lower = lower * 1.18 if lower < 0 else 0.0
    padded_upper = upper * 1.18 if upper > 0 else 1.0
    return float(padded_lower), float(padded_upper)


def capital_structure_industry_ratios(
    frame: pd.DataFrame,
    core_codes: Iterable[str],
    ancillary_codes: Iterable[str],
    period_order: Iterable[str],
    *,
    denominator_code: str = "ACTUAL_CAPITAL",
) -> tuple[float, float, str] | None:
    """Calculate latest-period industry core/ancillary capital shares."""
    periods = [str(period) for period in period_order]
    if frame.empty or not periods:
        return None
    latest_period = periods[-1]
    core_codes = tuple(str(code) for code in core_codes)
    ancillary_codes = tuple(str(code) for code in ancillary_codes)
    codes = (*core_codes, *ancillary_codes, denominator_code)
    rows = frame[
        frame["报告期"].astype(str).eq(latest_period)
        & frame["指标编码"].astype(str).isin(codes)
    ].copy()
    rows["数值"] = pd.to_numeric(rows["数值"], errors="coerce")
    rows = rows.dropna(subset=["数值"]).copy()
    if rows.empty:
        return None
    if "期间口径" in rows.columns:
        scope = rows["期间口径"].fillna("").astype(str).str.strip()
        rows["_口径优先级"] = _scope_priority(scope)
        rows = rows.sort_values(
            ["公司", "指标编码", "_口径优先级"],
            kind="stable",
        )
    rows = rows.drop_duplicates(["公司", "指标编码"], keep="first")
    pivot = rows.pivot_table(
        index="公司",
        columns="指标编码",
        values="数值",
        aggfunc="first",
    )
    if pivot.empty:
        return None
    for code in codes:
        if code not in pivot.columns:
            pivot[code] = 0.0
    core = pivot[list(core_codes)].sum(axis=1)
    ancillary = pivot[list(ancillary_codes)].sum(axis=1)
    actual = pd.to_numeric(pivot[denominator_code], errors="coerce")
    fallback_total = core + ancillary
    actual = actual.where(actual.notna() & actual.gt(0), fallback_total)
    valid = actual.gt(0)
    if not valid.any():
        return None
    total_actual = float(actual.loc[valid].sum())
    if total_actual <= 0:
        return None
    core_ratio = float(core.loc[valid].sum()) / total_actual * 100.0
    ancillary_ratio = float(ancillary.loc[valid].sum()) / total_actual * 100.0
    return core_ratio, ancillary_ratio, latest_period


def quant_risk_industry_sentence(
    frame: pd.DataFrame,
    component_specs: Iterable[tuple[str, str, str]],
    period_order: Iterable[str],
    *,
    negative_component_codes: Iterable[str] = (),
    denominator_code: str = "QUANT_RISK_CAPITAL",
) -> str:
    """Build a data-driven latest-year quantitative-risk summary sentence."""
    periods = [str(period) for period in period_order]
    if frame.empty or not periods:
        return ""
    latest_period = periods[-1]
    specs = [(str(code), str(label)) for code, label, _ in component_specs]
    codes = [code for code, _ in specs] + [denominator_code]
    rows = frame[
        frame["报告期"].astype(str).eq(latest_period)
        & frame["指标编码"].astype(str).isin(codes)
    ].copy()
    rows["数值"] = pd.to_numeric(rows["数值"], errors="coerce")
    rows = rows.dropna(subset=["数值"]).copy()
    if rows.empty:
        return ""
    if "期间口径" in rows.columns:
        scope = rows["期间口径"].fillna("").astype(str).str.strip()
        rows["_口径优先级"] = _scope_priority(scope)
        rows = rows.sort_values(
            ["公司", "指标编码", "_口径优先级"],
            kind="stable",
        )
    rows = rows.drop_duplicates(["公司", "指标编码"], keep="first")
    pivot = rows.pivot_table(
        index="公司",
        columns="指标编码",
        values="数值",
        aggfunc="first",
    )
    if denominator_code not in pivot.columns:
        return ""
    denominator = pd.to_numeric(pivot[denominator_code], errors="coerce")
    valid = denominator.gt(0)
    if not valid.any():
        return ""
    negative_codes = {str(code) for code in negative_component_codes}
    averages: dict[str, float] = {}
    for code, label in specs:
        if code in negative_codes or code not in pivot.columns:
            continue
        ratio = (
            pd.to_numeric(pivot.loc[valid, code], errors="coerce")
            / denominator.loc[valid]
            * 100.0
        ).dropna()
        if not ratio.empty:
            averages[label] = float(ratio.mean())
    if not averages:
        return ""

    # 风险分散效应与特征系数影响不是"占比"型正向项目，单独计算其抵减幅度。
    # 两者都是负数/抵减项，取绝对值后按量化风险最低资本归一，便于比较集中度。
    diversification_series = pd.Series(dtype=float)
    diversification_code = "QUANT_RISK_DIVERSIFICATION_EFFECT"
    if diversification_code in pivot.columns:
        diversification_series = (
            pd.to_numeric(pivot.loc[valid, diversification_code], errors="coerce")
            .abs()
            / denominator.loc[valid]
            * 100.0
        ).dropna()

    feature_series = pd.Series(dtype=float)
    required_components = (
        "NON_LIFE_INSURANCE_RISK_CAPITAL",
        "MARKET_RISK_CAPITAL",
        "CREDIT_RISK_CAPITAL",
        "QUANT_RISK_DIVERSIFICATION_EFFECT",
    )
    optional_components = (
        "LIFE_INSURANCE_RISK_CAPITAL",
        "CONTRACT_LOSS_ABSORPTION_EFFECT",
    )
    # 优先按公式重算，避免只覆盖少数公司的披露值把全行业分布带偏。
    # 源表中的“风险分散效应”以正数记录抵减额，公式中应作为减项。
    if all(component in pivot.columns for component in required_components):
        optional_present = tuple(
            component
            for component in optional_components
            if component in pivot.columns
        )
        feature_components = (
            "NON_LIFE_INSURANCE_RISK_CAPITAL",
            "MARKET_RISK_CAPITAL",
            "CREDIT_RISK_CAPITAL",
            *optional_present,
        )
        component_frame = pivot[list(feature_components)].apply(
            pd.to_numeric, errors="coerce"
        )
        # 只有必填风险项都非空的公司才重算；否则缺失项会被当成 0，
        # 反而算出明显偏高的“正”特征系数影响。
        feature_valid = (
            pivot[list(required_components)]
            .apply(pd.to_numeric, errors="coerce")
            .notna()
            .all(axis=1)
            & pd.to_numeric(pivot[denominator_code], errors="coerce").gt(0)
        )
        component_sum = component_frame.fillna(0.0).sum(axis=1) - (
            pd.to_numeric(
                pivot["QUANT_RISK_DIVERSIFICATION_EFFECT"], errors="coerce"
            )
            .abs()
            .fillna(0.0)
        )
        derived_feature = (
            pd.to_numeric(pivot[denominator_code], errors="coerce")
            - component_sum
        )
        feature_series = (
            derived_feature.loc[feature_valid]
            / pd.to_numeric(
                pivot.loc[feature_valid, denominator_code], errors="coerce"
            )
            * 100.0
        ).dropna()
    # 不再回退到稀疏的 FEATURE_FACTOR_IMPACT 披露值：该列只有少数公司，
    # 口径与全行业重算值不可比，混用会把分布带偏。

    dominant_label = max(averages, key=averages.get)
    dominant_short = (
        "保险风险" if dominant_label.startswith("非寿险业务保险风险") else dominant_label
    )
    period_text = str(latest_period)
    sentence = (
        f"财产险公司{period_text}的最低资本以{dominant_short}为主"
        f"（平均占比{averages[dominant_label]:.1f}%）"
    )

    market = averages.get("市场风险")
    credit = averages.get("信用风险")
    if market is not None and credit is not None:
        difference = credit - market
        if abs(difference) < 0.3:
            comparison = (
                "；市场风险与信用风险平均占比接近，"
                f"分别为{market:.1f}%和{credit:.1f}%"
            )
        else:
            higher_label = "信用风险" if difference > 0 else "市场风险"
            higher_value = credit if difference > 0 else market
            lower_label = "市场风险" if difference > 0 else "信用风险"
            lower_value = market if difference > 0 else credit
            verb = "略高于" if abs(difference) <= 5.0 else "高于"
            comparison = (
                f"；{higher_label}平均占比{higher_value:.1f}%，"
                f"{verb}{lower_label}的{lower_value:.1f}%"
            )
    elif market is not None:
        comparison = f"；市场风险平均占比{market:.1f}%"
    elif credit is not None:
        comparison = f"；信用风险平均占比{credit:.1f}%"
    else:
        comparison = ""

    concentration_parts: list[str] = []
    outlier_parts: list[str] = []
    if not diversification_series.empty:
        div_median = float(diversification_series.median())
        div_low = float(diversification_series.quantile(0.10))
        div_high = float(diversification_series.quantile(0.90))
        concentration_parts.append(
            f"风险分散率中位数为{div_median:.1f}%，"
            f"多数公司集中在{div_low:.1f}%–{div_high:.1f}%"
        )
        if float(diversification_series.min()) < div_low:
            outlier_parts.append(
                f"风险分散率最低为{diversification_series.idxmin()}"
                f"（{float(diversification_series.min()):.1f}%）"
            )
    if not feature_series.empty:
        feature_median = float(feature_series.median())
        feature_low = float(feature_series.quantile(0.10))
        feature_high = float(feature_series.quantile(0.90))
        concentration_parts.append(
            f"特征系数影响中位数为{feature_median:.1f}%，"
            f"多数公司集中在{feature_low:.1f}%至{feature_high:.1f}%"
        )
        if float(feature_series.max()) > feature_high:
            outlier_parts.append(
                f"特征系数影响最高为{feature_series.idxmax()}"
                f"（{float(feature_series.max()):.1f}%）"
            )

    concentration_text = ""
    if concentration_parts:
        concentration_text = (
            "；行业的风险分散效应和特征系数影响整体较为集中，"
            + "；".join(concentration_parts)
        )
        if outlier_parts:
            concentration_text += (
                "，个别公司表现突出（" + "；".join(outlier_parts) + "）"
            )
        else:
            concentration_text += "，个别公司表现突出"
    if is_english():
        english_parts = [
            f"In {period_text}, property insurers' minimum capital was primarily driven by "
            f"{translate_text(dominant_short)} (average share {averages[dominant_label]:.1f}%)."
        ]
        if market is not None and credit is not None:
            if abs(difference) < 0.3:
                english_parts.append(
                    f"Average market and credit risk shares were similar at "
                    f"{market:.1f}% and {credit:.1f}%, respectively."
                )
            else:
                higher_name = "Credit risk" if difference > 0 else "Market risk"
                lower_name = "Market risk" if difference > 0 else "Credit risk"
                verb = "slightly exceeded" if abs(difference) <= 5.0 else "exceeded"
                english_parts.append(
                    f"The average {higher_name.lower()} share of {higher_value:.1f}% {verb} "
                    f"the average {lower_name.lower()} share of {lower_value:.1f}%."
                )
        concentration_bits: list[str] = []
        if not diversification_series.empty:
            concentration_bits.append(
                f"the median diversification rate was {div_median:.1f}%, with most companies "
                f"between {div_low:.1f}% and {div_high:.1f}%"
            )
        if not feature_series.empty:
            concentration_bits.append(
                f"the median feature-factor impact was {feature_median:.1f}%, with most "
                f"companies between {feature_low:.1f}% and {feature_high:.1f}%"
            )
        if concentration_bits:
            english_parts.append(
                "Risk concentration was relatively stable: " + "; ".join(concentration_bits) + "."
            )
        if outlier_parts:
            english_outliers: list[str] = []
            if not diversification_series.empty and float(diversification_series.min()) < div_low:
                english_outliers.append(
                    f"{diversification_series.idxmin()} had the lowest diversification rate "
                    f"({float(diversification_series.min()):.1f}%)"
                )
            if not feature_series.empty and float(feature_series.max()) > feature_high:
                english_outliers.append(
                    f"{feature_series.idxmax()} had the highest feature-factor impact "
                    f"({float(feature_series.max()):.1f}%)"
                )
            if english_outliers:
                english_parts.append("Notable observations: " + "; ".join(english_outliers) + ".")
        return " ".join(english_parts)
    return sentence + comparison + concentration_text + "。"


def insurance_risk_industry_sentence(
    frame: pd.DataFrame,
    period_order: Iterable[str],
    *,
    group_column: str = "同业分类",
    denominator_code: str = "NON_LIFE_INSURANCE_RISK_CAPITAL",
) -> str:
    """Build a data-driven summary for non-life insurance-risk composition."""
    periods = [str(period) for period in period_order]
    if frame.empty or not periods:
        return ""
    latest_period = periods[-1]
    premium_code = "NON_LIFE_PREMIUM_RESERVE_RISK_CAPITAL"
    catastrophe_code = "NON_LIFE_CATASTROPHE_RISK_CAPITAL"
    codes = (premium_code, catastrophe_code, denominator_code)
    rows = frame[
        frame["报告期"].astype(str).eq(latest_period)
        & frame["指标编码"].astype(str).isin(codes)
    ].copy()
    rows["数值"] = pd.to_numeric(rows["数值"], errors="coerce")
    rows = rows.dropna(subset=["数值"]).copy()
    if rows.empty:
        return ""
    if "期间口径" in rows.columns:
        scope = rows["期间口径"].fillna("").astype(str).str.strip()
        rows["_口径优先级"] = _scope_priority(scope)
        rows = rows.sort_values(
            ["公司", "指标编码", "_口径优先级"],
            kind="stable",
        )
    rows = rows.drop_duplicates(["公司", "指标编码"], keep="first")
    pivot = rows.pivot_table(
        index="公司",
        columns="指标编码",
        values="数值",
        aggfunc="first",
    )
    required = {premium_code, catastrophe_code, denominator_code}
    if not required.issubset(pivot.columns):
        return ""
    denominator = pd.to_numeric(pivot[denominator_code], errors="coerce")
    valid = denominator.gt(0)
    if not valid.any():
        return ""

    ratios = pd.DataFrame(
        {
            "公司": pivot.index[valid],
            "保费及准备金风险": (
                pd.to_numeric(pivot.loc[valid, premium_code], errors="coerce")
                / denominator.loc[valid]
                * 100.0
            ).to_numpy(),
            "巨灾风险": (
                pd.to_numeric(pivot.loc[valid, catastrophe_code], errors="coerce")
                / denominator.loc[valid]
                * 100.0
            ).to_numpy(),
        }
    ).dropna()
    if ratios.empty:
        return ""

    group_values = pd.Series("未分类", index=ratios.index, dtype=object)
    if group_column in frame.columns:
        mapping = (
            frame[["公司", group_column]]
            .dropna()
            .assign(
                **{
                    group_column: lambda data: data[group_column]
                    .fillna("")
                    .astype(str)
                    .str.strip()
                    .replace("", "未分类")
                }
            )
            .drop_duplicates("公司", keep="first")
            .set_index("公司")[group_column]
            .to_dict()
        )
        group_values = ratios["公司"].map(mapping).fillna("未分类")
    ratios["公司分类"] = group_values.to_numpy()

    premium_means = (
        ratios.groupby("公司分类")["保费及准备金风险"]
        .mean()
        .sort_values(ascending=False)
    )
    preferred_means = premium_means[
        ~premium_means.index.astype(str).isin(["其他", "未分类"])
    ]
    if not preferred_means.empty:
        premium_means = preferred_means
    overall_premium = float(ratios["保费及准备金风险"].mean())
    high_groups = [
        str(group)
        for group, value in premium_means.items()
        if float(value) >= overall_premium
    ][:3]
    if not high_groups and not premium_means.empty:
        high_groups = [str(premium_means.index[0])]
    if len(high_groups) == 1:
        group_text = high_groups[0]
    else:
        group_text = "、".join(high_groups[:-1]) + "和" + high_groups[-1]
    premium_dispersion = float(ratios["保费及准备金风险"].std(ddof=0))
    premium_range = float(
        ratios["保费及准备金风险"].max() - ratios["保费及准备金风险"].min()
    )
    premium_distribution = (
        "较分散"
        if premium_dispersion >= 8.0 or premium_range >= 25.0
        else "相对集中"
    )
    first_sentence = (
        f"保费及准备金风险最低资本平均占比较高的主要是{group_text}，"
        f"公司间分布{premium_distribution}。"
    )

    catastrophe_group_means = ratios.groupby("公司分类")["巨灾风险"].mean()
    group_median = (
        float(catastrophe_group_means.median())
        if not catastrophe_group_means.empty
        else 0.0
    )
    group_dispersion = (
        float((catastrophe_group_means - group_median).abs().median())
        if len(catastrophe_group_means) > 1
        else 0.0
    )
    catastrophe_distribution = (
        "相对比较集中" if group_dispersion <= 3.0 else "存在一定差异"
    )
    catastrophe_values = ratios["巨灾风险"]
    mean_catastrophe = float(catastrophe_values.mean())
    std_catastrophe = float(catastrophe_values.std(ddof=0))
    outlier_threshold = mean_catastrophe + max(5.0, std_catastrophe)
    outliers = ratios[catastrophe_values > outlier_threshold].sort_values(
        "巨灾风险",
        ascending=False,
    )
    highest = ratios.loc[ratios["巨灾风险"].idxmax()]
    highest_text = (
        f"，其中{highest['公司']}最高（{float(highest['巨灾风险']):.1f}%）"
    )
    if 1 <= len(outliers) <= max(2, int(len(ratios) * 0.2)):
        outlier_text = "少数公司表现突出"
    elif len(outliers) > 1:
        outlier_text = "部分公司表现突出"
    else:
        outlier_text = "公司间差异较为平稳"
    second_sentence = (
        f"各类型公司的巨灾风险最低资本平均占比{catastrophe_distribution}，"
        f"{outlier_text}{highest_text}。"
    )
    if is_english():
        group_text_en = ", ".join(category_name(group) for group in high_groups)
        premium_distribution_en = (
            "a relatively dispersed distribution across companies"
            if premium_distribution == "较分散"
            else "a relatively concentrated distribution across companies"
        )
        catastrophe_distribution_en = (
            "was relatively concentrated across company categories"
            if catastrophe_distribution == "相对比较集中"
            else "showed some differences across company categories"
        )
        outlier_en = {
            "少数公司表现突出": "a small number of companies stood out",
            "部分公司表现突出": "some companies stood out",
            "公司间差异较为平稳": "differences across companies remained stable",
        }[outlier_text]
        return (
            f"Premium and reserve risk capital was highest among {group_text_en}, with "
            f"{premium_distribution_en}. Average catastrophe risk capital shares "
            f"{catastrophe_distribution_en}; {outlier_en}. "
            f"The highest share was {highest['公司']} at {float(highest['巨灾风险']):.1f}%."
        )
    return first_sentence + second_sentence


def insurance_risk_composition_metrics(
    frame: pd.DataFrame,
    period_order: Iterable[str],
    *,
    group_column: str = "同业分类",
    denominator_code: str = "NON_LIFE_INSURANCE_RISK_CAPITAL",
) -> dict[str, object]:
    """Return the latest-period insurance-risk composition statistics.

    The composition chart previously showed a plain sentence.  These values
    feed the compact insight card above the chart (sample size, average
    premium/reserve and catastrophe shares, and the highest-catastrophe
    company) while keeping every number data-driven.
    """
    periods = [str(period) for period in period_order]
    if frame.empty or not periods:
        return {}
    latest_period = periods[-1]
    premium_code = "NON_LIFE_PREMIUM_RESERVE_RISK_CAPITAL"
    catastrophe_code = "NON_LIFE_CATASTROPHE_RISK_CAPITAL"
    diversification_code = "NON_LIFE_INSURANCE_RISK_DIVERSIFICATION_EFFECT"
    codes = (premium_code, catastrophe_code, diversification_code, denominator_code)
    rows = frame[
        frame["报告期"].astype(str).eq(latest_period)
        & frame["指标编码"].astype(str).isin(codes)
    ].copy()
    rows["数值"] = pd.to_numeric(rows["数值"], errors="coerce")
    rows = rows.dropna(subset=["数值"])
    if rows.empty:
        return {}
    if "期间口径" in rows.columns:
        scope = rows["期间口径"].fillna("").astype(str).str.strip()
        rows["_口径优先级"] = _scope_priority(scope)
        rows = rows.sort_values(
            ["公司", "指标编码", "_口径优先级"], kind="stable"
        )
    rows = rows.drop_duplicates(["公司", "指标编码"], keep="first")
    pivot = rows.pivot_table(
        index="公司",
        columns="指标编码",
        values="数值",
        aggfunc="first",
    )
    required = {premium_code, catastrophe_code, denominator_code}
    if not required.issubset(pivot.columns):
        return {}
    denominator = pd.to_numeric(pivot[denominator_code], errors="coerce")
    valid = denominator.gt(0)
    if not valid.any():
        return {}
    ratios = pd.DataFrame(
        {
            "公司": pivot.index[valid],
            "保费及准备金风险": (
                pd.to_numeric(pivot.loc[valid, premium_code], errors="coerce")
                / denominator.loc[valid]
                * 100.0
            ).to_numpy(),
            "巨灾风险": (
                pd.to_numeric(pivot.loc[valid, catastrophe_code], errors="coerce")
                / denominator.loc[valid]
                * 100.0
            ).to_numpy(),
        }
    ).dropna()
    if ratios.empty:
        return {}
    diversification_mean = None
    if diversification_code in pivot.columns:
        div_ratio = (
            pd.to_numeric(
                pivot.loc[valid, diversification_code], errors="coerce"
            )
            .abs()
            / denominator.loc[valid]
            * 100.0
        ).dropna()
        if not div_ratio.empty:
            diversification_mean = float(div_ratio.mean())

    group_values = pd.Series("未分类", index=ratios.index, dtype=object)
    if group_column in frame.columns:
        mapping = (
            frame[["公司", group_column]]
            .dropna()
            .assign(
                **{
                    group_column: lambda data: data[group_column]
                    .fillna("")
                    .astype(str)
                    .str.strip()
                    .replace("", "未分类")
                }
            )
            .drop_duplicates("公司", keep="first")
            .set_index("公司")[group_column]
            .to_dict()
        )
        group_values = ratios["公司"].map(mapping).fillna("未分类")
    ratios["公司分类"] = group_values.to_numpy()
    premium_means = (
        ratios.groupby("公司分类")["保费及准备金风险"]
        .mean()
        .sort_values(ascending=False)
    )
    preferred_means = premium_means[
        ~premium_means.index.astype(str).isin(["其他", "未分类"])
    ]
    if not preferred_means.empty:
        premium_means = preferred_means
    overall_premium = float(ratios["保费及准备金风险"].mean())
    top_groups = [
        str(group)
        for group, value in premium_means.items()
        if float(value) >= overall_premium
    ][:3]
    highest = ratios.loc[ratios["巨灾风险"].idxmax()]
    return {
        "period": latest_period,
        "n": int(len(ratios)),
        "premium_mean": overall_premium,
        "premium_median": float(ratios["保费及准备金风险"].median()),
        "catastrophe_mean": float(ratios["巨灾风险"].mean()),
        "catastrophe_median": float(ratios["巨灾风险"].median()),
        "diversification_mean": diversification_mean,
        "top_groups": top_groups,
        "highest_company": str(highest["公司"]),
        "highest_value": float(highest["巨灾风险"]),
    }


def component_stack_axis_domain(
    frame: pd.DataFrame,
    component_specs: Iterable[tuple[str, str, str]],
    period_order: Iterable[str],
    *,
    negative_component_codes: Iterable[str] = (),
) -> tuple[float, float]:
    """Return one signed stack domain shared by every displayed company."""
    codes = {str(code) for code, _, _ in component_specs}
    periods = {str(period) for period in period_order}
    rows = clean_numeric(
        frame[frame["指标编码"].astype(str).isin(codes) & frame["报告期"].astype(str).isin(periods)]
    ).drop_duplicates(["公司", "报告期", "指标编码"], keep="last")
    if rows.empty:
        return 0.0, 1.0
    negative_codes = {str(code) for code in negative_component_codes}
    if negative_codes:
        negative_mask = rows["指标编码"].astype(str).isin(negative_codes)
        rows.loc[negative_mask, "数值"] = -pd.to_numeric(
            rows.loc[negative_mask, "数值"], errors="coerce"
        ).abs()
    rows["正向值"] = pd.to_numeric(rows["数值"], errors="coerce").clip(lower=0)
    rows["负向值"] = pd.to_numeric(rows["数值"], errors="coerce").clip(upper=0)
    totals = rows.groupby(["公司", "报告期"], dropna=False)[["正向值", "负向值"]].sum()
    lower = min(0.0, float(totals["负向值"].min()))
    upper = max(0.0, float(totals["正向值"].max()))
    padded_lower = lower * 1.08 if lower < 0 else 0.0
    padded_upper = upper * 1.08 if upper > 0 else 1.0
    return float(padded_lower), float(padded_upper)


def attach_component_stack_ratios(
    rows: pd.DataFrame,
    frame: pd.DataFrame,
    label_ratio_codes: Mapping[str, str] | None,
    label_denominator_code: str,
) -> pd.DataFrame:
    """Attach reviewed component ratios, falling back to amount/denominator."""
    result = rows.copy()
    result["标签占比"] = pd.NA
    ratio_codes = dict(label_ratio_codes or {})
    ratio_to_component = {
        ratio_code: component_code for component_code, ratio_code in ratio_codes.items()
    }
    ratio_rows = clean_numeric(frame[frame["指标编码"].astype(str).isin(ratio_to_component)])
    if not ratio_rows.empty:
        ratio_rows = ratio_rows.drop_duplicates(["公司", "报告期", "指标编码"], keep="last")[
            ["公司", "报告期", "指标编码", "数值"]
        ].copy()
        ratio_rows["指标编码"] = ratio_rows["指标编码"].map(ratio_to_component)
        ratio_rows = ratio_rows.rename(columns={"数值": "折线指标占比"})
        result = result.merge(
            ratio_rows,
            on=["公司", "报告期", "指标编码"],
            how="left",
            validate="many_to_one",
        )
        result["标签占比"] = pd.to_numeric(result["折线指标占比"], errors="coerce")
        result = result.drop(columns="折线指标占比")
    if label_denominator_code:
        denominator_rows = clean_numeric(
            frame[frame["指标编码"].astype(str).eq(label_denominator_code)]
        )
        if not denominator_rows.empty:
            denominator_rows = denominator_rows.drop_duplicates(["公司", "报告期"], keep="last")[
                ["公司", "报告期", "数值"]
            ].rename(columns={"数值": "标签分母"})
            result = result.merge(
                denominator_rows,
                on=["公司", "报告期"],
                how="left",
                validate="many_to_one",
            )
            denominator = pd.to_numeric(result["标签分母"], errors="coerce")
            fallback_ratio = pd.to_numeric(result["数值"], errors="coerce") / denominator.where(
                denominator.ne(0)
            )
            result["标签占比"] = pd.to_numeric(result["标签占比"], errors="coerce").fillna(
                fallback_ratio
            )
            result = result.drop(columns="标签分母")
    # ★ 兜底：如果分母匹配失败、标签占比全 NaN，保留 _signed_stack_positions
    #   已经算好的"子项绝对值 / 子项绝对值之和"作为兜底比例，
    #   避免整家公司因分母缺失而被 build_component_stack_chart 跳过。
    new_ratio = pd.to_numeric(result["标签占比"], errors="coerce")
    if "构成占比" in result.columns:
        fallback_ratio_from_stack = pd.to_numeric(result["构成占比"], errors="coerce")
        result["构成占比"] = new_ratio.where(
            new_ratio.notna(),
            fallback_ratio_from_stack,
        )
    else:
        result["构成占比"] = new_ratio
    return result


def component_stack_proportion_axis_domain(
    frame: pd.DataFrame,
    component_specs: Iterable[tuple[str, str, str]],
    period_order: Iterable[str],
    *,
    label_ratio_codes: Mapping[str, str] | None = None,
    label_denominator_code: str = "",
    negative_component_codes: Iterable[str] = (),
) -> tuple[float, float]:
    """Return one signed ratio-stack domain shared by every displayed company."""
    codes = {str(code) for code, _, _ in component_specs}
    periods = {str(period) for period in period_order}
    rows = clean_numeric(
        frame[frame["指标编码"].astype(str).isin(codes) & frame["报告期"].astype(str).isin(periods)]
    ).drop_duplicates(["公司", "报告期", "指标编码"], keep="last")
    if rows.empty:
        return 0.0, 1.0
    negative_codes = {str(code) for code in negative_component_codes}
    if negative_codes:
        negative_mask = rows["指标编码"].astype(str).isin(negative_codes)
        rows.loc[negative_mask, "数值"] = -pd.to_numeric(
            rows.loc[negative_mask, "数值"], errors="coerce"
        ).abs()
    rows = attach_component_stack_ratios(
        rows,
        frame,
        label_ratio_codes,
        label_denominator_code,
    )
    rows["绘图占比"] = pd.to_numeric(rows["构成占比"], errors="coerce")
    negative_amount = pd.to_numeric(rows["数值"], errors="coerce").lt(0)
    rows.loc[negative_amount, "绘图占比"] = -rows.loc[negative_amount, "绘图占比"].abs()
    rows = rows[rows["绘图占比"].notna()].copy()
    if rows.empty:
        return 0.0, 1.0
    rows["正向占比"] = rows["绘图占比"].clip(lower=0)
    rows["负向占比"] = rows["绘图占比"].clip(upper=0)
    totals = rows.groupby(["公司", "报告期"], dropna=False)[["正向占比", "负向占比"]].sum()
    lower = min(0.0, float(totals["负向占比"].min()))
    upper = max(0.0, float(totals["正向占比"].max()))
    padded_lower = lower * 1.08 if lower < 0 else 0.0
    padded_upper = upper * 1.08 if upper > 0 else 1.0
    return float(padded_lower), float(padded_upper)


def build_component_stack_chart(
    frame: pd.DataFrame,
    component_specs: Iterable[tuple[str, str, str]],
    period_order: Iterable[str],
    title: str,
    *,
    label_ratio_codes: Mapping[str, str] | None = None,
    label_denominator_code: str = "",
    value_labels: bool = False,
    negative_component_codes: Iterable[str] = (),
    dense_layout: bool = False,
    panel_count: int = 1,
    shared_y_domain: tuple[float, float] | None = None,
    plot_proportions: bool = False,
    min_label_share: float | None = None,
    annual_labels: bool = False,
) -> alt.Chart:
    """Build a signed component stack with KPMG-only semantic colors."""
    label_share_threshold = (
        MIN_INSIDE_LABEL_SHARE if min_label_share is None else max(0.0, float(min_label_share))
    )
    specs = list(component_specs)
    periods = list(period_order)
    period_labels = [display_period_label(period) for period in periods]
    period_label_map = dict(zip(periods, period_labels))
    labels = [label for _, label, _ in specs]
    colors = [color for _, _, color in specs]
    code_to_label = {code: label for code, label, _ in specs}
    code_order = {code: index for index, (code, _, _) in enumerate(specs)}
    rows = clean_numeric(frame[frame["指标编码"].astype(str).isin(code_to_label)]).copy()
    if rows.empty:
        raise ValueError(f"{title}缺少可绘制的组成指标。")
    rows = rows.drop_duplicates(["公司", "报告期", "指标编码"], keep="last")
    rows["显示期"] = rows["报告期"].astype(str).map(period_label_map)
    negative_codes = {str(code) for code in negative_component_codes}
    if negative_codes:
        negative_mask = rows["指标编码"].astype(str).isin(negative_codes)
        rows.loc[negative_mask, "数值"] = -pd.to_numeric(
            rows.loc[negative_mask, "数值"], errors="coerce"
        ).abs()
    rows["组成类别"] = rows["指标编码"].map(code_to_label)
    rows["组成顺序"] = rows["指标编码"].map(code_order)
    label_color_map = {label: label_color_for_fill(color) for label, color in zip(labels, colors)}
    rows["标签颜色"] = rows["组成类别"].map(label_color_map).fillna("#0C233C")
    rows = _signed_stack_positions(rows, "数值", "组成顺序")
    ratio_codes = dict(label_ratio_codes or {})
    if value_labels:
        rows["占比标签"] = rows.apply(
            lambda row: (
                ""
                if pd.isna(row["数值"])
                or pd.isna(row["构成占比"])
                or abs(float(row["构成占比"])) < label_share_threshold
                else whole_number_label(row["数值"])
            ),
            axis=1,
        )
    elif ratio_codes or label_denominator_code:
        rows = attach_component_stack_ratios(
            rows,
            frame,
            ratio_codes,
            label_denominator_code,
        )
        rows["占比标签"] = rows["构成占比"].map(
            lambda value: (
                ""
                if pd.isna(value) or abs(float(value)) < label_share_threshold
                else whole_percent_label(value)
            )
        )
    if plot_proportions:
        if "构成占比" not in rows:
            raise ValueError(f"{title}缺少可用于比例堆叠的构成占比。")
        rows["绘图占比"] = pd.to_numeric(rows["构成占比"], errors="coerce")
        negative_amount = pd.to_numeric(rows["数值"], errors="coerce").lt(0)
        rows.loc[negative_amount, "绘图占比"] = -rows.loc[negative_amount, "绘图占比"].abs()
        rows = rows[rows["绘图占比"].notna()].copy()
        if rows.empty:
            raise ValueError(f"{title}缺少可用于比例堆叠的构成占比。")
        rows = _signed_stack_positions(rows, "绘图占比", "组成顺序")
        rows["构成占比"] = rows["绘图占比"]
        rows["占比标签"] = rows["构成占比"].map(
            lambda value: (
                ""
                if pd.isna(value) or abs(float(value)) < label_share_threshold
                else whole_percent_label(value)
            )
        )
    company_count = rows["公司"].nunique()
    facet_columns, panel_width, facet_spacing = facet_layout(company_count)
    density_count = panel_count if panel_count > 1 else company_count
    period_font_size, value_font_size, _ = panel_typography(
        density_count,
        dense_layout,
    )
    period_scale = compact_period_scale(density_count, dense_layout)
    stack_domain = shared_y_domain or (
        component_stack_proportion_axis_domain(
            frame,
            specs,
            periods,
            label_ratio_codes=ratio_codes,
            label_denominator_code=label_denominator_code,
            negative_component_codes=negative_component_codes,
        )
        if plot_proportions
        else component_stack_axis_domain(
            frame,
            specs,
            periods,
            negative_component_codes=negative_component_codes,
        )
    )
    y_scale = alt.Scale(domain=list(stack_domain), zero=True, nice=False)
    panel_height = 285
    base = alt.Chart()
    tooltips: list[alt.Tooltip | str] = [
        "公司:N",
        "报告期:N",
        "组成类别:N",
        alt.Tooltip("数值:Q", format=",.2f"),
    ]
    if not value_labels:
        tooltips.append(alt.Tooltip("构成占比:Q", format=".1%"))
    tooltips.append("单位:N")
    bars = base.mark_bar(width=responsive_bar_width(density_count, dense_layout)).encode(
        x=alt.X(
            "显示期:N" if annual_labels else "报告期:N",
            sort=period_labels if annual_labels else periods,
            title=None,
            axis=alt.Axis(
                title=None,
                labelAngle=0,
                labelFontSize=period_font_size,
                labelLimit=55,
            ),
            scale=period_scale,
        ),
        y=alt.Y(
            "堆叠终点:Q",
            title=None,
            axis=hidden_value_axis(),
            scale=y_scale,
        ),
        y2=alt.Y2("堆叠起点:Q"),
        color=alt.Color(
            "组成类别:N",
            scale=alt.Scale(domain=labels, range=colors),
            legend=None,
        ),
        tooltip=tooltips,
    )
    labels_chart = (
        base.transform_filter(alt.datum["占比标签"] != "")
        .mark_text(
            tooltip=False,
            fontSize=value_font_size,
            fontWeight="bold",
            baseline="middle",
        )
        .encode(
            x=alt.X(
                "显示期:N" if annual_labels else "报告期:N",
                sort=period_labels if annual_labels else periods,
                scale=period_scale,
            ),
            y=alt.Y("标签位置:Q", scale=y_scale),
            text="占比标签:N",
            color=alt.Color("标签颜色:N", scale=None, legend=None),
        )
    )
    zero = (
        alt.Chart(pd.DataFrame({"零线": [0]}))
        .mark_rule(color="#0C233C", strokeWidth=1)
        .encode(y=alt.Y("零线:Q", scale=y_scale))
    )
    chart = alt.layer(bars, labels_chart, zero, data=rows).properties(
        width=panel_width, height=panel_height
    )
    if company_count == 1:
        company = str(rows["公司"].dropna().astype(str).iloc[0])
        return apply_report_theme(
            alt.layer(bars, labels_chart, zero, data=rows).properties(
                height=270,
                title=company,
            )
        )
    return apply_report_theme(
        chart.facet(
            facet=alt.Facet("公司:N", title="公司"), columns=facet_columns, spacing=facet_spacing
        )
        .resolve_scale(y="shared")
        .properties(title=title)
    )


def build_capital_amount_combo(
    frame: pd.DataFrame,
    period_order: Iterable[str],
    *,
    dense_layout: bool = False,
    panel_count: int = 1,
    annual_labels: bool = False,
) -> alt.Chart:
    source = frame[frame["指标编码"].astype(str).isin(CAPITAL_AMOUNT_CODES)].copy()
    rows = clean_numeric(source)
    periods = list(period_order)
    if rows.empty:
        raise ValueError("资本规模与结构缺少可绘制数据。")

    # 财险公司当前仅展示核心一级和附属一级资本；
    # 缺失项补 0，保证其他公司的同一结构仍可正常绘制。
    pivoted = rows.pivot_table(
        index=["公司", "报告期"],
        columns="指标编码",
        values="数值",
        aggfunc="first",
    )
    for code in CAPITAL_AMOUNT_CODES:
        if code not in pivoted.columns:
            pivoted[code] = 0.0
    pivoted = pivoted[list(CAPITAL_AMOUNT_CODES)].fillna(0.0).reset_index()

    # 单位：按公司取第一个非空值
    unit_lookup = rows.dropna(subset=["单位"]).groupby("公司")["单位"].first().to_dict()
    pivoted["单位"] = pivoted["公司"].map(unit_lookup).fillna("")

    # 重新长表化，后续逻辑不变
    rows = pivoted.melt(
        id_vars=["公司", "报告期", "单位"],
        value_vars=list(CAPITAL_AMOUNT_CODES),
        var_name="指标编码",
        value_name="数值",
    )
    period_labels = [display_period_label(period) for period in periods]
    period_label_map = dict(zip(periods, period_labels))
    rows["显示期"] = rows["报告期"].astype(str).map(period_label_map)

    rows["资本类别"] = rows["指标编码"].map(COMPONENT_LABELS)
    rows["标签颜色"] = rows["资本类别"].map(
        lambda label: label_color_for_fill(CAPITAL_STRUCTURE_COLORS.get(label, "#ACEAFF"))
    )
    rows["资本合计"] = rows.groupby(["公司", "报告期"])["数值"].transform("sum")
    rows = rows[rows["资本合计"].abs().gt(1e-12)].copy()
    if rows.empty:
        raise ValueError("资本规模与结构缺少可计算占比的四级资本数据。")
    rows["资本占比"] = rows["数值"] / rows["资本合计"]
    rows["占比标签"] = rows["资本占比"].map(
        lambda value: (
            ""
            if pd.isna(value) or abs(value) < MIN_INSIDE_LABEL_SHARE
            else whole_percent_label(value)
        )
    )
    # This is a 100% structure chart: amount remains available in tooltips,
    # while normalized shares determine segment and total bar heights.
    rows = _capital_stack_positions(rows, "资本占比")
    company_count = rows["公司"].nunique()
    facet_columns, panel_width, facet_spacing = facet_layout(company_count)
    density_count = panel_count if panel_count > 1 else company_count
    period_font_size, value_font_size, _ = panel_typography(
        density_count,
        dense_layout,
    )
    period_scale = compact_period_scale(density_count, dense_layout)
    y_scale = alt.Scale(domain=[0.0, 1.0], zero=True, nice=False)
    panel_height = 285
    # Bind the company data only at the layered-chart level. If each child
    # layer carries the full dataset, Vega-Lite's outer facet cannot isolate
    # companies and every panel stacks all companies on the same bars.
    base = alt.Chart()
    bars = (
        base.transform_filter(
            alt.FieldOneOfPredicate(field="指标编码", oneOf=list(CAPITAL_AMOUNT_CODES))
        )
        .mark_bar(width=responsive_bar_width(density_count, dense_layout))
        .encode(
            x=alt.X(
                "显示期:N" if annual_labels else "报告期:N",
                sort=period_labels if annual_labels else periods,
                title=None,
                axis=alt.Axis(
                    title=None,
                    labelAngle=0,
                    labelFontSize=period_font_size,
                    labelLimit=55,
                ),
                scale=period_scale,
            ),
            y=alt.Y(
                "堆叠终点:Q",
                title=None,
                axis=hidden_value_axis(),
                scale=y_scale,
            ),
            y2=alt.Y2("堆叠起点:Q"),
            color=alt.Color(
                "资本类别:N",
                scale=alt.Scale(
                    domain=list(CAPITAL_STRUCTURE_COLORS),
                    range=list(CAPITAL_STRUCTURE_COLORS.values()),
                ),
                legend=None,
            ),
            order=alt.Order("资本类别:N"),
            tooltip=[
                "公司:N",
                "报告期:N",
                "资本类别:N",
                alt.Tooltip("资本占比:Q", title="结构占比", format=".1%"),
                alt.Tooltip("数值:Q", title="金额", format=",.2f"),
                "单位:N",
            ],
        )
    )
    labels = (
        base.transform_filter(
            alt.FieldOneOfPredicate(field="指标编码", oneOf=list(CAPITAL_AMOUNT_CODES))
        )
        .transform_filter(alt.datum["占比标签"] != "")
        .mark_text(
            tooltip=False,
            fontSize=value_font_size,
            fontWeight="bold",
            baseline="middle",
        )
        .encode(
            x=alt.X(
                "显示期:N" if annual_labels else "报告期:N",
                sort=period_labels if annual_labels else periods,
                scale=period_scale,
            ),
            y=alt.Y("标签位置:Q", scale=y_scale),
            text=alt.Text("占比标签:N"),
            color=alt.Color("标签颜色:N", scale=None, legend=None),
        )
    )
    chart = alt.layer(bars, labels, data=rows)
    if company_count == 1:
        company = str(rows["公司"].dropna().astype(str).iloc[0])
        return apply_report_theme(chart.properties(height=270, title=company))
    return apply_report_theme(
        chart.properties(width=panel_width, height=panel_height)
        .facet(
            facet=alt.Facet("公司:N", title="公司"), columns=facet_columns, spacing=facet_spacing
        )
        .resolve_scale(y="shared")
        .properties(title="资本规模与结构")
    )


def build_effect_diverging_chart(
    frame: pd.DataFrame,
    code: str,
    period_order: Iterable[str],
) -> alt.Chart:
    """Show one effect per company with direction, magnitude, and time trend."""
    rows = clean_numeric(frame[frame["指标编码"].astype(str).eq(code)])
    if rows.empty:
        raise ValueError(f"指标 {code} 没有可绘制数据。")
    rows = rows.drop_duplicates(
        subset=["公司", "报告期", "指标编码"],
        keep="last",
    ).copy()
    rows["效应类型"] = rows["指标编码"].map(COMPONENT_LABELS)
    rows["标签位置"] = rows["数值"] / 2
    periods = list(period_order)
    company_count = rows["公司"].nunique()
    facet_columns, panel_width, facet_spacing = facet_layout(company_count)
    panel_height = 230
    dense_layout = company_count >= 10
    period_font_size, value_font_size, company_font_size = panel_typography(
        company_count,
        dense_layout,
    )
    period_scale = compact_period_scale(company_count, dense_layout)
    shared_domain = metric_bar_axis_domain(rows, code, periods)
    y_scale = alt.Scale(domain=list(shared_domain), zero=True, nice=False)
    color = COMPONENT_COLORS.get(COMPONENT_LABELS.get(code, ""), KPMG_BRIGHT_CHART_COLORS[1])
    label_color = label_color_for_fill(color)
    # Keep the shared dataset on the layered chart so the outer company facet
    # filters each bar and label layer to that company's rows.
    base = alt.Chart()
    bars = base.mark_bar(
        width=responsive_bar_width(company_count, dense_layout),
        color=color,
        opacity=0.92,
        cornerRadius=2,
    ).encode(
        x=alt.X(
            "报告期:N",
            sort=periods,
            title=None,
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
        tooltip=["公司:N", "报告期:N", "效应类型:N", alt.Tooltip("数值:Q", format=".2%")],
    )
    labels = base.mark_text(
        tooltip=False,
        baseline="middle",
        fontSize=value_font_size,
        fontWeight="bold",
        color=label_color,
    ).encode(
        x=alt.X("报告期:N", sort=periods, scale=period_scale),
        y=alt.Y("标签位置:Q", scale=y_scale),
        text=alt.Text("数值:Q", format=".1%"),
    )
    zero = (
        alt.Chart(pd.DataFrame({"零线": [0]}))
        .mark_rule(
            color="#0C233C",
            strokeWidth=1.1,
        )
        .encode(y=alt.Y("零线:Q", scale=y_scale))
    )
    name = metric_title(rows, code)
    chart = alt.layer(bars, labels, zero, data=rows).properties(
        width=panel_width,
        height=panel_height,
    )
    return apply_report_theme(
        chart.facet(
            facet=alt.Facet(
                "公司:N",
                title="公司",
                header=alt.Header(labelFontSize=company_font_size),
            ),
            columns=facet_columns,
            spacing=facet_spacing,
        )
        .resolve_scale(y="shared")
        .properties(title=f"{name}跨期发散分析")
    )
