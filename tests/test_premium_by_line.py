# -*- coding: utf-8 -*-
"""分险种保费收入：集成表读取、同比增速、合并分组柱状图与中英文渲染回归。"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from openpyxl import load_workbook  # noqa: E402

from services.solvency_step7_charts import (  # noqa: E402
    INTEGRATED_SHEET_NAME,
    INTEGRATED_WORKBOOK_PATH,
    PREMIUM_BY_LINE_PATH,
    build_premium_by_line_chart,
    load_premium_by_line_data,
    ordered_premium_lines,
    prepare_premium_by_line_overview,
    premium_by_line_chart_frame,
    premium_by_line_source_key,
    premium_line_frame,
)
from services.report_i18n import localize_chart  # noqa: E402
from services.step7_excel_config import load_step7_config  # noqa: E402


def test_integrated_workbook_contains_sheet() -> None:
    assert INTEGRATED_WORKBOOK_PATH.exists(), "未找到行业集成数据工作簿"
    workbook = load_workbook(
        INTEGRATED_WORKBOOK_PATH,
        read_only=True,
        data_only=True,
    )
    try:
        assert INTEGRATED_SHEET_NAME in workbook.sheetnames
        sheet = workbook[INTEGRATED_SHEET_NAME]
        assert sheet.max_row == 52  # 表头 + 51 条记录
        assert sheet.max_column == 7
    finally:
        workbook.close()
    assert premium_by_line_source_key() == "integrated"


def test_source_data_is_loaded() -> None:
    assert PREMIUM_BY_LINE_PATH.exists()
    frame = load_premium_by_line_data()
    assert len(frame) == 51
    assert set(frame.columns) == {"年度", "险种", "原保险保费收入", "同比增速", "基期年度"}
    assert frame["年度"].min() == 2019
    assert frame["年度"].max() == 2025
    assert frame["险种"].nunique() == 9


def test_growth_uses_adjacent_year_with_data() -> None:
    frame = load_premium_by_line_data()
    motor = premium_line_frame(frame, "机动车辆保险")
    assert motor["年度"].tolist() == list(range(2019, 2026))
    latest = motor.iloc[-1]
    assert latest["原保险保费收入"] == 9409.0
    assert round(float(latest["同比增速"]), 2) == round((9409 / 9137 - 1) * 100, 2)
    assert latest["基期年度"] == "2024"

    engineering = premium_line_frame(frame, "工程保险")
    assert engineering["年度"].tolist() == [2019, 2020, 2021, 2022]
    assert engineering.iloc[-1]["同比增速"] is not None


def test_chart_frame_marks_missing_years() -> None:
    frame = load_premium_by_line_data()
    chart_frame = premium_by_line_chart_frame(frame)
    assert len(chart_frame) == 9 * 7  # 每个险种都补齐到 2019–2025
    missing = chart_frame[chart_frame["披露状态"].eq("未披露")]
    assert len(missing) == 4 * 3  # 4 个停报险种 × 2023–2025
    engineering = chart_frame[chart_frame["险种"].eq("工程保险")]
    assert engineering[engineering["年度"].eq("2022")]["原保险保费收入"].iloc[0] == 145.0
    assert set(engineering[engineering["披露状态"].eq("未披露")]["年度"]) == {
        "2023",
        "2024",
        "2025",
    }


def test_chart_is_one_grouped_bar_chart_with_missing_marks() -> None:
    frame = load_premium_by_line_data()
    lines = ordered_premium_lines(frame)
    chart = build_premium_by_line_chart(frame)
    spec = chart.to_dict()
    assert spec["width"] == "container"
    assert [layer["mark"]["type"] for layer in spec["layer"]] == [
        "bar",
        "bar",
        "rule",
        "line",
        "text",
    ]

    bars_other = spec["layer"][0]
    assert bars_other["encoding"]["x"]["field"] == "年度"
    assert bars_other["encoding"]["xOffset"]["field"] == "险种"
    assert bars_other["encoding"]["xOffset"]["sort"] == lines
    assert bars_other["encoding"]["y"]["field"] == "原保险保费收入"
    assert bars_other["encoding"]["y"]["axis"]["orient"] == "left"

    bars_motor = spec["layer"][1]
    assert bars_motor["encoding"]["y"]["field"] == "原保险保费收入"
    assert bars_motor["encoding"]["y"]["axis"]["orient"] == "right"
    assert bars_motor["encoding"]["y"]["title"] == "车险保费（亿元）"

    growth_line = spec["layer"][3]
    assert growth_line["encoding"]["y"]["field"] == "同比增速"
    assert growth_line["encoding"]["y"]["axis"]["orient"] == "right"
    assert growth_line["encoding"]["y"]["axis"]["offset"] == 58
    assert growth_line["encoding"]["y"]["axis"]["labelExpr"] == "datum.label + '%'"

    missing_marks = spec["layer"][4]
    assert missing_marks["mark"]["text"] == "×"
    assert missing_marks["encoding"]["xOffset"]["field"] == "险种"
    assert missing_marks["encoding"]["y"]["field"] == "标记位置"

    # The shared year axis must survive the layered independent y scales.
    vega_spec = chart.to_dict(format="vega")
    assert any(
        axis.get("scale") == "x" and axis.get("orient") == "bottom"
        for axis in vega_spec.get("axes", [])
    )


def test_english_localisation_has_no_chinese() -> None:
    frame = load_premium_by_line_data()
    spec = localize_chart(build_premium_by_line_chart(frame), "en").to_dict()
    text = str(spec)
    assert "Motor vehicle insurance" in text
    assert "Original premium income (RMB 100 mn)" in text
    assert "Not disclosed" in text

    rows: list[dict] = []
    for dataset_rows in spec.get("datasets", {}).values():
        if isinstance(dataset_rows, list):
            rows.extend(row for row in dataset_rows if isinstance(row, dict))
    assert any(row.get("险种") == "Motor vehicle insurance" for row in rows)
    assert any(row.get("披露状态") == "Not disclosed" for row in rows)
    assert not any(row.get("披露状态") == "未披露" for row in rows)


def test_overview_and_ordering() -> None:
    frame = load_premium_by_line_data()
    lines = ordered_premium_lines(frame)
    assert lines[0] == "机动车辆保险"
    assert lines[:5] == ["机动车辆保险", "健康险", "农业保险", "责任险", "意外险"]
    overview = prepare_premium_by_line_overview(frame)
    assert overview is not None
    assert overview.year == 2025
    assert overview.total == 15259.0
    assert overview.previous_total == 14570.0
    assert round(overview.growth, 2) == round((15259 / 14570 - 1) * 100, 2)
    assert overview.top_line == "机动车辆保险"
    assert overview.fastest_line == "健康险"
    assert set(overview.legacy_lines) == {
        "企业财产保险",
        "家庭财产保险",
        "工程保险",
        "保证保险",
    }


def test_template_places_section_between_scale_and_market_share() -> None:
    modules = load_step7_config(
        ROOT / "templates" / "Step7-偿付能力公司报告展示模块.xlsx"
    )
    industry = [module for module in modules if module.level_one == "行业概览"]
    assert [module.level_two for module in industry] == [
        "规模与发展",
        "分险种保费收入",
        "市场份额",
        "承保效益",
        "偿付能力披露整体情况",
    ]
    by_line = industry[1]
    assert by_line.chart_name == "分险种保费收入"
    assert by_line.chart_kind == "industry_premium_by_line"


def _app(level_two: str, language: str):
    from streamlit.testing.v1 import AppTest

    from test_workflow_smoke import synthetic_integrated_data

    template_path = ROOT / "templates" / "Step7-偿付能力公司报告展示模块.xlsx"
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=900)
    at.session_state["logged_in"] = True
    at.session_state["user_role"] = "项目组成员"
    at.session_state["active_profile_id"] = "NON_LIFE_SOLVENCY"
    data = synthetic_integrated_data()
    at.session_state["integrated_data"] = data
    at.session_state["standard_data"] = data
    at.session_state["step7_notes_bytes_NON_LIFE_SOLVENCY"] = template_path.read_bytes()
    at.session_state["workflow_main_tab"] = "STEP7 公司报告"
    at.session_state["company_nav_level_one"] = "行业概览"
    at.session_state["company_nav_level_two"] = level_two
    at.session_state["s7_company_types"] = ["全部"]
    at.session_state["s7_unit_mode"] = "亿元"
    at.session_state["s7_language"] = language
    at.session_state["s7_language_display"] = "English" if language == "en" else "中文"
    return at


def _dataset_text(at) -> str:
    return "\n".join(
        str(dataset.data)
        for chart in at.get("vega_lite_chart")
        for dataset in chart.proto.datasets
    )


def test_chinese_page_renders_one_grouped_chart_with_missing_marks() -> None:
    at = _app("分险种保费收入", "zh")
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    markdown_text = "\n".join(item.value for item in at.markdown)
    caption_text = "\n".join(item.value for item in at.caption)
    combined = markdown_text + "\n" + caption_text
    assert "分险种原保险保费收入与同比增速" in markdown_text
    assert "×" in combined
    assert "未披露" in combined
    assert len(at.get("vega_lite_chart")) == 1
    print("PASS 分险种保费收入：中文页面单张分组柱状图与缺失标记正常")


def test_english_page_has_no_chinese_leak() -> None:
    at = _app("分险种保费收入", "en")
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    markdown_text = "\n".join(item.value for item in at.markdown)
    caption_text = "\n".join(item.value for item in at.caption)
    combined = markdown_text + "\n" + caption_text
    assert "Original premium income by line" in combined
    assert "× Not disclosed" in combined
    assert len(at.get("vega_lite_chart")) == 1

    chart_text = ""
    for chart in at.get("vega_lite_chart"):
        chart_text += str(chart.proto.spec)
        chart_text += "\n".join(str(dataset.data) for dataset in chart.proto.datasets)

    combined = re.sub(r"<style[\s\S]*?</style>", "", combined)
    combined = re.sub(
        r'<section class="platform-page-heading"[\s\S]*?</section>',
        "",
        combined,
    )
    leaked = [
        line.strip()
        for line in (combined + "\n" + chart_text).splitlines()
        if re.search(r"[\u4e00-\u9fff]", line)
        and not any(
            marker in line
            for marker in (
                "当前身份：",
                "Profile：",
                "默认本地 Profile：",
                "目标表和定位关键词",
                "### 行业分析导航",
                "切换后会清空",
                "v2 配置：",
                "配置文件保存",
                "当前配置范围：",
            )
        )
    ]
    assert not leaked, "\n".join(leaked[:30])
    print("PASS 分险种保费收入：英文页面完成切换且无中文残留")


if __name__ == "__main__":
    test_integrated_workbook_contains_sheet()
    test_source_data_is_loaded()
    test_growth_uses_adjacent_year_with_data()
    test_chart_frame_marks_missing_years()
    test_chart_is_one_grouped_bar_chart_with_missing_marks()
    test_english_localisation_has_no_chinese()
    test_overview_and_ordering()
    test_template_places_section_between_scale_and_market_share()
    test_chinese_page_renders_one_grouped_chart_with_missing_marks()
    test_english_page_has_no_chinese_leak()
    print("全部通过")
