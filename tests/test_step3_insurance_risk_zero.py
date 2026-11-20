# -*- coding: utf-8 -*-
"""财险公司寿险业务保险风险最低资本默认 0。"""

from __future__ import annotations

import pandas as pd

from services.solvency_step3_standardizer import _fill_insurance_risk_zero_default


def _catalog() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "指标编码": "INSURANCE_RISK_CAPITAL",
                "指标名称": "寿险业务保险风险最低资本合计",
                "一级模块": "最低资本",
                "二级模块": "保险风险",
                "标准单位": "万元",
            }
        ]
    )


def _data(include_zero: bool = False) -> pd.DataFrame:
    rows = [
        {
            "公司": "平安产险",
            "报告期": "2025Q4",
            "指标编码": "QUANT_RISK_CAPITAL",
            "指标名称": "量化风险最低资本",
            "期间口径": "本季度末数",
            "数值": 100.0,
            "单位": "万元",
        },
        {
            "公司": "平安产险",
            "报告期": "2025Q4",
            "指标编码": "NON_LIFE_INSURANCE_RISK_CAPITAL",
            "指标名称": "非寿险业务保险风险最低资本合计",
            "期间口径": "本季度末数",
            "数值": 80.0,
            "单位": "万元",
        },
    ]
    if include_zero:
        rows.append(
            {
                "公司": "平安产险",
                "报告期": "2025Q4",
                "指标编码": "INSURANCE_RISK_CAPITAL",
                "指标名称": "寿险业务保险风险最低资本合计",
                "期间口径": "本季度末数",
                "数值": 0.0,
                "单位": "万元",
            }
        )
    return pd.DataFrame(rows)


def test_non_life_fills_zero_when_missing() -> None:
    result = _fill_insurance_risk_zero_default(
        _data(),
        _catalog(),
        company_type="财险",
        report_profile_id="NON_LIFE_SOLVENCY",
    )
    hit = result[result["指标编码"].eq("INSURANCE_RISK_CAPITAL")]
    assert len(hit) == 1
    assert float(hit.iloc[0]["数值"]) == 0.0
    assert "恒为0" in str(hit.iloc[0]["备注"])
    assert hit.iloc[0]["期间口径"] == "本季度末数"


def test_existing_value_not_duplicated() -> None:
    result = _fill_insurance_risk_zero_default(
        _data(include_zero=True),
        _catalog(),
        company_type="财险",
        report_profile_id="NON_LIFE_SOLVENCY",
    )
    hit = result[result["指标编码"].eq("INSURANCE_RISK_CAPITAL")]
    assert len(hit) == 1


def test_life_profile_not_filled() -> None:
    result = _fill_insurance_risk_zero_default(
        _data(),
        _catalog(),
        company_type="寿险",
        report_profile_id="LIFE_SOLVENCY",
    )
    assert result["指标编码"].eq("INSURANCE_RISK_CAPITAL").sum() == 0


if __name__ == "__main__":
    test_non_life_fills_zero_when_missing()
    test_existing_value_not_duplicated()
    test_life_profile_not_filled()
    print("test_step3_insurance_risk_zero: OK")
