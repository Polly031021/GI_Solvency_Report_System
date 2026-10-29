# -*- coding: utf-8 -*-
"""Step 7 company filters must keep every option and show checked selections."""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from services.step7_excel_config import load_step7_config
from test_workflow_smoke import synthetic_integrated_data


ROOT = Path(__file__).resolve().parents[1]


def test_filter_popovers_keep_options_visible_after_selection() -> None:
    data = synthetic_integrated_data()
    template_path = ROOT / "templates" / "Step7-偿付能力公司报告展示模块.xlsx"
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=900)
    at.session_state["logged_in"] = True
    at.session_state["user_role"] = "项目组成员"
    at.session_state["active_profile_id"] = "NON_LIFE_SOLVENCY"
    at.session_state["integrated_data"] = data
    at.session_state["standard_data"] = data
    at.session_state["step7_modules"] = load_step7_config(template_path)
    at.session_state["workflow_main_tab"] = "STEP7 公司报告"
    at.session_state["company_nav_level_one"] = "经营与偿付能力联动分析"
    at.session_state["company_nav_level_two"] = "保费增速"
    at.session_state["s7_company_types"] = ["全部"]
    at.session_state["s7_unit_mode"] = "亿元"
    at.run()

    assert not at.exception, [error.value for error in at.exception]
    type_checks = [
        item for item in at.checkbox if str(item.key).startswith("s7_company_types_option_")
    ]
    company_checks = [
        item
        for item in at.checkbox
        if str(item.key).startswith("s7_companies_scope_v3_")
    ]
    assert [item.label for item in type_checks][0] == "全部"
    assert set(item.label for item in type_checks[1:]) == {"头部", "中小"}
    assert [item.label for item in company_checks] == ["甲公司", "乙公司"]
    assert all(item.value for item in company_checks)

    company_checks[1].set_value(False).run()
    assert not at.exception, [error.value for error in at.exception]
    company_checks_after = [
        item
        for item in at.checkbox
        if str(item.key).startswith("s7_companies_scope_v3_")
    ]
    assert [item.label for item in company_checks_after] == ["甲公司", "乙公司"]
    assert [item.value for item in company_checks_after] == [True, False]
    canonical_key = str(company_checks_after[0].key).split("_option_", maxsplit=1)[0]
    assert at.session_state[canonical_key] == ["甲公司"]

    head_type = next(
        item
        for item in at.checkbox
        if str(item.key).startswith("s7_company_types_option_")
        and item.label == "头部"
    )
    head_type.set_value(True).run()
    assert not at.exception, [error.value for error in at.exception]

    type_checks_after = [
        item for item in at.checkbox if str(item.key).startswith("s7_company_types_option_")
    ]
    company_checks_after_type = [
        item
        for item in at.checkbox
        if str(item.key).startswith("s7_companies_scope_v3_")
    ]
    assert set(item.label for item in type_checks_after) == {"全部", "头部", "中小"}
    assert next(item for item in type_checks_after if item.label == "头部").value
    assert [item.label for item in company_checks_after_type] == ["甲公司"]
    assert company_checks_after_type[0].value

    module_expanders = [
        item
        for item in at.expander
        if item.label == "经营与偿付能力联动分析 - 保费增速"
    ]
    assert len(module_expanders) == 1
    # Module expansion is client-side; it must not create a server-side toggle
    # that reruns and redraws every expensive chart.
    assert not any(
        str(item.key).startswith("s7_module_expanded_") for item in at.toggle
    )
    assert at.get("vega_lite_chart")
