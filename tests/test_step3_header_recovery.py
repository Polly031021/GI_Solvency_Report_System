# -*- coding: utf-8 -*-
"""STEP3 表头恢复与跨页提取的回归测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from services.solvency_normalizer import (
    STANDARD_COLUMNS,
    load_taxonomy,
    normalize_tables,
    parse_numeric,
)
from services.solvency_step3_standardizer import (
    _deduplicate_candidates,
    _expand_table_blocks,
    _target_summary,
    step3_metric_catalog,
)
from services.solvency_table_extractor import _reconstruct_logical_columns

TAXONOMY = load_taxonomy("config/solvency_taxonomy.xlsx")


def _table(rows, *, table_id="ACTUAL_CAPITAL", blocks=None):
    return SimpleNamespace(
        table_id=table_id,
        table_name=table_id,
        page=1,
        rows=rows,
        source_pages=[1],
        table_config={},
        unit="万元",
        unit_records=[],
        table_blocks=blocks or [],
        whole_page_block=False,
    )


def _run(rows, *, table_id="ACTUAL_CAPITAL", blocks=None):
    tables = _expand_table_blocks([_table(rows, table_id=table_id, blocks=blocks)], TAXONOMY)
    return normalize_tables(
        tables,
        TAXONOMY,
        {"公司": "测试公司", "报告期": "2025Q4", "报告年度": 2025, "报告季度": "Q4"},
        "财险",
        allowed_company_types=("寿险", "健康险", "养老险", "财险"),
        report_profile_id="NON_LIFE_SOLVENCY",
    )


def test_title_row_before_header_is_skipped():
    frame = _run(
        [
            ["S02-实际资本明细表"],
            ["行次", "项目", "期末数", "期初数"],
            ["1", "核心一级资本合计", "100", "90"],
        ]
    )
    assert len(frame) == 2
    assert set(frame["期间口径"]) == {"本季度末数", "上季度末数"}


def test_cross_page_block_inherits_header():
    header = ["行次", "项目", "期末数", "期初数"]
    frame = _run(
        [header, ["1", "核心一级资本合计", "100", "90"]],
        blocks=[
            {"page": 1, "rows": [header, ["1", "核心一级资本合计", "100", "90"]]},
            {"page": 2, "rows": [["2", "核心一级资本合计", "200", "180"]]},
        ],
    )
    assert len(frame) == 4
    assert 200.0 in frame["数值"].astype(float).tolist()


def test_headerless_block_uses_canonical_header():
    frame = _run([["1", "核心一级资本合计", "100", "90"]])
    assert len(frame) == 2
    assert set(frame["期间口径"]) == {"本季度末数", "上季度末数"}


def test_date_and_generic_amount_headers():
    data = ["1", "核心一级资本合计", "100", "90"]
    dated = _run([["项目", "2024年12月31日", "2023年12月31日"], data])
    assert set(dated["期间口径"]) == {"本季度末数", "上季度末数"}
    generic = _run([["项目", "金额"], data])
    assert len(generic) == 2


def test_wrapped_label_with_value_row_is_joined():
    frame = _run(
        [
            ["行次", "项目", "期末数", "期初数"],
            ["1", "核心一级资", "", ""],
            ["", "本合计", "300", "280"],
        ]
    )
    assert len(frame) == 2
    assert set(frame["数值"].astype(float)) == {300.0, 280.0}


def test_exact_label_recovers_cross_table_metric():
    frame = _run(
        [["项目", "本季度末数"], ["综合偿付能力充足率", "188.5"]],
        table_id="ACTUAL_CAPITAL",
    )
    assert len(frame) == 1
    assert frame.iloc[0]["指标编码"] == "COMBINED_SOLVENCY_RATIO"


def test_dual_header_audit_qualifiers_are_distinct():
    """期末/期初 × 审计后/审计前四列必须保留四个不同期间口径。"""
    frame = _run(
        [
            ["项目", "期末数-审计后", "期末数-审计前", "期初数-审计后", "期初数-审计前"],
            ["核心一级资本", "100", "110", "90", "95"],
        ]
    )
    assert len(frame) == 4
    assert set(frame["期间口径"]) == {
        "本季度末数-审计后",
        "本季度末数-审计前",
        "上季度末数-审计后",
        "上季度末数-审计前",
    }


def test_cross_page_units_are_applied_per_block():
    """第一页万元、第二页元、第三页无单位，按块换算并继承上一块单位。"""
    frame = _run(
        [["placeholder"]],
        blocks=[
            {
                "page": 1,
                "rows": [
                    ["项目", "本季度末数"],
                    ["单位：万元", ""],
                    ["核心一级资本", "100"],
                ],
            },
            {
                "page": 2,
                "rows": [
                    ["项目", "本季度末数"],
                    ["单位：元", ""],
                    ["核心一级资本", "2000000"],
                ],
            },
            {
                "page": 3,
                "rows": [["核心一级资本", "3000000"]],
            },
        ],
    )
    by_page = {
        str(row["来源页码"]): float(row["数值"])
        for _, row in frame.iterrows()
    }
    assert by_page == {"1": 100.0, "2": 200.0, "3": 300.0}, by_page


def test_manual_edit_rebuilds_blocks_for_step3():
    """P0-4：手工编辑后仍保留可定位表头，Step3 按新表头提取。"""
    from services.batch_extraction import FlexibleTable
    from ui.step2_extraction import persist_table_rows

    table = FlexibleTable(
        table_id="ACTUAL_CAPITAL",
        table_name="S02 实际资本",
        page=1,
        rows=[
            ["项目", "本季度末数", "上季度末数"],
            ["核心一级资本", "100", "90"],
        ],
        unit="万元",
    )
    persist_table_rows(
        table,
        [
            ["项目", "本季度数", "本年累计数"],
            ["核心一级资本", "111", "222"],
        ],
    )
    assert table.table_blocks, "编辑后必须重建 table_blocks"
    assert table.table_blocks[0]["rows"][0] == [
        "项目", "本季度数", "本年累计数"
    ]

    tables = _expand_table_blocks([table], TAXONOMY)
    frame = normalize_tables(
        tables,
        TAXONOMY,
        {"公司": "测试公司", "报告期": "2025Q4", "报告年度": 2025, "报告季度": "Q4"},
        "财险",
        allowed_company_types=("寿险", "健康险", "养老险", "财险"),
        report_profile_id="NON_LIFE_SOLVENCY",
    )
    assert set(frame["期间口径"]) == {"本季度末数", "本年累计数"}
    assert set(frame["数值"].astype(float)) == {111.0, 222.0}


def test_candidate_scoring_keeps_best_and_reports_conflict():
    """P1：去重按候选得分保留正常披露值，并记录口径冲突。"""
    data = pd.DataFrame([
        {
            "公司统一编码": "C001",
            "报告期": "2025Q4",
            "指标编码": "CORE_T1_CAPITAL",
            "期间口径": "本季度末数",
            "数值": 999.0,
            "备注": "补漏匹配：原始标签'核心一级资本'",
            "来源工作表": "S02 实际资本",
            "来源页码": "10",
            "指标名称": "核心一级资本",
            "行次": "",
            "单位": "万元",
        },
        {
            "公司统一编码": "C001",
            "报告期": "2025Q4",
            "指标编码": "CORE_T1_CAPITAL",
            "期间口径": "本季度末数",
            "数值": 100.0,
            "备注": "",
            "来源工作表": "S02 实际资本",
            "来源页码": "10",
            "指标名称": "核心一级资本",
            "行次": "1",
            "单位": "万元",
        },
        {
            "公司统一编码": "C001",
            "报告期": "2025Q4",
            "指标编码": "CORE_T1_CAPITAL",
            "期间口径": "本季度末数",
            "数值": 100.0,
            "备注": "补漏匹配",
            "来源工作表": "S05 最低资本",
            "来源页码": "11",
            "指标名称": "核心一级资本",
            "行次": "",
            "单位": "万元",
        },
        {
            "公司统一编码": "C001",
            "报告期": "2025Q4",
            "指标编码": "ACTUAL_CAPITAL",
            "期间口径": "本季度末数",
            "数值": 50.0,
            "备注": "补漏匹配",
            "来源工作表": "S02 实际资本",
            "来源页码": "10",
            "指标名称": "实际资本",
            "行次": "",
            "单位": "万元",
        },
        {
            "公司统一编码": "C001",
            "报告期": "2025Q4",
            "指标编码": "ACTUAL_CAPITAL",
            "期间口径": "本季度末数",
            "数值": 50.0,
            "备注": "",
            "来源工作表": "S02 实际资本",
            "来源页码": "10",
            "指标名称": "实际资本",
            "行次": "2",
            "单位": "万元",
        },
    ])
    diagnostics: list[dict] = []
    kept, removed, conflicts = _deduplicate_candidates(data, diagnostics)
    assert len(kept) == 2
    assert removed == 3
    assert conflicts == 1
    core = kept[kept["指标编码"] == "CORE_T1_CAPITAL"].iloc[0]
    assert float(core["数值"]) == 100.0
    assert str(core["备注"]) == ""
    assert diagnostics[0]["状态"] == "口径冲突"
    assert "999" in diagnostics[0]["原因"]


def test_summary_diagnostic_status_is_refined():
    """P1：未填报目标按诊断细分为值为空/未匹配/期间未识别/歧义待确认。"""
    catalog = pd.DataFrame([
        {"指标编码": "A", "指标名称": "A", "一级模块": "M", "二级模块": "", "指标属性": "披露"},
        {"指标编码": "B", "指标名称": "B", "一级模块": "M", "二级模块": "", "指标属性": "披露"},
        {"指标编码": "C", "指标名称": "C", "一级模块": "M", "二级模块": "", "指标属性": "披露"},
        {"指标编码": "D", "指标名称": "D", "一级模块": "M", "二级模块": "", "指标属性": "披露"},
    ])
    diagnostics = pd.DataFrame([
        {"指标编码": "A", "状态": "值为空"},
        {"指标编码": "B", "状态": "未匹配"},
        {"指标编码": "C", "状态": "期间未识别"},
        {"指标编码": "D", "状态": "歧义待确认"},
    ])
    summary = _target_summary(
        catalog,
        pd.DataFrame(columns=STANDARD_COLUMNS),
        diagnostics,
    ).set_index("指标编码")
    assert summary.loc["A", "诊断状态"] == "值为空"
    assert summary.loc["B", "诊断状态"] == "未匹配"
    assert summary.loc["C", "诊断状态"] == "期间未识别"
    assert summary.loc["D", "诊断状态"] == "歧义待确认"
    assert set(summary["填报状态"]) == {"未填报"}


def test_parse_numeric_strips_footnotes_and_currency():
    """P2：脚注、圈号、货币符号不影响正常数字解析，负数括号保留。"""
    assert parse_numeric("1,234.56注1") == 1234.56
    assert parse_numeric("1,234.56*") == 1234.56
    assert parse_numeric("1,234.56①") == 1234.56
    assert parse_numeric("12.3%注1") == 12.3
    assert parse_numeric("￥1,234.56") == 1234.56
    assert parse_numeric("１２３４．５６") == 1234.56
    assert parse_numeric("1 234.56") == 1234.56
    assert parse_numeric("1,234.56[1]") == 1234.56
    assert parse_numeric("1,234.56(注1)") == 1234.56
    assert parse_numeric("(1,234.56)") == -1234.56
    assert parse_numeric("(1,234.56)注1") == -1234.56
    assert pd.isna(parse_numeric("不适用"))
    assert pd.isna(parse_numeric("—"))


def test_cross_page_header_inherits_with_missing_trailing_column():
    """P2：续页省略尾列时，继承表头按当前宽度补齐，值仍能进入标准数据。"""
    frame = _run(
        [["placeholder"]],
        blocks=[
            {
                "page": 1,
                "rows": [
                    ["行次", "项目", "期末数", "期初数"],
                    ["1", "核心一级资本", "100", "90"],
                ],
            },
            {
                "page": 2,
                "rows": [["2", "核心一级资本", "200"]],
            },
        ],
    )
    assert len(frame) == 3, frame
    assert 200.0 in set(frame["数值"].astype(float))


def test_precision_only_difference_is_not_a_conflict():
    """同一指标仅小数位精度不同时按同值处理，保留更高精度的一条。"""
    data = pd.DataFrame([
        {
            "公司统一编码": "C001", "报告期": "2025Q4",
            "指标编码": "CORE_T1_CAPITAL", "期间口径": "本季度末数",
            "数值": 91778.79, "备注": "", "来源工作表": "S02",
            "来源页码": "10", "指标名称": "核心一级资本",
            "行次": "1", "单位": "万元",
        },
        {
            "公司统一编码": "C001", "报告期": "2025Q4",
            "指标编码": "CORE_T1_CAPITAL", "期间口径": "本季度末数",
            "数值": 91778.792102, "备注": "", "来源工作表": "S02",
            "来源页码": "18", "指标名称": "核心一级资本",
            "行次": "1", "单位": "万元",
        },
    ])
    diagnostics: list[dict] = []
    kept, removed, conflicts = _deduplicate_candidates(data, diagnostics)
    assert len(kept) == 1
    assert removed == 1
    assert conflicts == 0
    assert float(kept.iloc[0]["数值"]) == 91778.792102
    assert diagnostics == []


def test_recognition_subheaders_become_distinct_periods():
    """认可价值/账面价值子表头不能被当成同一个本季度数。"""
    frame = _run(
        [
            ["项目", "本季度数/账面价值", "本季度数/非认可价值", "本季度数/认可价值"],
            ["核心一级资本", "100", "10", "90"],
        ]
    )
    periods = set(frame["期间口径"])
    assert "本季度末数" in periods
    assert "本季度末数-账面价值" in periods
    assert "本季度末数-非认可" in periods


def test_period_subheader_table_is_reconstructed():
    """双层表头（期间 × 账面/非认可/认可）重建为规范 8 列。"""
    rows = [
        [
            "行次账面价值非认可价值认可价值账面价值非认可价值认可价值",
            "项目", "本季度数", "", "", "上季度数", "", "",
        ],
        ["1", "现金及流动性管理工具", "100", "-", "100", "90", "-", "90"],
        ["1.1", "库存现金", "-", "-", "-", "-", "-", "-"],
    ]
    rebuilt = _reconstruct_logical_columns(rows)
    assert rebuilt[0] == [
        "行次", "项目",
        "本季度数/账面价值", "本季度数/非认可价值", "本季度数/认可价值",
        "上季度数/账面价值", "上季度数/非认可价值", "上季度数/认可价值",
    ]
    assert rebuilt[1][:3] == ["1", "现金及流动性管理工具", "100"]
