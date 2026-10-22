"""Loss and expense ratio: annual stacked bars with break-even line."""

from __future__ import annotations

from dataclasses import dataclass

import altair as alt
import pandas as pd

from .capital import annual_period_order
from .primitives import apply_report_theme
from .structure import display_period_label


LOSS_RATIO_CODE = "LOSS_RATIO"
EXPENSE_RATIO_CODE = "EXPENSE_RATIO"
COMBINED_RATIO_CODE = "COMBINED_RATIO"
BREAKEVEN = 100.0
LOSS_COLOR = "#00338D"
EXPENSE_COLOR = "#00B8F5"
BREAKEVEN_COLOR = "#ED2124"
LOSS_EXPENSE_GROUP_ORDER = (
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
class LossExpensePanel:
    company: str
    group: str
    bar_frame: pd.DataFrame
    total_frame: pd.DataFrame
    years: tuple[str, ...]


def _company_group(frame: pd.DataFrame, company: str, group_column: str) -> str:
    rows = frame[frame["公司"].fillna("").astype(str).eq(company)]
    for column in (group_column, "同业分类", "公司类型"):
        if not column or column not in rows.columns:
            continue
        values = (
            rows[column]
            .fillna("")
            .astype(str)
            .str.strip()
            .loc[lambda series: series.ne("")]
        )
        if not values.empty:
            return str(values.iloc[0])
    return "未分类"


def _ratio_map(
    frame: pd.DataFrame,
    company: str,
    code: str,
    periods: list[str],
) -> dict[str, float]:
    rows = frame[
        frame["公司"].fillna("").astype(str).eq(company)
        & frame["指标编码"].fillna("").astype(str).eq(code)
        & frame["报告期"].fillna("").astype(str).isin(periods)
    ].copy()
    if rows.empty:
        return {}
    rows["数值"] = pd.to_numeric(rows["数值"], errors="coerce")
    rows = rows.dropna(subset=["数值"]).copy()
    if rows.empty:
        return {}
    if "期间口径" in rows.columns:
        scope = rows["期间口径"].fillna("").astype(str).str.strip()
        annual = rows[scope.str.contains("本年累计", na=False)]
        if not annual.empty:
            rows = annual
        else:
            quarter = rows[scope.str.contains("本季度", na=False)]
            if not quarter.empty:
                rows = quarter
    rows = rows.drop_duplicates("报告期", keep="first")
    return {
        str(row.报告期): float(row.数值)
        for row in rows.itertuples(index=False)
        if pd.notna(row.数值)
    }


def prepare_loss_expense_panel(
    frame: pd.DataFrame,
    company: str,
    *,
    years: int = 5,
    group_column: str = "同业分类",
) -> LossExpensePanel:
    bar_columns = ["年度", "类别", "数值", "标签", "起点", "终点", "中点", "顺序"]
    empty_bars = pd.DataFrame(columns=bar_columns)
    empty_totals = pd.DataFrame(columns=["年度", "综合成本率", "口径"])
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return LossExpensePanel(company, "未分类", empty_bars, empty_totals, ())

    annual_periods = annual_period_order(
        frame["报告期"].dropna().astype(str).unique().tolist()
    )
    annual_periods = annual_periods[-max(1, int(years or 5)) :]
    if not annual_periods:
        return LossExpensePanel(company, "未分类", empty_bars, empty_totals, ())

    loss_map = _ratio_map(frame, company, LOSS_RATIO_CODE, annual_periods)
    expense_map = _ratio_map(frame, company, EXPENSE_RATIO_CODE, annual_periods)
    combined_map = _ratio_map(frame, company, COMBINED_RATIO_CODE, annual_periods)
    group = _company_group(frame, company, group_column)

    bar_rows: list[dict[str, object]] = []
    total_rows: list[dict[str, object]] = []
    display_years: list[str] = []
    for period in annual_periods:
        loss = loss_map.get(period)
        expense = expense_map.get(period)
        if loss is None or expense is None:
            continue
        year = display_period_label(period)
        display_years.append(year)
        calculated_total = max(float(loss), 0.0) + max(float(expense), 0.0)
        reported_total = combined_map.get(period)
        total = float(reported_total) if reported_total is not None else calculated_total
        total_rows.append(
            {
                "年度": year,
                "综合成本率": total,
                "口径": "" if reported_total is not None else "测算",
                "综合标签": (
                    f"{total:.1f}%"
                    if reported_total is not None
                    else f"{total:.1f}%*"
                ),
            }
        )
        start = 0.0
        for order, (label, value) in enumerate(
            (("赔付率", loss), ("费用率", expense))
        ):
            value = max(float(value), 0.0)
            end = start + value
            bar_rows.append(
                {
                    "年度": year,
                    "类别": label,
                    "数值": value,
                    "标签": f"{value:.0f}%",
                    "起点": start,
                    "终点": end,
                    "中点": (start + end) / 2.0,
                    "顺序": order,
                }
            )
            start = end

    return LossExpensePanel(
        company,
        group,
        pd.DataFrame(bar_rows, columns=bar_columns),
        pd.DataFrame(total_rows),
        tuple(display_years),
    )


def group_loss_expense_panels(
    panels: list[LossExpensePanel],
) -> list[tuple[str, list[LossExpensePanel]]]:
    grouped: dict[str, list[LossExpensePanel]] = {}
    for panel in panels:
        grouped.setdefault(panel.group or "未分类", []).append(panel)
    ordered = [name for name in LOSS_EXPENSE_GROUP_ORDER if name in grouped]
    remaining = sorted(name for name in grouped if name not in ordered and name != "其他")
    if "其他" in grouped:
        remaining.append("其他")
    return [(name, grouped[name]) for name in [*ordered, *remaining]]


def loss_expense_axis_domain(
    panels: list[LossExpensePanel],
) -> tuple[float, float]:
    values = [BREAKEVEN]
    for panel in panels:
        if not panel.total_frame.empty:
            values.extend(
                pd.to_numeric(panel.total_frame["综合成本率"], errors="coerce")
                .dropna()
                .astype(float)
                .tolist()
            )
    maximum = max(values) if values else BREAKEVEN
    upper = max(110.0, maximum * 1.12)
    upper = float(int((upper + 9.999) // 10) * 10)
    return 0.0, upper


def build_loss_expense_panel_chart(
    panel: LossExpensePanel,
    *,
    y_domain: tuple[float, float],
) -> alt.Chart:
    if panel.bar_frame.empty:
        raise ValueError(f"{panel.company} 缺少赔付率或费用率数据。")
    years = list(panel.years)
    y_scale = alt.Scale(domain=list(y_domain), zero=True, nice=False)
    base = alt.Chart(panel.bar_frame)
    bars = base.mark_bar(size=46).encode(
        x=alt.X(
            "年度:N",
            sort=years,
            title=None,
            axis=alt.Axis(labelAngle=0, labelFontSize=9, labelPadding=7),
        ),
        y=alt.Y(
            "起点:Q",
            stack=None,
            scale=y_scale,
            title="成本率（%）",
            axis=alt.Axis(format=".0f", tickCount=6),
        ),
        y2=alt.Y2("终点:Q"),
        color=alt.Color(
            "类别:N",
            scale=alt.Scale(
                domain=["赔付率", "费用率"],
                range=[LOSS_COLOR, EXPENSE_COLOR],
            ),
            legend=None,
        ),
        order=alt.Order("顺序:Q"),
        tooltip=[
            "公司:N",
            "年度:N",
            "类别:N",
            alt.Tooltip("数值:Q", title="比率（%）", format=".2f"),
        ],
    )
    segment_labels = (
        base.transform_filter(alt.datum["数值"] >= 5)
        .mark_text(
            fontSize=9,
            fontWeight="bold",
            baseline="middle",
            color="#FFFFFF",
        )
        .encode(
            x=alt.X("年度:N", sort=years),
            y=alt.Y("中点:Q", scale=y_scale),
            text=alt.Text("标签:N"),
        )
    )
    total_source = alt.Chart(panel.total_frame)
    total_labels = total_source.mark_text(
        dy=-10,
        fontSize=8,
        fontWeight="bold",
        color="#0C233C",
    ).encode(
        x=alt.X("年度:N", sort=years),
        y=alt.Y("综合成本率:Q", scale=y_scale),
        text=alt.Text("综合标签:N"),
    )
    breakeven = (
        alt.Chart(pd.DataFrame({"盈亏平衡线": [BREAKEVEN]}))
        .mark_rule(
            color=BREAKEVEN_COLOR,
            strokeDash=[5, 4],
            strokeWidth=1.2,
        )
        .encode(y=alt.Y("盈亏平衡线:Q", scale=y_scale))
    )
    return apply_report_theme(
        alt.layer(bars, segment_labels, breakeven, total_labels)
        .properties(
            title=alt.TitleParams(
                text=panel.company,
                anchor="middle",
                fontSize=13,
                color="#00338D",
                offset=6,
            ),
            width=260,
            height=255,
        )
    )

