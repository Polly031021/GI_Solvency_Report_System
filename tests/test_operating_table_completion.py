# -*- coding: utf-8 -*-
"""经营表通用补全：无表头/编号标签/换行标签/-- 占位符。"""

from __future__ import annotations

from services.company_extractors._operating_table import (
    _dedupe_rows,
    _parse_operating_rows,
)

SAMPLE = """
（三）主要经营指标
（一）保险业务收入
153,903,742.17
722,760,236.35
（二）净利润
106,400,488.07
201,814,115.94
（十一）效益类指标
--
--
1.综合成本率
--
84.95%
2.综合费用率
--
48.52%
3.综合赔付率
--
36.44%
（十二）规模类指标
--
--
1.签单保费
96,645,143.85
570,537,466.83
3.非车险前五大险种的签单
保费
154,115,800.00
472,897,700.00
"""


def test_parse_without_standard_header() -> None:
    rows = _parse_operating_rows(SAMPLE)
    labels = [row[0] for row in rows]
    assert "保险业务收入" in labels
    assert "综合成本率" in labels
    assert "综合费用率" in labels
    assert "综合赔付率" in labels
    assert "签单保费" in labels
    # 换行标签要合并，不能留下孤立的“保费”
    assert "非车险前五大险种的签单保费" in labels
    assert "保费" not in labels


def test_placeholder_kept_and_value_collected() -> None:
    rows = _parse_operating_rows(SAMPLE)
    expense = next(row for row in rows if row[0] == "综合费用率")
    assert expense[1] == "--"
    assert expense[2] == "48.52%"


def test_dedupe_by_cleaned_label() -> None:
    rows = [
        ["（一）保险业务收入", "1", "2"],
        ["保险业务收入", "1", "2"],
        ["综合费用率", "--", "48.52%"],
    ]
    deduped = _dedupe_rows(rows)
    assert [row[0] for row in deduped] == ["（一）保险业务收入", "综合费用率"]


if __name__ == "__main__":
    test_parse_without_standard_header()
    test_placeholder_kept_and_value_collected()
    test_dedupe_by_cleaned_label()
    print("test_operating_table_completion: OK")
