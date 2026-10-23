"""保费增速结构组合图：100% 车险/非车险堆叠柱 + 总保费同比增速折线。"""

from __future__ import annotations

from dataclasses import dataclass
import re

import altair as alt
import pandas as pd

from .primitives import apply_report_theme, label_color_for_fill


TOTAL_PREMIUM_CODE = "SIGNED_PREMIUM"
MOTOR_PREMIUM_CODE = "SIGNED_PREMIUM_MOTOR"
DEFAULT_ANNUAL_SCOPE = "本年累计数"
DEFAULT_YEARS = 5
PREMIUM_CATEGORY_COLORS = {
    "车险": "#00338D",
    "非车险": "#00B8F5",
    "未拆分": "#D9E2EC",
}
PREMIUM_CATEGORY_ORDER = {"车险": 0, "非车险": 1, "未拆分": 2}
GROWTH_LINE_COLOR = "#E36C0A"
ANNUAL_SCOPE_KEYWORDS = ("本年累计", "年度累计", "全年累计")
PREMIUM_GROWTH_GROUP_ORDER = (
    "前三大",
    "前4-10",
    "前十大",
    "大型公司",
    "中型公司",
    "小型公司",
    "农业保险",
    "外资系",
    "自保公司",
)


@dataclass(frozen=True)
class PremiumGrowthPanel:
    """One company's prepared five-year structure and growth series."""

    company: str
    bar_frame: pd.DataFrame
    line_frame: pd.DataFrame
    years: tuple[str, ...]
    company_group: str = "未分类"


def _period_parts(value: object) -> tuple[int | None, int]:
    """Return ``(year, quarter)``; undated year labels use quarter 0."""
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


def _annual_metric_values(
    company_frame: pd.DataFrame,
    code: str,
    annual_scope: str,
) -> dict[int, float]:
    """Pick one annual-cumulative value per year for a metric code."""
    if company_frame.empty or code not in set(company_frame["指标编码"].astype(str)):
        return {}
    work = company_frame[company_frame["指标编码"].astype(str).eq(code)].copy()
    work["数值"] = pd.to_numeric(work["数值"], errors="coerce")
    parts = work["报告期"].map(_period_parts)
    work["年度"] = parts.map(lambda item: item[0])
    work["季度"] = parts.map(lambda item: item[1])
    work = work.dropna(subset=["年度", "数值"]).copy()
    if work.empty:
        return {}
    work["年度"] = work["年度"].astype(int)

    scopes = work["期间口径"].fillna("").astype(str).str.strip()
    requested_scope = str(annual_scope or "").strip()
    if requested_scope:
        # 优先精确匹配配置的口径；如果匹配不到，回退到年度累计口径的模糊匹配
        scope_mask = scopes.eq(requested_scope)
        if not scope_mask.any():
            scope_mask = scopes.str.contains(
                "|".join(ANNUAL_SCOPE_KEYWORDS),
                regex=True,
                na=False,
            )
        if not scope_mask.any():
            scope_mask = pd.Series(True, index=work.index)
    else:
        scope_mask = scopes.str.contains(
            "|".join(ANNUAL_SCOPE_KEYWORDS),
            regex=True,
            na=False,
        )
        if not scope_mask.any():
            scope_mask = pd.Series(True, index=work.index)

    # Q4 is the annual anchor. Undated rows such as ``2024年`` are also safe.
    work = work[scope_mask & work["季度"].isin((0, 4))].copy()
    if work.empty:
        return {}
    work = (
        work.sort_values(["年度", "季度"], kind="stable")
        .drop_duplicates("年度", keep="last")
    )
    return {
        int(row.年度): float(row.数值)
        for row in work.itertuples(index=False)
        if pd.notna(row.数值)
    }


def _company_group(company_frame: pd.DataFrame, group_column: str) -> str:
    """Resolve one company's group, preferring the configured category field."""
    candidates = list(
        dict.fromkeys(
            [
                str(group_column or "").strip(),
                "同业分类",
                "公司类型",
            ]
        )
    )
    for column in candidates:
        if not column or column not in company_frame.columns:
            continue
        values = (
            company_frame[column]
            .fillna("")
            .astype(str)
            .str.strip()
            .loc[lambda series: series.ne("")]
            .drop_duplicates()
        )
        if not values.empty:
            return str(values.iloc[0])
    return "未分类"


def prepare_premium_growth_panel(
    frame: pd.DataFrame,
    company_name: str,
    *,
    years: int = DEFAULT_YEARS,
    total_code: str = TOTAL_PREMIUM_CODE,
    motor_code: str = MOTOR_PREMIUM_CODE,
    annual_scope: str = DEFAULT_ANNUAL_SCOPE,
    group_column: str = "同业分类",
) -> PremiumGrowthPanel:
    """Prepare a company panel from annual total and motor premium values.

    Motor share is ``motor / total``. Non-motor is derived as
    ``total - motor`` so the two shares always reconcile to 100%. The line is
    the year-on-year growth of total premium, not the growth of either segment.
    """
    columns = ["年度", "类别", "金额", "占比", "占比起点", "占比终点", "占比中点", "排序", "标签颜色"]
    empty_bars = pd.DataFrame(columns=columns)
    empty_lines = pd.DataFrame(
        columns=["年度", "同比增速", "本年保费", "上年保费", "标签dy", "折线组"]
    )
    company = str(company_name or "").strip()
    if not isinstance(frame, pd.DataFrame) or frame.empty or not company:
        return PremiumGrowthPanel(company, empty_bars, empty_lines, (), "未分类")

    work = frame[frame["公司"].fillna("").astype(str).eq(company)].copy()
    if work.empty:
        return PremiumGrowthPanel(company, empty_bars, empty_lines, (), "未分类")
    company_group = _company_group(work, group_column)

    total_values = _annual_metric_values(work, total_code, annual_scope)
    motor_values = _annual_metric_values(work, motor_code, annual_scope)
    total_years = sorted(total_values)
    if not total_years:
        return PremiumGrowthPanel(company, empty_bars, empty_lines, (), company_group)

    display_years = total_years[-max(1, int(years or DEFAULT_YEARS)) :]
    bar_rows: list[dict[str, object]] = []
    line_rows: list[dict[str, object]] = []
    line_group = 0
    previous_line_year: int | None = None

    for year in display_years:
        total = total_values.get(year)
        if total is None or total <= 0:
            continue
        motor = motor_values.get(year)
        if motor is None or motor < 0 or motor > total + 1e-9:
            parts = [("未拆分", float(total), 100.0)]
        else:
            motor_share = float(motor) / float(total) * 100.0
            parts = [
                ("车险", float(motor), motor_share),
                ("非车险", float(total - motor), 100.0 - motor_share),
            ]

        start = 0.0
        for category, amount, share in parts:
            if share <= 1e-9:
                continue
            end = min(100.0, start + float(share))
            color = PREMIUM_CATEGORY_COLORS.get(category, "#B8BDC7")
            bar_rows.append(
                {
                    "年度": str(year),
                    "类别": category,
                    "金额": float(amount),
                    "占比": float(share),
                    "占比起点": start,
                    "占比终点": end,
                    "占比中点": (start + end) / 2.0,
                    "排序": PREMIUM_CATEGORY_ORDER.get(category, 9),
                    "标签颜色": label_color_for_fill(color),
                }
            )
            start = end

        previous_total = total_values.get(year - 1)
        if previous_total is not None and previous_total > 0:
            growth = (float(total) - float(previous_total)) / float(previous_total) * 100.0
            if previous_line_year is not None and year != previous_line_year + 1:
                line_group += 1
            line_rows.append(
                {
                    "年度": str(year),
                    "同比增速": growth,
                    "本年保费": float(total),
                    "上年保费": float(previous_total),
                    "标签dy": 12 if growth < 0 else -10,
                    "折线组": line_group,
                }
            )
            previous_line_year = year

    return PremiumGrowthPanel(
        company,
        pd.DataFrame(bar_rows, columns=columns),
        pd.DataFrame(line_rows),
        tuple(str(year) for year in display_years),
        company_group,
    )


def group_premium_growth_panels(
    panels: list[PremiumGrowthPanel],
) -> list[tuple[str, list[PremiumGrowthPanel]]]:
    """Group panels in the same category order used by the company report."""
    grouped: dict[str, list[PremiumGrowthPanel]] = {}
    for panel in panels:
        group_name = str(panel.company_group or "").strip() or "未分类"
        grouped.setdefault(group_name, []).append(panel)
    ordered_names = [name for name in PREMIUM_GROWTH_GROUP_ORDER if name in grouped]
    remaining = sorted(
        name for name in grouped if name not in ordered_names and name != "其他"
    )
    if "其他" in grouped:
        remaining.append("其他")
    return [(name, grouped[name]) for name in [*ordered_names, *remaining]]


def premium_growth_axis_domain(
    panels: list[PremiumGrowthPanel],
) -> tuple[float, float]:
    """Return one shared right-axis range so company panels stay comparable."""
    values: list[float] = []
    for panel in panels:
        if panel.line_frame.empty:
            continue
        numeric = pd.to_numeric(panel.line_frame["同比增速"], errors="coerce").dropna()
        values.extend(float(value) for value in numeric if abs(float(value)) < 1_000)
    if not values:
        return -10.0, 35.0
    low = min(0.0, min(values) * 1.15)
    high = max(0.0, max(values) * 1.15)
    if high - low < 10.0:
        high += 5.0
        low -= 5.0
    return low, high


def build_premium_growth_structure_chart(
    panel: PremiumGrowthPanel,
    *,
    growth_domain: tuple[float, float],
    unit_label: str,
) -> alt.Chart:
    """Build a company panel with a 100% stack and one total-premium growth line."""
    if panel.bar_frame.empty:
        raise ValueError(f"{panel.company} 没有可绘制的年度保费结构数据。")

    years = list(panel.years)
    share_scale = alt.Scale(domain=[0, 100], zero=True, nice=False)
    growth_scale = alt.Scale(domain=list(growth_domain), zero=False, nice=True)
    x_axis = alt.Axis(
        title=None,
        labelAngle=0,
        labelFontSize=9,
        labelPadding=7,
    )
    x = alt.X("年度:N", sort=years, axis=x_axis)

    bar_base = alt.Chart(panel.bar_frame).transform_calculate(
        占比标签 = "datum['占比'] != null && !isNaN(datum['占比']) ? format(datum['占比'], '.0f') + '%' : ''"
    )
    bars = bar_base.mark_bar(size=46).encode(
        x=x,
        y=alt.Y(
            "占比起点:Q",
            stack=None,
            scale=share_scale,
            title="占比（%）",
            axis=alt.Axis(
                format=".0f",
                tickCount=6,
                titlePadding=5,
                titleFontSize=8,
                labelFontSize=8,
            ),
        ),
        y2=alt.Y2("占比终点:Q"),
        color=alt.Color(
            "类别:N",
            scale=alt.Scale(
                domain=list(PREMIUM_CATEGORY_COLORS),
                range=list(PREMIUM_CATEGORY_COLORS.values()),
            ),
            legend=None,
        ),
        order=alt.Order("排序:Q"),
        tooltip=[
            alt.Tooltip("年度:N", title="年度"),
            alt.Tooltip("类别:N", title="结构"),
            alt.Tooltip("金额:Q", title=f"保费（{unit_label}）", format=",.1f"),
            alt.Tooltip("占比:Q", title="占比（%）", format=".1f"),
        ],
    )
    bar_labels = bar_base.mark_text(
        fontSize=9,
        fontWeight="bold",
        baseline="middle",
    ).encode(
        x=x,
        y=alt.Y("占比中点:Q", stack=None, scale=share_scale, axis=None),
        text=alt.condition(
            alt.datum["占比"] >= 4,
            "占比标签:N",
            alt.value(""),
        ),
        color=alt.Color(
            "标签颜色:N",
            scale=alt.Scale(
                domain=["#FFFFFF", "#0C233C"],
                range=["#FFFFFF", "#0C233C"],
            ),
            legend=None,
        ),
    )

    growth_layers: list[alt.Chart] = []
    if not panel.line_frame.empty:
        line_base = alt.Chart(panel.line_frame)
        line_x = x
        line_y = alt.Y(
            "同比增速:Q",
            scale=growth_scale,
            axis=alt.Axis(
                orient="right",
                format=".0f",
                title="增速（%）",
                titlePadding=3,
                titleFontSize=8,
                labelFontSize=8,
                tickCount=5,
                grid=False,
            ),
        )
        line_y_without_axis = alt.Y(
            "同比增速:Q",
            scale=growth_scale,
            axis=None,
        )
        zero_axis = alt.Chart(pd.DataFrame({"零点": [0.0]})).mark_rule(
            color="#9AA4B2",
            strokeDash=[4, 4],
            strokeWidth=1,
        ).encode(y=alt.Y("零点:Q", scale=growth_scale, axis=None))
        line = line_base.mark_line(
            color=GROWTH_LINE_COLOR,
            strokeWidth=2.5,
        ).encode(
            x=line_x,
            y=line_y,
            detail=alt.Detail("折线组:N"),
            tooltip=[
                alt.Tooltip("年度:N", title="年度"),
                alt.Tooltip("同比增速:Q", title="总保费同比增速（%）", format="+.2f"),
                alt.Tooltip("上年保费:Q", title=f"上年保费（{unit_label}）", format=",.1f"),
                alt.Tooltip("本年保费:Q", title=f"本年保费（{unit_label}）", format=",.1f"),
            ],
        )
        points = line_base.mark_point(
            size=70,
            filled=True,
            color=GROWTH_LINE_COLOR,
            stroke="#FFFFFF",
            strokeWidth=1.4,
        ).encode(
            x=line_x,
            y=line_y_without_axis,
            tooltip=[
                alt.Tooltip("年度:N", title="年度"),
                alt.Tooltip("同比增速:Q", title="总保费同比增速（%）", format="+.2f"),
            ],
        )
        positive_labels = (
            line_base.transform_filter(alt.datum["同比增速"] >= 0)
            .mark_text(
                fontSize=8,
                fontWeight="bold",
                color=GROWTH_LINE_COLOR,
                baseline="middle",
                dy=-10,
                dx=8,
            )
            .encode(
                x=line_x,
                y=line_y_without_axis,
                text=alt.Text("同比增速:Q", format="+.1f"),
            )
        )
        negative_labels = (
            line_base.transform_filter(alt.datum["同比增速"] < 0)
            .mark_text(
                fontSize=8,
                fontWeight="bold",
                color=GROWTH_LINE_COLOR,
                baseline="middle",
                dy=12,
                dx=8,
            )
            .encode(
                x=line_x,
                y=line_y_without_axis,
                text=alt.Text("同比增速:Q", format="+.1f"),
            )
        )
        growth_layers = [zero_axis, line, points, positive_labels, negative_labels]

    chart = (
        alt.layer(bars, bar_labels, *growth_layers)
        .resolve_scale(y="independent", color="independent")
        .properties(
            title=alt.TitleParams(
                text=panel.company,
                anchor="middle",
                fontSize=12,
                color="#00338D",
                offset=8,
            ),
            width=260,
            height=255,
        )
    )
    return apply_report_theme(chart)

