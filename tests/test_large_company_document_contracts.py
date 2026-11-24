"""Structural checks for the top-ten plus large-company migration scope."""

import ast
from pathlib import Path

from services.company_extractors import get_company_document_hooks
from services.extraction_rules import resolve_rule
from tools.company_extraction_audit import ALL_COMPANIES
from tools.large_company_contract_audit import scope_companies


ROOT = Path(__file__).resolve().parents[1]


def test_all_large_companies_have_document_hooks_and_six_contracts() -> None:
    companies = scope_companies(
        ROOT / "财险公司列表.xlsx",
        {"前十大", "大型公司"},
    )
    assert len(companies) == 22
    missing_hooks: list[str] = []
    missing_contracts: list[str] = []
    for company in companies:
        rule = resolve_rule(company, 2025, profile_id="NON_LIFE_SOLVENCY")
        assert rule is not None, company
        locator, extractor = get_company_document_hooks(rule.extractor)
        if not callable(locator) or not callable(extractor):
            missing_hooks.append(rule.canonical)
        if set(rule.layout_contracts) != {
            "SOLVENCY_MAIN",
            "NON_LIFE_OPERATING_METRICS",
            "ACTUAL_CAPITAL",
            "MINIMUM_CAPITAL",
            "LIQUIDITY_MONITORING",
            "THREE_YEAR_INVESTMENT_RETURN",
        }:
            missing_contracts.append(rule.canonical)
    assert not missing_hooks, missing_hooks
    assert not missing_contracts, missing_contracts
    print("PASS 22 家大型公司均有公司专属 hooks 与六表契约")


def test_all_repository_companies_have_explicit_document_hooks() -> None:
    missing: list[str] = []
    for company in ALL_COMPANIES:
        rule = resolve_rule(
            company,
            2025,
            profile_id="NON_LIFE_SOLVENCY",
        )
        assert rule is not None, company
        module_path = (
            ROOT
            / "services"
            / "company_extractors"
            / f"{rule.extractor}.py"
        )
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        functions = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        if not {
            "locate_company_tables",
            "extract_company_tables",
        }.issubset(functions):
            missing.append(rule.canonical)
            continue
        locator, extractor = get_company_document_hooks(rule.extractor)
        if not callable(locator) or not callable(extractor):
            missing.append(rule.canonical)
    assert not missing, missing
    print("PASS 89 家公司均显式声明 document hooks")


def test_all_workbook_company_names_route_to_rules() -> None:
    companies = scope_companies(
        ROOT / "财险公司列表.xlsx",
        {
            "前十大",
            "大型公司",
            "中型公司",
            "小型公司",
            "外资系",
            "农业保险",
            "自保公司",
        },
    )
    missing = [
        company
        for company in companies
        if resolve_rule(company, 2025, profile_id="NON_LIFE_SOLVENCY") is None
    ]
    assert len(companies) == 89
    assert not missing, missing
    print("PASS 89 个公司清单名称均可路由到公司规则")


if __name__ == "__main__":
    test_all_large_companies_have_document_hooks_and_six_contracts()
    test_all_repository_companies_have_explicit_document_hooks()
    test_all_workbook_company_names_route_to_rules()
