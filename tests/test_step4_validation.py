# -*- coding: utf-8 -*-
"""STEP4 配置驱动勾稽检查回归测试。"""

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
    validate_rule_configuration,
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


def _complete_rows() -> list[dict[str, object]]:
    return [
        _row("RECOGNIZED_ASSETS", "认可资产", 1000),
        _row("RECOGNIZED_LIABILITIES", "认可负债", 600),
        _row("ACTUAL_CAPITAL", "实际资本", 400),
        _row("CORE_T1_CAPITAL", "核心一级资本", 300),
        _row("CORE_T2_CAPITAL", "核心二级资本", 90),
        _row("ANC_T1_CAPITAL", "附属一级资本", 10),
        _row("ANC_T2_CAPITAL", "附属二级资本", 0),
        _row("MINIMUM_CAPITAL", "最低资本", 300),
        _row("QUANT_RISK_CAPITAL", "量化风险最低资本", 250),
        _row(
            "QUANT_RISK_CAPITAL_BEFORE_FEATURE_FACTOR",
            "量化风险最低资本（未考虑特征系数前）",
            250,
        ),
        _row("CONTROL_RISK_CAPITAL", "控制风险最低资本", 50),
        _row("QUANT_RISK_DIVERSIFICATION_EFFECT", "量化风险分散效应", 80),
        _row("NON_LIFE_INSURANCE_RISK_CAPITAL", "非寿险业务保险风险最低资本合计", 180),
        _row("NON_LIFE_PREMIUM_RESERVE_RISK_CAPITAL", "保费及准备金风险最低资本", 200),
        _row("NON_LIFE_CATASTROPHE_RISK_CAPITAL", "巨灾风险最低资本", 50),
        _row(
            "NON_LIFE_INSURANCE_RISK_DIVERSIFICATION_EFFECT",
            "非寿险业务保险风险分散效应",
            70,
        ),
        _row("MARKET_RISK_CAPITAL", "市场风险最低资本合计", 100),
        _row("INTEREST_RATE_RISK_CAPITAL", "利率风险最低资本", 40),
        _row("EQUITY_RISK_CAPITAL", "权益价格风险最低资本", 30),
        _row("REAL_ESTATE_RISK_CAPITAL", "房地产价格风险最低资本", 10),
        _row("OVERSEAS_FIXED_INCOME_RISK_CAPITAL", "境外固定收益类资产价格风险最低资本", 5),
        _row("OVERSEAS_EQUITY_RISK_CAPITAL", "境外权益类资产价格风险最低资本", 5),
        _row("FOREIGN_EXCHANGE_RISK_CAPITAL", "汇率风险最低资本", 20),
        _row("MARKET_RISK_DIVERSIFICATION_EFFECT", "市场风险分散效应", 10),
        _row("CREDIT_RISK_CAPITAL", "信用风险最低资本合计", 50),
        _row("SPREAD_RISK_CAPITAL", "利差风险最低资本", 30),
        _row("COUNTERPARTY_RISK_CAPITAL", "交易对手违约风险最低资本", 30),
        _row("CREDIT_RISK_DIVERSIFICATION_EFFECT", "信用风险分散效应", 10),
        _row("COMBINED_SOLVENCY_SURPLUS", "综合偿付能力溢额", 100),
        _row("CORE_SOLVENCY_RATIO", "核心偿付能力充足率", 130.0, unit="%"),
        _row(
            "COMBINED_SOLVENCY_RATIO",
            "综合偿付能力充足率",
            400 / 300 * 100,
            unit="%",
        ),
    ]


def _standard_frame() -> pd.DataFrame:
    frame = pd.DataFrame(_complete_rows())
    for column in STANDARD_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
    return frame[STANDARD_COLUMNS]


def _validate(frame: pd.DataFrame | None = None):
    taxonomy = load_taxonomy(ROOT / "config" / "solvency_taxonomy.xlsx")
    rules = load_validation_rules(ROOT / "config" / "solvency_validation_rules.xlsx")
    return validate_standard_data_detailed(
        frame if frame is not None else _standard_frame(),
        rules,
        taxonomy,
    )


def test_rule_configuration_contains_all_configured_formulas() -> None:
    taxonomy = load_taxonomy(ROOT / "config" / "solvency_taxonomy.xlsx")
    rules = load_validation_rules(ROOT / "config" / "solvency_validation_rules.xlsx")
    expected = {
        "COMBINED_RATIO",
        "CORE_RATIO",
        "COMBINED_SURPLUS",
        "RECOGNIZED_ASSETS_BALANCE",
        "ACTUAL_CAPITAL_COMPONENTS",
        "MINIMUM_CAPITAL",
        "QUANT_RISK_CAPITAL_BEFORE_FEATURE_FACTOR",
        "NON_LIFE_INSURANCE_RISK_CAPITAL",
        "MARKET_RISK_CAPITAL",
        "CREDIT_RISK_CAPITAL",
    }
    assert expected.issubset(set(rules["规则ID"].astype(str)))
    assert set(rules["容差"].astype(float)) == {0.0001}
    check = validate_rule_configuration(rules, taxonomy)
    assert check["状态"].eq("通过").all(), check
    print("PASS 勾稽规则全部来自配置文件，语法与指标引用有效")


def test_all_configured_formulas_pass_for_consistent_data() -> None:
    results, _ = _validate()
    formula_rows = results[results["check_type"].eq("公式勾稽")]
    assert not formula_rows.empty
    failures = formula_rows[formula_rows["status"].ne("通过")]
    assert failures.empty, failures[["rule_id", "status", "failure_reason"]]
    print("PASS 十条配置化公式在一致数据上全部通过")


def test_tolerance_is_strictly_less_than_00001() -> None:
    pass_frame = _standard_frame()
    pass_frame.loc[
        pass_frame["指标编码"].eq("COMBINED_SOLVENCY_SURPLUS"),
        "数值",
    ] = 100.00005
    results, _ = _validate(pass_frame)
    assert (
        results.loc[
            results["rule_id"].eq("COMBINED_SURPLUS"),
            "status",
        ].iloc[0]
        == "通过"
    )

    fail_frame = _standard_frame()
    fail_frame.loc[
        fail_frame["指标编码"].eq("COMBINED_SOLVENCY_SURPLUS"),
        "数值",
    ] = 100.0002
    results, _ = _validate(fail_frame)
    row = results[results["rule_id"].eq("COMBINED_SURPLUS")].iloc[0]
    assert row["status"] == "未通过"
    assert "0.0001" in row["failure_reason"]
    print("PASS 勾稽差额按严格小于 0.0001 判定")


def test_missing_dependency_has_reason_and_evidence() -> None:
    frame = _standard_frame()
    frame = frame[~frame["指标编码"].eq("CONTROL_RISK_CAPITAL")].copy()
    results, evidence = _validate(frame)
    row = results[results["rule_id"].eq("MINIMUM_CAPITAL")].iloc[0]
    assert row["status"] == "缺失", row["status"]
    assert "CONTROL_RISK_CAPITAL" in row["failure_reason"]
    related = evidence[evidence["result_id"].eq(row["result_id"])]
    missing = related[related["metric_code"].eq("CONTROL_RISK_CAPITAL")]
    assert not missing.empty
    assert missing.iloc[0]["status"] == "缺失"
    print("PASS 缺失指标给出原因与 STEP3 缺失依据")


def test_ratio_failure_shows_formula_values_and_step3_records() -> None:
    frame = _standard_frame()
    frame.loc[
        frame["指标编码"].eq("COMBINED_SOLVENCY_RATIO"),
        "数值",
    ] = 135.0
    results, evidence = _validate(frame)
    row = results[results["rule_id"].eq("COMBINED_RATIO")].iloc[0]
    assert row["status"] == "未通过", row["status"]
    assert "135.00%" in row["failure_reason"]
    assert "133.33%" in row["failure_reason"]
    assert "ACTUAL_CAPITAL / MINIMUM_CAPITAL * 100" in row["failure_reason"]
    related = evidence[evidence["result_id"].eq(row["result_id"])]
    assert set(related["metric_code"]) >= {
        "COMBINED_SOLVENCY_RATIO",
        "ACTUAL_CAPITAL",
        "MINIMUM_CAPITAL",
    }
    assert related["record_id"].notna().any()
    print("PASS 比率勾稽失败给出配置公式、代入值与 STEP3 记录号")


def test_cross_table_duplicate_compares_source_sheets() -> None:
    frame = pd.concat(
        [
            _standard_frame(),
            pd.DataFrame(
                [
                    _row(
                        "ACTUAL_CAPITAL",
                        "实际资本",
                        410,
                        sheet="实际资本表",
                        page="20",
                    )
                ]
            ),
        ],
        ignore_index=True,
    )
    results, evidence = _validate(frame)
    row = results[results["rule_id"].eq("DUPLICATE_ACTUAL_CAPITAL")].iloc[0]
    assert row["status"] == "未通过", row["status"]
    assert "主要指标表" in row["failure_reason"]
    assert "实际资本表" in row["failure_reason"]
    assert row["actual"] == 410
    assert row["expected"] == 400
    related = evidence[evidence["result_id"].eq(row["result_id"])]
    assert set(related["source_sheet"]) >= {"主要指标表", "实际资本表"}
    print("PASS 跨表一致性按来源工作表比对")


def test_schema_accepts_type_alias_metric_alias_and_period_alias() -> None:
    frame = _standard_frame()
    frame.loc[
        frame["指标编码"].eq("CORE_SOLVENCY_RATIO"),
        "数据类型",
    ] = "比率"
    extra_rows = [
        _row("REGISTERED_CAPITAL", "注册资本", 1_000),
        _row("INSURANCE_REVENUE", "保险业务收入", 80_000),
        _row("EXPENSE_RATIO", "费用率", 30.0, unit="%"),
        _row("SIGNED_PREMIUM_TOP5", "第五大险种签单保费", 100),
        _row("INVESTMENT_RETURN", "投资收益率", 2.5, unit="%"),
        _row("FINANCIAL_STATEMENT_NET_ASSETS", "财务报表净资产总额", 500),
    ]
    extra = pd.DataFrame(extra_rows)
    extra.loc[
        extra["指标编码"].isin(
            {"EXPENSE_RATIO", "SIGNED_PREMIUM_TOP5"}
        ),
        "期间口径",
    ] = "本季度数"
    extra.loc[
        extra["指标编码"].eq("INVESTMENT_RETURN"),
        "期间口径",
    ] = "近三年平均"
    extra.loc[
        extra["指标编码"].eq("INSURANCE_REVENUE"),
        "期间口径",
    ] = "本季度数"
    for column in STANDARD_COLUMNS:
        if column not in extra.columns:
            extra[column] = ""
    frame = pd.concat(
        [frame, extra[STANDARD_COLUMNS]],
        ignore_index=True,
    )
    results, _ = _validate(frame)
    schema = results[results["check_type"].eq("数据规范")]
    failed = schema[schema["status"].ne("通过")]
    assert failed.empty, failed[["rule_id", "failure_reason"]]
    print("PASS 比率/百分比、指标别名、期间别名与注册资本编码校验")


def test_export_contains_failure_reason_and_step3_sheets() -> None:
    frame = _standard_frame()
    frame.loc[
        frame["指标编码"].eq("COMBINED_SOLVENCY_RATIO"),
        "数值",
    ] = 135.0
    results, evidence = _validate(frame)
    workbook = validation_results_to_xlsx(results, evidence, frame)
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
    frame = _standard_frame()
    frame.loc[
        frame["指标编码"].eq("COMBINED_SOLVENCY_RATIO"),
        "数值",
    ] = 135.0
    at.session_state["standard_data"] = frame
    at.run()
    at.button(key="validate_data").click().run()
    assert not at.exception, [item.value for item in at.exception]
    assert not at.session_state["validation_evidence"].empty
    assert any(box.key == "step4_issue_selector" for box in at.selectbox)
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
    test_rule_configuration_contains_all_configured_formulas()
    test_all_configured_formulas_pass_for_consistent_data()
    test_tolerance_is_strictly_less_than_00001()
    test_missing_dependency_has_reason_and_evidence()
    test_ratio_failure_shows_formula_values_and_step3_records()
    test_cross_table_duplicate_compares_source_sheets()
    test_schema_accepts_type_alias_metric_alias_and_period_alias()
    test_export_contains_failure_reason_and_step3_sheets()
    test_step4_page_drills_down_to_step3()
    print("全部通过")
