# -*- coding: utf-8 -*-
"""STEP7 英文展示切换的回归测试。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from streamlit.testing.v1 import AppTest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_step7_excel_modules import build_modules  # noqa: E402
from test_workflow_smoke import synthetic_integrated_data  # noqa: E402


def _app_with_modules(language: str) -> AppTest:
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=900)
    at.session_state["logged_in"] = True
    at.session_state["user_role"] = "项目组成员"
    at.session_state["active_profile_id"] = "NON_LIFE_SOLVENCY"
    data = synthetic_integrated_data()
    at.session_state["integrated_data"] = data
    at.session_state["standard_data"] = data
    at.session_state["step7_modules"] = build_modules()
    at.session_state["company_nav_level_one"] = "回归测试"
    at.session_state["company_nav_level_two"] = "全部"
    at.session_state["workflow_main_tab"] = "STEP7 公司报告"
    at.session_state["s7_company_types"] = ["全部"]
    at.session_state["s7_language"] = language
    at.session_state["s7_language_display"] = "English" if language == "en" else "中文"
    return at


def test_step7_english_display() -> None:
    at = _app_with_modules("en")
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    markdown_text = "\n".join(item.value for item in at.markdown)
    caption_text = "\n".join(item.value for item in at.caption)
    combined = markdown_text + "\n" + caption_text
    assert "Company Solvency Benchmarking Report" in combined
    assert "公司级偿付能力对标报告" not in combined
    assert "companies" in combined
    print("PASS STEP7 英文模式：报告标题与模块文案切换为英文")


def test_step7_chinese_display_by_default() -> None:
    at = _app_with_modules("zh")
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    markdown_text = "\n".join(item.value for item in at.markdown)
    assert "公司级偿付能力对标报告" in markdown_text
    print("PASS STEP7 中文模式：默认展示保持不变")


if __name__ == "__main__":
    test_step7_english_display()
    test_step7_chinese_display_by_default()
    print("全部通过")
