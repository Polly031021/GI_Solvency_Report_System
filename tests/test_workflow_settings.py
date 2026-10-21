# -*- coding: utf-8 -*-
"""工作流设置导出/恢复回归测试。"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.solvency_pdf_locator import PageMatch  # noqa: E402
from services.workflow_settings import (  # noqa: E402
    apply_pending_settings_to_batch_documents,
    apply_workflow_settings_bundle,
    build_workflow_settings_bundle,
    bundle_from_json_bytes,
    bundle_to_json_bytes,
    count_workflow_settings,
)


def _match() -> PageMatch:
    return PageMatch(
        table_id="SOLVENCY_MAIN",
        table_name="偿付能力充足率指标",
        pages=[12, 13],
        score=1,
        evidence="配置恢复测试",
        review_required=False,
        review_reason="",
        sources={},
        strategy_id="",
        table_config={},
        confidence=95,
        confidence_basis="人工确认",
    )


def test_export_restore_pages_and_chart_settings() -> None:
    pdf = b"%PDF-config-test"
    state = {
        "llm_api_key": "must-not-export",
        "company_nav_level_one": "行业概览",
        "company_nav_level_two": "规模与发展",
        "step6_decimals": 3,
        "s7_unit_mode": "亿元",
        "batch_documents": {
            "众安产险": {
                "company": "众安产险",
                "pdf_name": "众安产险.pdf",
                "pdf_bytes": pdf,
                "metadata": {"页数": 32},
                "matches": [_match()],
                "edited_pages": {"SOLVENCY_MAIN": [12, 13]},
                "pages_confirmed": True,
            }
        },
    }
    bundle = build_workflow_settings_bundle(
        state,
        profile_id="NON_LIFE_SOLVENCY",
        scope="全部 Step",
    )
    exported = bundle_to_json_bytes(bundle)
    restored_bundle = bundle_from_json_bytes(exported)
    assert "llm_api_key" not in restored_bundle["settings"]
    assert restored_bundle["settings"]["company_nav_level_one"] == "行业概览"
    assert restored_bundle["settings"]["step6_decimals"] == 3

    target = {
        "batch_documents": {
            "众安产险": {
                "company": "众安产险",
                "pdf_name": "众安产险.pdf",
                "pdf_bytes": pdf,
                "metadata": {"页数": 32},
                "matches": [],
                "edited_pages": {},
                "pages_confirmed": False,
            }
        }
    }
    summary = apply_workflow_settings_bundle(
        restored_bundle,
        target,
        profile_id="NON_LIFE_SOLVENCY",
    )
    assert summary["applied_documents"] == 1
    document = target["batch_documents"]["众安产险"]
    assert document["matches"][0].pages == [12, 13]
    assert document["pages_confirmed"] is True
    assert target["company_nav_level_one"] == "行业概览"
    print("PASS 工作流配置导出与页码/图表设置恢复")


def test_pending_config_binds_after_pdf_upload() -> None:
    pdf = b"%PDF-pending-test"
    saved = {
        "company": "甲公司",
        "pdf_name": "甲公司.pdf",
        "file_size": len(pdf),
        "pdf_sha256": __import__("hashlib").sha256(pdf).hexdigest(),
        "matches": [_match().__dict__],
    }
    documents = {
        "甲公司": {
            "company": "甲公司",
            "pdf_name": "甲公司.pdf",
            "pdf_bytes": None,
            "uploaded_file": SimpleNamespace(size=len(pdf)),
            "metadata": {},
            "matches": [],
        }
    }
    applied = apply_pending_settings_to_batch_documents(documents, [saved])
    assert applied == ["甲公司"]
    assert documents["甲公司"]["matches"][0].pages == [12, 13]
    print("PASS 配置文件先导入、PDF 后上传时自动绑定页码")


def test_action_widget_keys_never_restored() -> None:
    """按钮/上传器的 key 不能被当成设置导出或写回会话状态。"""
    state = {
        "batch_locate_all": True,
        "batch_locate_workers": 4,
        "batch_locate_targets_NON_LIFE_SOLVENCY": ["S05-最低资本表"],
        "monitor_all": True,
        "monitor_one": True,
        "step4_goto_step3_abc": True,
    }
    bundle = build_workflow_settings_bundle(
        state,
        profile_id="NON_LIFE_SOLVENCY",
        scope="STEP1 页码定位",
    )
    assert "batch_locate_all" not in bundle["settings"]
    assert bundle["settings"]["batch_locate_workers"] == 4

    all_steps = build_workflow_settings_bundle(
        state,
        profile_id="NON_LIFE_SOLVENCY",
        scope="全部 Step",
    )
    assert "monitor_all" not in all_steps["settings"]
    assert "monitor_one" not in all_steps["settings"]
    assert "step4_goto_step3_abc" not in all_steps["settings"]

    target = {
        "batch_locate_all": True,
        "step4_goto_step3_old": True,
        "batch_locate_workers": 1,
    }
    summary = apply_workflow_settings_bundle(
        {
            "schema_version": 1,
            "profile_id": "NON_LIFE_SOLVENCY",
            "settings": {
                "batch_locate_all": True,
                "step4_goto_step3_old": True,
                "batch_locate_workers": 6,
            },
            "documents": [],
        },
        target,
        profile_id="NON_LIFE_SOLVENCY",
    )
    assert "batch_locate_all" not in target
    assert "step4_goto_step3_old" not in target
    assert target["batch_locate_workers"] == 6
    assert summary["applied_settings"] == 1
    assert count_workflow_settings(target, scope="STEP1 页码定位") == 1
    print("PASS 动作按钮 key 不会被导入为工作流设置")


if __name__ == "__main__":
    test_export_restore_pages_and_chart_settings()
    test_pending_config_binds_after_pdf_upload()
    test_action_widget_keys_never_restored()
    print("全部通过")
