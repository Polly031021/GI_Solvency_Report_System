# -*- coding: utf-8 -*-
"""STEP1 页码与公司 YAML 对比：通过/偏移/超出/未定位/未配置与自动确认条件。"""

from __future__ import annotations

from types import SimpleNamespace

from services.extraction_rules import (
    ResolvedRule,
    compare_pages_with_rule,
    page_check_passed,
)


def _match(pages, *, table_id="SOLVENCY_MAIN", review=False):
    return SimpleNamespace(
        table_id=table_id,
        table_name="偿付能力充足率指标",
        pages=list(pages),
        review_required=review,
        review_reason="",
    )


def _rule(expected=(10, 11), tolerance=2, table_id="SOLVENCY_MAIN"):
    return ResolvedRule(
        canonical="测试公司",
        source="测试公司.yaml",
        rule_version="1",
        structure_version=None,
        tables={table_id: {"expected_pages": list(expected), "tolerance": tolerance}},
    )


def test_exact_match_passes() -> None:
    checks = compare_pages_with_rule([_match([10, 11])], _rule())
    assert checks[0].status == "通过"
    assert checks[0].passed is True


def test_drift_inside_window_passes() -> None:
    checks = compare_pages_with_rule([_match([9])], _rule())
    assert checks[0].status == "偏移"
    assert checks[0].passed is True
    assert checks[0].window == (8, 13)


def test_outside_window_fails() -> None:
    checks = compare_pages_with_rule([_match([20])], _rule())
    assert checks[0].status == "超出窗口"
    assert checks[0].passed is False


def test_missing_pages_fails() -> None:
    checks = compare_pages_with_rule([_match([])], _rule())
    assert checks[0].status == "未定位"
    assert checks[0].passed is False


def test_unconfigured_table_requires_manual_confirm() -> None:
    checks = compare_pages_with_rule([_match([10])], None)
    assert checks[0].status == "未配置"
    assert checks[0].passed is False
    assert page_check_passed(checks) is False


def test_auto_confirm_only_when_all_pass() -> None:
    matches = [_match([10, 11])]
    checks = compare_pages_with_rule(matches, _rule())
    assert page_check_passed(checks, matches) is True

    conflict_matches = [_match([10, 11], review=True)]
    conflict_checks = compare_pages_with_rule(conflict_matches, _rule())
    assert page_check_passed(conflict_checks, conflict_matches) is False

    mixed = [
        _match([10, 11]),
        SimpleNamespace(
            table_id="S05",
            table_name="最低资本表",
            pages=[20],
            review_required=False,
            review_reason="",
        ),
    ]
    mixed_checks = compare_pages_with_rule(mixed, _rule())
    assert mixed_checks[0].passed and not mixed_checks[1].passed
    assert page_check_passed(mixed_checks, mixed) is False


if __name__ == "__main__":
    test_exact_match_passes()
    test_drift_inside_window_passes()
    test_outside_window_fails()
    test_missing_pages_fails()
    test_unconfigured_table_requires_manual_confirm()
    test_auto_confirm_only_when_all_pass()
    print("test_page_check_with_rule: OK")
