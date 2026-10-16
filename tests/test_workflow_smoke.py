# -*- coding: utf-8 -*-
"""全流程回归测试：注入合成数据后逐页渲染 STEP0-STEP8，验证页面不报错。

与 test_app_smoke.py 的区别：
- test_app_smoke 只验证"登录后首页"能渲染；
- 本测试为 STEP6/STEP7/STEP8 准备了一份最小可用的集成数据集，
  并把工作流主标签逐个切换过去，确保拆分模块后每个页面仍然可渲染。

测试完全离线：不调用任何大模型接口，也不访问外网。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.solvency_navigation import (  # noqa: E402
    COMPANY_NAVIGATION,
    INDUSTRY_NAVIGATION,
)
from services.solvency_normalizer import STANDARD_COLUMNS  # noqa: E402

WORKFLOW_TAB_LABELS = (
    "STEP0 报告监控",
    "STEP1 页码定位",
    "STEP2 表格提取",
    "STEP3 标准化",
    "STEP4 勾稽检查",
    "STEP5 数据集成",
    "STEP6 自定义分析",
    "STEP7 公司报告",
    "STEP8 行业分析",
)

PERIODS = ("2025Q3", "2025Q4")
COMPANIES = ("甲公司", "乙公司")
INDUSTRY_TOTAL = "行业合计"


def _is_percent_metric(code: str) -> bool:
    """比率类指标按百分比构造，其余按金额（万元）构造。"""
    markers = (
        "RATIO", "TO_QUANT_CAPITAL", "TO_ASSETS", "TO_LIABILITIES",
        "TO_CORE_CAPITAL", "EFFECT", "GROWTH",
    )
    return any(marker in code for marker in markers)


def _metric_codes() -> list[str]:
    codes: list[str] = []
    for entry in (*COMPANY_NAVIGATION, *INDUSTRY_NAVIGATION):
        codes.extend(entry.metric_codes)
    codes.extend([
        "REGISTERED_CAPITAL", "SIGNED_PREMIUM", "NET_PROFIT",
        "RECOGNIZED_ASSETS", "MINIMUM_CAPITAL",
    ])
    return list(dict.fromkeys(code for code in codes if code))


def _row(
    code: str,
    metric_index: int,
    period: str,
    company: str,
    company_index: int,
    period_index: int,
) -> dict[str, object]:
    percent = _is_percent_metric(code)
    if percent:
        value = 80.0 + metric_index * 3 + company_index * 12.5 + period_index * 4
        unit, data_type = "%", "百分比"
    else:
        value = 100_000.0 * (metric_index + 1) * (company_index + 2) * (period_index + 1)
        unit, data_type = "万元", "金额"
    return {
        "公司": company,
        "公司类型": "财险",
        "同业分类": "头部" if company_index == 0 else "中小",
        "报告类型": "NON_LIFE_SOLVENCY",
        "报告年度": 2025,
        "报告季度": period,
        "报告期": period,
        "一级模块": "偿付能力",
        "二级模块": "资本",
        "指标编码": code,
        "指标名称": f"指标{metric_index}",
        "期间口径": "本季度末数",
        "数值": value,
        "单位": unit,
        "数据类型": data_type,
        "来源类型": "合成数据",
        "指标属性": "披露",
        "来源文件": "synthetic.xlsx",
        "计算逻辑": "",
    }


def synthetic_integrated_data() -> pd.DataFrame:
    """构造满足标准窄表 schema 的合成集成数据（含行业合计行）。"""
    rows: list[dict[str, object]] = []
    for metric_index, code in enumerate(_metric_codes()):
        for period_index, period in enumerate(PERIODS):
            for company_index, company in enumerate(COMPANIES):
                rows.append(
                    _row(code, metric_index, period, company, company_index, period_index)
                )
            # 行业合计行：STEP8 的行业概览与水瀑布图依赖这些记录
            rows.append(
                _row(code, metric_index, period, INDUSTRY_TOTAL, 0, period_index)
            )
    frame = pd.DataFrame(rows)
    frame.loc[frame["公司"].eq(INDUSTRY_TOTAL), "公司类型"] = INDUSTRY_TOTAL
    for column in STANDARD_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
    return frame[STANDARD_COLUMNS]


def _app_with_data() -> AppTest:
    """启动应用并注入"已登录的项目组成员 + 合成集成数据"的会话状态。"""
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=600)
    at.session_state["logged_in"] = True
    at.session_state["user_role"] = "项目组成员"
    at.session_state["active_profile_id"] = "NON_LIFE_SOLVENCY"
    data = synthetic_integrated_data()
    at.session_state["integrated_data"] = data
    at.session_state["standard_data"] = data
    at.session_state["integration_preview"] = data
    at.session_state["integration_preview_mode"] = "标准窄表"
    at.session_state["llm_base_url"] = ""
    at.session_state["llm_model"] = ""
    at.session_state["llm_api_key"] = ""
    return at


def test_every_workflow_page_renders() -> None:
    """逐个切换工作流标签，任何页面抛异常都会让测试失败。"""
    at = _app_with_data()
    at.run()
    assert not at.exception, [e.value for e in at.exception]

    for label in WORKFLOW_TAB_LABELS:
        at.session_state["workflow_main_tab"] = label
        at.run()
        assert not at.exception, (label, [e.value for e in at.exception])
        print(f"PASS 页面渲染：{label}")


def test_print_all_charts_render() -> None:
    """公司报告/行业报告选择"全部报表"时，批量渲染所有内置图表。"""
    from services.solvency_navigation import PRINT_ALL_LABEL

    at = _app_with_data()
    at.session_state["company_nav_level_one"] = PRINT_ALL_LABEL
    at.session_state["industry_nav_level_one"] = PRINT_ALL_LABEL
    at.session_state["workflow_main_tab"] = "STEP7 公司报告"
    at.run()
    assert not at.exception, ("STEP7", [e.value for e in at.exception])
    at.session_state["workflow_main_tab"] = "STEP8 行业分析"
    at.run()
    assert not at.exception, ("STEP8", [e.value for e in at.exception])
    print("PASS 全部报表批量渲染（STEP7 + STEP8）")


if __name__ == "__main__":
    test_every_workflow_page_renders()
    test_print_all_charts_render()
    print("全部通过")
