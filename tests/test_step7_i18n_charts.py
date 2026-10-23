# -*- coding: utf-8 -*-
"""STEP7 图表英文展示的单元回归测试。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import altair as alt
import pandas as pd
import plotly.graph_objects as go

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.report_i18n import localize_chart, localize_plotly, translate_text  # noqa: E402
from services.solvency_step7_charts import (  # noqa: E402
    MarketShareData,
    build_market_share_pies,
)
from services.solvency_step7_charts.industry_market_share import (  # noqa: E402
    build_market_share_amount_bars,
)


def test_altair_domain_and_values_are_translated() -> None:
    frame = pd.DataFrame(
        {
            "年度": ["2024", "2024"],
            "类别": ["车险", "非车险"],
            "金额": [1.0, 2.0],
        }
    )
    chart = (
        alt.Chart(frame)
        .mark_bar()
        .encode(
            x=alt.X("年度:N", title="年度"),
            y=alt.Y("金额:Q", title="金额（亿元）"),
            color=alt.Color(
                "类别:N",
                title="类别",
                scale=alt.Scale(domain=["车险", "非车险"]),
            ),
            tooltip=["年度:N", "类别:N", "金额:Q"],
        )
        .properties(title="保费结构")
    )
    spec = localize_chart(chart, "en").to_dict()
    color = spec["encoding"]["color"]
    assert color["scale"]["domain"] == ["Motor", "Non-motor"]
    dataset = next(iter(spec["datasets"].values()))
    assert {row["类别"] for row in dataset} == {"Motor", "Non-motor"}
    assert spec["encoding"]["x"]["title"] == "Year"


def test_plotly_pie_labels_and_layout_are_english() -> None:
    categories = pd.DataFrame(
        {
            "公司类别": ["前三大", "大型公司"],
            "公司数": [2, 3],
            "保费": [100.0, 50.0],
            "净利润": [10.0, 5.0],
            "承保利润": [8.0, 2.0],
            "保费占比": [0.67, 0.33],
            "净利润占比": [0.67, 0.33],
            "承保利润占比": [0.8, 0.2],
        }
    )
    data = MarketShareData(
        year="2025",
        categories=categories,
        company_detail=pd.DataFrame(),
        company_count=5,
        coverage=1.0,
    )
    figure = build_market_share_pies(data)
    assert all(trace.textposition == "inside" for trace in figure.data)
    assert figure.layout.height == 420

    localized = localize_plotly(figure, "en")
    assert list(localized.data[0].labels) == ["Top 3", "Large companies"]
    assert [annotation.text for annotation in localized.layout.annotations] == [
        "Premium",
        "Net profit",
        "Underwriting profit",
    ]
    assert "RMB 100 mn" in localized.data[0].hovertemplate
    assert "：" not in localized.data[0].hovertemplate

    bars = localize_plotly(build_market_share_amount_bars(data), "en")
    assert [annotation.text for annotation in bars.layout.annotations] == [
        "Premium scale",
        "Net profit / Underwriting profit",
    ]
    assert list(bars.data[0].x) == ["Top 3", "Large companies"]


def test_company_names_are_not_mangled() -> None:
    chart = alt.Chart(pd.DataFrame({"公司": ["国元农险"], "数值": [1]})).mark_bar().encode(
        x="公司:N",
        y="数值:Q",
    )
    spec = localize_chart(chart, "en").to_dict()
    dataset = next(iter(spec["datasets"].values()))
    assert dataset[0]["公司"] == "国元农险"
    assert translate_text("类型", "en") == "Type"


if __name__ == "__main__":
    test_altair_domain_and_values_are_translated()
    test_plotly_pie_labels_and_layout_are_english()
    test_company_names_are_not_mangled()
    print("全部通过")
