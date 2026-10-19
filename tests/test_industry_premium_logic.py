# -*- coding: utf-8 -*-
"""行业整体保费结构的纯逻辑回归：Q4 全年累计、车险拆分与五年窗口。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.solvency_step7_charts import (  # noqa: E402
    build_industry_premium_structure_chart,
    prepare_industry_premium_annual,
)


def _complete_rows(*, end_year: int = 2025) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for year in range(2022, end_year + 1):
        for company_index, company in enumerate(("甲公司", "乙公司", "丙公司")):
            base = 10_000.0 * (company_index + 1) * (year - 2021)
            for quarter in range(1, 5):
                # Q1-Q3 values are deliberately cumulative stepping stones;
                # the chart must pick the Q4 annual-cumulative value.
                total = base * quarter
                motor = total * 0.6
                for code, value in (
                    ("SIGNED_PREMIUM", total),
                    ("SIGNED_PREMIUM_MOTOR", motor),
                ):
                    rows.append(
                        {
                            "公司": company,
                            "报告期": f"{year}Q{quarter}",
                            "指标编码": code,
                            "期间口径": "本年累计数",
                            "数值": value,
                            "单位": "万元",
                        }
                    )
    return pd.DataFrame(rows)


def test_industry_premium_uses_q4_annual_cumulative() -> None:
    frame = _complete_rows()
    annual, detail, companies = prepare_industry_premium_annual(frame, years=5)
    assert len(companies) == 3
    assert annual["年度"].tolist() == ["2022", "2023", "2024", "2025"]
    assert annual["完整"].all()
    # Three companies at Q4 2022: 40k + 80k + 120k.
    assert annual.iloc[0]["合计"] == 240_000.0
    assert annual.iloc[0]["车险"] == 144_000.0
    assert annual.iloc[0]["非车险"] == 96_000.0
    assert annual.iloc[-1]["合计"] == 960_000.0
    assert set(detail["状态"].astype(str)) <= {"", "nan"}


def test_industry_premium_chart_renders_complete_2022_2025() -> None:
    annual, _, _ = prepare_industry_premium_annual(_complete_rows(), years=5)
    chart = build_industry_premium_structure_chart(annual, unit_mode="亿元")
    spec = chart.to_dict()
    assert len(spec["layer"]) == 3
    assert spec["resolve"]["scale"]["color"] == "independent"
    assert spec["layer"][0]["encoding"]["color"]["scale"]["range"] == [
        "#1E49E2",
        "#00B8F5",
        "#D9E2EC",
    ]
    assert spec["layer"][2]["encoding"]["color"]["scale"]["range"] == [
        "#7213EA",
        "#FD349C",
        "#00338D",
    ]
    x_sort = spec["layer"][0]["encoding"]["x"]["sort"]
    assert x_sort == ["2022", "2023", "2024", "2025"]


def test_industry_premium_handles_2022_2026_window() -> None:
    annual, _, _ = prepare_industry_premium_annual(
        _complete_rows(end_year=2026),
        years=5,
    )
    assert annual["年度"].tolist() == ["2022", "2023", "2024", "2025", "2026"]
    assert annual["完整"].all()
    chart = build_industry_premium_structure_chart(annual, unit_mode="亿元")
    assert chart.to_dict()["layer"][0]["encoding"]["x"]["sort"] == [
        "2022",
        "2023",
        "2024",
        "2025",
        "2026",
    ]


def test_industry_premium_still_renders_partial_year() -> None:
    frame = _complete_rows()
    frame = frame[
        ~(
            frame["报告期"].str.startswith("2023")
            & frame["指标编码"].eq("SIGNED_PREMIUM_MOTOR")
            & frame["公司"].eq("丙公司")
        )
    ]
    annual, _, _ = prepare_industry_premium_annual(frame, years=5)
    assert not annual.loc[annual["年度"].eq("2023"), "完整"].iloc[0]
    chart = build_industry_premium_structure_chart(annual, unit_mode="亿元")
    spec = chart.to_dict()
    assert len(spec["layer"]) == 3
    assert spec["layer"][0]["encoding"]["x"]["sort"] == ["2022", "2023", "2024", "2025"]
    assert spec["layer"][0]["encoding"]["color"]["scale"]["domain"] == [
        "车险",
        "非车险",
        "未拆分",
    ]


if __name__ == "__main__":
    test_industry_premium_uses_q4_annual_cumulative()
    test_industry_premium_chart_renders_complete_2022_2025()
    test_industry_premium_handles_2022_2026_window()
    test_industry_premium_still_renders_partial_year()
    print("PASS 行业保费结构：Q4 全年累计、车险拆分与 2022–2026 窗口")
