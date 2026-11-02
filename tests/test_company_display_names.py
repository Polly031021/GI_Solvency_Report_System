# -*- coding: utf-8 -*-
"""公司英文展示名回归测试。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.report_i18n import (  # noqa: E402
    COMPANY_NAMES_EN,
    company_name,
    t,
    translate_dataframe,
    translate_text,
)


def test_mapping_covers_all_companies_with_english_values() -> None:
    assert len(COMPANY_NAMES_EN) == 89
    assert all(isinstance(key, str) and key.strip() for key in COMPANY_NAMES_EN)
    assert all(
        isinstance(value, str) and value.strip() and not any(
            "\u4e00" <= char <= "\u9fff" for char in value
        )
        for value in COMPANY_NAMES_EN.values()
    )
    print("PASS 公司英文名：89 家公司均有非空英文展示名")


def test_representative_brand_names() -> None:
    expected = {
        "人保财险": "PICC P&C",
        "平安产险": "Ping An P&C",
        "太保产险": "CPIC P&C",
        "国寿财险": "China Life P&C",
        "中华财险": "China United",
        "众安保险": "ZhongAn",
        "利宝保险": "Liberty",
    }
    for chinese, english in expected.items():
        assert company_name(chinese, "en") == english
    assert company_name("平安产险（偿付能力）", "en") == "Ping An P&C"
    assert company_name("人保财险", "zh") == "人保财险"
    assert company_name("未收录公司", "en") == "未收录公司"
    print("PASS 公司英文名：品牌简称、后缀兼容与回退行为正确")


def test_all_names_translate_in_text_and_dataframes() -> None:
    for chinese, english in COMPANY_NAMES_EN.items():
        assert translate_text(chinese, "en") == english

    frame = pd.DataFrame(
        {"公司": ["人保财险", "平安产险"], "指标名称": ["保费", "保费"]}
    )
    translated = translate_dataframe(frame, "en")
    assert translated["Company"].tolist() == ["PICC P&C", "Ping An P&C"]
    assert translated["Metric"].tolist() == ["Premium", "Premium"]

    assert t("行业第一为人保财险", "Leader: 人保财险", "en") == "Leader: PICC P&C"
    assert t("行业第一为人保财险", "行业第一为人保财险", "zh") == "行业第一为人保财险"
    print("PASS 公司英文名：文本、表格与汇总文案均应用英文展示名")


if __name__ == "__main__":
    test_mapping_covers_all_companies_with_english_values()
    test_representative_brand_names()
    test_all_names_translate_in_text_and_dataframes()
    print("全部通过")
