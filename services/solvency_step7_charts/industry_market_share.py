"""行业市场份额：公司类别占比饼图与分类金额柱状图。"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from ..report_i18n import t, unit_name
from ..solvency_company_identity import (
    CompanyMaster,
    load_company_master_workbook,
    resolve_peer_group,
)
from ..report_style import (
    COBALT_BLUE,
    LIGHT_GREEN,
    LIGHT_PINK,
    LIGHT_PURPLE,
    PACIFIC_BLUE,
    PINK,
    PRIMARY_BLUE,
    PURPLE,
    REPORT_AXIS_COLOR,
    REPORT_DOMAIN_COLOR,
    REPORT_FONT,
    REPORT_GRID_COLOR,
    REPORT_TEXT_COLOR,
)
from .industry_premium import (
    ANNUAL_SCOPE_ALIASES,
    _annual_rows,
    _canonical_company_name,
    _year_from_period,
)


ROOT = Path(__file__).resolve().parents[2]
COMPANY_LIST_PATH = ROOT / "财险公司列表.xlsx"
COMPANY_ALIAS_WORKBOOK = ROOT / "config" / "non_life_solvency_company_urls.xlsx"

TOP10_CATEGORY = "前十大"
TOP3_CATEGORY = "前三大"
TOP4_10_CATEGORY = "前4-10"
TOP3_NONLIFE_PREFIXES = ("人保", "平安", "太保")
CATEGORY_ORDER = (
    TOP3_CATEGORY,
    TOP4_10_CATEGORY,
    TOP10_CATEGORY,
    "大型公司",
    "中型公司",
    "小型公司",
    "外资系",
    "自保公司",
    "农业保险",
    "其他",
)
CATEGORY_COLORS = {
    TOP3_CATEGORY: PRIMARY_BLUE,
    TOP4_10_CATEGORY: COBALT_BLUE,
    TOP10_CATEGORY: PRIMARY_BLUE,
    "大型公司": PACIFIC_BLUE,
    "中型公司": "#00C0AE",
    "小型公司": "#63EBB2",
    "外资系": PURPLE,
    "自保公司": PINK,
    "农业保险": "#F1C44D",
    "其他": "#9AA6B2",
}
# Loss-making underwriting categories use a single warning colour and a
# white hatch, so they remain distinguishable from the profit categories.
LOSS_SECTOR_COLOR = "#C2410C"
FALLBACK_COLORS = (
    LIGHT_PURPLE,
    LIGHT_GREEN,
    LIGHT_PINK,
    "#76D2FF",
)

PREMIUM_CODES = ("SIGNED_PREMIUM", "INSURANCE_REVENUE", "PREMIUM_INCOME")
NET_PROFIT_CODE = "NET_PROFIT"
UNDERWRITING_PROFIT_CODES = (
    "UNDERWRITING_PROFIT",
    "UNDERWRITING_PROFIT_AMOUNT",
)
COMBINED_RATIO_CODE = "COMBINED_RATIO"
DISPLAY_UNITS = ("十亿元", "亿元", "百万元", "十万元")
DEFAULT_DISPLAY_UNIT = "亿元"
DIVISOR_BY_UNIT = {
    "十亿元": 100_000.0,
    "亿元": 10_000.0,
    "百万元": 100.0,
    "十万元": 10.0,
}


@dataclass(frozen=True)
class MarketShareData:
    year: str
    categories: pd.DataFrame
    company_detail: pd.DataFrame
    company_count: int
    coverage: float
    top_grouping: str = "split"


@lru_cache(maxsize=1)
def _company_master() -> CompanyMaster | None:
    if not COMPANY_LIST_PATH.exists():
        return None
    return load_company_master_workbook(
        COMPANY_LIST_PATH,
        alias_workbook=COMPANY_ALIAS_WORKBOOK if COMPANY_ALIAS_WORKBOOK.exists() else None,
    )


def _category_for(company: object, master: CompanyMaster | None) -> str:
    if master is None:
        return "其他"
    return resolve_peer_group(company, master.peer_group_map, "其他")


def _data_category_map(frame: pd.DataFrame) -> dict[str, str]:
    """Use the uploaded category as a fallback for unmatched master names."""
    if "同业分类" not in frame.columns:
        return {}
    work = frame[["公司", "同业分类"]].copy()
    work["公司"] = work["公司"].fillna("").astype(str).str.strip()
    work["同业分类"] = work["同业分类"].fillna("").astype(str).str.strip()
    work = work[
        work["公司"].ne("")
        & work["同业分类"].ne("")
        & work["同业分类"].ne("其他")
    ]
    if work.empty:
        return {}
    counts = (
        work.groupby(["公司", "同业分类"])
        .size()
        .reset_index(name="记录数")
        .sort_values(["公司", "记录数"], ascending=[True, False])
    )
    return counts.drop_duplicates("公司", keep="first").set_index("公司")["同业分类"].to_dict()


def _annual_map(
    frame: pd.DataFrame,
    code: str,
    years: list[int],
) -> dict[tuple[str, int], float]:
    rows = _annual_rows(
        frame,
        code,
        years,
        ANNUAL_SCOPE_ALIASES,
    )
    if rows.empty:
        return {}
    return {
        (str(row.公司), int(row.年度)): float(row.数值)
        for row in rows.itertuples(index=False)
    }


def _first_available_map(
    frame: pd.DataFrame,
    codes: tuple[str, ...],
    years: list[int],
) -> dict[tuple[str, int], float]:
    result: dict[tuple[str, int], float] = {}
    for code in codes:
        for key, value in _annual_map(frame, code, years).items():
            result.setdefault(key, value)
    return result


def _combined_ratio_percent(
    frame: pd.DataFrame,
    years: list[int],
) -> dict[tuple[str, int], float]:
    result: dict[tuple[str, int], float] = {}
    rows = _annual_rows(
        frame,
        COMBINED_RATIO_CODE,
        years,
        ANNUAL_SCOPE_ALIASES,
    )
    if rows.empty:
        return result
    for row in rows.itertuples(index=False):
        value = float(row.数值)
        unit = str(getattr(row, "单位", "") or "").strip()
        if unit == "倍" or (unit not in {"%", "％"} and abs(value) <= 1.5):
            value *= 100.0
        result[(str(row.公司), int(row.年度))] = value
    return result


def _ordered_categories(categories: list[str]) -> list[str]:
    present = [str(value).strip() for value in categories if str(value).strip()]
    ordered = [value for value in CATEGORY_ORDER if value in present]
    return [*ordered, *(value for value in present if value not in ordered)]


def _category_color_map(categories: list[str]) -> dict[str, str]:
    colors: dict[str, str] = {}
    fallback_index = 0
    for category in categories:
        color = CATEGORY_COLORS.get(category)
        if color is None:
            color = FALLBACK_COLORS[fallback_index % len(FALLBACK_COLORS)]
            fallback_index += 1
        colors[category] = color
    return colors


def market_share_category_colors(categories: list[str]) -> dict[str, str]:
    """Return the stable category-to-color mapping used by pies and legends."""
    return _category_color_map(categories)


def _build_year_view(
    frame: pd.DataFrame,
    *,
    year: int,
    companies: list[str],
    master: CompanyMaster | None,
    premium_map: dict[tuple[str, int], float],
    profit_map: dict[tuple[str, int], float],
    combined_map: dict[tuple[str, int], float],
    underwriting_map: dict[tuple[str, int], float],
    data_categories: dict[str, str],
    top_grouping: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, object]] = []
    for company in companies:
        key = (company, year)
        premium = premium_map.get(key)
        net_profit = profit_map.get(key)
        combined = combined_map.get(key)
        underwriting = underwriting_map.get(key)
        if underwriting is None and premium is not None and combined is not None:
            underwriting = premium * (1.0 - combined / 100.0)
        master_category = _category_for(company, master)
        category = (
            master_category
            if master_category != "其他"
            else data_categories.get(company, "其他")
        )
        if top_grouping == "split" and category == TOP10_CATEGORY:
            category = (
                TOP3_CATEGORY
                if str(company).strip().startswith(TOP3_NONLIFE_PREFIXES)
                else TOP4_10_CATEGORY
            )
        rows.append(
            {
                "公司": company,
                "公司类别": category,
                "保费": premium,
                "净利润": net_profit,
                "承保利润": underwriting,
                "综合成本率": combined,
                "承保利润测算": (
                    underwriting is not None
                    and (company, year) not in underwriting_map
                ),
                "有数据": any(
                    value is not None
                    for value in (premium, net_profit, underwriting)
                ),
            }
        )
    detail = pd.DataFrame(rows)
    if detail.empty:
        return pd.DataFrame(), detail
    detail[["保费", "净利润", "承保利润"]] = detail[
        ["保费", "净利润", "承保利润"]
    ].apply(pd.to_numeric, errors="coerce")

    category = (
        detail.groupby("公司类别", dropna=False)
        .agg(
            公司数=("有数据", "sum"),
            保费=("保费", lambda series: series.sum(min_count=1)),
            净利润=("净利润", lambda series: series.sum(min_count=1)),
            承保利润=("承保利润", lambda series: series.sum(min_count=1)),
        )
        .reset_index()
    )
    category["公司类别"] = category["公司类别"].fillna("其他").astype(str)
    order = _ordered_categories(category["公司类别"].tolist())
    category["公司类别"] = pd.Categorical(
        category["公司类别"], categories=order, ordered=True
    )
    category = category.sort_values("公司类别").reset_index(drop=True)
    category["公司类别"] = category["公司类别"].astype(str)
    return category, detail


def prepare_market_share_data(
    frame: pd.DataFrame | None,
    *,
    min_coverage: float = 0.8,
    top_grouping: str = "split",
) -> MarketShareData | None:
    """Prepare the latest sufficiently complete annual market-share view."""
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return None
    work = frame.copy()
    for column in ("公司", "报告期", "指标编码", "期间口径", "单位", "数值"):
        if column not in work.columns:
            work[column] = ""
    work["公司"] = work["公司"].fillna("").astype(str).str.strip()
    work = work[work["公司"].ne("") & ~work["公司"].eq("行业合计")].copy()
    work["公司"] = work["公司"].map(_canonical_company_name)
    if work.empty:
        return None

    companies = sorted(work["公司"].unique().tolist())
    all_years = sorted(
        {
            year
            for year in work["报告期"].map(_year_from_period).dropna().astype(int)
        }
    )
    if not all_years:
        return None
    premium_map = _first_available_map(work, PREMIUM_CODES, all_years)
    profit_map = _annual_map(work, NET_PROFIT_CODE, all_years)
    combined_map = _combined_ratio_percent(work, all_years)
    underwriting_map = _first_available_map(
        work,
        UNDERWRITING_PROFIT_CODES,
        all_years,
    )
    if not premium_map:
        return None

    master = _company_master()
    data_categories = _data_category_map(work)
    best: tuple[int, pd.DataFrame, pd.DataFrame, float, int] | None = None
    threshold = max(1, int(len(companies) * min_coverage))
    for year in all_years:
        category, detail = _build_year_view(
            work,
            year=year,
            companies=companies,
            master=master,
            premium_map=premium_map,
            profit_map=profit_map,
            combined_map=combined_map,
            underwriting_map=underwriting_map,
            data_categories=data_categories,
            top_grouping=top_grouping,
        )
        if category.empty:
            continue
        premium_count = int(pd.to_numeric(detail["保费"], errors="coerce").notna().sum())
        profit_count = int(pd.to_numeric(detail["净利润"], errors="coerce").notna().sum())
        underwriting_count = int(
            pd.to_numeric(detail["承保利润"], errors="coerce").notna().sum()
        )
        coverage = min(premium_count, profit_count, underwriting_count) / max(len(companies), 1)
        if (
            premium_count >= threshold
            and profit_count >= threshold
            and underwriting_count >= threshold
        ):
            best = (year, category, detail, coverage, len(companies))
    if best is None:
        return None

    year, category, detail, coverage, company_count = best
    for metric in ("保费", "净利润", "承保利润"):
        numeric = pd.to_numeric(category[metric], errors="coerce")
        # Premium and net profit shares keep the usual positive-value basis.
        # Underwriting profit uses absolute values so loss-making categories
        # remain visible in the pie instead of being dropped.
        magnitude = numeric.abs() if metric == "承保利润" else numeric.clip(lower=0)
        total = float(magnitude.sum())
        category[f"{metric}占比"] = (
            magnitude / total if total > 0 else pd.Series(0.0, index=category.index)
        )
    return MarketShareData(
        year=str(year),
        categories=category,
        company_detail=detail,
        company_count=company_count,
        coverage=coverage,
        top_grouping=top_grouping,
    )


def _signed_amount_label(value: float) -> str:
    """Format a signed amount with an explicit sign for pie hover data."""
    if value < 0:
        return f"−{abs(value):,.1f}"
    return f"+{value:,.1f}"


def build_market_share_pies(
    data: MarketShareData,
    *,
    unit_mode: str = DEFAULT_DISPLAY_UNIT,
    showlegend: bool = True,
) -> go.Figure:
    unit = unit_mode if unit_mode in DISPLAY_UNITS else DEFAULT_DISPLAY_UNIT
    divisor = DIVISOR_BY_UNIT[unit]
    categories = data.categories.copy()
    color_map = _category_color_map(categories["公司类别"].tolist())
    fig = make_subplots(
        rows=1,
        cols=3,
        specs=[[{"type": "domain"}, {"type": "domain"}, {"type": "domain"}]],
        subplot_titles=("保费", "净利润", "承保利润"),
        horizontal_spacing=0.08,
    )
    for index, metric in enumerate(("保费", "净利润", "承保利润")):
        is_underwriting = metric == "承保利润"
        raw = pd.to_numeric(categories[metric], errors="coerce").fillna(0)
        # Premium and net profit keep the usual positive-value shares.  For
        # underwriting profit, loss-making categories are included at their
        # absolute value so the pie shows both profitable and loss categories.
        magnitudes = raw.abs() if is_underwriting else raw.clip(lower=0)
        mask = magnitudes.gt(0)
        plot = categories.loc[mask, ["公司类别"]].copy()
        plot["金额"] = raw.loc[mask] / divisor
        plot["扇区值"] = magnitudes.loc[mask] / divisor
        if float(plot["扇区值"].sum()) <= 0:
            fig.add_trace(
                go.Pie(
                    labels=["无正值"],
                    values=[1],
                    marker_colors=[REPORT_GRID_COLOR],
                    textinfo="none",
                    showlegend=False,
                ),
                row=1,
                col=index + 1,
            )
            continue
        total = float(plot["扇区值"].sum())
        shares = [
            float(value) / total if total > 0 else 0.0
            for value in plot["扇区值"]
        ]
        # Keep labels inside the slices and show only percentages; category
        # names stay in the legend.  Outside labels with category names can
        # overlap the subplot title when the figure is placed in a report.
        # Loss slices are sized by absolute value but are usually too narrow
        # for inside text, which Plotly would clip into a misleading fragment;
        # the renderer lists their amounts and shares below the chart instead.
        labels: list[str] = []
        for amount, share in zip(plot["金额"], shares):
            if is_underwriting and float(amount) < 0:
                labels.append("")
            elif share < 0.03:
                labels.append("")
            else:
                labels.append(f"{share:.1%}")
        loss_flags = [
            is_underwriting and float(amount) < 0 for amount in plot["金额"]
        ]
        sector_colors = [
            LOSS_SECTOR_COLOR if loss else color_map.get(category, "#9AA6B2")
            for category, loss in zip(plot["公司类别"], loss_flags)
        ]
        if is_underwriting:
            hover_template = (
                "<b>%{label}</b><br>"
                "承保利润：%{customdata} "
                f"{unit}<br>"
                "绝对值占比：%{percent:.1%}<extra></extra>"
            )
        else:
            hover_template = (
                "<b>%{label}</b><br>"
                f"{metric}：%{{value:,.1f}} {unit}<br>"
                "占比：%{percent:.1%}<extra></extra>"
            )
        top_share = max(shares) if shares else 0.0
        # Rotate a dominant slice so the small remainder sits in the
        # bottom-right quadrant instead of crowding the upper-left labels.
        rotation = (
            (315.0 + top_share * 360.0 - 90.0) % 360.0
            if top_share >= 0.5
            else 0.0
        )
        fig.add_trace(
            go.Pie(
                labels=plot["公司类别"],
                values=plot["扇区值"],
                customdata=(
                    [
                        _signed_amount_label(float(amount))
                        for amount in plot["金额"]
                    ]
                    if is_underwriting
                    else plot["金额"].tolist()
                ),
                hole=0,
                sort=False,
                direction="clockwise",
                rotation=rotation,
                marker=dict(
                    colors=sector_colors,
                    line=dict(color="#FFFFFF", width=2),
                    pattern=dict(
                        shape=["/" if loss else "" for loss in loss_flags],
                        fgcolor="#FFFFFF",
                        bgcolor=sector_colors,
                    ),
                ),
                text=labels,
                textinfo="text",
                textposition="inside",
                insidetextorientation="horizontal",
                textfont=dict(
                    family=REPORT_FONT,
                    size=9,
                    color="#FFFFFF",
                ),
                automargin=False,
                showlegend=showlegend and index == 0,
                name=metric,
                hovertemplate=hover_template,
            ),
            row=1,
            col=index + 1,
        )
    fig.update_layout(
        height=420,
        paper_bgcolor="#FFFFFF",
        plot_bgcolor="#FFFFFF",
        margin=dict(t=64, b=64, l=16, r=16),
        legend=dict(
            orientation="h",
            y=-0.08,
            x=0.5,
            xanchor="center",
            title=None,
            font=dict(family=REPORT_FONT, size=10, color=REPORT_TEXT_COLOR),
        ),
        font=dict(family=REPORT_FONT, color=REPORT_TEXT_COLOR, size=11),
        hoverlabel=dict(
            bgcolor="#FFFFFF",
            bordercolor=REPORT_DOMAIN_COLOR,
            font=dict(family=REPORT_FONT, size=11, color=REPORT_TEXT_COLOR),
        ),
    )
    fig.update_annotations(
        font=dict(family=REPORT_FONT, size=12, color=PRIMARY_BLUE)
    )
    return fig


def build_market_share_premium_pie(
    data: MarketShareData,
    *,
    unit_mode: str = DEFAULT_DISPLAY_UNIT,
) -> go.Figure:
    """Render the premium market-share pie as a single full-width donut."""
    unit = unit_mode if unit_mode in DISPLAY_UNITS else DEFAULT_DISPLAY_UNIT
    divisor = DIVISOR_BY_UNIT[unit]
    categories = data.categories.copy()
    color_map = _category_color_map(categories["公司类别"].tolist())
    raw = pd.to_numeric(categories["保费"], errors="coerce").fillna(0)
    mask = raw.gt(0)
    plot = categories.loc[mask, ["公司类别"]].copy()
    plot["金额"] = raw.loc[mask] / divisor
    total = float(plot["金额"].sum())
    if total <= 0:
        return go.Figure()

    shares = [float(value) / total for value in plot["金额"]]
    labels = [
        f"{share:.1%}" if share >= 0.025 else ""
        for share in shares
    ]
    top_share = max(shares) if shares else 0.0
    rotation = (
        (315.0 + top_share * 360.0 - 90.0) % 360.0
        if top_share >= 0.5
        else 0.0
    )
    fig = go.Figure(
        go.Pie(
            labels=plot["公司类别"],
            values=plot["金额"],
            hole=0.46,
            sort=False,
            direction="clockwise",
            rotation=rotation,
            marker=dict(
                colors=[
                    color_map.get(category, "#9AA6B2")
                    for category in plot["公司类别"]
                ],
                line=dict(color="#FFFFFF", width=3),
            ),
            text=labels,
            textinfo="text",
            textposition="inside",
            insidetextorientation="horizontal",
            textfont=dict(
                family=REPORT_FONT,
                size=11,
                color="#FFFFFF",
            ),
            hovertemplate=(
                "<b>%{label}</b><br>"
                f"{t('保费', 'Premium')}：%{{value:,.1f}} "
                f"{unit_name(unit)}<br>"
                f"{t('占比', 'Share')}：%{{percent:.1%}}<extra></extra>"
            ),
        )
    )
    fig.update_layout(
        height=440,
        paper_bgcolor="#FFFFFF",
        plot_bgcolor="#FFFFFF",
        margin=dict(t=8, b=8, l=8, r=8),
        showlegend=False,
        annotations=[
            dict(
                text=t("保费合计", "Total premium"),
                x=0.5,
                y=0.545,
                xref="paper",
                yref="paper",
                showarrow=False,
                font=dict(
                    family=REPORT_FONT,
                    size=12,
                    color=REPORT_AXIS_COLOR,
                ),
            ),
            dict(
                text=f"{total:,.1f}",
                x=0.5,
                y=0.485,
                xref="paper",
                yref="paper",
                showarrow=False,
                font=dict(
                    family=REPORT_FONT,
                    size=25,
                    color=PRIMARY_BLUE,
                ),
            ),
            dict(
                text=unit_name(unit),
                x=0.5,
                y=0.425,
                xref="paper",
                yref="paper",
                showarrow=False,
                font=dict(
                    family=REPORT_FONT,
                    size=12,
                    color=REPORT_AXIS_COLOR,
                ),
            ),
        ],
        font=dict(family=REPORT_FONT, color=REPORT_TEXT_COLOR, size=11),
        hoverlabel=dict(
            bgcolor="#FFFFFF",
            bordercolor=REPORT_DOMAIN_COLOR,
            font=dict(family=REPORT_FONT, size=11, color=REPORT_TEXT_COLOR),
        ),
    )
    return fig


def build_market_share_profit_bars(
    data: MarketShareData,
    *,
    unit_mode: str = DEFAULT_DISPLAY_UNIT,
) -> go.Figure:
    """Render premium, net profit and underwriting profit with share labels.

    The first row keeps the signed-premium amount bars and uses the right-hand
    axis for each category's share of total signed premium.  The second row
    keeps net profit and underwriting profit together as grouped bars; the
    percentage above each bar is the category's share of that metric's
    absolute-value total, so loss-making categories stay negative.
    """
    unit = unit_mode if unit_mode in DISPLAY_UNITS else DEFAULT_DISPLAY_UNIT
    divisor = DIVISOR_BY_UNIT[unit]
    categories = data.categories.copy()
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.16,
        row_heights=[0.45, 0.55],
        specs=[[{"secondary_y": True}], [{"secondary_y": False}]],
        subplot_titles=(
            t("签单保费", "Signed premium"),
            t("净利润 / 承保利润", "Net profit / Underwriting profit"),
        ),
    )
    x_values = categories["公司类别"].tolist()
    premium_values = (
        pd.to_numeric(categories["保费"], errors="coerce").fillna(0) / divisor
    ).tolist()
    premium_total = float(sum(abs(value) for value in premium_values))
    premium_shares = [
        (float(value) / premium_total if premium_total else 0.0)
        for value in premium_values
    ]
    premium_high = max([0.0, *premium_values])
    premium_pad = max(premium_high * 0.14, 1.0)
    premium_high_pad = premium_high + premium_pad
    fig.add_trace(
        go.Bar(
            x=x_values,
            y=premium_values,
            name=t("签单保费", "Signed premium"),
            marker_color=PRIMARY_BLUE,
            customdata=premium_shares,
            text=[f"{share:.1%}" for share in premium_shares],
            textposition="outside",
            textfont=dict(family=REPORT_FONT, size=8.5, color="#0C233C"),
            cliponaxis=False,
            showlegend=False,
            hovertemplate=(
                "<b>%{x}</b><br>"
                f"{t('签单保费', 'Signed premium')}"
                f"{t('：', ': ')}%{{y:,.1f}} {unit_name(unit)}<br>"
                f"{t('占签单保费合计', 'Share of total signed premium')}"
                f"{t('：', ': ')}%{{customdata:.1%}}<extra></extra>"
            ),
        ),
        row=1,
        col=1,
        secondary_y=False,
    )
    # An invisible point keeps the right-hand percentage axis visible even
    # though the bars themselves are plotted against the amount axis.
    fig.add_trace(
        go.Scatter(
            x=x_values,
            y=[0.0] * len(x_values),
            mode="markers",
            marker=dict(opacity=0.0, size=1),
            showlegend=False,
            hoverinfo="skip",
        ),
        row=1,
        col=1,
        secondary_y=True,
    )
    fig.update_yaxes(
        title_text=t(
            f"签单保费（{unit}）",
            f"Signed premium ({unit_name(unit)})",
        ),
        row=1,
        col=1,
        secondary_y=False,
        showgrid=True,
        gridcolor=REPORT_GRID_COLOR,
        gridwidth=1,
        range=[0.0, premium_high_pad],
        zeroline=False,
        tickfont=dict(family=REPORT_FONT, size=10, color=REPORT_AXIS_COLOR),
        title_font=dict(family=REPORT_FONT, size=10, color=REPORT_AXIS_COLOR),
    )
    if premium_total:
        premium_share_max = max(0.8, premium_high_pad / premium_total)
        fig.update_yaxes(
            title_text=t("占比（%）", "Share (%)"),
            row=1,
            col=1,
            secondary_y=True,
            side="right",
            showgrid=False,
            range=[0.0, premium_share_max],
            tickformat=".0%",
            dtick=0.2,
            zeroline=False,
            tickfont=dict(family=REPORT_FONT, size=9, color=REPORT_AXIS_COLOR),
            title_font=dict(
                family=REPORT_FONT,
                size=10,
                color=REPORT_AXIS_COLOR,
            ),
        )
    profit_metrics = (
        ("净利润", PACIFIC_BLUE, t("净利润", "Net profit")),
        ("承保利润", PINK, t("承保利润", "Underwriting profit")),
    )
    profit_low = 0.0
    profit_high = 0.0
    for metric, color, english_name in profit_metrics:
        values = (
            pd.to_numeric(categories[metric], errors="coerce").fillna(0) / divisor
        ).tolist()
        # Shares use the absolute-value total so positive and loss-making
        # categories add up to 100% while loss categories stay negative.
        denominator = float(sum(abs(value) for value in values))
        shares = [
            (float(value) / denominator if denominator else 0.0)
            for value in values
        ]
        low = min([0.0, *values])
        high = max([0.0, *values])
        profit_low = min(profit_low, low)
        profit_high = max(profit_high, high)
        fig.add_trace(
            go.Bar(
                x=x_values,
                y=values,
                name=english_name,
                marker_color=color,
                customdata=shares,
                text=[f"{share:.1%}" for share in shares],
                textposition="outside",
                textfont=dict(
                    family=REPORT_FONT,
                    size=8.5,
                    color="#0C233C",
                ),
                cliponaxis=False,
                hovertemplate=(
                    "<b>%{x}</b><br>"
                    f"{english_name}"
                    f"{t('：', ': ')}%{{y:,.1f}} {unit_name(unit)}<br>"
                    f"{t('占该指标绝对值合计', 'Share of the metric absolute-value total')}"
                    f"{t('：', ': ')}%{{customdata:.1%}}<extra></extra>"
                ),
            ),
            row=2,
            col=1,
            secondary_y=False,
        )
    profit_span = profit_high - profit_low
    profit_pad = profit_span * 0.18 if profit_span > 0 else max(abs(profit_high), 1.0) * 0.15
    fig.update_yaxes(
        title_text=t(
            f"净利润 / 承保利润（{unit}）",
            f"Net profit / Underwriting profit ({unit_name(unit)})",
        ),
        row=2,
        col=1,
        secondary_y=False,
        showgrid=True,
        gridcolor=REPORT_GRID_COLOR,
        gridwidth=1,
        range=[profit_low - profit_pad, profit_high + profit_pad],
        zeroline=True,
        zerolinecolor="#9AA6B2",
        tickfont=dict(family=REPORT_FONT, size=10, color=REPORT_AXIS_COLOR),
        title_font=dict(family=REPORT_FONT, size=10, color=REPORT_AXIS_COLOR),
    )
    fig.update_xaxes(
        title=None,
        row=1,
        col=1,
        showticklabels=False,
        showline=True,
        linecolor=REPORT_DOMAIN_COLOR,
        linewidth=1,
        ticks="",
    )
    fig.update_xaxes(
        title=None,
        row=2,
        col=1,
        showline=True,
        linecolor=REPORT_DOMAIN_COLOR,
        linewidth=1,
        ticks="",
        tickfont=dict(family=REPORT_FONT, size=10, color=REPORT_AXIS_COLOR),
    )
    fig.update_layout(
        height=640,
        paper_bgcolor="#FFFFFF",
        plot_bgcolor="#FFFFFF",
        margin=dict(t=58, b=76, l=76, r=68),
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="top",
            y=-0.13,
            x=0.5,
            xanchor="center",
            font=dict(family=REPORT_FONT, size=10, color=REPORT_TEXT_COLOR),
            bgcolor="rgba(0,0,0,0)",
            borderwidth=0,
        ),
        barmode="group",
        bargap=0.34,
        bargroupgap=0.06,
        font=dict(family=REPORT_FONT, color=REPORT_TEXT_COLOR, size=11),
        hoverlabel=dict(
            bgcolor="#FFFFFF",
            bordercolor=REPORT_DOMAIN_COLOR,
            font=dict(family=REPORT_FONT, size=11, color=REPORT_TEXT_COLOR),
        ),
    )
    fig.update_annotations(
        font=dict(family=REPORT_FONT, size=11.5, color=PRIMARY_BLUE)
    )
    return fig
def build_market_share_amount_bars(
    data: MarketShareData,
    *,
    unit_mode: str = DEFAULT_DISPLAY_UNIT,
) -> go.Figure:
    unit = unit_mode if unit_mode in DISPLAY_UNITS else DEFAULT_DISPLAY_UNIT
    divisor = DIVISOR_BY_UNIT[unit]
    categories = data.categories.copy()
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.11,
        row_heights=[0.40, 0.60],
        subplot_titles=("保费规模", "净利润 / 承保利润"),
    )

    def values(metric: str) -> list[float]:
        return (
            pd.to_numeric(categories[metric], errors="coerce").fillna(0) / divisor
        ).tolist()

    x_values = categories["公司类别"].tolist()
    premium_values = values("保费")
    profit_values = values("净利润")
    underwriting_values = values("承保利润")

    premium_high = max([0.0, *premium_values])
    premium_high = premium_high if premium_high > 0 else 1.0
    premium_tickvals = [
        0.0,
        premium_high * 0.25,
        premium_high * 0.50,
        premium_high * 0.75,
        premium_high,
    ]
    premium_ticktext = [f"{value:,.0f}" for value in premium_tickvals]

    fig.add_trace(
        go.Bar(
            x=x_values,
            y=premium_values,
            name="保费",
            marker_color=PRIMARY_BLUE,
            hovertemplate="<b>%{x}</b><br>保费：%{y:,.1f} " + unit + "<extra></extra>",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Bar(
            x=x_values,
            y=profit_values,
            name="净利润",
            marker_color=PACIFIC_BLUE,
            hovertemplate="<b>%{x}</b><br>净利润：%{y:,.1f} " + unit + "<extra></extra>",
        ),
        row=2,
        col=1,
    )
    fig.add_trace(
        go.Bar(
            x=x_values,
            y=underwriting_values,
            name="承保利润",
            marker_color=PINK,
            hovertemplate="<b>%{x}</b><br>承保利润：%{y:,.1f} " + unit + "<extra></extra>",
        ),
        row=2,
        col=1,
    )
    fig.update_layout(
        barmode="group",
        bargap=0.40,
        bargroupgap=0.16,
        height=520,
        paper_bgcolor="#FFFFFF",
        plot_bgcolor="#FFFFFF",
        margin=dict(t=48, b=56, l=20, r=20),
        legend=dict(
            orientation="h",
            y=1.12,
            x=0,
            xanchor="left",
            title=None,
            font=dict(family=REPORT_FONT, size=10, color=REPORT_TEXT_COLOR),
        ),
        font=dict(family=REPORT_FONT, color=REPORT_TEXT_COLOR, size=11),
        hoverlabel=dict(
            bgcolor="#FFFFFF",
            bordercolor=REPORT_DOMAIN_COLOR,
            font=dict(family=REPORT_FONT, size=11, color=REPORT_TEXT_COLOR),
        ),
    )
    fig.update_xaxes(
        title=None,
        row=1,
        col=1,
        showticklabels=False,
        showline=True,
        linecolor=REPORT_DOMAIN_COLOR,
        linewidth=1,
        ticks="",
        tickfont=dict(family=REPORT_FONT, size=10, color=REPORT_AXIS_COLOR),
    )
    fig.update_xaxes(
        title=None,
        row=2,
        col=1,
        showline=True,
        linecolor=REPORT_DOMAIN_COLOR,
        linewidth=1,
        ticks="",
        tickfont=dict(family=REPORT_FONT, size=10, color=REPORT_AXIS_COLOR),
    )
    fig.update_yaxes(
        title_text=f"保费（{unit}）",
        row=1,
        col=1,
        gridcolor=REPORT_GRID_COLOR,
        gridwidth=1,
        range=[0.0, premium_high * 1.08],
        rangemode="normal",
        tickmode="array",
        tickvals=premium_tickvals,
        ticktext=premium_ticktext,
        zeroline=True,
        zerolinecolor=REPORT_DOMAIN_COLOR,
        tickfont=dict(family=REPORT_FONT, size=10, color=REPORT_AXIS_COLOR),
        title_font=dict(family=REPORT_FONT, size=10, color=REPORT_AXIS_COLOR),
    )
    profit_min = min([0.0, *profit_values, *underwriting_values])
    profit_max = max([0.0, *profit_values, *underwriting_values])
    profit_span = profit_max - profit_min
    profit_pad = profit_span * 0.08 if profit_span > 0 else 1.0
    fig.update_yaxes(
        title_text=f"利润（{unit}）",
        row=2,
        col=1,
        showgrid=False,
        range=[profit_min - (profit_pad if profit_min < 0 else 0.0), profit_max + profit_pad],
        rangemode="normal",
        zeroline=True,
        zerolinecolor="#9AA6B2",
        tickfont=dict(family=REPORT_FONT, size=10, color=REPORT_AXIS_COLOR),
        title_font=dict(family=REPORT_FONT, size=10, color=REPORT_AXIS_COLOR),
    )
    fig.update_annotations(
        font=dict(family=REPORT_FONT, size=12, color=PRIMARY_BLUE)
    )
    return fig
