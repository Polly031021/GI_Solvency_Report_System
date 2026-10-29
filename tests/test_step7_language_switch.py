# -*- coding: utf-8 -*-
"""STEP7 英文展示切换的回归测试。"""
from __future__ import annotations

import re
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


def test_step7_english_all_modules_have_no_chinese_ui_text() -> None:
    at = _app_with_modules("en")
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    texts = [
        item.value
        for item in [*at.markdown, *at.caption]
        if isinstance(item.value, str)
    ]
    combined = "\n".join(texts)
    combined = re.sub(r"<style[\s\S]*?</style>", "", combined)
    combined = re.sub(
        r'<section class="platform-page-heading"[\s\S]*?</section>',
        "",
        combined,
    )
    for company in ("甲公司", "乙公司"):
        combined = combined.replace(company, "")
    leaked = [
        line.strip()
        for line in combined.splitlines()
        if re.search(r"[\u4e00-\u9fff]", line)
        and not any(
            marker in line
            for marker in (
                "### 行业分析导航",
                "当前身份：",
                "切换后会清空",
                "目标表和定位关键词",
                "默认本地 Profile：",
                "Profile：",
                "v2 配置：",
                "配置文件保存",
                "当前配置范围：",
            )
        )
    ]
    charts = at.get("vega_lite_chart")
    chart_text = []
    for chart in charts:
        chart_text.append(str(chart.proto.spec))
        chart_text.extend(str(dataset.data) for dataset in chart.proto.datasets)
    chart_combined = "\n".join(chart_text)
    for company in ("甲公司", "乙公司"):
        chart_combined = chart_combined.replace(company, "")
    chart_leaks = [
        line.strip()
        for line in chart_combined.splitlines()
        if re.search(r"[\u4e00-\u9fff]", line)
    ]
    assert not chart_leaks, "\n".join(chart_leaks[:40])
    assert len(at.expander) >= len(build_modules())
    assert not leaked, "\n".join(leaked[:40])


if __name__ == "__main__":
    test_step7_english_display()
    test_step7_chinese_display_by_default()
    test_step7_english_all_modules_have_no_chinese_ui_text()
    print("全部通过")
