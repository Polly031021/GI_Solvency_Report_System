# -*- coding: utf-8 -*-
"""STEP4 勾稽检查：失败原因、STEP3 依据与页面下钻回归测试。"""

from __future__ import annotations

import io
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.solvency_normalizer import STANDARD_COLUMNS, load_taxonomy  # noqa: E402
from services.solvency_validator import (  # noqa: E402
    load_validation_rules,
    validate_standard_data_detailed,
)
from ui.cache import validation_results_to_xlsx  # noqa: E402


def _row(
    code: str,
    name: str,
    value: float | str,
    *,
    sheet: str = "主要指标表",
    page: str = "10",
    unit: str = "万元",
) -> dict[str, object]:
    return {
        "公司": "甲公司",
        "原始公司名称": "甲公司",
        "标准公司名称": "甲公司",
        "公司统一编码": "C001",
        "公司类型": "财险",
        "同业分类": "大型公司",
        "报告类型": "NON_LIFE_SOLVENCY",
        "报告年度": 2025,
        "报告季度": "Q4",
        "报告期": "2025Q4",
        "披露日期": "",
        "一级模块": "偿付能力",
        "二级模块": "资本",
        "行次": "",
        "指标编码": code,
        "指标名称": name,
        "期间口径": "本季度末数",
        "数值": value,
        "单位": unit,
        "数据类型": "金额" if unit == "万元" else "百分比",
        "是否预测": "否",
        "来源页码": page,
        "原始披露值": value,
        "备注": "",
        "来源类型": "披露",
        "指标属性": "披露",
        "来源文件": "test.pdf",
        "来源工作表": sheet,
        "导入批次": "test",
        "计算逻辑": "",
    }


def _standard_frame() -> pd.DataFrame:
    rows = [
        _row("RECOGNIZED_ASSETS", "认可资产", 1000),
        _row("RECOGNIZED_LIABILITIES", "认可负债", 600),
        _row("ACTUAL_CAPITAL", "实际资本", 400),
        _row("CORE_T1_CAPITAL", "核心一级资本", 300),
        _row("CORE_T2_CAPITAL", "核心二级资本", 90),
        _row("ANC_T1_CAPITAL", "附属一级资本", 10),
        _row("ANC_T2_CAPITAL", "附属二级资本", 0),
        _row("MINIMUM_CAPITAL", "最低资本", 300),
        _row("QUANT_RISK_CAPITAL", "量化风险最低资本", 250),
        _row("CONTROL_RISK_CAPITAL", "控制风险最低资本", 40),
        _row("CORE_SOLVENCY_SURPLUS", "核心偿付能力溢额", 90),
        _row("COMBINED_SOLVENCY_SURPLUS", "综合偿付能力溢额", 99),
        _row("CORE_SOLVENCY_RATIO", "核心偿付能力充足率", 130.0, unit="%"),
        _row("COMBINED_SOLVENCY_RATIO", "综合偿付能力充足率", 135.0, unit="%"),
        _row("ACTUAL_CAPITAL", "实际资本", 410, sheet="实际资本表", page="20"),
    ]
    frame = pd.DataFrame(rows)
    for column in STANDARD_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
    return frame[STANDARD_COLUMNS]


def _validate():
    taxonomy = load_taxonomy(ROOT / "config" / "solvency_taxonomy.xlsx")
    rules = load_validation_rules(ROOT / "config" / "solvency_validation_rules.xlsx")
    return validate_standard_data_detailed(_standard_frame(), rules, taxonomy)


def test_missing_dependency_has_reason_and_evidence() -> None:
    results, evidence = _validate()
    row = results[results["rule_id"].eq("MINIMUM_CAPITAL")].iloc[0]
    assert row["status"] == "缺失", row["status"]
    assert "ADDITIONAL_CAPITAL" in row["failure_reason"]
    related = evidence[evidence["result_id"].eq(row["result_id"])]
    missing = related[related["metric_code"].eq("ADDITIONAL_CAPITAL")]
    assert not missing.empty
    assert missing.iloc[0]["status"] == "缺失"
    print("PASS 缺失指标给出原因与 STEP3 缺失依据")


def test_ratio_failure_shows_values_and_step3_records() -> None:
    results, evidence = _validate()
    row = results[results["rule_id"].eq("COMBINED_RATIO")].iloc[0]
    assert row["status"] == "未通过", row["status"]
    assert "135.00%" in row["failure_reason"]
    assert "133.33%" in row["failure_reason"]
    related = evidence[evidence["result_id"].eq(row["result_id"])]
    assert set(related["metric_code"]) >= {
        "COMBINED_SOLVENCY_RATIO",
        "ACTUAL_CAPITAL",
        "MINIMUM_CAPITAL",
    }
    assert related["record_id"].notna().any()
    print("PASS 比率勾稽失败给出代入值与 STEP3 记录号")


def test_cross_table_duplicate_compares_source_sheets() -> None:
    results, evidence = _validate()
    row = results[results["rule_id"].eq("DUPLICATE_ACTUAL_CAPITAL")].iloc[0]
    assert row["status"] == "未通过", row["status"]
    assert "主要指标表" in row["failure_reason"]
    assert "实际资本表" in row["failure_reason"]
    assert row["actual"] == 410
    assert row["expected"] == 400
    related = evidence[evidence["result_id"].eq(row["result_id"])]
    assert set(related["source_sheet"]) >= {"主要指标表", "实际资本表"}
    print("PASS 跨表一致性按来源工作表比对")


def test_export_contains_failure_reason_and_step3_sheets() -> None:
    results, evidence = _validate()
    workbook = validation_results_to_xlsx(results, evidence, _standard_frame())
    excel = pd.ExcelFile(io.BytesIO(workbook))
    assert "失败原因与STEP3依据" in excel.sheet_names
    assert "STEP3相关记录" in excel.sheet_names
    issues = pd.read_excel(io.BytesIO(workbook), sheet_name="待处理事项")
    assert "失败原因" in issues.columns
    assert issues["失败原因"].fillna("").astype(str).str.len().gt(0).any()
    print("PASS 导出工作簿包含失败原因与 STEP3 依据")


def test_step4_page_drills_down_to_step3() -> None:
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=600)
    at.session_state["logged_in"] = True
    at.session_state["user_role"] = "项目组成员"
    at.session_state["active_profile_id"] = "NON_LIFE_SOLVENCY"
    at.session_state["workflow_main_tab"] = "STEP4 勾稽检查"
    at.run()
    at.session_state["standard_data"] = _standard_frame()
    at.run()
    at.button(key="validate_data").click().run()
    assert not at.exception, [item.value for item in at.exception]
    assert not at.session_state["validation_evidence"].empty
    assert any(
        box.key == "step4_issue_selector" for box in at.selectbox
    )
    goto_buttons = [
        button
        for button in at.button
        if button.key and button.key.startswith("step4_goto_step3_")
    ]
    assert goto_buttons
    goto_buttons[0].click().run()
    assert not at.exception, [item.value for item in at.exception]
    assert at.session_state["workflow_main_tab"] == "STEP3 标准化"
    assert at.session_state["step3_focus"]
    print("PASS STEP4 失败项可跳转并在 STEP3 定位记录")


if __name__ == "__main__":
    test_missing_dependency_has_reason_and_evidence()
    test_ratio_failure_shows_values_and_step3_records()
    test_cross_table_duplicate_compares_source_sheets()
    test_export_contains_failure_reason_and_step3_sheets()
    test_step4_page_drills_down_to_step3()
    print("全部通过")
