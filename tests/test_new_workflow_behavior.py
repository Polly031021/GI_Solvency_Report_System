# -*- coding: utf-8 -*-
"""回归测试：Step2 整页全表、默认智能模式、财险公司分类与 Profile 刷新。"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import fitz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services import solvency_rule_table_extractor as router  # noqa: E402
from services.batch_extraction import FlexibleTable  # noqa: E402
from services.solvency_company_identity import (  # noqa: E402
    load_company_master_workbook,
    resolve_peer_group,
)
from services.solvency_normalizer import (  # noqa: E402
    load_taxonomy,
    resolve_metric_match,
)
from services.solvency_step3_standardizer import (  # noqa: E402
    _expand_table_blocks,
    standardize_to_target,
    step3_metric_catalog,
)
from ui.cache import profile_registry_token  # noqa: E402
from ui.session import new_state_defaults  # noqa: E402
from ui.step2_extraction import (  # noqa: E402
    _frame_matches_target,
    _page_table_key_parts,
    _target_frame_terms,
)


def _draw_table(page, rows, *, y0: float) -> None:
    x0, col_w, row_h = 60.0, 150.0, 24.0
    width = col_w * len(rows[0])
    for index in range(len(rows) + 1):
        y = y0 + index * row_h
        page.draw_line(fitz.Point(x0, y), fitz.Point(x0 + width, y))
    for index in range(len(rows[0]) + 1):
        x = x0 + index * col_w
        page.draw_line(fitz.Point(x, y0), fitz.Point(x, y0 + row_h * len(rows)))
    for row_index, row in enumerate(rows):
        for col_index, cell in enumerate(row):
            page.insert_text(
                fitz.Point(
                    x0 + col_index * col_w + 5,
                    y0 + row_index * row_h + 16,
                ),
                str(cell),
                fontname="china-s",
                fontsize=9,
            )


def build_three_table_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    _draw_table(
        page,
        [
            ["指标名称", "本季度数", "本年度累计数"],
            ["保险业务收入", "964,379,502.91", "7,362,400,072.66"],
            ["净利润", "61,793,719.10", "274,176,547.39"],
        ],
        y0=100,
    )
    _draw_table(
        page,
        [
            ["指标名称", "本季度末数", "上季度末数"],
            ["认可资产", "1,234,567.89", "1,100,000.00"],
            ["综合偿付能力充足率", "144.86%", "125.00%"],
        ],
        y0=250,
    )
    _draw_table(
        page,
        [
            ["流动性风险监管指标", "本季度数", "上季度可比数"],
            ["基本情景 LCR1", "111.47%", "105.83%"],
        ],
        y0=370,
    )
    return doc.tobytes()


def test_defaults_are_auto_and_whole_page() -> None:
    defaults = new_state_defaults()
    assert router.DEFAULT_METHOD == router.MODE_AUTO
    assert defaults["extraction_method"] == router.MODE_AUTO
    assert defaults["extract_whole_page"] is True
    print("PASS Step2 默认智能模式 + 整页全表")


def test_whole_page_rule_keeps_every_table() -> None:
    pdf = build_three_table_pdf()
    outcome = router.extract_page_with_method(
        pdf_bytes=pdf,
        page_number=1,
        table_id="SOLVENCY_MAIN",
        table_name="偿付能力充足率指标",
        method=router.MODE_RULE,
        has_text=True,
        whole_page=True,
    )
    assert outcome.succeeded, outcome.note
    assert len(outcome.frames) == 3, outcome.note
    text = "\n".join(frame.astype(str).to_string() for frame in outcome.frames)
    assert "保险业务收入" in text
    assert "认可资产" in text
    assert "LCR1" in text
    assert "未做目标表边界裁剪" in outcome.note
    print("PASS 整页全表保留三张表")


def test_company_master_maps_short_and_full_names() -> None:
    master = load_company_master_workbook(
        ROOT / "财险公司列表.xlsx",
        alias_workbook=ROOT / "config" / "non_life_solvency_company_urls.xlsx",
    )
    assert resolve_peer_group("人保财险.pdf", master.peer_group_map) == "前十大"
    assert (
        resolve_peer_group(
            "中国人民财产保险股份有限公司.pdf",
            master.peer_group_map,
        )
        == "前十大"
    )
    assert resolve_peer_group("紫金财产.pdf", master.peer_group_map) == "大型公司"
    assert master.company_type_map["人保财险"] == "财险"
    print("PASS 财险公司短名称/全称分类")


def test_page_tables_are_assigned_to_their_target() -> None:
    """同页多目标表时，流动性表不能被主表去重掉。"""
    main_rows = [
        ["指标名称", "本季度末数", "上季度末数"],
        ["认可资产", "100", "90"],
        ["综合偿付能力充足率", "150%", "140%"],
    ]
    liquidity_rows = [
        ["指标名称", "本季度数", "上季度数"],
        ["综合退保率", "5.2%", "4.8%"],
        ["融资杠杆比例", "1.2", "1.1"],
    ]
    main_match = SimpleNamespace(
        table_id="SOLVENCY_MAIN",
        table_name="偿付能力充足率指标",
        table_config={},
    )
    liquidity_match = SimpleNamespace(
        table_id="LIQUIDITY_MONITORING",
        table_name="流动性风险监测指标",
        table_config={},
    )
    main_terms = _target_frame_terms(main_match, "SOLVENCY_MAIN")
    liquidity_terms = _target_frame_terms(
        liquidity_match,
        "LIQUIDITY_MONITORING",
    )
    assert _frame_matches_target(main_rows, main_terms)
    assert not _frame_matches_target(liquidity_rows, main_terms)
    assert _frame_matches_target(liquidity_rows, liquidity_terms)
    assert not _frame_matches_target(main_rows, liquidity_terms)
    print("PASS 同页主表与流动性表分别归属各自目标")


def test_combined_table_keeps_page_blocks_for_step3() -> None:
    blocks = [
        {
            "page": 1,
            "rows": [
                ["指标名称", "本季度数", "本年度累计数"],
                ["保险业务收入", "964,379,502.91", "7,362,400,072.66"],
            ],
        },
        {
            "page": 2,
            "rows": [
                ["指标名称", "本季度末数", "上季度末数"],
                ["认可资产", "1,234,567.89", "1,100,000.00"],
            ],
        },
    ]
    table = FlexibleTable(
        table_id="SOLVENCY_MAIN",
        table_name="偿付能力充足率指标",
        page=1,
        source_pages=[1, 2],
        rows=[["合并展示表头"], ["合并展示数据"]],
        table_blocks=blocks,
        unit="元",
        unit_source="元",
    )
    expanded = _expand_table_blocks([table])
    assert len(expanded) == 2
    assert expanded[0].source_pages == [1]
    assert expanded[1].source_pages == [2]

    taxonomy = load_taxonomy(str(ROOT / "config" / "solvency_taxonomy.xlsx"))
    catalog = step3_metric_catalog(taxonomy, include_derived=False)
    master = load_company_master_workbook(ROOT / "财险公司列表.xlsx")
    result = standardize_to_target(
        [table],
        taxonomy,
        {"公司": "人保财险", "报告期": "2025Q4", "来源文件": "人保财险.pdf"},
        "财险",
        catalog,
        report_profile_id="NON_LIFE_SOLVENCY",
        allowed_company_types=("财险",),
        peer_group_map=master.peer_group_map,
        default_peer_group=master.default_peer_group,
        company_type_map=master.company_type_map,
        include_derived=False,
    )
    revenue = result.data[result.data["指标编码"] == "INSURANCE_REVENUE"]
    assert set(revenue["期间口径"]) == {"本季度数", "本年累计数"}, revenue
    assert set(result.data["同业分类"]) == {"前十大"}, result.data["同业分类"]
    print("PASS 合并中间表内部按页内表块识别期间")


def test_profile_token_and_whole_page_keys() -> None:
    token = profile_registry_token()
    assert "NON_LIFE_SOLVENCY_profile.xlsx" in token
    assert "~$" not in token
    base, index = _page_table_key_parts("SOLVENCY_MAIN__PAGE_3__ALL2")
    assert base == "SOLVENCY_MAIN"
    assert index == 2
    assert _page_table_key_parts("SOLVENCY_MAIN") == ("SOLVENCY_MAIN", None)
    print("PASS Profile 指纹与整页结果键")


def test_blank_capital_components_are_zero_filled() -> None:
    """实际资本表中的四层资本明细，空白单元格应按 0 生成记录。"""
    table = FlexibleTable(
        table_id="ACTUAL_CAPITAL",
        table_name="S02 实际资本",
        page=1,
        rows=[
            ["项目", "本季度末数", "上季度末数"],
            ["核心一级资本", "1,000", "900"],
            ["核心二级资本", "", ""],
            ["附属一级资本", "50", "40"],
            ["附属二级资本", "", "20"],
        ],
        unit="万元",
    )
    taxonomy = load_taxonomy(str(ROOT / "config" / "solvency_taxonomy.xlsx"))
    catalog = step3_metric_catalog(taxonomy, include_derived=False)
    result = standardize_to_target(
        [table],
        taxonomy,
        {"公司": "测试人寿", "报告期": "2025Q4", "来源文件": "测试人寿.pdf"},
        "寿险",
        catalog,
        report_profile_id="LIFE_SOLVENCY",
        allowed_company_types=("寿险", "健康险", "养老险"),
        include_derived=False,
    )
    rows = result.data[
        result.data["指标编码"].isin(
            ["CORE_T1_CAPITAL", "CORE_T2_CAPITAL", "ANC_T1_CAPITAL", "ANC_T2_CAPITAL"]
        )
    ]
    assert set(rows["指标编码"]) == {
        "CORE_T1_CAPITAL",
        "CORE_T2_CAPITAL",
        "ANC_T1_CAPITAL",
        "ANC_T2_CAPITAL",
    }
    assert (rows["数值"] == 0).sum() == 3, rows[["指标编码", "期间口径", "数值"]]
    assert rows["备注"].astype(str).str.contains("资本明细空白，按0处理").any()
    summary = result.target_summary[result.target_summary["指标编码"] == "CORE_T2_CAPITAL"]
    assert summary["填报状态"].iloc[0] == "已填报"
    print("PASS 资本明细空白按0生成记录")


def test_lcr_period_wording_is_normalized() -> None:
    """LCR1/LCR3 的“三个月/一年”应匹配到未来3个月/12个月口径。"""
    taxonomy = load_taxonomy(str(ROOT / "config" / "solvency_taxonomy.xlsx"))
    cases = {
        "流动性覆盖率 LCR1（三个月）（%）": "LCR1_3M",
        "流动性覆盖率 LCR1（一年）（%）": "LCR1_12M",
        "LCR3（3个月）": "LCR3_3M",
        "LCR3（12个月）": "LCR3_12M",
    }
    for label, expected in cases.items():
        decision = resolve_metric_match(
            label,
            taxonomy,
            table_id="LIQUIDITY_MONITORING",
        )
        assert decision.metric is not None, (label, decision.reason)
        assert str(decision.metric.get("指标编码")) == expected, (
            label,
            decision.metric.get("指标编码"),
        )
    print("PASS LCR期限写法归一化匹配")


if __name__ == "__main__":
    test_defaults_are_auto_and_whole_page()
    test_whole_page_rule_keeps_every_table()
    test_company_master_maps_short_and_full_names()
    test_page_tables_are_assigned_to_their_target()
    test_combined_table_keeps_page_blocks_for_step3()
    test_profile_token_and_whole_page_keys()
    test_blank_capital_components_are_zero_filled()
    test_lcr_period_wording_is_normalized()
    print("全部通过")
