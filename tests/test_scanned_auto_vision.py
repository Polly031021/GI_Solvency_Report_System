# -*- coding: utf-8 -*-
"""扫描版 PDF 自动转视觉大模型定位的回归测试（离线，不访问网络）。

覆盖：
1. 整本扫描件在规则法模式下、未配置模型接口时，给出明确提示且不改变定位结果。
2. 配置了模型接口后，规则定位失败会自动改用 hybrid 定位并接管结果。
3. 文字版 PDF 完全不受影响（不触发自动转视觉）。
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import fitz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services import batch_processor  # noqa: E402
from services.batch_processor import BatchContext, locate_one_pdf_bytes  # noqa: E402
from services.solvency_pdf_locator import PageMatch  # noqa: E402

FEATURE_CONFIG = {
    "tables": [
        {
            "table_id": "SOLVENCY_MAIN",
            "table_name": "偿付能力状况表",
            "title_terms": ["偿付能力状况表"],
            "content_terms": ["认可资产", "认可负债"],
            "minimum_score": 8,
        }
    ]
}


def _blank_pdf(pages: int = 2) -> bytes:
    document = fitz.open()
    for _ in range(pages):
        document.new_page()
    data = document.tobytes()
    document.close()
    return data


def _text_pdf() -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Solvency table: recognized assets, liabilities, capital.", fontsize=12)
    data = document.tobytes()
    document.close()
    return data


def _context(api_key: str = "", base_url: str = "", model: str = "") -> BatchContext:
    profile = SimpleNamespace(profile_id="", locator_context=lambda: {})
    return BatchContext(
        profile=profile,
        feature_config=FEATURE_CONFIG,
        table_configs=FEATURE_CONFIG["tables"],
        taxonomy=None,
        target_catalog=None,
        include_derived=False,
        company_type_map={},
        default_company_type="",
        peer_group_map={},
        default_peer_group="",
        api_key=api_key,
        base_url=base_url,
        model=model,
        use_vision=False,
    )


def _fake_match() -> PageMatch:
    return PageMatch(
        table_id="SOLVENCY_MAIN",
        table_name="偿付能力状况表",
        pages=[2],
        score=9.0,
        evidence="视觉模型",
        confidence=80,
        confidence_basis="模型识别",
    )


def test_scanned_without_credentials_reports_hint():
    calls = []

    def fake_locate(pdf_bytes, feature_config, **kwargs):
        calls.append(kwargs.get("mode"))
        return [], "规则法未命中"

    original = batch_processor.locate_tables_hybrid
    batch_processor.locate_tables_hybrid = fake_locate
    try:
        result = locate_one_pdf_bytes("扫描件公司", _blank_pdf(), _context(), mode="rule")
    finally:
        batch_processor.locate_tables_hybrid = original
    assert calls == ["rule"], calls  # 没有配置时不应发起第二次（视觉）定位
    assert "扫描版" in result["说明"] and "配置" in result["说明"], result["说明"]
    assert result["matches"] == []
    print("PASS 扫描件未配置模型：给出配置提示且不改结果")


def test_scanned_with_credentials_switches_to_hybrid():
    calls = []

    def fake_locate(pdf_bytes, feature_config, **kwargs):
        mode = kwargs.get("mode")
        calls.append(mode)
        if mode == "rule":
            return [], "规则法未命中"
        return [_fake_match()], "视觉模型命中"

    original = batch_processor.locate_tables_hybrid
    batch_processor.locate_tables_hybrid = fake_locate
    try:
        result = locate_one_pdf_bytes(
            "扫描件公司",
            _blank_pdf(),
            _context("sk-test", "http://127.0.0.1:9/v1", "vision-test"),
            mode="rule",
        )
    finally:
        batch_processor.locate_tables_hybrid = original
    assert calls == ["rule", "hybrid"], calls
    assert result["扫描件自动识别"] is True
    assert [m.table_id for m in result["matches"]] == ["SOLVENCY_MAIN"]
    assert "视觉大模型" in result["说明"]
    print("PASS 扫描件已配置模型：自动转 hybrid 并接管结果")


def test_text_pdf_not_rerouted():
    calls = []

    def fake_locate(pdf_bytes, feature_config, **kwargs):
        calls.append(kwargs.get("mode"))
        return [_fake_match()], "规则法命中"

    original = batch_processor.locate_tables_hybrid
    batch_processor.locate_tables_hybrid = fake_locate
    try:
        result = locate_one_pdf_bytes(
            "文字版公司",
            _text_pdf(),
            _context("sk-test", "http://127.0.0.1:9/v1", "vision-test"),
            mode="rule",
        )
    finally:
        batch_processor.locate_tables_hybrid = original
    assert calls == ["rule"], calls
    assert "扫描件自动识别" not in result
    assert "视觉识别" not in result["说明"]
    print("PASS 文字版 PDF 不触发自动转视觉")


if __name__ == "__main__":
    test_scanned_without_credentials_reports_hint()
    test_scanned_with_credentials_switches_to_hybrid()
    test_text_pdf_not_rerouted()
    print("全部通过")
