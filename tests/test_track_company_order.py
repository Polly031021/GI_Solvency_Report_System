# -*- coding: utf-8 -*-
"""Track Company 置顶规则：类型池内可置顶/自动并入，类型池外不得绕过。"""

from __future__ import annotations

from step7_solvency.selection import merge_tracked_company


def test_no_track_keeps_order() -> None:
    companies = ["甲公司", "乙公司", "丙公司"]
    assert merge_tracked_company(companies, "无") == companies
    assert merge_tracked_company(companies, "") == companies
    assert merge_tracked_company(companies, "   ") == companies


def test_tracked_company_pinned_first() -> None:
    companies = ["甲公司", "乙公司", "丙公司"]
    assert merge_tracked_company(companies, "丙公司") == [
        "丙公司",
        "甲公司",
        "乙公司",
    ]


def test_tracked_company_added_when_missing() -> None:
    # 追踪公司在类型池内、但用户没勾选展示：自动并入并置顶。
    companies = ["甲公司", "乙公司"]
    assert merge_tracked_company(companies, "丙公司", ["甲公司", "乙公司", "丙公司"]) == [
        "丙公司",
        "甲公司",
        "乙公司",
    ]


def test_tracked_company_outside_type_pool_is_ignored() -> None:
    # 不允许绕过公司类型筛选：类型池外的追踪公司保持原顺序。
    companies = ["甲公司", "乙公司"]
    assert merge_tracked_company(
        companies, "丁公司", ["甲公司", "乙公司", "丙公司"]
    ) == companies


def test_tracked_company_inside_list_but_outside_pool_is_ignored() -> None:
    companies = ["甲公司", "乙公司", "丁公司"]
    assert merge_tracked_company(
        companies, "丁公司", ["甲公司", "乙公司"]
    ) == companies


def test_empty_company_list_with_tracked() -> None:
    assert merge_tracked_company([], "丙公司", ["丙公司"]) == ["丙公司"]
    assert merge_tracked_company([], "丙公司", ["甲公司"]) == []


if __name__ == "__main__":
    test_no_track_keeps_order()
    test_tracked_company_pinned_first()
    test_tracked_company_added_when_missing()
    test_tracked_company_outside_type_pool_is_ignored()
    test_tracked_company_inside_list_but_outside_pool_is_ignored()
    test_empty_company_list_with_tracked()
    print("test_track_company_order: OK")
