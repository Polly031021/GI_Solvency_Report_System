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


if __name__ == "__main__":
    test_document_type_text()
    test_document_type_scanned()
    test_rule_mode_offline()
    test_rule_mode_llm_verify_agree()
    test_rule_mode_llm_verify_disagree()
    test_s05_single_row_tail_with_terminal_substring()
    print("全部通过")




