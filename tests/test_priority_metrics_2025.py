"""PDF-backed regression for the user's two priority metrics."""

from pathlib import Path

import pytest

from tools.company_extraction_audit import (
    ROOT, TOP_TEN, audit_one, build_context_from_profile, load_profile_workbook,
    load_validation_rules,
)
from tools.priority_metrics_audit import audit_priority_metrics


GROWTH = {
    "人保产险": (3.29, 3.47, 18), "平安产险": (6.5, 6.9, 18),
    "太保产险": (-0.1, 0.1, 11), "国寿财险": (1.36, 3.0, 19),
    "众安产险": (6.93, 5.64, 14), "英大财产": (2.88, 3.14, 14),
    "大地财产": (3.99, 6.01, 23), "中华联合": (3.68, 3.2, 18),
    "太平财险": (4.0, 4.24, 14), "阳光产险": (0.26, 0.81, 14),
}


@pytest.fixture(scope="module")
def context():
    profile = load_profile_workbook(
        (ROOT / "config/report_profiles/NON_LIFE_SOLVENCY_profile.xlsx").read_bytes(),
        project_root=ROOT,
    )
    return build_context_from_profile(profile, api_key="", base_url="", model="")


@pytest.mark.parametrize("company", TOP_TEN)
def test_priority_disclosures_2025(company, context, tmp_path):
    rules = load_validation_rules(ROOT / "config/solvency_validation_rules.xlsx")
    result = audit_one(company, 2025, context, tmp_path, rules)
    assert result["状态"] == "成功"
    report = audit_priority_metrics(tmp_path)
    growth = report[report["指标编码"].eq("PREMIUM_GROWTH")].set_index("期间口径")
    current, prior, page = GROWTH[company]
    assert growth.loc["本季度末数", "数值"] == pytest.approx(current)
    assert growth.loc["上季度末数", "数值"] == pytest.approx(prior)
    assert growth["来源页码"].astype(int).eq(page).all()
    loss = report[report["指标编码"].eq("CONTRACT_LOSS_ABSORPTION_EFFECT")]
    if company in {"人保产险", "英大财产"}:
        assert loss["状态"].eq("原文空白按0").all()
        assert loss["数值"].eq(0).all()
    elif company == "太平财险":
        assert loss["状态"].eq("未找到披露行").all()
        assert loss["数值"].isna().all()
    else:
        assert loss["状态"].eq("已提取").all()
        assert loss["数值"].eq(0).all()
