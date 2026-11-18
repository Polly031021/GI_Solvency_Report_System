# -*- coding: utf-8 -*-
"""公司专属 extractor 单文件/批量共用入口测试。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from services.company_extractors import (
    apply_company_extractor_for_tables,
    extractor_from_matches,
)

ROOT = Path(__file__).resolve().parents[1]
PINGAN_2022 = ROOT / "Data/pdfs(2022)/平安产险.pdf"


def _match(extractor: str = "pingan") -> SimpleNamespace:
    return SimpleNamespace(
        table_config={"_extraction_rule": {"extractor": extractor}}
    )


def _table(table_id: str, rows, *, page: int = 15) -> SimpleNamespace:
    return SimpleNamespace(
        table_id=table_id,
        table_name="测试表",
        page=page,
        source_pages=[page],
        rows=[list(row) for row in rows],
        table_blocks=[{"page": page, "rows": [list(row) for row in rows]}],
    )


def test_extractor_name_from_matches() -> None:
    assert extractor_from_matches([_match("pingan")]) == "pingan"
    assert extractor_from_matches([SimpleNamespace(table_config={})]) == ""
    assert extractor_from_matches([]) == ""
    assert extractor_from_matches(None) == ""


def test_unknown_extractor_is_noop() -> None:
    table = _table("SOLVENCY_MAIN", [["指标名称"], ["认可资产", "", "1"]])
    updated, log = apply_company_extractor_for_tables(
        [table], [_match("no_such")], pdf_bytes=b"x"
    )
    assert log is None
    assert updated[0].rows[1] == ["认可资产", "", "1"]


def test_pingan_solvency_main_is_fixed_in_place() -> None:
    if not PINGAN_2022.exists():
        print("skip: 平安 2022 样例不存在")
        return
    header = ["指标名称", "本季度末数", "上季度末数", "下季度末预测数"]
    broken = ["认可资产", "", "50,456,00551,044,518", "49,065,744"]
    split_label = ["核心一", "级资本", "10,119,26610,653,295", "10,166,595"]
    table = _table("SOLVENCY_MAIN", [header, broken, split_label])
    updated, log = apply_company_extractor_for_tables(
        [table],
        [_match("pingan")],
        pdf_bytes=PINGAN_2022.read_bytes(),
    )
    assert log is not None and log["status"] == "成功"
    rows = updated[0].rows
    assert rows[1] == ["认可资产", "50,456,005", "51,044,518", "49,065,744"]
    assert rows[2][0] == "核心一级资本"
    assert rows[2][1] == "10,119,266"
    assert rows[2][2] == "10,653,295"
    # blocks 必须同步，否则 STEP3 仍读到旧值。
    block_row = updated[0].table_blocks[0]["rows"][1]
    assert block_row == ["认可资产", "50,456,005", "51,044,518", "49,065,744"]


def test_pingan_operating_table_is_completed() -> None:
    if not PINGAN_2022.exists():
        print("skip: 平安 2022 样例不存在")
        return
    # 2024 版式：首行是分组标题，表头丢失。
    table = _table(
        "NON_LIFE_OPERATING_METRICS",
        [["1.主要指标", "", ""], ["保险业务收入", "7,601,781", "29,807,448"]],
        page=17,
    )
    updated, log = apply_company_extractor_for_tables(
        [table],
        [_match("pingan")],
        pdf_bytes=PINGAN_2022.read_bytes(),
    )
    assert log is not None and log["status"] == "成功"
    rows = updated[0].rows
    assert rows[0] == ["指标名称", "本季度数", "本年度累计数"]
    labels = [str(row[0]) for row in rows[1:]]
    assert any("净利润" in label for label in labels)
    assert any("签单保费" in label for label in labels)
    assert all(label not in {"--", "-"} for label in labels)


if __name__ == "__main__":
    test_extractor_name_from_matches()
    test_unknown_extractor_is_noop()
    test_pingan_solvency_main_is_fixed_in_place()
    test_pingan_operating_table_is_completed()
    print("test_company_extractor_integration: OK")
