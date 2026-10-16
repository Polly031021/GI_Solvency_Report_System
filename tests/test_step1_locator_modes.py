# -*- coding: utf-8 -*-
"""STEP1 定位改造的回归测试（离线，不访问网络）。

覆盖三个关键行为：
1. analyze_document_type：文字版/扫描版/混合版分类与推荐方法。
2. locate_tables_hybrid(mode="rule")：零大模型调用也能给出页码与置信度。
3. llm_verify：用假模型接口模拟“复核一致/不一致”两种结果。
"""
import json as jsonlib
import sys
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.solvency_hybrid_pipeline import locate_tables_hybrid  # noqa: E402
from services.solvency_pdf_locator import analyze_document_type  # noqa: E402

FEATURE_CONFIG = {
    "tables": [
        {
            "table_id": "SOLVENCY_MAIN",
            "table_name": "偿付能力状况表",
            "title_terms": ["偿付能力状况表"],
            "content_terms": ["认可资产", "认可负债", "实际资本", "最低资本"],
            "header_terms": ["项目", "金额"],
            "continuation_terms": ["实际资本", "最低资本"],
            "stop_terms": ["风险综合评级"],
            "minimum_score": 8,
            "anchor_min_structure_hits": 1,
            "max_pages": 2,
            "include_continuation": True,
            "continuation_min_structure_hits": 1,
            "continuation_min_numeric_lines": 1,
            "continuation_min_score": 2,
        }
    ]
}


class FakeResponse:
    """模拟大模型 chat/completions 响应。"""

    def __init__(self, payload):
        self._payload = payload
        self.ok = True
        self.status_code = 200

    def json(self):
        return self._payload


def make_post_func(pages_by_table):
    """返回一个假的 requests.post：按表名返回指定页码，不发网络请求。"""

    def post_func(url, *, headers=None, json=None, timeout=None):
        content = jsonlib.dumps(
            {name: pages for name, pages in pages_by_table.items()},
            ensure_ascii=False,
        )
        return FakeResponse(
            {
                "choices": [
                    {"message": {"role": "assistant", "content": content}}
                ]
            }
        )

    return post_func


def build_pdf(scanned_pages: int = 0) -> bytes:
    """生成一份 4 页 PDF：第 2、3 页放目标表，其余为普通正文。

    scanned_pages > 0 时，把前 N 页做成无文字层的“空白图片页”，
    用于测试混合版/扫描版分类。
    """
    doc = fitz.open()
    body_pages = [
        "中国某财产保险股份有限公司偿付能力季度报告摘要\n"
        "公司经营稳健，各项指标符合监管要求。",
        "偿付能力状况表\n项目\t金额（万元）\n"
        "认可资产\t1,000\n认可负债\t800\n实际资本\t200\n最低资本\t150",
        "实际资本\t200\n最低资本\t150\n综合偿付能力充足率\t133%\n"
        "核心偿付能力充足率\t120%",
        "风险综合评级\\n公司最近两次风险综合评级均为B类，评级结果保持稳定。\\n公司持续加强偿付能力管理，优化资产配置结构，\\n完善风险管理体系，确保偿付能力充足率满足监管要求。",
    ]
    for index, text in enumerate(body_pages):
        page = doc.new_page()
        if index < scanned_pages:
            continue  # 空白页：无文字层
        page.insert_textbox(
            fitz.Rect(40, 60, 550, 780), text, fontname="china-s", fontsize=12
        )
    return doc.tobytes()


def test_document_type_text():
    profile = analyze_document_type(build_pdf())
    assert profile.doc_type == "TEXT", profile
    assert profile.recommended_method == "RULE", profile
    print("PASS 文字版分类:", profile.label, profile.recommendation)


def test_document_type_scanned():
    profile = analyze_document_type(build_pdf(scanned_pages=3))
    assert profile.doc_type in {"SCANNED", "MIXED"}, profile
    print("PASS 扫描版分类:", profile.label, profile.recommendation)


def test_rule_mode_offline():
    pdf = build_pdf()
    matches, message = locate_tables_hybrid(
        pdf,
        FEATURE_CONFIG,
        api_key="",
        base_url="",
        model="",
        mode="rule",
    )
    assert "未调用大模型" in message, message
    match = matches[0]
    assert 2 in match.pages, (match.pages, match.evidence)
    assert match.confidence > 0, (match.confidence, match.confidence_basis)
    print(f"PASS 规则法定位: pages={match.pages} 置信度={match.confidence}% 依据={match.confidence_basis}")


def test_rule_mode_llm_verify_agree():
    pdf = build_pdf()
    matches, message = locate_tables_hybrid(
        pdf,
        FEATURE_CONFIG,
        api_key="sk-test",
        base_url="https://example.invalid/v1",
        model="test-model",
        mode="rule",
        llm_verify=True,
        post_func=make_post_func({"偿付能力状况表": [2, 3]}),
    )
    match = matches[0]
    assert "大模型复核一致" in match.confidence_basis, match.confidence_basis
    assert match.confidence >= 85, match.confidence
    assert not match.review_required
    print(f"PASS 复核一致: pages={match.pages} 置信度={match.confidence}%")


def test_rule_mode_llm_verify_disagree():
    pdf = build_pdf()
    matches, _ = locate_tables_hybrid(
        pdf,
        FEATURE_CONFIG,
        api_key="sk-test",
        base_url="https://example.invalid/v1",
        model="test-model",
        mode="rule",
        llm_verify=True,
        post_func=make_post_func({"偿付能力状况表": [4]}),
    )
    match = matches[0]
    assert match.review_required, "不一致时必须标记人工确认"
    assert match.confidence <= 55, match.confidence
    print(f"PASS 复核不一致: 置信度={match.confidence}% review_reason={match.review_reason}")




# ============================================================
# 回归：S05-最低资本表“中间子行含终止词 + 单行跨页”场景
# 复现安盛天平季度报告的真实版式：
#   第2页表格的中间子行“1.2 非寿险业务保险风险最低资本合计”
#   包含终止词“最低资本合计”的子串；真正的终止行“4 最低资本”
#   单独跨到下一页，且该页页首有大量空白行。
# ============================================================
S05_FEATURE_CONFIG = {
    "tables": [
        {
            "table_id": "MINIMUM_CAPITAL",
            "table_name": "S05-最低资本表",
            "title_terms": ["S05-最低资本表", "最低资本表"],
            "content_terms": ["量化风险最低资本", "控制风险最低资本", "附加资本"],
            "header_terms": ["行次", "期末数", "期初数"],
            "continuation_terms": ["控制风险最低资本", "附加资本", "最低资本合计"],
            "stop_terms": ["风险综合评级"],
            "terminal_terms": ["最低资本合计", "最低资本总额"],
            "minimum_score": 8,
            "max_pages": 2,
            "include_continuation": True,
            "continuation_min_score": 5.0,
            "continuation_min_structure_hits": 2,
            "continuation_min_numeric_lines": 2,
            "allow_single_row_tail": True,
        }
    ]
}


def build_s05_pdf() -> bytes:
    """第2页表格主体，第3页单行续表（页首空白行），第4页下一节。"""
    doc = fitz.open()
    pages = [
        "中国某财产保险股份有限公司2025年第4季度偿付能力报告摘要\n"
        "公司经营稳健，各项偿付能力指标符合监管要求。",
        "S05-最低资本表\n行次 项目 期末数 期初数\n"
        "1 量化风险最低资本 100.00 90.00\n"
        "1.2 非寿险业务保险风险最低资本合计 80.00 70.00\n"
        "2 控制风险最低资本 10.00 9.00\n"
        "3 附加资本 0.00 0.00",
        "   \n   \n   \n4 最低资本 110.00 99.00\n",
        "风险综合评级\n公司最近两次风险综合评级均为B类，"
        "评级结果保持稳定，公司持续完善全面风险管理体系。",
    ]
    for text in pages:
        page = doc.new_page()
        page.insert_textbox(fitz.Rect(40, 60, 550, 780), text, fontname="china-s", fontsize=12)
    return doc.tobytes()


def test_s05_single_row_tail_with_terminal_substring():
    """中间子行含终止词时不得截断跨页；页首空白行不得阻断单行续表识别。"""
    pdf = build_s05_pdf()
    matches, message = locate_tables_hybrid(
        pdf,
        S05_FEATURE_CONFIG,
        api_key="",
        base_url="",
        model="",
        mode="rule",
    )
    match = matches[0]
    assert match.pages == [2, 3], (match.pages, match.evidence)
    assert "页首单行续表" in match.evidence, match.evidence
    assert "未调用大模型" in message, message
    print(f"PASS S05单行跨页: pages={match.pages} 置信度={match.confidence}%")




# ============================================================
# 回归：S05 表后紧跟 IR02 明细表（安华农业真实版式）
# 第3页是 IR02-巨灾风险明细表，行内容与 S05 共享大量关键词
# （巨灾风险、风险分散效应、行次、期末数），但没有终止行。
# 依靠“外来报表编号表题”这一通用边界信号将其排除。
# ============================================================
def test_foreign_form_code_boundary():
    doc = fitz.open()
    pages = [
        "中国某财产保险股份有限公司2025年第4季度偿付能力报告摘要\n"
        "公司经营稳健，各项偿付能力指标符合监管要求。",
        "S05-最低资本表\n行次 项目 期末数 期初数\n"
        "1 量化风险最低资本 100.00 90.00\n"
        "2 控制风险最低资本 10.00 9.00\n"
        "4 最低资本 110.00 99.00",
        "IR02-财险和人身险公司非寿险业务保险风险-巨灾风险\n"
        "单位：元\n行次 项目 期末数\n"
        "1 国内车险巨灾风险最低资本 4,171,929.70\n"
        "2 巨灾风险分散效应 4,329,666.62\n"
        "7 非寿险业务巨灾风险最低资本 9,434,960.30",
    ]
    for text in pages:
        page = doc.new_page()
        page.insert_textbox(fitz.Rect(40, 60, 550, 780), text, fontname="china-s", fontsize=12)
    pdf = doc.tobytes()

    matches, message = locate_tables_hybrid(
        pdf,
        S05_FEATURE_CONFIG,
        api_key="",
        base_url="",
        model="",
        mode="rule",
    )
    match = matches[0]
    assert match.pages == [2], (match.pages, match.evidence)
    print(f"PASS 外来表号边界: pages={match.pages}（IR02明细页已排除）")




# ============================================================
# 回归：续页以一级章节标题开头（比亚迪保险真实版式）
# 经营指标表跨到第3页结束；第4页以“四、风险管理能力”开头，
# 叙述文字恰好提及“签单保费”“总资产”（经营指标内容词）且有数字行，
# 依靠一级章节标题边界将其排除。
# ============================================================
OPERATING_HEADING_CONFIG = {
    "tables": [
        {
            "table_id": "NON_LIFE_OPERATING_METRICS",
            "table_name": "主要经营指标（财险）",
            "title_terms": ["主要经营指标"],
            "content_terms": ["签单保费", "总资产", "车险车均保费"],
            "header_terms": ["指标名称", "本季度数"],
            "continuation_terms": ["签单保费"],
            "stop_terms": [],
            "terminal_terms": ["其他渠道", "其他渠道签单保费"],
            "minimum_score": 8,
            "max_pages": 4,
            "include_continuation": True,
            "continuation_min_score": 5.0,
            "continuation_min_structure_hits": 2,
            "continuation_min_numeric_lines": 2,
            "allow_single_row_tail": True,
        }
    ]
}


def build_operating_heading_pdf() -> bytes:
    doc = fitz.open()
    pages = [
        "某财产保险公司2025年第4季度偿付能力报告摘要",
        "主要经营指标\n指标名称 本季度数 本年度累计数\n"
        "（一）保险业务收入 100.00 300.00\n"
        "（二）净利润 10.00 30.00",
        "5.各渠道签单保费 100.00 300.00\n"
        "5.4 其他渠道签单保费 -- --",
        "四、风险管理能力\n"
        "（一）公司分类标准的情况\n"
        "最近会计年度的签单保费 28.97 亿元\n"
        "最近会计年度的总资产 64.27 亿元\n"
        "公司成立于2016年2月16日，属于II类保险公司。",
    ]
    for text in pages:
        page = doc.new_page()
        page.insert_textbox(fitz.Rect(40, 60, 550, 780), text, fontname="china-s", fontsize=12)
    return doc.tobytes()


def test_level_one_heading_boundary():
    """新章节页即使共享内容词和数字行，也不得并入上一张表。"""
    pdf = build_operating_heading_pdf()
    matches, _ = locate_tables_hybrid(
        pdf,
        OPERATING_HEADING_CONFIG,
        api_key="",
        base_url="",
        model="",
        mode="rule",
    )
    match = matches[0]
    assert match.pages == [2, 3], (match.pages, match.evidence)
    print(f"PASS 一级标题边界: pages={match.pages}（新章节页已排除）")


def test_numeric_row_not_heading():
    """以中文数字开头的表格数据行（带数值）不得被误判为章节标题。"""
    from services.solvency_pdf_locator import _level_one_heading_terms

    text = (
        "五、现金及流动性管理工具占比    指标值    4.68%\n"
        "六、季均融资杠杆比例           指标值    0.00%\n"
        "四、风险管理能力\n"
        "十一、最低资本\n"
    )
    headings = _level_one_heading_terms(text)
    assert headings == ["四、风险管理能力", "十一、最低资本"], headings
    print(f"PASS 数字行判别: {headings}")




# ============================================================
# 回归：稀疏文字层续页（比亚迪保险真实版式）
# 流动性表第2页结束；第3页页首有14行空白，数据行均不在
# 配置的内容词里，依靠“结构续表”（数值行+无边界）兜底纳入。
# ============================================================
LIQUIDITY_SPARSE_CONFIG = {
    "tables": [
        {
            "table_id": "LIQUIDITY_MONITORING",
            "table_name": "流动性风险监测指标",
            "title_terms": ["流动性风险监测指标"],
            "content_terms": ["规模保费同比增速", "季均融资杠杆比例"],
            "header_terms": ["指标名称", "本季度数"],
            "continuation_terms": ["续表"],
            "stop_terms": [],
            "terminal_terms": [],
            "minimum_score": 8,
            "max_pages": 2,
            "include_continuation": True,
            "continuation_min_score": 4.0,
            "continuation_min_structure_hits": 1,
            "continuation_min_numeric_lines": 1,
            "continuation_generic_numeric_lines": 2,
            "allow_single_row_tail": True,
        }
    ]
}


def build_sparse_tail_pdf() -> bytes:
    doc = fitz.open()
    pages = [
        "某财产保险公司2025年第4季度偿付能力报告摘要",
        "流动性风险监测指标\n指标名称 本季度数\n"
        "规模保费同比增速 10.07%\n季均融资杠杆比例 0.00%",
        "\n" * 14 + "AA级以下境内固定收益类资产占比 1.38% 1.52%\n"
        "应收款项占比 25.31% 33.66%\n持有关联方资产占比 0.00% 0.00%",
    ]
    for text in pages:
        page = doc.new_page()
        page.insert_textbox(fitz.Rect(40, 60, 550, 780), text, fontname="china-s", fontsize=12)
    return doc.tobytes()


def test_sparse_tail_generic_continuation():
    """页首大量空白且尾行不在内容词配置中时，结构续表兜底必须生效。"""
    pdf = build_sparse_tail_pdf()
    matches, _ = locate_tables_hybrid(
        pdf,
        LIQUIDITY_SPARSE_CONFIG,
        api_key="",
        base_url="",
        model="",
        mode="rule",
    )
    match = matches[0]
    assert match.pages == [2, 3], (match.pages, match.evidence)
    print(f"PASS 稀疏续页: pages={match.pages}")


def test_wrapped_label_not_heading():
    """换行排版：中文数字行标签后跟纯数值行，不得判为章节标题。"""
    from services.solvency_pdf_locator import _level_one_heading_terms

    text = (
        "五、现金及流动性管理工具占比\n"
        "4.68%  0.00%\n"
        "六、季均融资杠杆比例\n"
        "0.00%  0.00%\n"
        "四、风险管理能力\n"
        "（一）公司分类标准的情况\n"
    )
    headings = _level_one_heading_terms(text)
    assert headings == ["四、风险管理能力"], headings
    print(f"PASS 换行行标签判别: {headings}")


def test_consecutive_numeral_group_rows_not_heading():
    """大类行无数值（子行在下方）且编号连续时，不得判为章节标题。"""
    from services.solvency_pdf_locator import _level_one_heading_terms

    text = (
        "一、效益类指标\n"
        "1.综合成本率 102.49%\n"
        "2.综合费用率 5.21%\n"
        "二、规模类指标\n"
        "1.签单保费 961,400,344.73\n"
    )
    headings = _level_one_heading_terms(text)
    assert headings == [], headings
    print(f"PASS 连续编号大类行判别: {headings}")




# ============================================================
# 回归：紧邻其后的一张表在续页重复通用表头（安华农业真实版式）
# 摘要第10页依次排布“（三）流动性风险监测指标”（末行为持有关联方资产
# 占比）与“（六）主要经营指标”；第11页继续主要经营指标的行，并在页内
# 出现一级标题“四、风险管理能力”。
# 第11页只重复了通用表头“指标名称/本季度数”，没有监测指标的任何内容词，
# 该表头属于后一张表，不得据此把流动性监测指标延长到第11页。
# 同页的两张表互为对照：主要经营指标在第11页有真实内容词，必须保留。
# ============================================================
ADJACENT_TABLE_CONFIG = {
    "tables": [
        {
            "table_id": "LIQUIDITY_MONITORING",
            "table_name": "流动性风险监测指标",
            "title_terms": ["流动性风险监测指标", "流动性风险监管指标"],
            "content_terms": [
                "规模保费同比增速",
                "现金及流动性管理工具占比",
                "季均融资杠杆比例",
                "经营活动净现金流",
            ],
            "header_terms": ["指标名称", "本季度数", "上季度数"],
            "continuation_terms": ["续表"],
            "stop_terms": ["偿付能力风险管理能力", "风险综合评级"],
            "terminal_terms": [],
            "minimum_score": 6.0,
            "anchor_min_structure_hits": 1,
            "max_pages": 2,
            "minimum_rows": 3,
            "include_continuation": True,
            "continuation_min_structure_hits": 1,
            "continuation_min_numeric_lines": 1,
            "continuation_min_score": 4.0,
            "continuation_generic_numeric_lines": 2,
            "allow_single_row_tail": True,
        },
        {
            "table_id": "NON_LIFE_OPERATING_METRICS",
            "table_name": "主要经营指标（财险）",
            "title_terms": ["主要经营指标", "经营指标"],
            "content_terms": ["签单保费", "总资产", "车险车均保费"],
            "header_terms": ["指标名称", "本季度数"],
            "continuation_terms": ["签单保费"],
            "stop_terms": [],
            "terminal_terms": ["其他渠道签单保费"],
            "minimum_score": 7.0,
            "max_pages": 4,
            "minimum_rows": 3,
            "include_continuation": True,
            "continuation_min_structure_hits": 2,
            "continuation_min_numeric_lines": 2,
            "continuation_min_score": 4.0,
            "allow_single_row_tail": True,
        },
    ]
}


def build_adjacent_table_pdf() -> bytes:
    doc = fitz.open()
    pages = [
        "某财产保险公司2025年第4季度偿付能力报告摘要",
        "三、主要指标\n"
        "（二）流动性风险监管指标\n"
        "指标名称 本季度数 上季度可比数\n"
        "其中：基本情景 LCR1（％） 111.47% 105.83%\n"
        "（三）流动性风险监测指标\n"
        "指标名称 本季度末数 上季度末可比数\n"
        "经营活动净现金流（元） 718,430,831.15 847,841,117.53\n"
        "规模保费同比增速（％） 10.07% 9.71%\n"
        "现金及流动性管理工具占比（％） 18.51% 16.68%\n"
        "季均融资杠杆比例（％） 0.00 0.00\n"
        "应收款项占比（％） 25.31% 33.66%\n"
        "持有关联方资产占比（％） 0.00 0.00\n"
        "（六）主要经营指标\n"
        "指标名称 本季度数 本年度累计数\n"
        "保险业务收入（元） 964,379,502.91 7,362,400,072.66\n"
        "净利润（元） 61,793,719.10 274,176,547.39",
        "指标名称 本季度数 本年度累计数\n"
        "手续费及佣金占比（％） -- 4.94%\n"
        "签单保费（元） 961,400,344.73 7,339,853,702.59\n"
        "车险签单保费（元） 566,168,968.61 2,343,987,842.30\n"
        "四、风险管理能力\n"
        "（一）公司基本情况\n"
        "公司成立于2004年12月，属于I类保险公司。",
    ]
    for text in pages:
        page = doc.new_page()
        page.insert_textbox(fitz.Rect(40, 60, 550, 780), text, fontname="china-s", fontsize=12)
    return doc.tobytes()


def test_repeated_generic_header_does_not_extend_previous_table():
    """后一张表在续页重复的通用表头，不得把前一张表延长到该页。"""
    pdf = build_adjacent_table_pdf()
    matches, _ = locate_tables_hybrid(
        pdf,
        ADJACENT_TABLE_CONFIG,
        api_key="",
        base_url="",
        model="",
        mode="rule",
    )
    by_id = {match.table_id: match for match in matches}
    liquidity = by_id["LIQUIDITY_MONITORING"]
    operating = by_id["NON_LIFE_OPERATING_METRICS"]
    assert liquidity.pages == [2], (liquidity.pages, liquidity.evidence)
    assert operating.pages == [2, 3], (operating.pages, operating.evidence)
    print(
        f"PASS 相邻表重复表头: 流动性监测指标={liquidity.pages}，"
        f"主要经营指标={operating.pages}"
    )


if __name__ == "__main__":
    test_document_type_text()
    test_document_type_scanned()
    test_rule_mode_offline()
    test_rule_mode_llm_verify_agree()
    test_rule_mode_llm_verify_disagree()
    test_s05_single_row_tail_with_terminal_substring()
    test_foreign_form_code_boundary()
    test_level_one_heading_boundary()
    test_numeric_row_not_heading()
    test_sparse_tail_generic_continuation()
    test_wrapped_label_not_heading()
    test_consecutive_numeral_group_rows_not_heading()
    test_repeated_generic_header_does_not_extend_previous_table()
    print("全部通过")




