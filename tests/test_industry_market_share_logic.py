# -*- coding: utf-8 -*-
"""行业市场份额：最新完整年度、公司类别聚合与图表结构回归。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.solvency_step7_charts import (  # noqa: E402
    build_market_share_amount_bars,
    build_market_share_pies,
    build_market_share_premium_pie,
    build_market_share_profit_bars,
    prepare_market_share_data,
)


COMPANIES = (
    "人保财险",
    "平安产险",
    "太保财险",
    "国寿财产",
    "紫金财产",
    "富邦财险",
    "比亚迪保险",
    "京东安联",
    "铁路自保",
    "安信农业",
)


def _market_frame() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for year in (2024, 2025):
        for index, company in enumerate(COMPANIES):
            premium = 100_000.0 * (index + 2) * (year - 2023)
            profit = 2_000.0 * (index + 1) * (1 if index % 3 else -1)
            combined = 92.0 + index * 1.8
            for code, value, unit in (
                ("SIGNED_PREMIUM", premium, "万元"),
                ("NET_PROFIT", profit, "万元"),
                ("COMBINED_RATIO", combined, "%"),
            ):
                rows.append(
                    {
                        "公司": company,
                        "报告期": f"{year}Q4",
                        "指标编码": code,
                        "期间口径": "本年累计数",
                        "数值": value,
                        "单位": unit,
                    }
                )
    # Partial 2026 must not displace the latest complete year.
    rows.append(
        {
            "公司": "人保财险",
            "报告期": "2026Q4",
            "指标编码": "SIGNED_PREMIUM",
            "期间口径": "本年累计数",
            "数值": 999_999.0,
            "单位": "万元",
        }
    )
    return pd.DataFrame(rows)


def test_market_share_uses_latest_complete_year_and_categories() -> None:
    data = prepare_market_share_data(_market_frame())
    assert data is not None
    assert data.year == "2025"
    assert data.top_grouping == "split"
    assert len(data.categories) < len(COMPANIES)
    assert set(data.company_detail["公司类别"]) == {
        "前三大",
        "前4-10",
        "大型公司",
        "中型公司",
        "小型公司",
        "外资系",
        "自保公司",
        "农业保险",
    }
    top3 = set(
        data.company_detail.loc[
            data.company_detail["公司类别"].eq("前三大"), "公司"
        ]
    )
    assert top3 == {"人保财险", "平安产险", "太保财险"}
    top4 = set(
        data.company_detail.loc[
            data.company_detail["公司类别"].eq("前4-10"), "公司"
        ]
    )
    assert top4 == {"国寿财产"}
    for column in ("保费占比", "净利润占比", "承保利润占比"):
        assert abs(float(data.categories[column].sum()) - 1.0) < 1e-6


def test_market_share_can_merge_top10_again() -> None:
    data = prepare_market_share_data(_market_frame(), top_grouping="merged")
    assert data is not None
    assert data.top_grouping == "merged"
    categories = set(data.company_detail["公司类别"])
    assert "前十大" in categories
    assert "前三大" not in categories
    assert "前4-10" not in categories
    top10 = set(
        data.company_detail.loc[
            data.company_detail["公司类别"].eq("前十大"), "公司"
        ]
    )
    assert top10 == {"人保财险", "平安产险", "太保财险", "国寿财产"}


def test_underwriting_pie_includes_losses_as_absolute_values() -> None:
    data = prepare_market_share_data(_market_frame())
    assert data is not None
    underwriting = pd.to_numeric(data.categories["承保利润"], errors="coerce").fillna(0)
    assert underwriting.lt(0).any()
    pies = build_market_share_pies(data)
    trace = pies.data[2]
    expected = (underwriting.abs()[underwriting.abs() > 0] / 10_000.0).tolist()
    assert len(trace.values) == len(expected)
    assert abs(float(sum(trace.values)) - float(sum(expected))) < 1e-6
    assert all(float(value) > 0 for value in trace.values)
    assert "#C2410C" in list(trace.marker.colors)
    assert "/" in list(trace.marker.pattern.shape)
    assert any(str(value).startswith("−") for value in trace.customdata)
    assert all(
        not str(value) or str(value).endswith("%") for value in trace.text
    )
    assert any(str(value).endswith("%") for value in trace.text)
    assert abs(float(data.categories["承保利润占比"].sum()) - 1.0) < 1e-6


def test_premium_pie_and_profit_share_bars() -> None:
    data = prepare_market_share_data(_market_frame())
    assert data is not None
    pie = build_market_share_premium_pie(data)
    assert len(pie.data) == 1
    assert pie.layout.height == 440
    assert abs(float(sum(pie.data[0].values)) - float(data.categories["保费"].sum()) / 10_000.0) < 1e-6
    assert all(str(value).endswith("%") or not str(value) for value in pie.data[0].text)
    hover_template = str(pie.data[0].hovertemplate)
    assert "%{value:,.1f}" in hover_template
    assert "%{percent:.1%}" in hover_template
    assert "customdata" not in hover_template

    bars = build_market_share_profit_bars(data)
    # Premium amount bar + invisible right-axis point + two grouped profit bars.
    assert len(bars.data) == 4
    assert bars.data[0].marker.color == "#00338D"
    assert bars.data[2].marker.color == "#00B8F5"
    assert bars.data[3].marker.color == "#FD349C"
    assert bars.layout.yaxis.title.text == "签单保费（亿元）"
    assert bars.layout.yaxis3.title.text == "净利润 / 承保利润（亿元）"
    assert bars.layout.yaxis2.tickformat == ".0%"
    assert bars.layout.barmode == "group"
    assert bars.layout.showlegend is True
    assert all(str(value).endswith("%") for value in bars.data[0].text)
    assert all(str(value).endswith("%") for value in bars.data[2].text)
    assert all(str(value).endswith("%") for value in bars.data[3].text)
    assert any(str(value).startswith("-") for value in bars.data[3].text)
    assert abs(float(sum(abs(value) for value in bars.data[0].customdata)) - 1.0) < 1e-6
    assert abs(float(sum(abs(value) for value in bars.data[2].customdata)) - 1.0) < 1e-6
    assert abs(float(sum(abs(value) for value in bars.data[3].customdata)) - 1.0) < 1e-6


def test_market_share_chart_structure() -> None:
    data = prepare_market_share_data(_market_frame())
    assert data is not None
    pies = build_market_share_pies(data)
    bars = build_market_share_amount_bars(data)
    assert len(pies.data) == 3
    assert len(bars.data) == 3
    assert pies.layout.font.family == "Microsoft YaHei"
    assert bars.layout.font.family == "Microsoft YaHei"
    assert bars.data[0].marker.color == "#00338D"
    assert bars.data[1].marker.color == "#00B8F5"
    assert bars.data[2].marker.color == "#FD349C"
    assert bars.layout.yaxis.range[0] == 0.0
    assert bars.layout.yaxis.title.text == "保费（亿元）"
    assert bars.layout.yaxis2.title.text == "利润（亿元）"
    assert bars.layout.legend.x == 0
    assert bars.layout.legend.xanchor == "left"
    assert not bars.layout.shapes


if __name__ == "__main__":
    test_market_share_uses_latest_complete_year_and_categories()
    test_market_share_can_merge_top10_again()
    test_underwriting_pie_includes_losses_as_absolute_values()
    test_premium_pie_and_profit_share_bars()
    test_market_share_chart_structure()
    print("PASS 行业市场份额：最新完整年度、前三大/前4-10拆分、合并口径与图表结构")
