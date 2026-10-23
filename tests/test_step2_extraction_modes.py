# -*- coding: utf-8 -*-
"""STEP2 提取改造的回归测试（离线，不访问网络）。

覆盖四个关键行为：
1. 千分位误识别修复：多组三位分段自动还原，单一分段用原文佐证，百分比不动。
2. 规则法离线提取：文字版页面由 pdfplumber 线框识别重建目标表。
3. 智能模式路由：文字版页面零模型调用；扫描版页面直接走图片识别；
   规则法未通过时自动转文本大模型。
4. 中间表编辑：单元格改动、行增删写回表格对象并刷新快照。
"""
import sys
from contextlib import contextmanager
from pathlib import Path

import fitz
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services import solvency_rule_table_extractor as router  # noqa: E402
from services.batch_extraction import FlexibleTable  # noqa: E402
from services.batch_extraction import detect_table_unit  # noqa: E402
from services.solvency_ai_table_extractor import PageGrid, build_pdf_grids  # noqa: E402
from services.table_number_repair import (  # noqa: E402
    build_number_index,
    repair_frame,
    repair_number_text,
    repair_thousand_separators,
)
from services.solvency_table_extractor import (  # noqa: E402
    _merge_number_fragment_columns,
    _merge_split_cjk_label,
    _merge_wrapped_table_rows,
    _reconstruct_logical_columns,
    _repair_operating,
)
from ui.step2_extraction import (  # noqa: E402
    _add_column,
    _delete_columns,
    _editor_frame,
    _rows_from_editor,
    normalize_table_unit,
    persist_table_rows,
    rows_to_frame,
)


# ============================================================
# 一、千分位误识别修复
# ============================================================
def test_repair_multi_group_thousand_separators():
    """多组三位分段不可能是合法小数，即使原文无证据也必须还原为千分位逗号。"""
    assert repair_number_text("1.234.567.89")[0] == "1,234,567.89"
    assert repair_number_text("1.234.567")[0] == "1,234,567"
    assert repair_number_text("(12.345.678.90)")[0] == "(12,345,678.90)"
    # 规范写法与常规小数不得被改动
    assert repair_number_text("1,234,567.89")[0] == "1,234,567.89"
    assert repair_number_text("1234.56")[0] == "1234.56"
    assert repair_number_text("2025")[0] == "2025"
    # 比率类不动
    assert repair_number_text("102.49%")[0] == "102.49%"
    assert repair_number_text("1.234%")[0] == "1.234%"
    print("PASS 千分位多段修复")


def test_repair_uses_source_comma_layout():
    """原文用逗号做千分位时，单元格丢失或错写分隔符都要按原文还原。"""
    source_text = (
        "认可资产 1,234,567.89 1,100,000.00 300,000.00 280,000.00 102.49%"
    )
    rows = [
        ["指标名称", "本季度末数", "上季度末数"],
        ["认可资产", "1.234.567.89", "1100000.00"],
        ["最低资本", "300,000.00", "280.000.00"],
        ["综合成本率", "102,49%", "98.10%"],
        ["其他金额", "555.55", "666.66"],
    ]
    outcome = repair_thousand_separators(rows, source_text)
    assert outcome.rows[1][1] == "1,234,567.89", outcome.rows  # 点 → 逗号
    assert outcome.rows[1][2] == "1,100,000.00", outcome.rows  # 原文本有逗号 → 补齐
    assert outcome.rows[2][1] == "300,000.00", outcome.rows  # 与原文一致 → 不改
    assert outcome.rows[2][2] == "280,000.00", outcome.rows  # 错写的分组 → 还原
    assert outcome.rows[3][1] == "102.49%", outcome.rows  # 百分号误写逗号 → 还原
    assert outcome.rows[4][1] == "555.55", outcome.rows  # 原文无此数值 → 保持原样
    assert outcome.changed
    assert "数值格式校正" in outcome.note
    # 已被改写过的千分位不会重复出现在修复记录里
    assert not any("300,000.00" in item for item in outcome.repairs), outcome.repairs
    print("PASS 按原文逗号版式校正:", outcome.note[:70])


def test_repair_handles_full_width_and_reconstruction():
    """全角逗号原文与千分位缺位的写法都要收敛到半角逗号。"""
    source_text = "保险业务收入（元） 1，234，567.89 987，654.32"
    rows = [
        ["指标名称", "本季度数", "本年度累计数"],
        ["保险业务收入（元）", "1.234.567.89", "987654.32"],
        ["已规范写法", "1,234,567.89", "987,654.32"],
    ]
    outcome = repair_thousand_separators(rows, source_text)
    assert outcome.rows[1][1] == "1,234,567.89", outcome.rows
    assert outcome.rows[1][2] == "987,654.32", outcome.rows
    # 原文本身用全角逗号且单元格已一致时，保留单元格写法，不做无意义的改写
    assert outcome.rows[2][1] == "1,234,567.89", outcome.rows
    frame, frame_outcome = repair_frame(pd.DataFrame(rows), source_text)
    assert frame.iloc[1, 1] == "1,234,567.89"
    assert frame_outcome.changed
    print("PASS 全角逗号与缺位分组还原:", outcome.note[:60])


def test_repair_single_group_needs_source_evidence():
    """单一三位分段（1.234）有歧义：只有原文出现该数值的千分位写法时才改写。"""
    index = build_number_index("认可资产 1,234.00 3,456.78")
    fixed, repair_note, review_note = repair_number_text("1.234", index)
    assert fixed == "1.234", fixed  # 小数位不同，不能凭位数猜
    assert not repair_note

    fixed2, repair_note2, _ = repair_number_text(
        "1.234",
        build_number_index("认可资产 1,234"),
    )
    assert fixed2 == "1,234", fixed2
    assert repair_note2

    kept, _, review_note2 = repair_number_text("1.234", build_number_index(""))
    assert kept == "1.234", kept
    assert "无法判断" in review_note2
    print("PASS 单一分段按原文佐证处理")


def test_repair_european_separators():
    """欧式写法（点做千分位、逗号做小数点）要还原为标准千分位格式。"""
    assert repair_number_text("12.345.678,90")[0] == "12,345,678.90"
    assert repair_number_text("1.234,56")[0] == "1,234.56"
    print("PASS 欧式千分位写法还原")


def test_text_alignment_split_label_is_repaired():
    """文字对齐把标签首字拆成独立列时，应重新合并为一个指标名称。"""
    rows = [
        ["1", "核", "心一级资本", "34,034,737,015.50", "33,827,747,615.99"],
        ["2", "认", "可资产", "7,798,723", "7,908,241"],
    ]
    repaired = _merge_split_cjk_label(rows)
    assert repaired[0] == [
        "1",
        "核心一级资本",
        "34,034,737,015.50",
        "33,827,747,615.99",
    ]
    assert repaired[1] == ["2", "认可资产", "7,798,723", "7,908,241"]
    print("PASS 文字对齐标签首字断列修复")


def test_wrapped_metric_rows_are_joined_without_swallowing_independent_rows():
    rows = [
        ["行次", "项目", "本季度末数", "上季度末数"],
        ["1", "量化风险最低资本（未考虑", "100", "90"],
        ["", "特征系数前）", "", ""],
        ["2", "最低资本", "200", "180"],
        ["", "其中：控制风险最低资本", "20", "18"],
    ]
    repaired, events = _merge_wrapped_table_rows(rows)
    assert repaired[1][1] == "量化风险最低资本（未考虑特征系数前）"
    assert repaired[2][1] == "最低资本"
    assert repaired[3][1] == "其中：控制风险最低资本"
    assert len(events) == 1
    print("PASS 换行指标逻辑行合并，且不吞并其中/独立项目")


def test_repair_large_single_dot_uses_source_integer():
    """12345.678 这类大数字：原文有对应千分位整数时才还原，否则进入待核对。"""
    index = build_number_index("认可资产 12,345,678 3,456.78")
    fixed, repair_note, _ = repair_number_text("12345.678", index)
    assert fixed == "12,345,678", fixed
    assert repair_note
    kept, _, review_note = repair_number_text("12345.678", build_number_index(""))
    assert kept == "12345.678", kept
    assert ("千分位" in review_note) or ("小数点" in review_note), review_note
    print("PASS 大数字千分位误读修复")


# ============================================================
# 二、规则法离线提取（真实线框表格）
# ============================================================
def build_text_table_pdf() -> bytes:
    """生成一页带真实线框的偿付能力状况表。"""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        fitz.Point(60, 80),
        "中国某财产保险股份有限公司2025年第4季度偿付能力报告摘要",
        fontname="china-s",
        fontsize=12,
    )
    rows = [
        ["指标名称", "本季度末数", "上季度末数"],
        ["认可资产", "1,234,567.89", "1,100,000.00"],
        ["认可负债", "800,000.00", "750,000.00"],
        ["实际资本", "434,567.89", "350,000.00"],
        ["最低资本", "300,000.00", "280,000.00"],
        ["综合偿付能力充足率", "144.86%", "125.00%"],
    ]
    x0, y0, col_w, row_h = 60.0, 120.0, 150.0, 26.0
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
                fitz.Point(x0 + col_index * col_w + 5, y0 + row_index * row_h + 17),
                str(cell),
                fontname="china-s",
                fontsize=10,
            )
    return doc.tobytes()


def build_text_only_pdf() -> bytes:
    """生成一页只有正文、没有目标表的文字版页面。"""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        fitz.Point(60, 80),
        "四、风险管理能力\n公司持续完善全面风险管理体系，健全风险偏好与限额管理。",
        fontname="china-s",
        fontsize=12,
    )
    return doc.tobytes()


def _draw_table(page, rows, *, y0: float, x0: float = 60.0, col_w: float = 150.0, row_h: float = 26.0):
    """在页面指定位置画一张带线框的表，返回其纵向范围 (top, bottom)。"""
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
                fitz.Point(x0 + col_index * col_w + 5, y0 + row_index * row_h + 17),
                str(cell),
                fontname="china-s",
                fontsize=10,
            )
    return y0, y0 + row_h * len(rows)


def build_multi_table_pdf() -> tuple[bytes, tuple[float, float]]:
    """生成一页三张表的 PDF：目标表（偿付能力状况表）夹在中间。

    返回 (PDF字节, 目标表自身的纵向范围)。
    """
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        fitz.Point(60, 70),
        "中国某财产保险股份有限公司2025年第4季度偿付能力报告摘要",
        fontname="china-s",
        fontsize=12,
    )
    _draw_table(
        page,
        [
            ["指标名称", "本季度数", "本年度累计数"],
            ["保险业务收入", "964,379,502.91", "7,362,400,072.66"],
            ["净利润", "61,793,719.10", "274,176,547.39"],
            ["签单保费", "961,400,344.73", "7,339,853,702.59"],
        ],
        y0=120.0,
    )
    target_range = _draw_table(
        page,
        [
            ["指标名称", "本季度末数", "上季度末数"],
            ["认可资产", "1,234,567.89", "1,100,000.00"],
            ["认可负债", "800,000.00", "750,000.00"],
            ["实际资本", "434,567.89", "350,000.00"],
            ["最低资本", "300,000.00", "280,000.00"],
            ["综合偿付能力充足率", "144.86%", "125.00%"],
        ],
        y0=260.0,
    )
    _draw_table(
        page,
        [
            ["流动性风险监管指标", "本季度数", "上季度可比数"],
            ["其中：基本情景 LCR1（％）", "111.47%", "105.83%"],
            ["其中：基本情景 LCR2（％）", "98.20%", "96.11%"],
        ],
        y0=430.0,
    )
    return doc.tobytes(), target_range


def test_region_locate_middle_table_on_multi_table_page():
    """一页三张表时，区域定位必须只圈住夹在中间的目标表。"""
    from services.solvency_table_region import locate_table_region

    pdf, target_range = build_multi_table_pdf()
    region = locate_table_region(pdf, 1, "SOLVENCY_MAIN", {})
    assert region is not None, "多表同页时应当能定位出目标表区域"
    top, bottom = target_range
    assert top - 20 <= region.bbox[1] <= top + 20, region.bbox
    assert bottom - 20 <= region.bbox[3] <= bottom + 20, region.bbox
    assert region.start_item == "认可资产", region.start_item
    assert "综合偿付能力充足率" in region.end_item, region.end_item
    print("PASS 页内区域定位:", region.note)


def test_rule_extraction_picks_middle_table_only():
    """规则法在夹心表场景下只输出目标表，不得混入上下两张相邻表。"""
    pdf, _ = build_multi_table_pdf()
    grid = build_pdf_grids(pdf, [1])[0]
    outcome = router.extract_page_with_method(
        pdf_bytes=pdf,
        page_number=1,
        table_id="SOLVENCY_MAIN",
        table_name="偿付能力状况表",
        method=router.MODE_RULE,
        grid=grid,
        has_text=True,
    )
    assert outcome.succeeded, outcome.note
    text = outcome.frame.astype(str).to_string()
    assert "认可资产" in text and "综合偿付能力充足率" in text, text
    assert "保险业务收入" not in text, text
    assert "签单保费" not in text, text
    assert "LCR1" not in text, text
    assert outcome.region is not None, "应记录页内区域，供界面画红框"
    print("PASS 夹心表规则法提取:", outcome.note[:100])


def build_two_table_pdf() -> bytes:
    """生成一页两张表：目标表在下，干扰表在上（无边界配置的目标表用）。"""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        fitz.Point(60, 70),
        "某财产保险公司2025年第4季度偿付能力报告摘要",
        fontname="china-s",
        fontsize=12,
    )
    _draw_table(
        page,
        [
            ["指标名称", "本季度末数", "上季度末数"],
            ["认可资产", "1,234,567.89", "1,100,000.00"],
            ["综合偿付能力充足率", "144.86%", "125.00%"],
        ],
        y0=120.0,
    )
    _draw_table(
        page,
        [
            ["指标名称", "本季度数", "本年度累计数"],
            ["保险业务收入（元）", "964,379,502.91", "7,362,400,072.66"],
            ["签单保费（元）", "961,400,344.73", "7,339,853,702.59"],
            ["综合成本率（％）", "102.49%", "99.10%"],
        ],
        y0=260.0,
    )
    return doc.tobytes()


def test_config_terms_pick_target_among_page_tables():
    """没有边界配置时，用 Profile 的表名/内容词把目标表挑出来。"""
    pdf = build_two_table_pdf()
    config = {
        "title_terms": ["主要经营指标"],
        "content_terms": ["签单保费", "综合成本率"],
        "header_terms": ["指标名称", "本年度累计数"],
        "exclude_item_terms": ["前五大产品的信息"],
    }
    frame, note, _ = router.extract_single_page_rule(
        pdf,
        1,
        "NON_LIFE_OPERATING_METRICS",
        "主要经营指标（财险）",
        table_config=config,
    )
    assert frame is not None, note
    text = frame.astype(str).to_string()
    assert "签单保费" in text and "综合成本率" in text, text
    assert "认可资产" not in text, text
    print("PASS 配置关键词选定目标表:", note[:80])


def test_profile_start_end_terms_isolate_target_table():
    """财险经营指标表只配了首尾项目时，也要据此把相邻表裁掉。"""
    pdf = build_two_table_pdf()
    config = {
        "title_terms": ["主要经营指标"],
        "content_terms": ["保险业务收入", "签单保费", "综合成本率"],
        "header_terms": ["指标名称", "本季度数", "本年度累计数"],
        "start_item_terms": ["保险业务收入"],
        "end_item_terms": ["综合成本率", "综合赔付率", "费用率"],
        "exclude_item_terms": ["前五大险种", "前五大产品"],
    }
    grid = build_pdf_grids(pdf, [1])[0]
    outcome = router.extract_page_with_method(
        pdf_bytes=pdf,
        page_number=1,
        table_id="NON_LIFE_OPERATING_METRICS",
        table_name="主要经营指标（财险）",
        method=router.MODE_RULE,
        grid=grid,
        has_text=True,
        table_config=config,
    )
    assert outcome.succeeded, outcome.note
    text = outcome.frame.astype(str).to_string()
    assert "保险业务收入" in text and "综合成本率" in text, text
    assert "认可资产" not in text, text
    assert "综合偿付能力充足率" not in text, text
    print("PASS Profile首尾项目隔离相邻表:", outcome.note[:100])


def test_step2_unit_detected_but_not_converted():
    """STEP2 识别单位但保持原文数值，元→万元的换算留给 STEP3。"""
    grid = [
        ["指标名称", "本季度末数", "上季度末数"],
        ["认可资产（元）", "8,393,334,151.18", "9,045,331,009.31"],
        ["综合偿付能力充足率（%）", "317.74", "350.78"],
    ]
    out_grid, unit = normalize_table_unit(grid, "", "SOLVENCY_MAIN", [12])
    assert unit == "元", unit
    assert out_grid == grid, out_grid
    assert out_grid[1][1] == "8,393,334,151.18", out_grid[1][1]
    print("PASS STEP2单位识别但不换算:", unit)


def test_table_unit_ignores_row_level_unit_labels():
    rows = [
        ["指标名称（单位：万元）", "本季度数", "本年累计数"],
        ["基本每股收益(元)", "1.065", "1.065"],
        ["车险车均保费（元）", "1,097.19", "1,023.39"],
    ]
    assert detect_table_unit(rows) == "万元"
    print("PASS 表单位优先，行级(元)不覆盖整表万元")


def test_interleaved_header_columns_are_reconstructed():
    rows = [
        ["", "指标名称", "", "本季度数", "", "上季度可比数"],
        ["保险业务收入", "", "880,244.17", "", "3,573,594.47", ""],
        ["净利润", "", "-17,940.38", "", "167,935.69", ""],
    ]
    repaired = _reconstruct_logical_columns(rows)
    assert repaired == [
        ["指标名称", "本季度数", "上季度可比数"],
        ["保险业务收入", "880,244.17", "3,573,594.47"],
        ["净利润", "-17,940.38", "167,935.69"],
    ]
    print("PASS 众安式交错空列重建为真实三列")


def test_split_period_header_columns_are_reconstructed():
    """期间表头被列边界切碎时，按列位置重建，不能把本年累计数挪到本季度列。"""
    rows = [
        ["指标名称", "本季度", "数 本年", "累计数"],
        ["保险业务收入", "", "98,336.16", "340,728.21"],
        ["净利润", "", "4,700.15", "7,249.53"],
        ["总资产", "", "", "597,221.63"],
        ["净资产", "", "", "147,299.36"],
        ["净资产收益率（%）", "", "3.24%", "5.05%"],
    ]
    repaired = _reconstruct_logical_columns(rows)
    assert repaired == [
        ["指标名称", "本季度数", "本年累计数"],
        ["保险业务收入", "98,336.16", "340,728.21"],
        ["净利润", "4,700.15", "7,249.53"],
        ["总资产", "", "597,221.63"],
        ["净资产", "", "147,299.36"],
        ["净资产收益率（%）", "3.24%", "5.05%"],
    ], repaired
    print("PASS 切碎的期间表头重建，且空值保持原列位")


def test_number_split_by_column_edge_is_stitched():
    """右对齐长数值被列边界劈成 '9' + '84,390,606.64' 时必须拼回。"""
    rows = [
        ["", "", "", "", "", "本季度数", "", "本年度累计数"],
        ["1", ".", "签单保费", "", "9", "84,390,606.64", "5,", "399,707,901.15"],
        ["2", ".", "车险签单保费", "", "6", "48,063,745.92", "2,", "787,465,267.99"],
        ["3", ".", "车险车均保费", "", "", "1117.54", "", "1079.12"],
        ["", "", "排序", "险种", "", "本季度数", "", "本年度累计数"],
        ["", "", "1", "健康险", "", "18,554,373.16", "", "1,198,540,778.27"],
        ["", "5", ". 各渠道签单保", "费", "", "", "", ""],
        ["", "", "1", "代理渠道", "", "652,817,423.73", "", "3,053,666,299.17"],
    ]
    merged = _merge_number_fragment_columns(rows)
    assert merged[1][-2:] == ["984,390,606.64", "5,399,707,901.15"], merged[1]
    assert merged[2][-2:] == ["648,063,745.92", "2,787,465,267.99"], merged[2]
    assert merged[3][-2:] == ["1117.54", "1079.12"], merged[3]
    rebuilt = _reconstruct_logical_columns(merged)
    assert rebuilt[0] == ["指标名称", "本季度数", "本年度累计数"], rebuilt[0]
    assert rebuilt[1] == ["签单保费", "984,390,606.64", "5,399,707,901.15"], rebuilt[1]
    assert ["各渠道签单保费", "", ""] in rebuilt, rebuilt
    assert ["代理渠道", "652,817,423.73", "3,053,666,299.17"] in rebuilt, rebuilt
    print("PASS 被列边界劈开的数值与标签拼回", rebuilt[1])

    # 两张真实的数值列（左列每行都有值）不能被误拼
    two_columns = [
        ["指标名称", "数量", "金额"],
        ["甲", "123", "456.78"],
        ["乙", "45", "678.90"],
    ]
    assert _merge_number_fragment_columns(two_columns) == two_columns


def test_blank_metric_row_is_not_merged_into_previous_label():
    """整行为空的独立指标（如基本每股收益）不能被当成上一行的换行标签。"""
    rows = [
        ["指标名称", "本季度数", "本年累计数"],
        ["保险合同负债", "", "207,314.90"],
        ["基本每股收益", "", ""],
        ["车险车均保费", "", "0.09"],
    ]
    repaired, events = _merge_wrapped_table_rows(rows)
    assert [row[0] for row in repaired] == [
        "指标名称",
        "保险合同负债",
        "基本每股收益",
        "车险车均保费",
    ], repaired
    assert not events, events

    # 真正的换行标签仍要合并：短片段与补全未闭合括号两种形态
    wrapped = [
        ["行次", "项目", "本季度末数", "上季度末数"],
        ["1", "量化风险最低资本（未考虑", "100", "90"],
        ["", "特征系数前）", "", ""],
        ["2", "核心一级资", "200", "180"],
        ["", "本", "", ""],
    ]
    merged, wrap_events = _merge_wrapped_table_rows(wrapped)
    assert merged[1][1] == "量化风险最低资本（未考虑特征系数前）", merged
    assert merged[2][1] == "核心一级资本", merged
    assert len(wrap_events) == 2, wrap_events
    print("PASS 整行空白指标不被吞并，真实换行标签仍合并")


def test_split_header_operating_table_end_to_end():
    """端到端：无边框、表头按词散落的三列经营指标表应按期间正确重建。"""
    font, size = "china-s", 9.0

    def right_text(page, x_right, y, text):
        width = fitz.get_text_length(text, fontname=font, fontsize=size)
        page.insert_text((x_right - width, y), text, fontname=font, fontsize=size)

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((40, 50), "（三）主要经营指标", fontname=font, fontsize=12)
    page.insert_text((40, 72), "指标名称", fontname=font, fontsize=size)
    page.insert_text((250, 72), "本季度", fontname=font, fontsize=size)
    page.insert_text((286, 72), "数", fontname=font, fontsize=size)
    page.insert_text((350, 72), "本年", fontname=font, fontsize=size)
    page.insert_text((374, 72), "累计数", fontname=font, fontsize=size)
    rows = [
        ("保险业务收入", "98,336.16", "340,728.21"),
        ("净利润", "4,700.15", "7,249.53"),
        ("总资产", "", "597,221.63"),
        ("基本每股收益", "", ""),
    ]
    y = 92
    for label, quarter, annual in rows:
        page.insert_text((40, y), label, fontname=font, fontsize=size)
        if quarter:
            right_text(page, 330, y, quarter)
        if annual:
            right_text(page, 440, y, annual)
        y += 18
    pdf = doc.tobytes()
    doc.close()

    grids = build_pdf_grids(pdf, [1])
    outcome = router.extract_page_with_method(
        pdf_bytes=pdf,
        page_number=1,
        table_id="OPERATING_METRICS",
        table_name="主要经营指标",
        method=router.MODE_RULE,
        grid=grids[0] if grids else None,
        has_text=True,
        table_config={
            "start_item_terms": ["保险业务收入"],
            "end_item_terms": ["基本每股收益"],
            "boundary_end_mode": "page_tail",
            "content_terms": ["保险业务收入", "基本每股收益"],
        },
    )
    assert outcome.status == "成功", outcome.note
    table = outcome.frame.fillna("").astype(str).values.tolist()
    assert table == [
        ["指标名称", "本季度数", "本年累计数"],
        ["保险业务收入", "98,336.16", "340,728.21"],
        ["净利润", "4,700.15", "7,249.53"],
        ["总资产", "", "597,221.63"],
        ["基本每股收益", "", ""],
    ], table
    print("PASS 端到端：散落表头重建为三列，空值行保留")


def test_solvency_header_with_brackets_survives_region_crop():
    """表头写成“本季度(末)数”时，页内区域裁切不能把表头行裁掉。"""
    font, size = "china-s", 9.0

    def right_text(page, x_right, y, text):
        width = fitz.get_text_length(text, fontname=font, fontsize=size)
        page.insert_text((x_right - width, y), text, fontname=font, fontsize=size)

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((60, 60), "（一）偿付能力充足率指标", fontname=font, fontsize=11)
    page.insert_text((90, 90), "指标名称", fontname=font, fontsize=size)
    right_text(page, 430, 90, "本季度(末)数")
    right_text(page, 540, 90, "上季度(末)数")
    rows = [
        ("认可资产（万元）", "590,800.15", "605,113.10"),
        ("认可负债（万元）", "431,301.57", "447,380.39"),
        ("实际资本（万元）", "159,498.58", "157,732.71"),
        ("核心一级资本（万元）", "159,498.58", "157,732.71"),
        ("核心二级资本（万元）", "", ""),
        ("最低资本（万元）", "54,394.66", "57,127.88"),
        ("核心偿付能力充足率（%）", "293.22%", "276.10%"),
        ("综合偿付能力充足率（%）", "293.22%", "276.10%"),
    ]
    y = 112
    for label, current, previous in rows:
        page.insert_text((60, y), label, fontname=font, fontsize=size)
        if current:
            right_text(page, 430, y, current)
        if previous:
            right_text(page, 540, y, previous)
        y += 20
    pdf = doc.tobytes()
    doc.close()

    grids = build_pdf_grids(pdf, [1])
    config = {
        "start_item_terms": ["认可资产"],
        "end_item_terms": ["综合偿付能力充足率"],
        "content_terms": ["认可资产", "最低资本"],
    }
    for whole_page in (True, False):
        outcome = router.extract_page_with_method(
            pdf_bytes=pdf,
            page_number=1,
            table_id="SOLVENCY_MAIN",
            table_name="偿付能力充足率指标",
            method=router.MODE_RULE,
            grid=grids[0] if grids else None,
            has_text=True,
            table_config=config,
            whole_page=whole_page,
        )
        assert outcome.status == "成功", outcome.note
        table = outcome.frame.fillna("").astype(str).values.tolist()
        assert table[0] == ["指标名称", "本季度(末)数", "上季度(末)数"], table[0]
        assert ["核心二级资本（万元）", "", ""] in table, table
        assert "含表头" in outcome.note, outcome.note
    print("PASS 括号写法表头在区域裁切后仍然保留")


def test_operating_repair_keeps_annual_only_rows():
    """主要经营指标的三列已经是标准布局时不能被重排，空的本季度列要保留。"""
    standard = [
        ["指标名称", "本季度数", "本年累计数"],
        ["保险业务收入", "98,336.16", "340,728.21"],
        ["总资产", "", "597,221.63"],
        ["基本每股收益", "", ""],
    ]
    assert _repair_operating(standard) == standard

    # 只有 pdfplumber 把数值劈成两段（四列）时才重建
    split = [
        ["指标名称", "本季度数", "本年累计数", ""],
        ["保险业务收入", "98,336", ".16", "340,728.21"],
        ["总资产", "", "", "597,221.63"],
    ]
    assert _repair_operating(split) == [
        ["指标名称", "本季度（末）数", "本年累计数"],
        ["保险业务收入", "98,336.16", "340,728.21"],
        ["总资产", "", "597,221.63"],
    ]
    print("PASS 主要经营指标三列放行，四列劈裂才重建")


def test_editor_column_operations():
    """编辑器应支持追加列、删除列，并至少保留一列。"""
    rows = [
        ["指标名称", "本季度末数", "上季度末数"],
        ["认可资产", "8,393,334,151.18", "9,045,331,009.31"],
    ]
    added = _add_column(rows)
    assert all(len(row) == 4 for row in added), added
    assert added[0][3] == "" and added[1][3] == ""

    remaining = _delete_columns(rows, {1})
    assert remaining == [
        ["指标名称", "上季度末数"],
        ["认可资产", "9,045,331,009.31"],
    ], remaining

    only_one = _delete_columns(rows, {0, 1, 2})
    assert all(len(row) == 1 and row[0] == "" for row in only_one), only_one
    print("PASS 编辑器增删列")


def test_editor_frame_roundtrip_keeps_header_editable():
    """编辑器网格应包含表头行，且往返转换不丢失内容。"""
    rows = [
        ["指标名称", "本季度末数"],
        ["认可资产", "8,393,334,151.18"],
    ]
    frame, width = _editor_frame(rows)
    assert width == 2, width
    assert list(frame.columns) == ["列1", "列2"], frame.columns
    assert _rows_from_editor(frame) == rows
    print("PASS 编辑器网格往返一致")


def test_vision_crop_uses_region_then_falls_back():
    """图片识别优先按区域裁切；裁切结果不完整时回退整页。"""
    pdf, _ = build_multi_table_pdf()
    grid = build_pdf_grids(pdf, [1])[0]
    crops: list = []

    def fake_vision(
        pdf_bytes,
        page_num,
        expected_name,
        api_key,
        base_url,
        model_name,
        *,
        bbox=None,
        region_note="",
        zoom=3.0,
    ):
        crops.append(bbox)
        if bbox is not None:
            # 模拟"裁切结果缺行"，触发回退整页
            return pd.DataFrame(
                [["指标名称", "本季度末数", "上季度末数"], ["认可资产", "1,234,567.89", ""]]
            ), "裁切结果"
        return pd.DataFrame(
            [
                ["指标名称", "本季度末数", "上季度末数"],
                ["认可资产", "1,234,567.89", "1,100,000.00"],
                ["认可负债", "800,000.00", "750,000.00"],
                ["实际资本", "434,567.89", "350,000.00"],
                ["最低资本", "300,000.00", "280,000.00"],
                ["综合偿付能力充足率", "144.86%", "125.00%"],
            ]
        ), "整页结果"

    original = router.extract_single_page_vision
    router.extract_single_page_vision = fake_vision
    try:
        outcome = router.extract_page_with_method(
            pdf_bytes=pdf,
            page_number=1,
            table_id="SOLVENCY_MAIN",
            table_name="偿付能力状况表",
            method=router.MODE_VISION,
            grid=grid,
            has_text=True,
            **CREDENTIALS,
        )
    finally:
        router.extract_single_page_vision = original
    assert len(crops) == 2, crops
    assert crops[0] is not None, "第一次调用应按区域裁切"
    assert crops[1] is None, "裁切结果不完整时必须回退整页"
    assert outcome.succeeded
    assert "回退整页识别" in outcome.note, outcome.note
    assert "综合偿付能力充足率" in outcome.frame.astype(str).to_string()
    print("PASS 图片模式区域裁切与回退:", outcome.note[:90])


def test_rule_extraction_offline():
    pdf = build_text_table_pdf()
    frame, note, repair = router.extract_single_page_rule(
        pdf,
        1,
        "SOLVENCY_MAIN",
        "偿付能力状况表",
    )
    assert frame is not None, note
    text = frame.astype(str).to_string()
    assert "认可资产" in text and "综合偿付能力充足率" in text, text
    assert "1,234,567.89" in text, text
    assert not repair.changed, repair.note
    print("PASS 规则法离线提取:", note)


def test_rule_quality_gate():
    pdf = build_text_table_pdf()
    frame, _, _ = router.extract_single_page_rule(
        pdf, 1, "SOLVENCY_MAIN", "偿付能力状况表"
    )
    rows = frame.fillna("").astype(str).values.tolist()
    # 质量门禁依据真实的页面文字网格，与 STEP2 运行时一致
    grid = build_pdf_grids(pdf, [1])[0]
    passed, note = router.validate_rule_page_rows("SOLVENCY_MAIN", rows, grid)
    assert passed, note
    # 丢了大半数据行的结果必须被质量门禁拦下
    passed_short, note_short = router.validate_rule_page_rows(
        "SOLVENCY_MAIN",
        rows[:2],
        grid,
    )
    assert not passed_short, "缺失关键指标时应判定为未通过"
    print("PASS 规则法质量门禁:", note, "|", note_short[:60])


# ============================================================
# 三、智能模式路由
# ============================================================
class _Recorder:
    """替换模型调用：记录调用次数并返回预置结果。"""

    def __init__(self, frame=None):
        self.calls = []
        self.kwargs = []
        self.frame = frame

    def __call__(
        self,
        pdf_bytes,
        page_num,
        expected_name,
        api_key,
        base_url,
        model_name,
        **kwargs,
    ):
        self.calls.append((page_num, expected_name))
        self.kwargs.append(kwargs)
        if self.frame is None:
            return None, "模拟失败"
        return self.frame.copy(), "模拟模型返回"


def _fake_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            ["指标名称", "本季度末数", "上季度末数"],
            ["认可资产", "1,234,567.89", "1,100,000.00"],
        ]
    )


@contextmanager
def patched_model_calls(text_frame=None, vision_frame=None):
    text_recorder = _Recorder(text_frame)
    vision_recorder = _Recorder(vision_frame)
    original_text = router.extract_single_page
    original_vision = router.extract_single_page_vision
    router.extract_single_page = text_recorder
    router.extract_single_page_vision = vision_recorder
    try:
        yield text_recorder, vision_recorder
    finally:
        router.extract_single_page = original_text
        router.extract_single_page_vision = original_vision


CREDENTIALS = {
    "api_key": "sk-test",
    "base_url": "https://example.invalid/v1",
    "model": "test-model",
}


def test_auto_mode_uses_rule_without_calling_model():
    pdf = build_text_table_pdf()
    with patched_model_calls(_fake_frame(), _fake_frame()) as (text_calls, vision_calls):
        outcome = router.extract_page_with_method(
            pdf_bytes=pdf,
            page_number=1,
            table_id="SOLVENCY_MAIN",
            table_name="偿付能力状况表",
            method=router.MODE_AUTO,
            grid=PageGrid(page_number=1, grid_text="", word_count=120),
            has_text=True,
            **CREDENTIALS,
        )
    assert outcome.engine == router.ENGINE_RULE, outcome.note
    assert outcome.succeeded, outcome.note
    assert text_calls.calls == [], "文字版页面不得调用大模型"
    assert vision_calls.calls == [], "文字版页面不得调用图片模型"
    print("PASS 智能模式·文字版零模型调用:", outcome.note[:70])


def test_auto_mode_scanned_page_uses_vision():
    pdf = build_text_table_pdf()
    with patched_model_calls(_fake_frame(), _fake_frame()) as (text_calls, vision_calls):
        outcome = router.extract_page_with_method(
            pdf_bytes=pdf,
            page_number=1,
            table_id="SOLVENCY_MAIN",
            table_name="偿付能力状况表",
            method=router.MODE_AUTO,
            grid=PageGrid(page_number=1, grid_text="", word_count=0),
            has_text=False,
            **CREDENTIALS,
        )
    assert outcome.engine == router.ENGINE_LLM_VISION, outcome.note
    assert vision_calls.calls == [(1, "偿付能力状况表")], vision_calls.calls
    assert text_calls.calls == []
    print("PASS 智能模式·扫描版直接走图片识别")


def test_auto_mode_falls_back_to_text_model():
    """规则法未命中目标表时自动转文本大模型。"""
    pdf = build_text_only_pdf()
    with patched_model_calls(_fake_frame(), _fake_frame()) as (text_calls, vision_calls):
        outcome = router.extract_page_with_method(
            pdf_bytes=pdf,
            page_number=1,
            table_id="SOLVENCY_MAIN",
            table_name="偿付能力状况表",
            method=router.MODE_AUTO,
            grid=PageGrid(page_number=1, grid_text="", word_count=40),
            has_text=True,
            **CREDENTIALS,
        )
    assert outcome.engine == router.ENGINE_LLM_TEXT, outcome.note
    assert router.RULE_FAILURE_PREFIX in outcome.note, outcome.note
    assert text_calls.calls == [(1, "偿付能力状况表")], text_calls.calls
    assert vision_calls.calls == []
    print("PASS 规则法未命中自动转文本大模型")


def test_rule_only_mode_never_calls_model():
    pdf = build_text_table_pdf()
    with patched_model_calls(_fake_frame(), _fake_frame()) as (text_calls, vision_calls):
        outcome = router.extract_page_with_method(
            pdf_bytes=pdf,
            page_number=1,
            table_id="SOLVENCY_MAIN",
            table_name="偿付能力状况表",
            method=router.MODE_RULE,
            grid=PageGrid(page_number=1, grid_text="", word_count=120),
            has_text=True,
        )
        scanned = router.extract_page_with_method(
            pdf_bytes=pdf,
            page_number=1,
            table_id="SOLVENCY_MAIN",
            table_name="偿付能力状况表",
            method=router.MODE_RULE,
            grid=PageGrid(page_number=1, grid_text="", word_count=0),
            has_text=False,
        )
    assert outcome.succeeded, outcome.note
    assert router.ENGINE_RULE in scanned.engine
    assert "扫描版" in scanned.note, scanned.note
    assert text_calls.calls == [] and vision_calls.calls == []
    print("PASS 全部规则法·零模型调用")


def test_auto_mode_without_credentials_reports_reason():
    pdf = build_text_only_pdf()
    outcome = router.extract_page_with_method(
        pdf_bytes=pdf,
        page_number=1,
        table_id="SOLVENCY_MAIN",
        table_name="偿付能力状况表",
        method=router.MODE_AUTO,
        grid=PageGrid(page_number=1, grid_text="", word_count=0),
        has_text=False,
    )
    assert not outcome.succeeded
    assert "需要 AI 配置" in outcome.note, outcome.note
    print("PASS 扫描版缺 AI 配置时给出明确原因")


# ============================================================
# 四、中间表编辑
# ============================================================
def _make_table(rows) -> FlexibleTable:
    frame = rows_to_frame(rows)
    return FlexibleTable(
        table_id="SOLVENCY_MAIN",
        table_name="偿付能力状况表",
        page=1,
        source_pages=[1],
        rows=[list(row) for row in rows],
        to_frame=lambda *args, _df=frame, **kwargs: _df,
        strategy="并发·规则法",
        quality_score=1.0,
        evidence="",
        table_config={},
        unit_records=[],
        unit="万元",
    )


def test_editor_persists_edits_and_reverts():
    table = _make_table(
        [
            ["指标名称", "本季度末数", "上季度末数"],
            ["认可资产", "1,234,567.89", "1,100,000.00"],
        ]
    )
    edited = [
        ["指标名称", "本季度末数", "上季度末数"],
        ["认可资产", "1,235,000.00", "1,100,000.00"],
        ["认可负债", "800,000.00", "750,000.00"],
    ]
    persist_table_rows(table, edited)
    assert table.rows == edited
    assert table.to_frame().shape == (2, 3)
    assert list(table.to_frame().columns) == ["指标名称", "本季度末数", "上季度末数"]
    assert table.original_rows[1][1] == "1,234,567.89"
    assert table.edited is True
    table.rows = [list(row) for row in table.original_rows]
    assert len(table.rows) == 2
    assert table.rows[1][1] == "1,234,567.89"
    print("PASS 中间表编辑与撤销")


def test_editor_handles_added_blank_rows():
    table = _make_table([["指标名称", "本季度末数"], ["认可资产", "1,234,567.89"]])
    edited = [
        ["指标名称", "本季度末数"],
        ["认可资产", "1,234,567.89"],
        ["最低资本", ""],
    ]
    persist_table_rows(table, edited)
    frame = table.to_frame()
    assert frame.shape == (2, 2)
    assert frame.iloc[1, 1] == ""
    print("PASS 中间表新增空白行")


def test_step2_page_renders_editor_and_method_selector():
    """渲染整个应用并注入 STEP2 会话数据，确认新控件与编辑器可用。"""
    from streamlit.testing.v1 import AppTest

    from services.solvency_pdf_locator import PageMatch

    pdf = build_text_table_pdf()
    table = _make_table(
        [
            ["指标名称", "本季度末数", "上季度末数"],
            ["认可资产", "1,234,567.89", "1,100,000.00"],
        ]
    )
    table.table_blocks = [
        {
            "page": 1,
            "rows": [
                ["指标名称", "本季度末数", "上季度末数"],
                ["认可资产", "1,234,567.89", "1,100,000.00"],
            ],
        }
    ]
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=300)
    at.session_state["logged_in"] = True
    at.session_state["user_role"] = "项目组成员"
    # 先跑一次让会话默认值就绪：STEP1 检测到配置版本变化时会清空页码与提取结果，
    # 因此必须在版本号写入后再注入 STEP2 数据。
    at.run()
    at.session_state["workflow_main_tab"] = "STEP2 表格提取"
    at.session_state["pdf_bytes"] = pdf
    at.session_state["pdf_name"] = "测试财险2025Q4.pdf"
    at.session_state["pages_confirmed"] = True
    at.session_state["page_matches"] = [
        PageMatch(
            table_id="SOLVENCY_MAIN",
            table_name="偿付能力状况表",
            pages=[1],
            score=12.0,
            evidence="测试",
            confidence=90,
        )
    ]
    at.session_state["raw_tables"] = [table]
    at.session_state["ai_extraction_logs"] = [
        {
            "目标表": "偿付能力状况表",
            "物理页码": 1,
            "识别方式": "规则法",
            "状态": "成功",
            "说明": "规则法（线框识别）识别到2条数据行",
        }
    ]
    at.session_state["extraction_number_reviews"] = [
        {"目标表": "偿付能力状况表", "物理页码": 1, "待核对": "1.234（无原文千分位证据）"}
    ]
    at.run()
    assert not at.exception, [error.value for error in at.exception]
    method_radio = [item for item in at.radio if item.key == "extraction_method"]
    assert method_radio, "STEP2 应提供提取方式选择"
    assert at.session_state["extraction_method"] in router.METHOD_OPTIONS
    assert any("中间表编辑与重新提取" in item.value for item in at.markdown), "缺少中间表编辑区"
    assert any(
        "可直接在表格里修改任意单元格" in item.value for item in at.get("caption")
    ), "缺少中间表编辑说明"
    button_keys = {item.key for item in at.button}
    assert "reextract_table_SOLVENCY_MAIN" in button_keys, "缺少“重新提取该表”按钮"
    assert "add_col_SOLVENCY_MAIN" in button_keys, "缺少“添加一列”按钮"
    assert "del_col_btn_SOLVENCY_MAIN" in button_keys, "缺少“删除选中列”按钮"
    assert any(
        key.startswith("step2_step3_preview_") for key in button_keys
    ), "缺少 STEP3 视图预览按钮"
    assert any(
        "按页内表块查看" in str(getattr(item, "label", ""))
        for item in at.expander
    ), "缺少页内表块视图"
    assert at.get("download_button"), "STEP2 应保留下载入口"
    print("PASS STEP2 页面渲染：提取方式开关 + 中间表编辑器 + STEP3 预览")


if __name__ == "__main__":
    test_repair_multi_group_thousand_separators()
    test_repair_uses_source_comma_layout()
    test_repair_handles_full_width_and_reconstruction()
    test_repair_single_group_needs_source_evidence()
    test_repair_european_separators()
    test_text_alignment_split_label_is_repaired()
    test_wrapped_metric_rows_are_joined_without_swallowing_independent_rows()
    test_repair_large_single_dot_uses_source_integer()
    test_region_locate_middle_table_on_multi_table_page()
    test_rule_extraction_picks_middle_table_only()
    test_config_terms_pick_target_among_page_tables()
    test_profile_start_end_terms_isolate_target_table()
    test_step2_unit_detected_but_not_converted()
    test_table_unit_ignores_row_level_unit_labels()
    test_interleaved_header_columns_are_reconstructed()
    test_split_period_header_columns_are_reconstructed()
    test_number_split_by_column_edge_is_stitched()
    test_blank_metric_row_is_not_merged_into_previous_label()
    test_split_header_operating_table_end_to_end()
    test_solvency_header_with_brackets_survives_region_crop()
    test_operating_repair_keeps_annual_only_rows()
    test_editor_column_operations()
    test_editor_frame_roundtrip_keeps_header_editable()
    test_vision_crop_uses_region_then_falls_back()
    test_rule_extraction_offline()
    test_rule_quality_gate()
    test_auto_mode_uses_rule_without_calling_model()
    test_auto_mode_scanned_page_uses_vision()
    test_auto_mode_falls_back_to_text_model()
    test_rule_only_mode_never_calls_model()
    test_auto_mode_without_credentials_reports_reason()
    test_editor_persists_edits_and_reverts()
    test_editor_handles_added_blank_rows()
    test_step2_page_renders_editor_and_method_selector()
    print("全部通过")
