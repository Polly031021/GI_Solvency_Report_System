# -*- coding: utf-8 -*-
"""Regression tests for the five-year loss/expense stack chart."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.step7_excel_config import load_step7_config  # noqa: E402
from services.solvency_step7_charts import (  # noqa: E402
    build_loss_expense_panel_chart,
    group_loss_expense_panels,
    loss_expense_axis_domain,
    prepare_loss_expense_panel,
)


def _loss_expense_frame() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    years = range(2020, 2026)
    for company, loss_values, expense_values in (
        ("甲公司", (60, 62, 65, 64, 66, 67), (28, 27, 26, 27, 25, 24)),
        ("乙公司", (72, 74, 73, 71, 70, 69), (31, 30, 29, 30, 28, 27)),
    ):
        for index, year in enumerate(years):
            period = f"{year}Q4"
            base = {
                "公司": company,
                "报告期": period,
                "期间口径": "本年累计数",
                "同业分类": "前十大" if company == "甲公司" else "中型公司",
                "单位": "%",
            }
            rows.extend(
                [
                    {**base, "指标编码": "LOSS_RATIO", "数值": loss_values[index]},
                    {**base, "指标编码": "EXPENSE_RATIO", "数值": expense_values[index]},
                ]
            )
            if not (company == "甲公司" and year == 2025):
                rows.append(
                    {
                        **base,
                        "指标编码": "COMBINED_RATIO",
                        "数值": loss_values[index] + expense_values[index],
                    }
                )
    return pd.DataFrame(rows)


def test_prepare_loss_expense_uses_latest_five_years() -> None:
    panel = prepare_loss_expense_panel(_loss_expense_frame(), "甲公司")
    assert panel.years == ("2021", "2022", "2023", "2024", "2025")
    assert len(panel.bar_frame) == 10
    last_total = panel.total_frame.set_index("年度").loc["2025"]
    assert last_total["口径"] == "测算"
    assert abs(float(last_total["综合成本率"]) - 91.0) < 1e-9


def test_loss_expense_grouping_and_chart() -> None:
    frame = _loss_expense_frame()
    panels = [
        prepare_loss_expense_panel(frame, company)
        for company in ("乙公司", "甲公司")
    ]
    grouped = group_loss_expense_panels(panels)
    assert [name for name, _ in grouped] == ["前十大", "中型公司"]
    chart = build_loss_expense_panel_chart(
        panels[1],
        y_domain=loss_expense_axis_domain(panels),
    )
    spec_text = json.dumps(chart.to_dict(), ensure_ascii=False)
    assert "91.0%*" in spec_text
    assert "67%" in spec_text
    assert "24%" in spec_text
    assert "100" in spec_text


def test_template_configures_combined_loss_expense_ratio_chart() -> None:
    modules = load_step7_config(
        ROOT / "templates" / "Step7-偿付能力公司报告展示模块.xlsx"
    )
    module = next(
        item
        for item in modules
        if item.level_one == "经营与偿付能力联动分析"
        and item.level_two == "赔付率&费用率"
    )
    assert module.chart_name == "赔付率与费用率"
    assert module.chart_kind == "loss_expense_cost_ratio"
    assert module.metric_codes == (
        "LOSS_RATIO",
        "EXPENSE_RATIO",
        "COMBINED_RATIO",
    )
    assert module.chart_config["years"] == 5


if __name__ == "__main__":
    test_prepare_loss_expense_uses_latest_five_years()
    test_loss_expense_grouping_and_chart()
    test_template_configures_combined_loss_expense_ratio_chart()
    print("全部通过")
