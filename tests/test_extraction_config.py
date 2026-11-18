# -*- coding: utf-8 -*-
"""表级提取配置：内置默认、YAML 覆盖、别名与配置变更检测。"""

from __future__ import annotations

import tempfile
from pathlib import Path

from services import extraction_config as cfg
from services import solvency_table_extractor as extractor


def _write_yaml(text: str) -> Path:
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".yaml", delete=False, encoding="utf-8"
    )
    handle.write(text)
    handle.close()
    return Path(handle.name)


def _missing_path() -> Path:
    return Path(tempfile.gettempdir()) / "gi_solvency_no_such_tables.yaml"


def test_builtin_defaults_used_when_config_file_is_absent() -> None:
    maps = cfg.load_table_maps(_missing_path())
    assert maps["signatures"]["SOLVENCY_MAIN"] == (
        "认可资产",
        "综合偿付能力充足率",
    )
    assert maps["headers"]["OPERATING_METRICS"] == ("指标名称", "本季度", "本年累计")
    assert maps["start_markers"]["MINIMUM_CAPITAL"][0] == "最低资本表"
    assert maps["end_markers"]["MINIMUM_CAPITAL"] == ()
    assert [name for name, _ in maps["strategies"]] == ["线框识别", "文字对齐识别"]


def test_repository_config_keeps_legacy_values_and_alias() -> None:
    maps = cfg.load_table_maps()
    # 迁移后与旧常量逐项一致（抽检覆盖全部五个字段）。
    assert maps["signatures"]["ACTUAL_CAPITAL"] == ("核心一级资本", "实际资本合计")
    assert maps["exclusions"]["OPERATING_METRICS"][0] == "流动性风险监测指标"
    assert maps["headers"]["THREE_YEAR_INVESTMENT_RETURN"] == ("投资收益率", "近三年")
    assert maps["start_markers"]["SOLVENCY_MAIN"] == ("指标名称", "认可资产")
    assert maps["end_markers"]["ACTUAL_CAPITAL"][0] == "（三）认可资产"
    # Profile 的 table_id 必须与内置 ID 共用同一份定义。
    assert maps["headers"]["NON_LIFE_OPERATING_METRICS"] == maps["headers"]["OPERATING_METRICS"]
    assert maps["signatures"]["NON_LIFE_OPERATING_METRICS"] == ("保险业务收入",)


def test_yaml_overrides_only_given_fields() -> None:
    path = _write_yaml(
        """
tables:
  OPERATING_METRICS:
    signature_terms: [保险业务收入, 净利润]
    header_terms: [指标名称]
"""
    )
    try:
        maps = cfg.load_table_maps(path, force=True)
        assert maps["signatures"]["OPERATING_METRICS"] == ("保险业务收入", "净利润")
        assert maps["headers"]["OPERATING_METRICS"] == ("指标名称",)
        # 未覆盖的字段回退内置默认。
        assert maps["exclusions"]["OPERATING_METRICS"][0] == "流动性风险监测指标"
        assert maps["end_markers"]["SOLVENCY_MAIN"] == (
            "监管指标名称",
            "流动性覆盖率",
            "LCR1",
        )
    finally:
        path.unlink(missing_ok=True)


def test_table_level_and_top_level_aliases() -> None:
    path = _write_yaml(
        """
aliases:
  RAW_OPERATING: OPERATING_METRICS
tables:
  ACTUAL_CAPITAL:
    aliases: [NON_LIFE_ACTUAL_CAPITAL]
"""
    )
    try:
        maps = cfg.load_table_maps(path, force=True)
        assert maps["headers"]["RAW_OPERATING"] == maps["headers"]["OPERATING_METRICS"]
        assert (
            maps["headers"]["NON_LIFE_ACTUAL_CAPITAL"]
            == maps["headers"]["ACTUAL_CAPITAL"]
        )
        assert maps["aliases"]["NON_LIFE_ACTUAL_CAPITAL"] == "ACTUAL_CAPITAL"
    finally:
        path.unlink(missing_ok=True)


def test_invalid_alias_target_raises() -> None:
    path = _write_yaml(
        """
aliases:
  BROKEN: NOT_A_TABLE
"""
    )
    try:
        try:
            cfg.load_table_maps(path, force=True)
        except cfg.ExtractionConfigError as exc:
            assert "NOT_A_TABLE" in str(exc)
        else:  # pragma: no cover - 失败时给出明确信息
            raise AssertionError("未知 alias 目标应当报错")
    finally:
        path.unlink(missing_ok=True)


def test_invalid_terms_type_raises() -> None:
    path = _write_yaml(
        """
tables:
  SOLVENCY_MAIN:
    signature_terms: 12345
"""
    )
    try:
        try:
            cfg.load_table_maps(path, force=True)
        except cfg.ExtractionConfigError as exc:
            assert "signature_terms" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("非法 terms 类型应当报错")
    finally:
        path.unlink(missing_ok=True)


def test_custom_extraction_strategy() -> None:
    path = _write_yaml(
        """
extraction_strategies:
  - name: 自研识别
    settings: {vertical_strategy: explicit, horizontal_strategy: lines}
"""
    )
    try:
        maps = cfg.load_table_maps(path, force=True)
        assert maps["strategies"] == (
            ("自研识别", {"vertical_strategy": "explicit", "horizontal_strategy": "lines"}),
        )
    finally:
        path.unlink(missing_ok=True)


def test_config_change_detection() -> None:
    path = _write_yaml(
        """
tables:
  SOLVENCY_MAIN:
    header_terms: [项目]
"""
    )
    try:
        first = cfg.load_table_maps(path, force=True)
        assert first["headers"]["SOLVENCY_MAIN"] == ("项目",)
        signature_before = cfg.config_signature(path)
        # 不 sleep：直接改写内容并保证 size 变化，mtime_ns 通常随之变化。
        path.write_text(
            "tables:\n  SOLVENCY_MAIN:\n    header_terms: [项目, 指标名称]\n",
            encoding="utf-8",
        )
        signature_after = cfg.config_signature(path)
        assert signature_after != signature_before
        refreshed = cfg.load_table_maps(path)
        assert refreshed["headers"]["SOLVENCY_MAIN"] == ("项目", "指标名称")
    finally:
        path.unlink(missing_ok=True)


def test_extractor_module_maps_are_live_objects() -> None:
    # 热更新依赖“外部 import 的是同一个 dict 对象”，这里锁定该约定。
    assert extractor.TABLE_HEADERS is extractor.TABLE_HEADERS
    assert "NON_LIFE_OPERATING_METRICS" in extractor.TABLE_HEADERS
    before = dict(extractor.TABLE_HEADERS)
    extractor.refresh_table_definitions()
    assert extractor.TABLE_HEADERS == before
    extractor.refresh_table_definitions()  # 幂等


if __name__ == "__main__":
    test_builtin_defaults_used_when_config_file_is_absent()
    test_repository_config_keeps_legacy_values_and_alias()
    test_yaml_overrides_only_given_fields()
    test_table_level_and_top_level_aliases()
    test_invalid_alias_target_raises()
    test_invalid_terms_type_raises()
    test_custom_extraction_strategy()
    test_config_change_detection()
    test_extractor_module_maps_are_live_objects()
    print("test_extraction_config: OK")
