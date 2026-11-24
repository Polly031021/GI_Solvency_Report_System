"""Unit checks for deterministic OCR table reconstruction."""

from __future__ import annotations

from services.company_extractors._ocr import OCRToken, group_tokens_into_lines
from services.company_extractors import hengbang, zhongmei
from services.batch_extraction import FlexibleTable


def _token(text: str, x0: float, y0: float, width: float = 20) -> OCRToken:
    return OCRToken(
        text=text,
        score=0.99,
        x0=x0,
        y0=y0,
        x1=x0 + width,
        y1=y0 + 12,
    )


def test_group_tokens_into_visual_lines() -> None:
    lines = group_tokens_into_lines(
        [
            _token("资产", 10, 30, 40),
            _token("100", 120, 2, 30),
            _token("负债", 10, 2, 40),
            _token("90", 120, 30, 20),
        ]
    )
    assert ["".join(token.text for token in line) for line in lines] == [
        "负债100",
        "资产90",
    ]


def test_hengbang_main_accepts_split_forecast_header() -> None:
    rows = hengbang._rebuild_main(
        [
            "项目",
            "本季度数",
            "上季度数",
            "基本情景下下季度预",
            "测数",
            "认可资产（万元）",
            "443,350.68",
            "455,165.32",
            "445,919.41",
            "综合偿付能力充足率",
            "533.59%",
            "538.55%",
            "520.72%",
        ]
    )
    assert rows is not None
    assert rows[0][-1] == "基本情景下下季度预测数"
    assert rows[1] == ["认可资产（万元）", "443,350.68", "455,165.32", "445,919.41"]


def test_hengbang_return_value_removes_ocr_spaces() -> None:
    rows = hengbang._rebuild_returns(
        [
            "近三年平均投资收益率",
            "4. 17%",
            "近三年平均综合投资收益率",
            "3.85%",
        ]
    )
    assert rows == [
        ["指标名称", "指标额"],
        ["近三年平均投资收益率", "4.17%"],
        ["近三年平均综合投资收益率", "3.85%"],
    ]


def test_zhongmei_rejoins_split_row_numbers_and_amounts() -> None:
    lines = zhongmei._normalize_ocr_lines(
        [
            "1. 2.",
            "3",
            "实际资本合计",
            "1,174,501,804.",
            "34",
        ]
    )
    assert lines == [
        "1.2.3",
        "实际资本合计",
        "1,174,501,804.34",
    ]


def test_flexible_table_defaults_to_rows_dataframe() -> None:
    table = FlexibleTable(
        table_id="ACTUAL_CAPITAL",
        table_name="实际资本",
        rows=[
            ["行次", "项目", "期末数"],
            ["1", "核心一级资本", "100"],
        ],
    )

    frame = table.to_frame()

    assert list(frame.columns) == ["行次", "项目", "期末数"]
    assert frame.iloc[0].tolist() == ["1", "核心一级资本", "100"]
