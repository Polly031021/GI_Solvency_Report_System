"""Critical disclosure shapes for the top-ten document migration."""

from types import SimpleNamespace

from services.company_extractors._document import (
    CAPITAL, _number_rows, _priority_rows, _regulatory_rows, contract_failure, locate_from_texts,
)
from services.company_extractors.pingan import _loss_absorption_row


def test_locator_skips_directory_and_keeps_minimum_continuation():
    layout = {"MINIMUM_CAPITAL": {"title": "十、最低资本", "continuation": 1,
                                 "tail_terms": ["信用风险最低资本"]}}
    pages = locate_from_texts(["目录\n十、最低资本……24", "十、最低资本\n非寿险业务",
                              "信用风险最低资本 946007 1013582"], layout)
    assert pages["MINIMUM_CAPITAL"] == [2, 3]


def test_guoshou_lcr_periods_and_self_stress():
    rows = _regulatory_rows([
        ["LCR2（必测压力情景）", "217.93%", "102.71%", "150.72%", "100.89%"],
        ["LCR2（自测压力情景）", "218.36%", "128.04%", "202.61%", "126.25%"],
    ], "国寿财险")
    assert rows == [["LCR2未来3个月", "217.93%", "150.72%"],
                    ["LCR2未来12个月", "102.71%", "100.89%"]]


def test_yingda_has_only_current_period_and_uses_mandatory_columns():
    rows = _regulatory_rows([["LCR2（压力情景）", "", "", "", "320.40%",
                             "164.58%", "322.47%", "166.34%"]], "英大财产")
    assert rows == [["LCR2未来3个月", "320.40%", ""],
                    ["LCR2未来12个月", "164.58%", ""]]


def test_merged_horizon_retains_lcr_kind():
    rows = _regulatory_rows([["流动性覆盖率", "LCR1", "未来三个月", "113%", "112.6%"],
                            [None, None, "未来十二个月", "103.4%", "103.4%"]], "太保产险")
    assert rows[-1] == ["LCR1未来12个月", "103.4%", "103.4%"]


def test_contract_rejects_wrong_width_and_keeps_disclosed_blank():
    table = SimpleNamespace(table_config={"_layout_contract": {
        "headers": CAPITAL, "min_rows": 2, "required_rows": ["核心一级资本"]}})
    assert not contract_failure(table, [CAPITAL, ["核心一级资本", "0", ""]])
    assert contract_failure(table, [CAPITAL, ["核心一级资本", "0", "", "extra"]])


def test_main_missing_forecast_keeps_blank_instead_of_dropping_row():
    assert _number_rows(["核心二级资本 - -"], 3) == [["核心二级资本", "-", "-", ""]]


def test_priority_loss_absorption_keeps_placeholder_and_alias():
    assert _priority_rows([["1.1.5", "特定类保险合同损失吸收效应", "", ""]], "MINIMUM_CAPITAL") == [
        ["特定类别保险合同损失吸收效应", "", ""]]
    assert not _priority_rows([["1.6.1", "损失吸收调整-不考虑上限", "5", "6"]], "MINIMUM_CAPITAL")


def test_pingan_loss_absorption_row_keeps_source_placeholder() -> None:
    assert _loss_absorption_row(
        ["特定类别保险合同损失吸收效应", "-", "-"]
    ) == ["特定类别保险合同损失吸收效应", "-", "-"]


def test_priority_growth_keeps_negative_value_and_merged_group():
    assert _priority_rows([[None, "规模保费同比增速（%）", "-0.1%", "0.1%"]], "LIQUIDITY_MONITORING") == [
        ["规模保费同比增速", "-0.1%", "0.1%"]]
    assert _priority_rows([["四、规模保费同\n比增速", "指标值（%）", "2.88%", "3.14%"],
                           [None, "当年累计规模保费（万元）", "1300905", "1155261"]], "LIQUIDITY_MONITORING") == [
        ["规模保费同比增速", "2.88%", "3.14%"]]
