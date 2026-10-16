"""提取器的数据模型、模式常量与文本工具。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..solvency_table_extractor import ExtractedTable

GRID_COLUMNS = 180
TEXT_MODE = "大模型网格重构"
VISION_MODE = "图片扫描重试"
VISION_HIGH_RES_MODE = "3倍高清图片扫描重试"
TEXT_RETRY_MODE = "网格纠错重试"
TABLE_COMPLETENESS_TERMS = {
    "SOLVENCY_MAIN": (
        "核心偿付能力充足率",
        "综合偿付能力充足率",
        "实际资本",
        "最低资本",
    ),
    "ACTUAL_CAPITAL": (
        "财务报表资产总额",
        "认可资产",
        "财务报表负债总额",
        "认可负债",
        "财务报表净资产总额",
        "实际资本",
        "核心一级资本",
        "核心二级资本",
        "附属一级资本",
        "附属二级资本",
        "实际资本合计",
    ),
}
TABLE_MIN_NUMERIC_ROWS = {
    "SOLVENCY_MAIN": 4,
}
SOURCE_ROW_COVERAGE_RATIO = 0.60
SOURCE_ITEM_RECALL_RATIO = 0.90
OPERATING_SECTION_TITLES = ("效益类指标", "规模类指标", "品质类指标")
AI_TABLE_END_MARKERS = {
    "SOLVENCY_MAIN": ("监管指标名称", "流动性覆盖率", "LCR1"),
    "OPERATING_METRICS": (
        "流动性风险监管指标",
        "流动性风险监测指标",
        "近三年综合投资收益率",
        "近三年（综合）投资收益率",
    ),
    "ACTUAL_CAPITAL": (
        "S03",
        "（三）认可资产",
        "(三)认可资产",
        "认可资产表",
        "认可负债表",
        "最低资本表",
    ),
    "THREE_YEAR_INVESTMENT_RETURN": ("S02", "实际资本表", "实际资本明细表", "最低资本表"),
    "MINIMUM_CAPITAL": ("S06", "风险综合评级", "偿付能力风险管理评估", "风险管理能力"),
}


class ExtractionQualityError(RuntimeError):
    pass


@dataclass
class ExtractionLog:
    table_id: str
    table_name: str
    pages: list[int]
    mode: str
    status: str
    message: str

    def to_dict(self) -> dict:
        return {
            "目标表": self.table_name,
            "物理页码": "、".join(map(str, self.pages)),
            "识别方式": self.mode,
            "状态": self.status,
            "说明": self.message,
        }


@dataclass
class AIExtractionBundle:
    tables: list[ExtractedTable]
    logs: list[ExtractionLog]


@dataclass
class PageGrid:
    page_number: int
    grid_text: str
    word_count: int


def _clean(value) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\u3000", " ")).strip()


def _compact(value: str) -> str:
    return re.sub(r"[\s：:（）()、，,。·—\-_/]", "", str(value or ""))
