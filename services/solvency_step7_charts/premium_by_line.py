"""分险种保费收入：数据读取、同比增速与年度汇总表。

数据优先从根目录《偿付能力行业集成数据_公司名称与分类规范化.xlsx》的
“分险种保费收入”工作表读取；若该工作表不存在，则回退到根目录
《分险种保费收入.xlsx》，最后回退到本模块内置的同一份数据。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import altair as alt
import pandas as pd

from ..report_i18n import t
from .primitives import apply_industry_chart_theme

ROOT = Path(__file__).resolve().parents[2]
PREMIUM_BY_LINE_PATH = ROOT / "分险种保费收入.xlsx"
INTEGRATED_WORKBOOK_PATH = ROOT / "偿付能力行业集成数据_公司名称与分类规范化.xlsx"
INTEGRATED_SHEET_NAME = "分险种保费收入"
DISPLAY_UNIT = "亿元"
MOTOR_LINE = "机动车辆保险"
AMOUNT_COLUMN = "原保险保费收入"
GROWTH_COLUMN = "同比增速"
BASE_COLUMN = "基期年度"

SOURCE_INTEGRATED = "integrated"
SOURCE_STANDALONE = "standalone"
SOURCE_EMBEDDED = "embedded"

LINE_NAMES_EN: dict[str, str] = {
    "机动车辆保险": "Motor vehicle insurance",
    "责任险": "Liability insurance",
    "农业保险": "Agricultural insurance",
    "健康险": "Health insurance",
    "意外险": "Accident insurance",
    "企业财产保险": "Commercial property insurance",
    "家庭财产保险": "Home property insurance",
    "工程保险": "Engineering insurance",
    "保证保险": "Guarantee insurance",
}

# Stable colours keep the same line of business recognisable in the legend.
LINE_COLORS: dict[str, str] = {
    "机动车辆保险": "#00338D",
    "健康险": "#FD349C",
    "农业保险": "#00C0AE",
    "责任险": "#7213EA",
    "意外险": "#F1C44D",
    "企业财产保险": "#1E49E2",
    "保证保险": "#ED2124",
    "家庭财产保险": "#B497FF",
    "工程保险": "#63EBB2",
}
FALLBACK_LINE_COLOR = "#94A3B8"
MISSING_MARK_COLOR = "#94A3B8"
DISCLOSED = "已披露"
NOT_DISCLOSED = "未披露"

# 与根目录《分险种保费收入.xlsx》完全一致的内置副本（单位：亿元）。
# 仅在源文件缺失时使用。
EMBEDDED_PREMIUM_BY_LINE: tuple[tuple[int, str, float], ...] = (
    (2025, "机动车辆保险", 9409.0),
    (2025, "责任险", 1428.0),
    (2025, "农业保险", 1555.0),
    (2025, "健康险", 2274.0),
    (2025, "意外险", 593.0),
    (2024, "机动车辆保险", 9137.0),
    (2024, "责任险", 1372.0),
    (2024, "农业保险", 1484.0),
    (2024, "健康险", 2043.0),
    (2024, "意外险", 534.0),
    (2023, "机动车辆保险", 8673.0),
    (2023, "责任险", 1268.0),
    (2023, "农业保险", 1430.0),
    (2023, "健康险", 1752.0),
    (2023, "意外险", 509.0),
    (2022, "企业财产保险", 553.0),
    (2022, "家庭财产保险", 164.0),
    (2022, "机动车辆保险", 8210.0),
    (2022, "工程保险", 145.0),
    (2022, "责任险", 1148.0),
    (2022, "保证保险", 552.0),
    (2022, "农业保险", 1219.0),
    (2022, "健康险", 1580.0),
    (2022, "意外险", 574.0),
    (2021, "企业财产保险", 520.0),
    (2021, "家庭财产保险", 98.0),
    (2021, "机动车辆保险", 7773.0),
    (2021, "工程保险", 144.0),
    (2021, "责任险", 1018.0),
    (2021, "保证保险", 521.0),
    (2021, "农业保险", 976.0),
    (2021, "健康险", 1378.0),
    (2021, "意外险", 627.0),
    (2020, "企业财产保险", 490.0),
    (2020, "家庭财产保险", 91.0),
    (2020, "机动车辆保险", 8245.0),
    (2020, "工程保险", 138.0),
    (2020, "责任险", 901.0),
    (2020, "保证保险", 689.0),
    (2020, "农业保险", 815.0),
    (2020, "健康险", 1114.0),
    (2020, "意外险", 541.0),
    (2019, "企业财产保险", 464.0),
    (2019, "家庭财产保险", 91.0),
    (2019, "机动车辆保险", 8188.0),
    (2019, "工程保险", 118.0),
    (2019, "责任险", 753.0),
    (2019, "保证保险", 844.0),
    (2019, "农业保险", 672.0),
    (2019, "健康险", 840.0),
    (2019, "意外险", 527.0),
)

_BASE_COLUMNS = ["年度", "险种", AMOUNT_COLUMN]
_SOURCE_TEXT = {
    SOURCE_INTEGRATED: (
        "根目录《偿付能力行业集成数据_公司名称与分类规范化.xlsx》"
        "的“分险种保费收入”工作表",
        "the premium-by-line sheet in the integrated solvency workbook",
    ),
    SOURCE_STANDALONE: (
        "根目录《分险种保费收入.xlsx》",
        "the premium-by-line workbook in the project root",
    ),
    SOURCE_EMBEDDED: ("内置数据", "built-in data"),
}


@dataclass(frozen=True)
class PremiumByLineOverview:
    """Latest-year aggregates used by the module insight card."""

    year: int
    total: float
    previous_total: float | None
    growth: float | None
    top_line: str
    top_share: float
    fastest_line: str
    fastest_growth: float
    slowest_line: str
    slowest_growth: float
    active_lines: tuple[str, ...]
    legacy_lines: tuple[str, ...]


def line_display_name(name: object) -> str:
    """Return the Chinese or English display name of one line of business."""
    text = str(name or "").strip()
    return t(text, LINE_NAMES_EN.get(text, text))


def premium_by_line_source_key() -> str:
    """Return which workbook supplied the data (integrated/standalone/embedded)."""
    return _load_with_source()[1]


def premium_by_line_source_text() -> str:
    """Return the localised source description used in captions/footnotes."""
    zh, en = _SOURCE_TEXT.get(premium_by_line_source_key(), _SOURCE_TEXT[SOURCE_EMBEDDED])
    return t(zh, en)


def _amount_factor(text: str) -> float:
    """Convert the workbook's declared amount unit into 亿元."""
    if "十亿元" in text:
        return 10.0
    if "百万元" in text:
        return 0.01
    if "亿元" in text:
        return 1.0
    if "万元" in text:
        return 0.0001
    if "元" in text:
        return 1e-8
    return 1.0


def _parse_tabular(raw: pd.DataFrame) -> pd.DataFrame:
    """Parse a 年份 / 险种 / 保费收入 table without relying on fixed columns."""
    empty = pd.DataFrame(columns=_BASE_COLUMNS)
    if not isinstance(raw, pd.DataFrame) or raw.empty:
        return empty
    header_index: int | None = None
    for index in range(min(12, len(raw))):
        values = [
            str(value).strip()
            for value in raw.iloc[index].tolist()
            if not pd.isna(value)
        ]
        has_year = any(("年份" in value or "年度" in value) for value in values)
        has_line = any("险种" in value for value in values)
        if has_year and has_line:
            header_index = index
            break
    if header_index is None:
        return empty

    header = [str(value).strip() for value in raw.iloc[header_index].tolist()]
    year_column = next(
        (index for index, name in enumerate(header) if "年份" in name or "年度" in name),
        None,
    )
    line_column = next(
        (index for index, name in enumerate(header) if "险种" in name),
        None,
    )
    amount_column = next(
        (
            index
            for index, name in enumerate(header)
            if ("保费" in name or "金额" in name) and "同比" not in name
        ),
        None,
    )
    unit_column = next(
        (index for index, name in enumerate(header) if name.strip() == "单位"),
        None,
    )
    if year_column is None or line_column is None:
        return empty
    if amount_column is None:
        amount_column = 2
    default_factor = _amount_factor(" ".join(header))

    rows: list[dict[str, object]] = []
    for _, row in raw.iloc[header_index + 1:].iterrows():
        year = pd.to_numeric(row.iloc[year_column], errors="coerce")
        line = row.iloc[line_column]
        amount = pd.to_numeric(row.iloc[amount_column], errors="coerce")
        line_text = "" if pd.isna(line) else str(line).strip()
        if pd.isna(year) or not line_text or pd.isna(amount):
            continue
        factor = default_factor
        if unit_column is not None:
            unit_text = row.iloc[unit_column]
            if not pd.isna(unit_text) and str(unit_text).strip():
                factor = _amount_factor(str(unit_text))
        rows.append(
            {
                "年度": int(year),
                "险种": line_text,
                AMOUNT_COLUMN: float(amount) * factor,
            }
        )
    if not rows:
        return empty
    return (
        pd.DataFrame(rows, columns=_BASE_COLUMNS)
        .drop_duplicates(["年度", "险种"], keep="last")
        .reset_index(drop=True)
    )


def _read_integrated_sheet() -> pd.DataFrame:
    """Read the by-line sheet from the large integrated workbook quickly."""
    if not INTEGRATED_WORKBOOK_PATH.exists():
        return pd.DataFrame(columns=_BASE_COLUMNS)
    try:
        # read_only avoids parsing the 60k+ row sheets just to read this table.
        from openpyxl import load_workbook

        workbook = load_workbook(
            INTEGRATED_WORKBOOK_PATH,
            read_only=True,
            data_only=True,
        )
        try:
            if INTEGRATED_SHEET_NAME not in workbook.sheetnames:
                return pd.DataFrame(columns=_BASE_COLUMNS)
            values = list(workbook[INTEGRATED_SHEET_NAME].iter_rows(values_only=True))
        finally:
            workbook.close()
        return _parse_tabular(pd.DataFrame(values))
    except Exception:
        return pd.DataFrame(columns=_BASE_COLUMNS)


def _read_standalone_workbook() -> pd.DataFrame:
    if not PREMIUM_BY_LINE_PATH.exists():
        return pd.DataFrame(columns=_BASE_COLUMNS)
    try:
        return _parse_tabular(pd.read_excel(PREMIUM_BY_LINE_PATH, header=None))
    except Exception:
        return pd.DataFrame(columns=_BASE_COLUMNS)


def _attach_growth(frame: pd.DataFrame) -> pd.DataFrame:
    """Sort each line by year and calculate YoY growth on available years."""
    result = frame.copy()
    result[AMOUNT_COLUMN] = pd.to_numeric(result[AMOUNT_COLUMN], errors="coerce")
    result["年度"] = pd.to_numeric(result["年度"], errors="coerce")
    result = result.dropna(subset=["年度", AMOUNT_COLUMN, "险种"]).copy()
    if result.empty:
        return pd.DataFrame(columns=[*_BASE_COLUMNS, GROWTH_COLUMN, BASE_COLUMN])
    result["年度"] = result["年度"].astype(int)
    result = result.sort_values(["险种", "年度"], kind="mergesort").reset_index(drop=True)

    growth: list[float] = [float("nan")] * len(result)
    bases: list[str] = [""] * len(result)
    for indices in result.groupby("险种", sort=False).groups.values():
        previous_year: int | None = None
        previous_value: float | None = None
        for index in indices:
            value = float(result.at[index, AMOUNT_COLUMN])
            if previous_value is not None and previous_value != 0:
                growth[index] = (value / previous_value - 1.0) * 100.0
                bases[index] = str(previous_year)
            previous_year = int(result.at[index, "年度"])
            previous_value = value
    result[GROWTH_COLUMN] = growth
    result[BASE_COLUMN] = bases
    return result


@lru_cache(maxsize=1)
def _load_with_source() -> tuple[pd.DataFrame, str]:
    """Load the frame once, preferring the integrated workbook's new sheet."""
    for source, loader in (
        (SOURCE_INTEGRATED, _read_integrated_sheet),
        (SOURCE_STANDALONE, _read_standalone_workbook),
    ):
        frame = loader()
        if not frame.empty:
            return _attach_growth(frame), source
    embedded = pd.DataFrame(EMBEDDED_PREMIUM_BY_LINE, columns=_BASE_COLUMNS)
    return _attach_growth(embedded), SOURCE_EMBEDDED


def load_premium_by_line_data() -> pd.DataFrame:
    """Load and normalise the by-line premium data (cached per process)."""
    return _load_with_source()[0]


def ordered_premium_lines(frame: pd.DataFrame) -> list[str]:
    """Order lines by latest reported amount, active lines before retired ones."""
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    latest = (
        frame.sort_values("年度")
        .groupby("险种", sort=False, as_index=False)
        .tail(1)
    )
    max_year = int(frame["年度"].max())
    active = latest[latest["年度"].eq(max_year)].sort_values(
        AMOUNT_COLUMN, ascending=False
    )
    legacy = latest[latest["年度"].lt(max_year)].sort_values(
        AMOUNT_COLUMN, ascending=False
    )
    return [*active["险种"].astype(str), *legacy["险种"].astype(str)]


def premium_line_frame(frame: pd.DataFrame, line_name: str) -> pd.DataFrame:
    """Return one line of business ordered by year."""
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return pd.DataFrame(columns=[*_BASE_COLUMNS, GROWTH_COLUMN, BASE_COLUMN])
    selected = frame[frame["险种"].astype(str).eq(str(line_name))].copy()
    return selected.sort_values("年度").reset_index(drop=True)


def premium_by_line_chart_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Return long rows for the grouped bar chart, including missing slots.

    Every line is expanded to every year so a missing year keeps its place in
    the grouped bar chart and can be marked with an explicit “×”.
    """
    columns = ["年度", "险种", AMOUNT_COLUMN, GROWTH_COLUMN, BASE_COLUMN, "披露状态"]
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return pd.DataFrame(columns=columns)
    years = sorted(int(year) for year in frame["年度"].dropna().unique())
    rows: list[dict[str, object]] = []
    for line_name in ordered_premium_lines(frame):
        line_frame = premium_line_frame(frame, line_name)
        by_year = {
            int(row.年度): row
            for row in line_frame.itertuples(index=False)
        }
        for year in years:
            record = by_year.get(year)
            if record is None:
                rows.append(
                    {
                        "年度": str(year),
                        "险种": line_name,
                        AMOUNT_COLUMN: float("nan"),
                        GROWTH_COLUMN: float("nan"),
                        BASE_COLUMN: "",
                        "披露状态": NOT_DISCLOSED,
                    }
                )
                continue
            growth = getattr(record, GROWTH_COLUMN)
            rows.append(
                {
                    "年度": str(year),
                    "险种": line_name,
                    AMOUNT_COLUMN: float(getattr(record, AMOUNT_COLUMN)),
                    GROWTH_COLUMN: float(growth) if pd.notna(growth) else float("nan"),
                    BASE_COLUMN: str(getattr(record, BASE_COLUMN) or ""),
                    "披露状态": DISCLOSED,
                }
            )
    return pd.DataFrame(rows, columns=columns)


def _line_color_scale(lines: list[str]) -> alt.Scale:
    return alt.Scale(
        domain=lines,
        range=[LINE_COLORS.get(line, FALLBACK_LINE_COLOR) for line in lines],
    )


def build_premium_by_line_chart(
    frame: pd.DataFrame,
    *,
    width: int | str = "container",
) -> alt.Chart:
    """Grouped bars with dual amount axes plus one YoY growth line per line.

    Motor vehicle insurance uses the right amount axis because its premium is
    roughly four times the next-largest line; every other line uses the left
    amount axis so their differences stay readable. Growth lines use a separate
    right-hand percentage axis. Missing line-year combinations keep their bar
    slot and are marked with “×”.
    """
    chart_frame = premium_by_line_chart_frame(frame)
    if chart_frame.empty:
        raise ValueError("没有可绘制的分险种保费收入数据。")
    lines = ordered_premium_lines(frame)
    years = sorted(chart_frame["年度"].unique().tolist())
    disclosed = chart_frame[chart_frame["披露状态"].eq(DISCLOSED)].copy()
    missing = chart_frame[chart_frame["披露状态"].eq(NOT_DISCLOSED)].copy()

    motor = disclosed[disclosed["险种"].astype(str).eq(MOTOR_LINE)].copy()
    others = disclosed[~disclosed["险种"].astype(str).eq(MOTOR_LINE)].copy()
    growth = disclosed.dropna(subset=[GROWTH_COLUMN]).copy()

    other_max = float(pd.to_numeric(others[AMOUNT_COLUMN], errors="coerce").max())
    motor_max = float(pd.to_numeric(motor[AMOUNT_COLUMN], errors="coerce").max())
    other_scale = alt.Scale(
        domain=[0, other_max * 1.18 if other_max > 0 else 1],
        zero=True,
        nice=False,
    )
    motor_scale = alt.Scale(
        domain=[0, motor_max * 1.18 if motor_max > 0 else 1],
        zero=True,
        nice=False,
    )
    if growth.empty:
        growth_scale = alt.Scale(domain=[0, 1], zero=False)
    else:
        growth_min = min(0.0, float(growth[GROWTH_COLUMN].min()))
        growth_max = max(0.0, float(growth[GROWTH_COLUMN].max()))
        growth_pad = max(6.0, (growth_max - growth_min) * 0.16)
        growth_scale = alt.Scale(
            domain=[growth_min - growth_pad, growth_max + growth_pad],
            zero=False,
            nice=False,
        )

    color_scale = _line_color_scale(lines)
    x_axis = alt.Axis(
        values=years,
        labels=True,
        labelAngle=0,
        labelFontSize=11,
        labelPadding=9,
        domain=True,
        ticks=True,
        tickSize=4,
        grid=False,
    )

    def x_encoding() -> alt.X:
        return alt.X(
            "年度:O",
            sort=years,
            title=t("年份", "Year"),
            scale=alt.Scale(paddingInner=0.18, paddingOuter=0.28),
            axis=x_axis,
        )

    def bar_tooltips(amount_title: str) -> list[alt.Tooltip]:
        return [
            alt.Tooltip("年度:N", title=t("年度", "Year")),
            alt.Tooltip("险种:N", title=t("险种", "Line of business")),
            alt.Tooltip(
                f"{AMOUNT_COLUMN}:Q",
                title=amount_title,
                format=",.1f",
            ),
            alt.Tooltip(
                f"{GROWTH_COLUMN}:Q",
                title=t("同比增速", "YoY growth"),
                format="+.1f",
            ),
            alt.Tooltip(
                f"{BASE_COLUMN}:N",
                title=t("基期年度", "Base year"),
            ),
            alt.Tooltip("披露状态:N", title=t("披露状态", "Status")),
        ]

    amount_title = t(
        "原保险保费收入（亿元）",
        "Original premium income (RMB 100 mn)",
    )
    motor_amount_title = t(
        "车险保费（亿元）",
        "Motor premium (RMB 100 mn)",
    )
    growth_title = t("同比增速（%）", "YoY growth (%)")

    bars_other = (
        alt.Chart(others)
        .mark_bar(
            cornerRadiusTopLeft=2,
            cornerRadiusTopRight=2,
        )
        .encode(
            x=x_encoding(),
            xOffset=alt.XOffset("险种:N", sort=lines),
            y=alt.Y(
                f"{AMOUNT_COLUMN}:Q",
                title=amount_title,
                scale=other_scale,
                axis=alt.Axis(
                    orient="left",
                    format=",.0f",
                    tickCount=5,
                    domain=True,
                    ticks=True,
                    tickSize=4,
                    grid=True,
                ),
            ),
            color=alt.Color("险种:N", scale=color_scale, legend=None),
            tooltip=bar_tooltips(amount_title),
        )
    )
    bars_motor = (
        alt.Chart(motor)
        .mark_bar(
            cornerRadiusTopLeft=2,
            cornerRadiusTopRight=2,
        )
        .encode(
            x=x_encoding(),
            xOffset=alt.XOffset("险种:N", sort=lines),
            y=alt.Y(
                f"{AMOUNT_COLUMN}:Q",
                title=motor_amount_title,
                scale=motor_scale,
                axis=alt.Axis(
                    orient="right",
                    format=",.0f",
                    tickCount=5,
                    titlePadding=6,
                    domain=True,
                    ticks=True,
                    tickSize=4,
                    grid=False,
                ),
            ),
            color=alt.Color("险种:N", scale=color_scale, legend=None),
            tooltip=bar_tooltips(motor_amount_title),
        )
    )

    layers: list[alt.Chart] = [bars_other, bars_motor]
    if not growth.empty:
        layers.append(
            alt.Chart(pd.DataFrame({GROWTH_COLUMN: [0.0]}))
            .mark_rule(color="#94A3B8", strokeDash=[4, 4], strokeWidth=1)
            .encode(
                y=alt.Y(
                    f"{GROWTH_COLUMN}:Q",
                    scale=growth_scale,
                    axis=None,
                )
            )
        )
        layers.append(
            alt.Chart(growth)
            .mark_line(
                strokeWidth=1.9,
                opacity=0.9,
                point=alt.OverlayMarkDef(
                    size=32,
                    filled=True,
                    stroke="#FFFFFF",
                    strokeWidth=0.8,
                ),
            )
            .encode(
                x=x_encoding(),
                y=alt.Y(
                    f"{GROWTH_COLUMN}:Q",
                    title=growth_title,
                    scale=growth_scale,
                    axis=alt.Axis(
                        orient="right",
                        offset=58,
                        format=".0f",
                        labelExpr="datum.label + '%'",
                        tickCount=5,
                        titlePadding=6,
                        domain=True,
                        ticks=True,
                        tickSize=4,
                        grid=False,
                    ),
                ),
                color=alt.Color("险种:N", scale=color_scale, legend=None),
                tooltip=[
                    alt.Tooltip("年度:N", title=t("年度", "Year")),
                    alt.Tooltip("险种:N", title=t("险种", "Line of business")),
                    alt.Tooltip(
                        f"{GROWTH_COLUMN}:Q",
                        title=growth_title,
                        format="+.1f",
                    ),
                    alt.Tooltip(
                        f"{BASE_COLUMN}:N",
                        title=t("基期年度", "Base year"),
                    ),
                ],
            )
        )

    if not missing.empty:
        missing = missing.assign(标记位置=0.0)
        layers.append(
            alt.Chart(missing)
            .mark_text(
                text="×",
                dy=-5,
                fontSize=12,
                fontWeight="bold",
                color=MISSING_MARK_COLOR,
            )
            .encode(
                x=x_encoding(),
                xOffset=alt.XOffset("险种:N", sort=lines),
                y=alt.Y("标记位置:Q", scale=other_scale, axis=None),
                tooltip=[
                    alt.Tooltip("年度:N", title=t("年度", "Year")),
                    alt.Tooltip("险种:N", title=t("险种", "Line of business")),
                    alt.Tooltip("披露状态:N", title=t("披露状态", "Status")),
                ],
            )
        )

    chart = alt.layer(*layers).resolve_scale(y="independent").properties(width=width)
    chart = apply_industry_chart_theme(chart, height=480)
    return chart.properties(
        padding={"top": 12, "right": 96, "bottom": 30, "left": 6}
    )


def prepare_premium_by_line_overview(
    frame: pd.DataFrame,
) -> PremiumByLineOverview | None:
    """Aggregate the latest-year active lines for the module insight card."""
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return None
    latest_rows = (
        frame.sort_values("年度")
        .groupby("险种", sort=False, as_index=False)
        .tail(1)
    )
    max_year = int(frame["年度"].max())
    active = latest_rows[latest_rows["年度"].eq(max_year)].copy()
    if active.empty:
        return None
    active = active.sort_values(AMOUNT_COLUMN, ascending=False)
    total = float(pd.to_numeric(active[AMOUNT_COLUMN], errors="coerce").sum())
    active_names = tuple(active["险种"].astype(str))
    top_row = active.iloc[0]
    top_line = str(top_row["险种"])
    top_value = float(top_row[AMOUNT_COLUMN])
    top_share = (top_value / total * 100.0) if total > 0 else 0.0

    previous_year = max_year - 1
    previous = frame[
        frame["年度"].eq(previous_year) & frame["险种"].astype(str).isin(active_names)
    ]
    previous_names = set(previous["险种"].astype(str))
    if previous_names == set(active_names):
        previous_total = float(
            pd.to_numeric(previous[AMOUNT_COLUMN], errors="coerce").sum()
        )
    else:
        previous_total = None
    growth = (
        (total / previous_total - 1.0) * 100.0
        if previous_total not in (None, 0) and total > 0
        else None
    )

    growth_rows = active.dropna(subset=[GROWTH_COLUMN]).copy()
    if growth_rows.empty:
        fastest_line = top_line
        fastest_growth = 0.0
        slowest_line = top_line
        slowest_growth = 0.0
    else:
        fastest = growth_rows.loc[growth_rows[GROWTH_COLUMN].idxmax()]
        slowest = growth_rows.loc[growth_rows[GROWTH_COLUMN].idxmin()]
        fastest_line = str(fastest["险种"])
        fastest_growth = float(fastest[GROWTH_COLUMN])
        slowest_line = str(slowest["险种"])
        slowest_growth = float(slowest[GROWTH_COLUMN])

    legacy_lines = tuple(
        latest_rows.loc[latest_rows["年度"].lt(max_year), "险种"].astype(str)
    )
    return PremiumByLineOverview(
        year=max_year,
        total=total,
        previous_total=previous_total,
        growth=growth,
        top_line=top_line,
        top_share=top_share,
        fastest_line=fastest_line,
        fastest_growth=fastest_growth,
        slowest_line=slowest_line,
        slowest_growth=slowest_growth,
        active_lines=active_names,
        legacy_lines=legacy_lines,
    )
