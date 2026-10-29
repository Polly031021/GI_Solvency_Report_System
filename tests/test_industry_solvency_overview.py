# -*- coding: utf-8 -*-
"""行业概览：偿付能力合计口径与第四个二级模块回归测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.solvency_step7_chart_plans import (  # noqa: E402
    SOLVENCY_DISCLOSURE_OVERVIEW,
    chart_plan_for,
)
from services.solvency_step7_charts import (  # noqa: E402
    prepare_industry_solvency_overview,
)
from services.step7_excel_config import load_step7_config  # noqa: E402


def _row(company: str, period: str, code: str, value: float) -> dict[str, object]:
    return {
        "公司": company,
        "报告期": period,
        "指标编码": code,
        "期间口径": "本季度末数",
        "数值": value,
        "单位": "万元",
    }


def _capital_frame() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for period, factor in (("2024Q4", 1.0), ("2025Q4", 1.2)):
        for company, actual, minimum, core1, core2 in (
            ("甲公司", 1_000.0, 500.0, 600.0, 100.0),
            ("乙公司", 2_000.0, 1_000.0, 900.0, 100.0),
        ):
            rows.extend(
                [
                    _row(company, period, "ACTUAL_CAPITAL", actual * factor),
                    _row(company, period, "MINIMUM_CAPITAL", minimum * factor),
                    _row(company, period, "CORE_T1_CAPITAL", core1 * factor),
                    _row(company, period, "CORE_T2_CAPITAL", core2 * factor),
                ]
            )
    return pd.DataFrame(rows)


def test_industry_solvency_aggregate_ratios() -> None:
    overview = prepare_industry_solvency_overview(_capital_frame())
    assert overview is not None
    assert overview.period == "2025Q4"
    assert overview.company_count == 2
    # 2025: actual 3,600 / minimum 1,800 = 200%.
    assert abs(overview.combined_ratio - 200.0) < 1e-9
    # Core capital = 2,040; 2,040 / 1,800 = 113.33%.
    assert abs(overview.core_ratio - 113.33333333333333) < 1e-9
    assert abs(overview.core_share - 56.66666666666667) < 1e-9
    assert overview.previous_period == "2024Q4"
    assert abs(overview.previous_combined_ratio - 200.0) < 1e-9


def test_industry_solvency_ignores_zero_minimum_capital() -> None:
    frame = _capital_frame()
    frame = frame[
        ~(
            frame["公司"].eq("乙公司")
            & frame["报告期"].eq("2025Q4")
            & frame["指标编码"].eq("MINIMUM_CAPITAL")
        )
    ]
    overview = prepare_industry_solvency_overview(frame)
    assert overview is not None
    assert overview.company_count == 1
    assert abs(overview.combined_ratio - 200.0) < 1e-9


def test_step7_template_has_fourth_industry_module() -> None:
    modules = load_step7_config(
        ROOT / "templates" / "Step7-偿付能力公司报告展示模块.xlsx"
    )
    industry = [m for m in modules if m.level_one == "行业概览"]
    assert [m.chart_name for m in industry] == [
        "行业整体保费规模与结构",
        "分险种保费收入",
        "行业市场份额",
        "承保效益",
        "偿付能力披露整体情况",
    ]
    assert industry[-1].chart_kind == SOLVENCY_DISCLOSURE_OVERVIEW
    assert chart_plan_for("偿付能力披露整体情况").kind == SOLVENCY_DISCLOSURE_OVERVIEW


if __name__ == "__main__":
    test_industry_solvency_aggregate_ratios()
    test_industry_solvency_ignores_zero_minimum_capital()
    test_step7_template_has_fourth_industry_module()
    print("PASS 行业偿付能力合计口径与第四个二级模块")
