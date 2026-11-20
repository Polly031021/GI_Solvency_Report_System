# -*- coding: utf-8 -*-
"""R4 缺失原因：原因码判定与 STEP3 诊断转换。"""

from __future__ import annotations

from types import SimpleNamespace

from services.extraction_trace import build_diagnostics, build_traces, trace_reason_map


def _match(
    table_id: str = "SOLVENCY_MAIN",
    name: str = "偿付能力充足率指标",
    pages=(1,),
    note: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        table_id=table_id,
        table_name=name,
        pages=list(pages),
        rule_note=note,
        table_config={},
    )


def _rule(tables=None, warnings=()) -> SimpleNamespace:
    return SimpleNamespace(
        label="测试公司（rule v1）",
        source="测试公司.yaml",
        warnings=tuple(warnings),
        tables=dict(tables or {}),
    )


def test_rule_not_found_when_no_config() -> None:
    rows = build_traces(
        resolved_rule=None,
        matches=[_match(pages=[])],
        logs=[],
        tables=[],
    )
    assert rows[0]["状态"] == "缺失"
    assert rows[0]["原因码"] == "RULE_NOT_FOUND"
    assert "未找到公司规则" in rows[0]["未命中原因"]


def test_anchor_not_found() -> None:
    rows = build_traces(
        resolved_rule=_rule(),
        matches=[_match(pages=[], note="规则窗口 10-14 未命中锚点（strict），标记缺失")],
        logs=[],
        tables=[],
    )
    assert rows[0]["原因码"] == "ANCHOR_NOT_FOUND"
    assert "未命中锚点" in rows[0]["未命中原因"]


def test_signature_missing_when_page_failed() -> None:
    rows = build_traces(
        resolved_rule=_rule(),
        matches=[_match(pages=[13])],
        logs=[
            {
                "目标表": "整页全表",
                "物理页码": 13,
                "状态": "失败",
                "说明": "整页全表提取未识别到可用表格",
            }
        ],
        tables=[],
    )
    assert rows[0]["状态"] == "缺失"
    assert rows[0]["原因码"] == "SIGNATURE_MISSING"
    assert "未识别到可用表格" in rows[0]["未命中原因"]


def test_required_item_missing() -> None:
    table = SimpleNamespace(
        table_id="NON_LIFE_OPERATING_METRICS",
        rows=[["指标名称", "本季度数"], ["保险业务收入", "100"]],
    )
    rule = _rule(
        tables={
            "NON_LIFE_OPERATING_METRICS": {
                "required_items": ["保险业务收入", "净利润", "综合成本率"]
            }
        }
    )
    rows = build_traces(
        resolved_rule=rule,
        matches=[
            _match(
                table_id="NON_LIFE_OPERATING_METRICS",
                name="主要经营指标",
                pages=[17],
            )
        ],
        logs=[{"目标表": "整页全表", "物理页码": 17, "状态": "成功", "说明": ""}],
        tables=[table],
    )
    assert rows[0]["状态"] == "部分成功"
    assert rows[0]["原因码"] == "REQUIRED_ITEM_MISSING"
    assert "净利润" in rows[0]["未命中原因"]
    assert "综合成本率" in rows[0]["未命中原因"]


def test_year_unknown_warning_is_attached() -> None:
    rows = build_traces(
        resolved_rule=_rule(warnings=("YEAR_UNKNOWN：封面未识别报告年度，仅应用公司级主体规则。",)),
        matches=[_match(pages=[])],
        logs=[],
        tables=[],
    )
    assert "未识别报告年度" in rows[0]["未命中原因"]


def test_build_diagnostics_only_for_failures() -> None:
    success = {
        "目标表": "偿付能力充足率指标",
        "物理页码": "17",
        "状态": "成功",
        "未命中原因": "",
    }
    failure = {
        "目标表": "S05-最低资本表",
        "物理页码": "42",
        "状态": "缺失",
        "未命中原因": "页面未识别到目标表结构",
    }
    rows = build_diagnostics([success, failure])
    assert len(rows) == 1
    assert rows[0]["状态"] == "来源缺失"
    assert rows[0]["原因"] == "页面未识别到目标表结构"


def test_trace_reason_map() -> None:
    traces = [
        {"目标表": "A", "状态": "成功", "未命中原因": ""},
        {"目标表": "B", "状态": "缺失", "未命中原因": "未找到表标题锚点"},
    ]
    mapping = trace_reason_map(traces)
    assert mapping == {"B": "未找到表标题锚点"}


if __name__ == "__main__":
    test_rule_not_found_when_no_config()
    test_anchor_not_found()
    test_signature_missing_when_page_failed()
    test_required_item_missing()
    test_year_unknown_warning_is_attached()
    test_build_diagnostics_only_for_failures()
    test_trace_reason_map()
    print("test_extraction_trace: OK")
