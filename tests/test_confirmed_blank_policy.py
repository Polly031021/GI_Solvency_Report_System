"""A missing extracted value must never silently become a source-confirmed zero."""

from services.batch_extraction import FlexibleTable
from services.solvency_normalizer import normalize_tables, load_taxonomy
from tools.company_extraction_audit import ROOT


def test_blank_requires_original_cell_evidence():
    taxonomy = load_taxonomy(str(ROOT / "config/solvency_taxonomy.xlsx"))
    table = FlexibleTable(table_id="LIQUIDITY_MONITORING", table_name="流动性监测",
                          page=14, source_pages=[14], unit="%",
                          rows=[["指标名称", "本季度数", "上季度数"],
                                ["规模保费同比增速", "", "3.14%"]])
    diagnostics = []
    missing = normalize_tables([table], taxonomy, {"公司": "英大财产"},
                               company_type="财险", allowed_company_types=("财险",), report_profile_id="NON_LIFE_SOLVENCY", diagnostics=diagnostics)
    assert len(missing) == 1
    assert missing.iloc[0]["数值"] == 3.14
    assert any("待核对" in row.get("原因", "") for row in diagnostics)
    table.blank_cell_evidence = [{"page": 14, "label": "规模保费同比增速", "value_index": 0}]
    confirmed = normalize_tables([table], taxonomy, {"公司": "英大财产"}, company_type="财险", allowed_company_types=("财险",), report_profile_id="NON_LIFE_SOLVENCY")
    assert set(confirmed["数值"]) == {0, 3.14}
    assert confirmed[confirmed["数值"].eq(0)]["备注"].str.contains("原PDF单元格已确认空白").all()


def test_evidence_does_not_apply_to_another_page_or_period():
    taxonomy = load_taxonomy(str(ROOT / "config/solvency_taxonomy.xlsx"))
    table = FlexibleTable(table_id="MINIMUM_CAPITAL", table_name="最低资本", page=33,
                          rows=[["指标名称", "期末数", "期初数"],
                                ["特定类别保险合同损失吸收效应", "", ""]], unit="万元")
    table.blank_cell_evidence = [{"page": 32, "label": "特定类别保险合同损失吸收效应", "value_index": 0}]
    assert normalize_tables([table], taxonomy, {}, company_type="财险", allowed_company_types=("财险",), report_profile_id="NON_LIFE_SOLVENCY").empty
    table.blank_cell_evidence[0]["page"] = 33
    result = normalize_tables([table], taxonomy, {}, company_type="财险", allowed_company_types=("财险",), report_profile_id="NON_LIFE_SOLVENCY")
    assert len(result) == 1
    assert result.iloc[0]["数值"] == 0
