# -*- coding: utf-8 -*-
"""STEP3 表头恢复与跨页提取的回归测试。"""

from __future__ import annotations

from types import SimpleNamespace

from services.solvency_normalizer import load_taxonomy, normalize_tables
from services.solvency_step3_standardizer import _expand_table_blocks

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
