# -*- coding: utf-8 -*-
"""公司级提取规则：别名归一、结构版本、表配置合并与校验。"""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

from services import extraction_rules as rules
from services.company_extractors.dadi import _contract_failure


def _write_rule(directory: Path, name: str, text: str) -> Path:
    path = directory / f"{name}.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _temp_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="gi_rules_"))


def test_repository_rules_load_and_normalize_aliases() -> None:
    catalog = rules.load_company_rules(force=True)
    assert set(catalog) >= {"人保产险", "平安产险", "太保产险"}

    resolved = rules.resolve_rule(
        "中国人民财产保险股份有限公司", 2024, profile_id="NON_LIFE_SOLVENCY"
    )
    assert resolved is not None
    assert resolved.canonical == "人保产险"
    assert resolved.tables["NON_LIFE_OPERATING_METRICS"]["expected_pages"] == [
        20,
        21,
        22,
        23,
    ]

    # 2025 文件名带“（偿付能力）”，文件名差异不影响封面路由。
    taibao = rules.resolve_rule(
        "中国太平洋财产保险股份有限公司", 2025, profile_id="NON_LIFE_SOLVENCY"
    )
    assert taibao is not None
    assert taibao.canonical == "太保产险"


def test_unknown_company_returns_none() -> None:
    assert rules.resolve_rule("不存在的公司", 2024) is None
    assert rules.resolve_rule("", 2024) is None


def test_missing_year_keeps_rule_and_warns() -> None:
    resolved = rules.resolve_rule("平安产险", None)
    assert resolved is not None
    assert resolved.structure_version is None
    assert any("YEAR_UNKNOWN" in warning for warning in resolved.warnings)


def test_structure_version_selection() -> None:
    directory = _temp_dir()
    _write_rule(
        directory,
        "测试公司",
        """
company:
  canonical: 测试公司
  profile_id: NON_LIFE_SOLVENCY
tables:
  NON_LIFE_OPERATING_METRICS:
    expected_pages: [10]
    page_match: prefer
structure_versions:
  - since: 2025
    notes: 结构改版
    tables:
      NON_LIFE_OPERATING_METRICS:
        expected_pages: [14]
        anchor_terms: [经营情况分析]
""",
    )
    try:
        v2024 = rules.resolve_rule("测试公司", 2024, directory=directory)
        v2025 = rules.resolve_rule("测试公司", 2025, directory=directory)
        assert v2024 is not None and v2025 is not None
        assert v2024.structure_version is None
        assert v2024.tables["NON_LIFE_OPERATING_METRICS"]["expected_pages"] == [10]
        assert v2025.structure_version == 2025
        assert v2025.tables["NON_LIFE_OPERATING_METRICS"]["expected_pages"] == [14]
        assert v2025.tables["NON_LIFE_OPERATING_METRICS"]["anchor_terms"] == [
            "经营情况分析"
        ]
        # 未覆盖的字段从主体继承。
        assert (
            v2025.tables["NON_LIFE_OPERATING_METRICS"]["page_match"] == "prefer"
        )
    finally:
        for path in directory.glob("*"):
            path.unlink(missing_ok=True)
        directory.rmdir()


def test_apply_rule_merges_profile_config_without_mutating() -> None:
    directory = _temp_dir()
    _write_rule(
        directory,
        "合并测试",
        """
company:
  canonical: 合并测试
tables:
  NON_LIFE_OPERATING_METRICS:
    expected_pages: [20, 21]
    tolerance: 2
    anchor_terms: [主要经营指标]
""",
    )
    try:
        resolved = rules.resolve_rule("合并测试", 2024, directory=directory)
        assert resolved is not None
        original = [
            {
                "table_id": "NON_LIFE_OPERATING_METRICS",
                "table_name": "主要经营指标",
                "title_terms": ["旧标题"],
                "max_pages": 2,
            },
            {"table_id": "SOLVENCY_MAIN", "table_name": "偿付能力充足率指标"},
        ]
        merged = rules.apply_rule_to_tables(original, resolved)
        assert original[0]["title_terms"] == ["旧标题"]
        assert merged[0]["expected_pages"] == [20, 21]
        assert merged[0]["tolerance"] == 2
        assert merged[0]["page_match"] == "prefer"
        assert merged[0]["title_terms"] == ["主要经营指标"]
        assert merged[0]["max_pages"] == 2
        assert merged[0]["_extraction_rule"]["source"] == "合并测试.yaml"
        # 规则里没有的表保持原样。
        assert merged[1] == original[1]
    finally:
        for path in directory.glob("*"):
            path.unlink(missing_ok=True)
        directory.rmdir()


def test_not_disclosed_table_flag_is_merged_into_profile_config() -> None:
    directory = _temp_dir()
    _write_rule(
        directory,
        "未披露测试",
        """
company:
  canonical: 未披露测试
tables:
  ACTUAL_CAPITAL:
    not_disclosed: true
    notes: 特殊主体不披露资本金额
""",
    )
    try:
        resolved = rules.resolve_rule("未披露测试", 2025, directory=directory)
        assert resolved is not None
        merged = rules.apply_rule_to_tables(
            [{"table_id": "ACTUAL_CAPITAL", "table_name": "实际资本"}],
            resolved,
        )
        assert merged[0]["not_disclosed"] is True
        assert merged[0]["notes"] == "特殊主体不披露资本金额"
    finally:
        for path in directory.glob("*"):
            path.unlink(missing_ok=True)
        directory.rmdir()


def test_dadi_layout_contract_is_applied_to_profile_tables() -> None:
    resolved = rules.resolve_rule(
        "大地财产",
        2022,
        profile_id="NON_LIFE_SOLVENCY",
    )
    assert resolved is not None
    assert resolved.layout_contracts["SOLVENCY_MAIN"]["headers"] == [
        "项目",
        "本季度数",
        "上季度数",
        "基本情景下季度预测数",
    ]
    merged = rules.apply_rule_to_tables(
        [
            {"table_id": "SOLVENCY_MAIN"},
            {"table_id": "NON_LIFE_OPERATING_METRICS"},
        ],
        resolved,
    )
    main_contract = merged[0]["_layout_contract"]
    assert main_contract["min_rows"] == 15
    assert "上季度数" in main_contract["headers"]
    assert "认可资产" in main_contract["required_rows"]
    operating_contract = merged[1]["_layout_contract"]
    assert operating_contract["headers"] == [
        "指标名称",
        "本季度数",
        "本年度累计数",
    ]
    print("PASS 大地版式契约进入表配置")


def test_layout_contract_rejects_missing_previous_period_column() -> None:
    table = SimpleNamespace(
        table_config={
            "_layout_contract": {
                "headers": ["项目", "本季度数", "上季度数"],
                "min_rows": 2,
                "required_rows": ["认可资产"],
            }
        }
    )
    failure = _contract_failure(
        table,
        [
            ["项目", "本季度数"],
            ["认可资产", "100"],
        ],
    )
    assert "表头不匹配" in failure
    assert "上季度数" in failure
    assert not _contract_failure(
        table,
        [
            ["项目", "本季度数", "上季度数"],
            ["认可资产", "100", "90"],
        ],
    )
    print("PASS 版式契约拒绝缺失上季度列的错表")


def test_structure_version_overrides_layout_contract() -> None:
    directory = _temp_dir()
    _write_rule(
        directory,
        "年度契约",
        """
company:
  canonical: 年度契约
  layout_contracts:
    SOLVENCY_MAIN:
      headers: [指标名称, 本季度末数]
      min_rows: 2
tables:
  SOLVENCY_MAIN:
    expected_pages: [10]
structure_versions:
  - since: 2025
    layout_contracts:
      SOLVENCY_MAIN:
        headers: [指标名称, 本季度末数, 上季度末数]
        min_rows: 5
        max_rows: 20
        unique_rows: true
""",
    )
    try:
        old = rules.resolve_rule("年度契约", 2024, directory=directory)
        new = rules.resolve_rule("年度契约", 2025, directory=directory)
        assert old is not None and new is not None
        assert old.layout_contracts["SOLVENCY_MAIN"]["headers"] == [
            "指标名称",
            "本季度末数",
        ]
        contract = new.layout_contracts["SOLVENCY_MAIN"]
        assert contract["headers"] == [
            "指标名称",
            "本季度末数",
            "上季度末数",
        ]
        assert contract["max_rows"] == 20
        assert contract["unique_rows"] is True
    finally:
        for path in directory.glob("*"):
            path.unlink(missing_ok=True)
        directory.rmdir()
    print("PASS structure_versions 可按年份覆盖版式契约")


def test_repository_rules_cover_all_profile_tables() -> None:
    """三家公司必须覆盖 Profile 的全部目标表，不能只配经营表。"""
    from services.report_profiles import load_profile_workbook

    root = Path(__file__).resolve().parents[1]
    profile = load_profile_workbook(
        (root / "config/report_profiles/NON_LIFE_SOLVENCY_profile.xlsx").read_bytes(),
        project_root=root,
    )
    expected_tables = {
        str(table["table_id"]) for table in profile.feature_config["tables"]
    }
    assert expected_tables, "Profile 未读到目标表"
    for company in ("人保产险", "平安产险", "太保产险"):
        resolved = rules.resolve_rule(company, 2025)
        assert resolved is not None, company
        missing = expected_tables - set(resolved.tables)
        assert not missing, f"{company} 缺少表：{sorted(missing)}"
        for table_id, config in resolved.tables.items():
            assert config.get("expected_pages"), f"{company}.{table_id} 缺少 expected_pages"


def test_alias_table_id_matches_builtin_rule_key() -> None:
    directory = _temp_dir()
    _write_rule(
        directory,
        "别名测试",
        """
company:
  canonical: 别名测试
tables:
  OPERATING_METRICS:
    expected_pages: [9]
""",
    )
    try:
        resolved = rules.resolve_rule("别名测试", 2024, directory=directory)
        assert resolved is not None
        merged = rules.apply_rule_to_tables(
            [{"table_id": "NON_LIFE_OPERATING_METRICS"}], resolved
        )
        assert merged[0]["expected_pages"] == [9]
    finally:
        for path in directory.glob("*"):
            path.unlink(missing_ok=True)
        directory.rmdir()


def test_unknown_table_id_raises() -> None:
    directory = _temp_dir()
    _write_rule(
        directory,
        "坏配置",
        """
company:
  canonical: 坏配置
tables:
  NO_SUCH_TABLE:
    expected_pages: [9]
""",
    )
    try:
        try:
            rules.load_company_rules(directory, force=True)
        except Exception as exc:  # ExtractionConfigError
            assert "NO_SUCH_TABLE" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("未知 table_id 应当报错")
    finally:
        for path in directory.glob("*"):
            path.unlink(missing_ok=True)
        directory.rmdir()


def test_canonical_must_match_filename() -> None:
    directory = _temp_dir()
    _write_rule(
        directory,
        "文件名",
        """
company:
  canonical: 另一个名字
""",
    )
    try:
        try:
            rules.load_company_rules(directory, force=True)
        except Exception as exc:
            assert "canonical" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("canonical 与文件名不一致应当报错")
    finally:
        for path in directory.glob("*"):
            path.unlink(missing_ok=True)
        directory.rmdir()


def test_invalid_page_match_and_tolerance() -> None:
    for body in (
        "    page_match: fuzzy\n",
        "    tolerance: -1\n",
        "    expected_pages: [0]\n",
    ):
        directory = _temp_dir()
        _write_rule(
            directory,
            "非法",
            "company:\n  canonical: 非法\ntables:\n  SOLVENCY_MAIN:\n"
            + body,
        )
        try:
            try:
                rules.load_company_rules(directory, force=True)
            except Exception:
                pass
            else:  # pragma: no cover
                raise AssertionError(f"非法配置应当报错：{body}")
        finally:
            for path in directory.glob("*"):
                path.unlink(missing_ok=True)
            directory.rmdir()


if __name__ == "__main__":
    test_repository_rules_load_and_normalize_aliases()
    test_unknown_company_returns_none()
    test_missing_year_keeps_rule_and_warns()
    test_structure_version_selection()
    test_apply_rule_merges_profile_config_without_mutating()
    test_dadi_layout_contract_is_applied_to_profile_tables()
    test_layout_contract_rejects_missing_previous_period_column()
    test_structure_version_overrides_layout_contract()
    test_repository_rules_cover_all_profile_tables()
    test_alias_table_id_matches_builtin_rule_key()
    test_unknown_table_id_raises()
    test_canonical_must_match_filename()
    test_invalid_page_match_and_tolerance()
    print("test_company_rule_catalog: OK")
