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
from services.semantic_period_fallback import resolve_period_labels_with_llm  # noqa: E402
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


def test_numeric_placeholders_are_missing_not_zero() -> None:
    """P0-1：空白/不适用/未披露/文本按缺失处理，不写假0；明确0仍保留。"""
    table = FlexibleTable(
        table_id="SOLVENCY_MAIN",
        table_name="偿付能力充足率指标",
        page=1,
        rows=[
            ["项目", "本季度末数", "上季度末数"],
            ["认可资产", "-", ""],
            ["认可负债", "不适用", "0.00"],
            ["实际资本", "未披露", "1,000"],
            ["综合偿付能力充足率", "文本", "150%"],
        ],
        unit="万元",
    )
    taxonomy = load_taxonomy(str(ROOT / "config" / "solvency_taxonomy.xlsx"))
    catalog = step3_metric_catalog(taxonomy, include_derived=False)
    result = standardize_to_target(
        [table],
        taxonomy,
        {"公司": "测试财险", "报告期": "2025Q4", "来源文件": "测试财险.pdf"},
        "财险",
        catalog,
        report_profile_id="NON_LIFE_SOLVENCY",
        allowed_company_types=("财险",),
        include_derived=False,
    )
    codes = set(result.data["指标编码"])
    assert "RECOGNIZED_ASSETS" not in codes
    assert codes == {
        "RECOGNIZED_LIABILITIES",
        "ACTUAL_CAPITAL",
        "COMBINED_SOLVENCY_RATIO",
    }
    assert set(result.data["期间口径"]) == {"上季度末数"}
    assert not result.data["数值"].isna().any()
    assert float(
        result.data[result.data["指标编码"] == "RECOGNIZED_LIABILITIES"]["数值"].iloc[0]
    ) == 0.0
    assert float(
        result.data[result.data["指标编码"] == "ACTUAL_CAPITAL"]["数值"].iloc[0]
    ) == 1000.0

    missing = result.diagnostics[result.diagnostics["状态"] == "值为空"]
    assert len(missing) == 5, missing[["指标编码", "原始项目", "原因"]]
    assert set(missing["指标编码"]) == {
        "RECOGNIZED_ASSETS",
        "RECOGNIZED_LIABILITIES",
        "ACTUAL_CAPITAL",
        "COMBINED_SOLVENCY_RATIO",
    }

    summary = result.target_summary.set_index("指标编码")
    assert "缺失值单元格数" in summary.columns
    assert int(summary.loc["RECOGNIZED_ASSETS", "缺失值单元格数"]) == 2
    assert int(summary.loc["RECOGNIZED_LIABILITIES", "缺失值单元格数"]) == 1
    assert summary.loc["RECOGNIZED_ASSETS", "填报状态"] == "未填报"
    assert summary.loc["RECOGNIZED_ASSETS", "诊断状态"] == "值为空"
    assert any("按缺失处理（未写0）" in warning for warning in result.warnings)
    print("PASS 空白/占位符按缺失处理，不写假0")


def test_supplement_rows_keep_company_and_report_period() -> None:
    """补漏匹配在 normalizer 未产出任何记录时也必须继承报告元数据。"""
    table = FlexibleTable(
        table_id="ACTUAL_CAPITAL",
        table_name="S02-实际资本明细表",
        page=33,
        rows=[
            ["项目", "列2"],
            ["实际资本", "2,469,438"],
        ],
        unit="万元",
    )
    taxonomy = load_taxonomy(str(ROOT / "config" / "solvency_taxonomy.xlsx"))
    catalog = step3_metric_catalog(taxonomy, include_derived=False)
    result = standardize_to_target(
        [table],
        taxonomy,
        {
            "公司": "测试财险",
            "报告期": "2025Q4",
            "报告年度": 2025,
            "报告季度": "Q4",
            "来源文件": "测试财险.pdf",
        },
        "财险",
        catalog,
        report_profile_id="NON_LIFE_SOLVENCY",
        allowed_company_types=("财险",),
        include_derived=False,
    )
    row = result.data[result.data["指标编码"].eq("ACTUAL_CAPITAL")].iloc[0]
    assert row["公司"] == "测试财险"
    assert row["报告期"] == "2025Q4"
    assert row["数值"] == 2_469_438
    assert "补漏匹配" in str(row["备注"])
    print("PASS 补漏匹配记录继承公司和报告期")


def test_source_unit_is_converted_to_standard_unit() -> None:
    """Step3 必须把元等来源单位统一换算为字典标准单位万元。"""
    table = FlexibleTable(
        table_id="ACTUAL_CAPITAL",
        table_name="S02-实际资本明细表",
        page=1,
        rows=[
            ["行次", "项目", "本季度末数", "上季度末数"],
            ["1", "实际资本", "10,000", "20,000"],
        ],
        unit="元",
        unit_source="元",
    )
    taxonomy = load_taxonomy(str(ROOT / "config" / "solvency_taxonomy.xlsx"))
    catalog = step3_metric_catalog(taxonomy, include_derived=False)
    result = standardize_to_target(
        [table],
        taxonomy,
        {"公司": "测试财险", "报告期": "2025Q4", "来源文件": "测试财险.pdf"},
        "财险",
        catalog,
        report_profile_id="NON_LIFE_SOLVENCY",
        allowed_company_types=("财险",),
        include_derived=False,
    )
    row = result.data[result.data["指标编码"].eq("ACTUAL_CAPITAL")].iloc[0]
    assert row["数值"] == 1
    assert row["单位"] == "万元"
    assert "已换算为万元" in str(row["备注"])
    print("PASS 来源单位元统一换算为万元")


def test_semantic_period_aliases_and_three_year_average() -> None:
    taxonomy = load_taxonomy(str(ROOT / "config" / "solvency_taxonomy.xlsx"))
    catalog = step3_metric_catalog(taxonomy, include_derived=False)
    annual_table = FlexibleTable(
        table_id="OPERATING_METRICS",
        table_name="主要经营指标（财险）",
        page=1,
        rows=[
            ["指标名称", "本年度（末）"],
            ["保险业务收入", "1,000"],
        ],
        unit="万元",
    )
    result = standardize_to_target(
        [annual_table],
        taxonomy,
        {"公司": "测试财险", "报告期": "2025Q4", "来源文件": "测试财险.pdf"},
        "财险",
        catalog,
        report_profile_id="NON_LIFE_SOLVENCY",
        allowed_company_types=("财险",),
        include_derived=False,
    )
    annual_row = result.data[result.data["指标编码"].eq("INSURANCE_REVENUE")].iloc[0]
    assert annual_row["期间口径"] == "本年累计数"

    three_year_table = FlexibleTable(
        table_id="THREE_YEAR_INVESTMENT_RETURN",
        table_name="近三年（综合）投资收益率",
        page=1,
        rows=[
            ["项目", "近三年平均"],
            ["近三年平均投资收益率", "1.23%"],
        ],
        unit="%",
    )
    result = standardize_to_target(
        [three_year_table],
        taxonomy,
        {"公司": "测试财险", "报告期": "2025Q4", "来源文件": "测试财险.pdf"},
        "财险",
        catalog,
        report_profile_id="NON_LIFE_SOLVENCY",
        allowed_company_types=("财险",),
        include_derived=False,
    )
    three_year_row = result.data[
        result.data["指标编码"].eq("INVESTMENT_RETURN")
        & result.data["期间口径"].eq("近三年平均")
    ]
    assert not three_year_row.empty
    print("PASS 本年度（末）语义归一及近三年平均指标")


def test_unknown_period_can_use_llm_semantic_fallback() -> None:
    table = FlexibleTable(
        table_id="OPERATING_METRICS",
        table_name="主要经营指标（财险）",
        page=1,
        rows=[
            ["指标名称", "观察窗口X"],
            ["保险业务收入", "1,000"],
        ],
        unit="万元",
    )

    def post_func(url, *, headers=None, json=None, timeout=None):
        content = '{"观察窗口X":"本年累计数"}'
        return SimpleNamespace(
            ok=True,
            status_code=200,
            headers={},
            json=lambda: {
                "choices": [
                    {"message": {"content": content}}
                ]
            },
        )

    mapping, message = resolve_period_labels_with_llm(
        [table],
        api_key="test-key",
        base_url="https://example.invalid/v1",
        model="test-model",
        post_func=post_func,
    )
    assert mapping == {"观察窗口X": "本年累计数"}
    assert "1 个表头" in message
    print("PASS 本地无法识别期间时的大模型语义兜底")


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


def test_quant_risk_before_feature_factor_is_distinct() -> None:
    taxonomy = load_taxonomy(str(ROOT / "config" / "solvency_taxonomy.xlsx"))
    base = resolve_metric_match(
        "量化风险最低资本",
        taxonomy,
        table_id="MINIMUM_CAPITAL",
    )
    before_factor = resolve_metric_match(
        "量化风险最低资本（未考虑特征系数前）",
        taxonomy,
        table_id="MINIMUM_CAPITAL",
    )
    assert str(base.metric.get("指标编码")) == "QUANT_RISK_CAPITAL"
    assert (
        str(before_factor.metric.get("指标编码"))
        == "QUANT_RISK_CAPITAL_BEFORE_FEATURE_FACTOR"
    )
    print("PASS 量化风险最低资本与未考虑特征系数前分开映射")


if __name__ == "__main__":
    test_defaults_are_auto_and_whole_page()
    test_whole_page_rule_keeps_every_table()
    test_company_master_maps_short_and_full_names()
    test_page_tables_are_assigned_to_their_target()
    test_combined_table_keeps_page_blocks_for_step3()
    test_profile_token_and_whole_page_keys()
    test_blank_capital_components_are_zero_filled()
    test_numeric_placeholders_are_missing_not_zero()
    test_supplement_rows_keep_company_and_report_period()
    test_source_unit_is_converted_to_standard_unit()
    test_semantic_period_aliases_and_three_year_average()
    test_unknown_period_can_use_llm_semantic_fallback()
    test_lcr_period_wording_is_normalized()
    test_quant_risk_before_feature_factor_is_distinct()
    print("全部通过")
