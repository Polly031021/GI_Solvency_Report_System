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
    prepare_market_share_data,
)


COMPANIES = (
    "人保财险",
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
    assert len(data.categories) == len(COMPANIES)
    assert set(data.company_detail["公司类别"]) == {
        "前十大",
        "大型公司",
        "中型公司",
        "小型公司",
        "外资系",
        "自保公司",
        "农业保险",
    }
    for column in ("保费占比", "净利润占比", "承保利润占比"):
        assert abs(float(data.categories[column].sum()) - 1.0) < 1e-6


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
    assert not bars.layout.shapes


if __name__ == "__main__":
    test_market_share_uses_latest_complete_year_and_categories()
    test_market_share_chart_structure()
    print("PASS 行业市场份额：最新完整年度、公司类别与饼图/柱状图结构")
