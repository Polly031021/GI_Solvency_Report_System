# -*- coding: utf-8 -*-
"""承保效益五年年度图的纯数据与图表规格测试。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load_underwriting():
    from step7_solvency import underwriting as module

    return module


def _row(
    company: str,
    code: str,
    period: str,
    value: float,
    *,
    scope: str = "本年累计数",
    unit: str = "%",
) -> dict[str, object]:
    return {
        "公司": company,
        "指标编码": code,
        "报告期": period,
        "期间口径": scope,
        "数值": value,
        "单位": unit,
    }


def _sample_frame() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    companies = ("甲公司", "乙公司", "丙公司", "丁公司")
    premiums = {"甲公司": 1000.0, "乙公司": 800.0, "丙公司": 600.0, "丁公司": 400.0}
    ratios = {"甲公司": 95.0, "乙公司": 100.0, "丙公司": 105.0, "丁公司": 110.0}
    for year in range(2021, 2026):
        for company in companies:
            premium = premiums[company] * (1.0 + (year - 2021) * 0.05)
            ratio = ratios[company]
            # One year in decimal form verifies the automatic x100 conversion.
            if year == 2021:
                rows.append(_row(company, "COMBINED_RATIO", f"{year}Q4", ratio / 100.0))
            else:
                rows.append(_row(company, "COMBINED_RATIO", f"{year}Q4", ratio))
            rows.append(_row(company, "SIGNED_PREMIUM", f"{year}Q4", premium, unit="万元"))
    # Q3 must not be treated as an annual observation.
    rows.append(_row("甲公司", "COMBINED_RATIO", "2025Q3", 1.0))
    rows.append(_row("甲公司", "SIGNED_PREMIUM", "2025Q3", 9999.0, unit="万元"))
    return pd.DataFrame(rows)


def test_five_year_annual_scatter_data() -> None:
    module = _load_underwriting()
    annual = module.prepare_underwriting_annual(_sample_frame(), years=5)

    assert sorted(annual["年度"].unique().tolist()) == [2021, 2022, 2023, 2024, 2025]
    assert len(annual) == 20
    assert annual.groupby("年度").size().eq(4).all()
    assert annual.groupby("年度")["保费排名"].apply(list).map(
        lambda values: sorted(values) == [1, 2, 3, 4]
    ).all()
    assert set(annual["综合成本率"].round(2)) == {95.0, 100.0, 105.0, 110.0}

    counts = module.annual_underwriting_counts(annual)
    assert counts["盈利"].tolist() == [1, 1, 1, 1, 1]
    assert counts["亏损"].tolist() == [2, 2, 2, 2, 2]
    assert counts["盈亏平衡"].tolist() == [1, 1, 1, 1, 1]
    assert counts["有效公司数"].tolist() == [4, 4, 4, 4, 4]


def test_latest_five_years_and_quarterly_premium_fallback() -> None:
    module = _load_underwriting()
    frame = _sample_frame()
    extra = _row("甲公司", "COMBINED_RATIO", "2020Q4", 96.0)
    frame = pd.concat([frame, pd.DataFrame([extra])], ignore_index=True)
    frame = pd.concat(
        [
            frame,
            pd.DataFrame(
                [
                    _row(
                        "乙公司",
                        "SIGNED_PREMIUM",
                        "2025Q4",
                        777.0,
                        scope="本季度数",
                        unit="万元",
                    )
                ]
            ),
        ],
        ignore_index=True,
    )

    annual = module.prepare_underwriting_annual(frame, years=5)
    assert 2020 not in annual["年度"].unique().tolist()
    assert annual["年度"].min() == 2021
    # The duplicate company/year is deduplicated; cumulative premium wins.
    assert annual[annual["公司"].eq("乙公司") & annual["年度"].eq(2025)].shape[0] == 1


def test_symmetric_domain_and_chart_builders() -> None:
    module = _load_underwriting()
    annual = module.prepare_underwriting_annual(_sample_frame(), years=5)
    lower, upper = module._symmetric_axis_domain(annual["综合成本率"])
    assert lower < 100.0 < upper
    assert abs((lower + upper) / 2.0 - 100.0) < 1e-9

    year_chart = module.build_underwriting_year_chart(annual, 2025)
    counts = module.annual_underwriting_counts(annual)
    trend_chart = module.build_underwriting_count_chart(
        counts, years=sorted(annual["年度"].unique().tolist())
    )
    assert year_chart is not None
    assert trend_chart is not None
    assert len(year_chart.to_dict()["layer"]) >= 5


def test_large_company_pool_axis_and_outliers() -> None:
    """More than 15 companies must not pass values=None to alt.Axis."""
    module = _load_underwriting()
    rows: list[dict[str, object]] = []
    for index in range(25):
        company = f"公司{index:02d}"
        ratio = 90.0 + index
        if index == 24:
            ratio = 400.0
        rows.append(_row(company, "COMBINED_RATIO", "2025Q4", ratio))
        rows.append(
            _row(
                company,
                "SIGNED_PREMIUM",
                "2025Q4",
                10_000.0 - index * 100.0,
                unit="万元",
            )
        )

    annual = module.prepare_underwriting_annual(pd.DataFrame(rows), years=1)
    assert len(annual) == 25
    chart = module.build_underwriting_year_chart(annual, 2025)
    assert chart is not None
    spec = chart.to_dict()
    point_layer = next(
        layer
        for layer in spec["layer"]
        if layer.get("mark", {}).get("type") == "circle"
    )
    axis = point_layer["encoding"]["x"]["axis"]
    assert "values" not in axis
    assert axis.get("tickCount") == 10
    assert point_layer["mark"].get("filled") is True
    assert any(
        layer.get("mark", {}).get("shape") == "triangle-up"
        for layer in spec["layer"]
    )


def test_step7_underwriting_page_five_year_layout() -> None:
    """The Step 7 module must render five annual cards and a count trend."""
    from streamlit.testing.v1 import AppTest

    from services.step7_excel_config import load_step7_config
    from test_workflow_smoke import synthetic_integrated_data

    base = synthetic_integrated_data()
    rows = []
    for year in range(2021, 2025):
        for company, ratio in (("甲公司", 95.0), ("乙公司", 105.0)):
            for code, value in (
                ("COMBINED_RATIO", ratio),
                ("SIGNED_PREMIUM", 1_000_000.0 if company == "甲公司" else 700_000.0),
            ):
                template = base[
                    base["公司"].eq(company) & base["指标编码"].eq(code)
                ].iloc[0].to_dict()
                template["报告期"] = f"{year}Q4"
                template["报告年度"] = year
                template["报告季度"] = "Q4"
                template["数值"] = value
                rows.append(template)
    data = pd.concat([base, pd.DataFrame(rows)], ignore_index=True)

    template_path = ROOT / "templates" / "Step7-偿付能力公司报告展示模块.xlsx"
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=900)
    at.session_state["logged_in"] = True
    at.session_state["user_role"] = "项目组成员"
    at.session_state["active_profile_id"] = "NON_LIFE_SOLVENCY"
    at.session_state["integrated_data"] = data
    at.session_state["standard_data"] = data
    at.session_state["step7_modules"] = load_step7_config(template_path)
    at.session_state["workflow_main_tab"] = "STEP7 公司报告"
    at.session_state["company_nav_level_one"] = "行业概览"
    at.session_state["company_nav_level_two"] = "承保效益"
    at.session_state["s7_company_types"] = ["全部"]
    at.session_state["s7_unit_mode"] = "亿元"
    at.run()
    assert not at.exception, [error.value for error in at.exception]

    markdown_text = "\n".join(item.value for item in at.markdown)
    for year in range(2021, 2026):
        assert f"{year} 年" in markdown_text, f"missing year card {year}"
    assert "盈利" in markdown_text and "亏损" in markdown_text
    assert "盈利 / 亏损公司数趋势" in markdown_text
    year_card_positions = [
        markdown_text.index(f"<div class='s7-uw-year-title'>{year} 年</div>")
        for year in range(2021, 2026)
    ]
    assert year_card_positions == sorted(year_card_positions, reverse=True)
    charts = at.get("vega_lite_chart")
    assert len(charts) >= 6, f"expected 6 charts, got {len(charts)}"


if __name__ == "__main__":
    test_five_year_annual_scatter_data()
    test_latest_five_years_and_quarterly_premium_fallback()
    test_symmetric_domain_and_chart_builders()
    test_large_company_pool_axis_and_outliers()
    test_step7_underwriting_page_five_year_layout()
    print("承保效益五年年度图测试通过")
