from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import pandas as pd
import pdfplumber

from .solvency_pdf_locator import PageMatch

TABLE_SIGNATURES = {
    "SOLVENCY_MAIN": ("认可资产", "综合偿付能力充足率"),
    "LIQUIDITY_RISK": ("LCR1", "未来3个月"),
    "LIQUIDITY_MONITORING": ("综合退保率", "融资杠杆比例"),
    "OPERATING_METRICS": ("保险业务收入",),
    "THREE_YEAR_INVESTMENT_RETURN": (
        "投资收益率",
        "综合投资收益率",
    ),
    "ACTUAL_CAPITAL": ("核心一级资本", "实际资本合计"),
    "RECOGNIZED_ASSETS": ("投资资产", "认可资产合计"),
    "RECOGNIZED_LIABILITIES": ("准备金负债", "认可负债合计"),
    "MINIMUM_CAPITAL": ("量化风险最低资本", "最低资本"),
}
TABLE_EXCLUSIONS = {
    "SOLVENCY_MAIN": ("监管指标名称", "流动性覆盖率", "LCR1", "LCR2", "LCR3"),
    "OPERATING_METRICS": (
        "流动性风险监测指标",
        "流动性风险监管指标",
        "LCR1",
        "前五大产品的信息",
        "报告期内签单保费占前五位的产品",
        "签单保费占前五位的产品",
    ),
    "ACTUAL_CAPITAL": ("认可资产表", "认可负债表", "最低资本表"),
    "THREE_YEAR_INVESTMENT_RETURN": ("流动性覆盖率", "最低资本表"),
    "MINIMUM_CAPITAL": ("认可资产表", "认可负债表"),
}

TABLE_HEADERS = {
    "SOLVENCY_MAIN": (
        "项目", "指标名称", "本季度数", "本季度末数",
        "上季度可比数", "上季度末数", "基本情景下的下季度预测数",
        "下季度预测数", "下季度末预测数",
    ),
    "OPERATING_METRICS": ("指标名称", "本季度", "本年累计"),
    "ACTUAL_CAPITAL": ("行次", "期末数", "期初数"),
    "THREE_YEAR_INVESTMENT_RETURN": ("投资收益率", "近三年"),
    "MINIMUM_CAPITAL": ("行次", "期末数", "期初数"),
}

TABLE_START_MARKERS = {
    "SOLVENCY_MAIN": ("指标名称", "认可资产"),
    "OPERATING_METRICS": ("主要经营指标", "效益类指标", "规模类指标", "品质类指标", "保险业务收入"),
    "ACTUAL_CAPITAL": (
        "实际资本表",
        "实际资本明细表",
        "实际资本汇总",
        "核心资本",
        "财务报表资产总额",
        "认可资产",
        "核心一级资本",
    ),
    "THREE_YEAR_INVESTMENT_RETURN": (
        "近三年（综合）投资收益率",
        "近三年平均投资收益率",
    ),
    "MINIMUM_CAPITAL": (
        "最低资本表",
        "最低资本",
        "量化风险最低资本",
        "保险风险最低资本汇总",
        "市场风险最低资本汇总",
        "信用风险最低资本汇总",
    ),
}

TABLE_END_MARKERS = {
    "SOLVENCY_MAIN": ("监管指标名称", "流动性覆盖率", "LCR1"),
    "OPERATING_METRICS": ("流动性风险监测指标", "流动性风险监管指标", "近三年综合投资收益率", "近三年（综合）投资收益率"),
    "ACTUAL_CAPITAL": (
        "（三）认可资产",
        "(三)认可资产",
        "认可资产表",
        "认可负债表",
        "最低资本表",
    ),
    "THREE_YEAR_INVESTMENT_RETURN": ("实际资本表", "认可资产表", "最低资本表"),
    "MINIMUM_CAPITAL": (),
}



@dataclass
class UnitRecord:
    """A unit declaration found in or immediately around an extracted table."""

    scope: str
    target: str
    raw_unit: str
    normalized_unit: str
    source_page: int = 0
    source_type: str = ""
    source_text: str = ""
    confidence: str = "\u9ad8"

    def dedupe_key(self) -> tuple:
        return (
            self.scope,
            self.target,
            self.normalized_unit,
            self.source_page,
            self.source_type,
            self.source_text,
        )

    def to_dict(self, table_name: str = "") -> dict:
        return {
            "\u76ee\u6807\u8868": table_name,
            "\u7269\u7406\u9875\u7801": self.source_page,
            "\u4f5c\u7528\u8303\u56f4": self.scope,
            "\u5bf9\u8c61": self.target,
            "\u539f\u59cb\u5355\u4f4d": self.raw_unit,
            "\u89c4\u8303\u5355\u4f4d": self.normalized_unit,
            "\u6765\u6e90\u7c7b\u578b": self.source_type,
            "\u6765\u6e90\u539f\u6587": self.source_text,
            "\u7f6e\u4fe1\u5ea6": self.confidence,
        }


@dataclass
class ExtractedTable:
    table_id: str
    table_name: str
    page: int
    table_index: int
    rows: list[list[str]]
    strategy: str = ""
    quality_score: float = 0.0
    evidence: str = ""
    source_pages: list[int] = field(default_factory=list)
    unit_records: list[UnitRecord] = field(default_factory=list)
    profile_strategy_id: str = ""

    @property
    def candidate_id(self) -> str:
        pages = "-".join(map(str, self.source_pages or [self.page]))
        return (
            f"{self.table_id}:{pages}:{self.profile_strategy_id}:"
            f"{self.strategy}:{self.table_index}"
        )


    def unit_summary(self) -> str:
        if not self.unit_records:
            return "\u672a\u8bc6\u522b\u5230\u660e\u786e\u5355\u4f4d\uff0c\u8bf7\u6838\u5bf9PDF\u539f\u9875\u3002"
        grouped: dict[tuple[str, str], list[UnitRecord]] = {}
        for record in self.unit_records:
            grouped.setdefault((record.scope, record.target), []).append(record)
        parts: list[str] = []
        for (scope, target), records in grouped.items():
            units = "/".join(dict.fromkeys(item.normalized_unit for item in records))
            pages = "/".join(
                dict.fromkeys(str(item.source_page) for item in records if item.source_page)
            )
            page_note = f"\uff0c\u7b2c{pages}\u9875" if pages else ""
            target_note = f"{target}\uff1a" if target else ""
            parts.append(f"{scope}{target_note}{units}{page_note}")
        return "\uff1b".join(parts)

    def units_frame(self) -> pd.DataFrame:
        columns = [
            "\u76ee\u6807\u8868", "\u7269\u7406\u9875\u7801", "\u4f5c\u7528\u8303\u56f4", "\u5bf9\u8c61", "\u539f\u59cb\u5355\u4f4d", "\u89c4\u8303\u5355\u4f4d",
            "\u6765\u6e90\u7c7b\u578b", "\u6765\u6e90\u539f\u6587", "\u7f6e\u4fe1\u5ea6",
        ]
        return pd.DataFrame(
            [record.to_dict(self.table_name) for record in self.unit_records],
            columns=columns,
        )

    def to_frame(self, *, include_unit_footer: bool = False) -> pd.DataFrame:
        width = max((len(row) for row in self.rows), default=0)
        padded = [row + [""] * (width - len(row)) for row in self.rows]
        if include_unit_footer:
            width = max(width, 2)
            padded = [row + [""] * (width - len(row)) for row in padded]
            footer = ["\u3010\u5355\u4f4d\u5907\u6ce8\u3011", self.unit_summary()]
            padded.append(footer + [""] * (width - len(footer)))
        return pd.DataFrame(padded)



def _clean_cell(value) -> str:
    if value is None:
        return ""
    value = re.sub(r"\s+", " ", str(value).replace("\u3000", " ")).strip()
    return value


def _compact_grid(cleaned: list[list[str]]) -> list[list[str]]:
    """Remove columns that are empty across every row."""
    if not cleaned:
        return []
    width = max(len(row) for row in cleaned)
    padded = [row + [""] * (width - len(row)) for row in cleaned]
    keep = [
        index
        for index in range(width)
        if any(row[index].strip() for row in padded)
    ]
    return [[row[index] for index in keep] for row in padded]


def _clean_rows(rows: list[list]) -> list[list[str]]:
    cleaned = [[_clean_cell(cell) for cell in row] for row in rows if row]
    cleaned = [row for row in cleaned if any(cell for cell in row)]
    return _compact_grid(cleaned)


def _merge_split_cjk_label(rows: list[list[str]]) -> list[list[str]]:
    """Merge a leading Chinese character split into its own text-table cell.

    Some PDFs position the first glyph of each label slightly to the left of
    the remaining text. Text-alignment extraction then produces
    ``["1", "核", "心一级资本", ...]``. Line-frame extraction is preferred, but
    this repair keeps the fallback usable for borderless tables.
    """
    repaired: list[list[str]] = []
    row_number_re = re.compile(r"^\d+(?:\.\d+)*$")
    for source in rows:
        row = list(source)
        nonempty = [
            index for index, cell in enumerate(row) if str(cell).strip()
        ]
        if len(nonempty) < 2:
            repaired.append(row)
            continue
        label_start = nonempty[0]
        first = str(row[label_start]).strip()
        if row_number_re.fullmatch(first) and len(nonempty) >= 3:
            label_start = nonempty[1]
        label_char = str(row[label_start]).strip()
        next_index = next(
            (index for index in nonempty if index > label_start),
            None,
        )
        if next_index is None:
            repaired.append(row)
            continue
        next_cell = str(row[next_index]).strip()
        if (
            len(label_char) == 1
            and "\u4e00" <= label_char <= "\u9fff"
            and next_cell
            and "\u4e00" <= next_cell[0] <= "\u9fff"
        ):
            row[label_start] = label_char + next_cell
            row[next_index] = ""
        repaired.append(row)
    return _compact_grid(repaired)


_ROW_NUMBER_RE = re.compile(r"^\d+(?:[.．]\d+)*$")
_WRAP_STOP_PREFIXES = (
    "其中",
    "减：",
    "减:",
    "加：",
    "加:",
    "注：",
    "注:",
    "单位：",
    "单位:",
    "公司名称",
)


def _is_numeric_cell(value: object) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if text in {"-", "--", "—", "–", "/", "－"}:
        return True
    cleaned = (
        text.replace(",", "")
        .replace("，", "")
        .replace("%", "")
        .replace("％", "")
        .strip("()（）")
    )
    try:
        float(cleaned)
        return True
    except (TypeError, ValueError):
        return False


def _row_number_index(row: list[str]) -> int | None:
    for index in range(min(3, len(row))):
        text = str(row[index] or "").strip()
        if _ROW_NUMBER_RE.fullmatch(text):
            return index
    return None


def _row_label_index(row: list[str], *, after: int = -1) -> int | None:
    for index in range(after + 1, len(row)):
        text = str(row[index] or "").strip()
        if not text or _ROW_NUMBER_RE.fullmatch(text) or _is_numeric_cell(text):
            continue
        if any("\u4e00" <= char <= "\u9fff" for char in text):
            return index
    return None


def _is_wrapped_continuation(row: list[str], previous: list[str] | None) -> bool:
    if previous is None or _row_number_index(row) is not None:
        return False
    nonempty = [
        str(cell or "").strip()
        for cell in row
        if str(cell or "").strip()
    ]
    if not nonempty or any(_is_numeric_cell(cell) for cell in nonempty):
        return False
    text = "".join(nonempty)
    if "单位" in text or any(
        text.startswith(prefix) for prefix in _WRAP_STOP_PREFIXES
    ):
        return False
    if text in {"效益类指标", "规模类指标", "品质类指标", "综合类指标"}:
        return False
    if re.match(r"^[一二三四五六七八九十]+[、.．]", text):
        return False
    if any(
        token in text
        for token in ("指标名称", "项目", "行次", "本季度", "上季度", "期末", "期初")
    ):
        return False
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _merge_wrapped_table_rows(
    rows: list[list[str]],
) -> tuple[list[list[str]], list[dict[str, str]]]:
    """Join visual text lines that belong to one logical table row.

    A wrapped label is normally a row with no row number, no numeric value and
    only a Chinese text fragment. It is appended to the preceding logical row's
    label. Section titles, "其中："/"减："/"加：" rows and rows with their own
    numbers are intentionally left alone.
    """
    if len(rows) < 2:
        return rows, []
    header_index = next(
        (
            index
            for index, row in enumerate(rows[:10])
            if any(
                token in str(cell)
                for cell in row
                for token in ("指标名称", "项目", "行次", "本季度", "期末", "期初")
            )
        ),
        -1,
    )
    merged: list[list[str]] = []
    repairs: list[dict[str, str]] = []
    for index, source in enumerate(rows):
        row = list(source)
        if header_index < 0 or index <= header_index:
            merged.append(row)
            continue
        previous = merged[-1] if merged else None
        if not _is_wrapped_continuation(row, previous):
            merged.append(row)
            continue
        previous_label_index = _row_label_index(previous or [])
        if previous_label_index is None:
            merged.append(row)
            continue
        fragment = "".join(
            str(cell or "").strip()
            for cell in row
            if str(cell or "").strip()
        )
        previous[previous_label_index] = (
            str(previous[previous_label_index] or "").strip() + fragment
        )
        repairs.append(
            {
                "行号": str(index + 1),
                "合并标签": fragment,
                "结果": previous[previous_label_index],
            }
        )
    return _compact_grid(merged), repairs


_PERIOD_HEADER_TOKENS = (
    "本季度",
    "本季末",
    "上季度",
    "上季末",
    "本年累计",
    "本年度累计",
    "期末",
    "期初",
    "下季度",
    "基本情景",
    "预测",
)


def _period_header_cells(row: list[str]) -> list[str]:
    raw_cells: list[str] = []
    for cell in row:
        text = str(cell or "").strip()
        if not text or not any(token in text for token in _PERIOD_HEADER_TOKENS):
            continue
        if (
            any(group in text for group in ("基本情景下", "压力情景下"))
            and "预测" not in text
            and "下季度" not in text
        ):
            # Group heading such as "基本情景下"; the actual period is the
            # sub-header on the next visual line.
            continue
        raw_cells.append(text)

    # 有的 pdfplumber 网格把“上季度数”和“下季度预测数”的前半截压进
    # 同一单元格，例如“上季度数 下季” + “度预测数”。拆成两个期间，
    # 避免第三个数值因表头不可识别而丢失。
    cells: list[str] = []
    index = 0
    while index < len(raw_cells):
        text = raw_cells[index]
        next_text = raw_cells[index + 1] if index + 1 < len(raw_cells) else ""
        if "上季度" in text and "下季" in text:
            cells.append("上季度数")
            cells.append("下季度末预测数")
            if "预测" in next_text:
                index += 1
        elif (
            ("下季" in text or "下季度" in text)
            and "预测" not in text
            and "预测" in next_text
        ):
            cells.append("下季度末预测数")
            index += 1
        else:
            cells.append(text)
        index += 1
    return cells


_PERIOD_SUBHEADER_RE = re.compile(r"非认可价值|认可价值|账面价值")
_PERIOD_CELL_TOKENS = (
    "下季度末预测数",
    "下季度预测数",
    "本季度末数",
    "上季度末数",
    "本季度数",
    "上季度数",
    "本年累计数",
    "期末数",
    "期初数",
)


def _period_cells_in_header(header_row: list[str]) -> list[tuple[int, str]]:
    cells: list[tuple[int, str]] = []
    for index, cell in enumerate(header_row):
        text = str(cell or "").strip()
        if not text:
            continue
        for token in _PERIOD_CELL_TOKENS:
            if token in text:
                cells.append((index, token))
                break
    return cells


def _reconstruct_period_subheader_columns(
    rows: list[list[str]],
) -> list[list[str]]:
    """还原“本季度数/上季度数 × 账面价值/非认可价值/认可价值”双层表头。

    这类表的 pdfplumber 原始表头常把六个子表头压进一个单元格，数据行却是
    独立的 6 列或 4 列。按期间列位置、数据行数值数量和子表头顺序重建
    规范表头，可避免 Step3 把认可价值误当成上季度数。
    """
    if len(rows) < 3:
        return rows
    header_index = next(
        (
            index
            for index, row in enumerate(rows[:12])
            if any(
                token in "".join(str(cell) for cell in row)
                for token in ("指标名称", "项目", "指标")
            )
        ),
        None,
    )
    if header_index is None:
        return rows
    header_row = rows[header_index]
    header_text = "".join(str(cell or "") for cell in header_row)
    subheaders = _PERIOD_SUBHEADER_RE.findall(header_text)
    period_cells = _period_cells_in_header(header_row)
    if len(subheaders) < 2 or len(period_cells) < 2:
        return rows

    data_rows = rows[header_index + 1:]
    capacities: list[int] = []
    for row in data_rows:
        label_index = _row_label_index(row)
        if label_index is None:
            continue
        values = [
            str(cell or "").strip()
            for cell in row[label_index + 1:]
            if str(cell or "").strip()
        ]
        capacities.append(len(values))
    if not capacities:
        return rows
    max_values = max(capacities)
    period_count = len(period_cells)
    if max_values < period_count or max_values % period_count:
        return rows
    group_width = max_values // period_count
    if group_width <= 0:
        return rows

    per_period_subheaders = len(subheaders) // period_count
    if group_width == per_period_subheaders:
        chosen_subheaders = subheaders[:group_width]
    elif group_width == 2 and {"账面价值", "认可价值"}.issubset(set(subheaders)):
        chosen_subheaders = ["账面价值", "认可价值"]
    elif group_width == 1:
        chosen_subheaders = []
    else:
        return rows

    canonical_header = ["行次", "项目"]
    for _, period in period_cells:
        for subheader in chosen_subheaders:
            canonical_header.append(
                f"{period}/{subheader}" if subheader else period
            )

    reconstructed = [canonical_header]
    changed = False
    total_values = group_width * period_count
    for row in data_rows:
        label_index = _row_label_index(row)
        if label_index is None:
            continue
        label = str(row[label_index] or "").strip()
        if not label:
            continue
        row_number = ""
        if label_index > 0:
            previous = str(row[label_index - 1] or "").strip()
            if _ROW_NUMBER_RE.fullmatch(previous):
                row_number = previous
        values = [
            str(cell or "").strip()
            for cell in row[label_index + 1:]
            if str(cell or "").strip()
        ]
        if not values and not row_number:
            continue
        values = [*values, *[""] * max(0, total_values - len(values))][:total_values]
        reconstructed.append([row_number, label, *values])
        changed = True
    return reconstructed if changed else rows


def _reconstruct_logical_columns(
    rows: list[list[str]],
) -> list[list[str]]:
    """Recover logical columns from PDF tables with merged header cells.

    Some reports expose a visually 3-column table as 5-8 pdfplumber columns:
    label and values alternate between boundary columns. This reconstructs
    the grid from the period headers and the numeric values in each data row.
    It is deliberately skipped unless the header/data counts agree exactly.
    """
    rebuilt = _reconstruct_period_subheader_columns(rows)
    if rebuilt is not rows:
        return rebuilt
    if len(rows) < 3:
        return rows
    header_index = next(
        (
            index
            for index, row in enumerate(rows[:12])
            if any(
                token in "".join(str(cell) for cell in row)
                for token in ("指标名称", "项目", "指标")
            )
        ),
        0,
    )
    header_row = rows[header_index]
    period_headers = _period_header_cells(header_row)
    # Look at the next header line for a split forecast header such as
    # "基本情景下" / "下季度预测数".
    if header_index + 1 < len(rows):
        next_periods = _period_header_cells(rows[header_index + 1])
        if next_periods and len(next_periods) <= len(period_headers):
            period_headers.extend(
                item for item in next_periods if item not in period_headers
            )
    if len(period_headers) < 2:
        return rows

    data_rows: list[tuple[list[str], list[str]]] = []
    matched = 0
    for source in rows[header_index + 1:]:
        row = list(source)
        label_index = _row_label_index(row)
        if label_index is None:
            data_rows.append(([], []))
            continue
        values = [
            str(cell or "").strip()
            for cell in row[label_index + 1:]
            if _is_numeric_cell(cell)
        ]
        labels = [str(row[label_index] or "").strip()]
        if len(values) == len(period_headers) and labels[0]:
            data_rows.append((labels, values))
            matched += 1
        else:
            data_rows.append((labels, values))
    if matched < 2:
        return rows
    # Only reconstruct if the majority of populated data rows agree with
    # the period header count; single rows with blank trailing values should
    # not block the whole table.
    populated = sum(1 for labels, values in data_rows if values)
    if matched < max(2, populated * 0.6):
        return rows

    header_label = next(
        (
            str(cell or "").strip()
            for cell in header_row
            if str(cell or "").strip()
        ),
        "指标名称",
    )
    if "指标" not in header_label and "项目" not in header_label:
        header_label = "指标名称"
    reconstructed = [[header_label, *period_headers]]
    for labels, values in data_rows:
        if not labels and not values:
            continue
        label = labels[0] if labels else ""
        reconstructed.append(
            [label, *values[: len(period_headers)]]
            + [""] * max(0, len(period_headers) - len(values))
        )
    return reconstructed
def _repair_solvency_main(rows: list[list[str]]) -> list[list[str]]:
    repaired = [["指标名称", "本季度末数", "上季度末数", "下季度末预测数"]]
    capital_component_labels = (
        "核心一级资本",
        "核心二级资本",
        "附属一级资本",
        "附属二级资本",
    )
    for row in rows:
        if len(row) < 3 or "指标名称" in row[0]:
            continue
        cells = row + [""] * max(0, 7 - len(row))
        label = "".join(cells[:2]).strip()
        current = cells[2].strip()
        previous = "".join(cells[3:5]).strip()
        forecast = "".join(cells[5:7]).strip()
        keep_blank_capital_row = any(
            term in label for term in capital_component_labels
        )
        if label and (any((current, previous, forecast)) or keep_blank_capital_row):
            repaired.append([label, current, previous, forecast])
    return repaired


def _repair_operating(rows: list[list[str]]) -> list[list[str]]:
    header_index = next(
        (index for index, row in enumerate(rows) if row and "指标名称" in row[0]),
        None,
    )
    if header_index is None:
        return rows
    repaired = [["指标名称", "本季度（末）数", "本年累计数"]]
    for row in rows[header_index + 1:]:
        cells = [cell for cell in row if cell]
        if len(cells) >= 4:
            repaired.append([cells[0], cells[1] + cells[2], cells[3]])
        elif len(cells) >= 3:
            repaired.append(cells[:3])
    return repaired




def _row_text(row: list[str]) -> str:
    return re.sub(r"\s+", "", "".join(row))


def _profile_terms(table_config: dict | None, key: str) -> tuple[str, ...]:
    """读取 Profile 配置的词表字段；缺省时返回空元组。"""
    value = (table_config or {}).get(key)
    if not value:
        return ()
    if isinstance(value, str):
        return tuple(
            item.strip()
            for item in re.split(r"[|\n\r]+", value)
            if item.strip()
        )
    return tuple(str(item).strip() for item in value if str(item).strip())


def _slice_target_rows(
    table_id: str,
    rows: list[list[str]],
    table_config: dict | None = None,
) -> list[list[str]]:
    """在同页多表或文字对齐结果中切出目标表边界。

    优先使用 Profile 配置的首尾项目（start_item_terms / end_item_terms），
    配置缺失时回退到内置的首尾标记表，避免把相邻表格读进目标表。
    """
    if not rows:
        return []
    config = dict(table_config or {})
    row_texts = [_row_text(row) for row in rows]

    start_index = 0
    if table_id == "SOLVENCY_MAIN":
        header_index = next(
            (
                index
                for index, text in enumerate(row_texts)
                if "指标名称" in text and ("本季度末数" in text or "期末数" in text)
            ),
            None,
        )
        if header_index is not None:
            start_index = header_index
        else:
            start_terms = _profile_terms(config, "start_item_terms")
            metric_index = next(
                (
                    index
                    for index, text in enumerate(row_texts)
                    if any(term in text for term in start_terms)
                    or "核心偿付能力充足率" in text
                ),
                0,
            )
            start_index = max(0, metric_index - 1)
    else:
        start_markers = _profile_terms(
            config,
            "start_item_terms",
        ) or TABLE_START_MARKERS.get(table_id, ())
        marker_index = next(
            (
                index
                for index, text in enumerate(row_texts)
                if any(marker in text for marker in start_markers)
            ),
            0,
        )
        start_index = max(0, marker_index - 1) if marker_index else 0

    end_index = len(rows)
    if str(config.get("boundary_end_mode", "") or "").strip() == "page_tail":
        # Operating-metrics tables often have no explicit final row on the
        # first page.  Keep every row until the next section (or page bottom)
        # instead of stopping at the first "综合成本率" sub-item.
        stop_terms = _profile_terms(config, "stop_terms")
        stop_index = next(
            (
                index
                for index in range(start_index + 1, len(rows))
                if any(term in row_texts[index] for term in stop_terms)
            ),
            None,
        )
        return rows[start_index:(stop_index if stop_index is not None else len(rows))]
    profile_end_markers = _profile_terms(config, "end_item_terms")
    end_markers = profile_end_markers or TABLE_END_MARKERS.get(table_id, ())
    for index in range(start_index + 1, len(rows)):
        if any(marker in row_texts[index] for marker in end_markers):
            # Profile 的 end_item_terms 是目标表的终止数据行，必须保留；
            # 内置 TABLE_END_MARKERS 是相邻表的标题，应当排除。
            end_index = index + 1 if profile_end_markers else index
            break
    return rows[start_index:end_index]


def _candidate_quality(table_id: str, rows: list[list[str]], strategy: str) -> tuple[float, str]:
    flat_text = _row_text([cell for row in rows for cell in row])
    required = TABLE_SIGNATURES.get(table_id, ())
    required_hits = [term for term in required if term in flat_text]
    if required and len(required_hits) != len(required):
        return -1.0, "缺少目标表核心关键词"

    exclusions = TABLE_EXCLUSIONS.get(table_id, ())
    exclusion_hits = [term for term in exclusions if term in flat_text]
    if table_id == "SOLVENCY_MAIN" and exclusion_hits:
        return -1.0, f"混入其他表：{'、'.join(exclusion_hits)}"

    headers = TABLE_HEADERS.get(table_id, ())
    header_hits = [term for term in headers if term in flat_text]
    numeric_cells = sum(
        bool(re.search(r"\d", cell))
        for row in rows
        for cell in row
        if cell
    )
    widths = [len(row) for row in rows if row]
    dominant_width_ratio = 0.0
    if widths:
        dominant_width_ratio = max(widths.count(width) for width in set(widths)) / len(widths)

    score = (
        len(required_hits) * 20
        + len(header_hits) * 4
        + min(numeric_cells, 20) * 0.3
        + dominant_width_ratio * 5
        + (3 if strategy == "线框识别" else 0)
        - len(exclusion_hits) * 12
    )
    evidence_parts = [*required_hits, *header_hits]
    evidence = "、".join(dict.fromkeys(evidence_parts)) or "结构匹配"
    return round(score, 2), evidence


def _candidate_fingerprint(rows: list[list[str]]) -> str:
    return "|".join(_row_text(row) for row in rows)


_PAGE_STRATEGIES = (
    (
        "线框识别",
        {
            "vertical_strategy": "lines",
            "horizontal_strategy": "lines",
            "snap_tolerance": 4,
            "join_tolerance": 4,
            "intersection_tolerance": 5,
        },
    ),
    (
        "文字对齐识别",
        {
            "vertical_strategy": "text",
            "horizontal_strategy": "text",
            "min_words_vertical": 2,
            "min_words_horizontal": 1,
        },
    ),
)


def page_table_candidates(
    page,
    table_id: str,
    table_name: str,
    page_number: int,
    table_config: dict | None = None,
) -> list[ExtractedTable]:
    """从一个 pdfplumber 页面对象提取并按质量排序候选表。

    ``page`` 可以是整页，也可以是 ``page.crop(bbox)`` 得到的页内区域——
    一页多表时先裁切再识别，可以避免选到相邻的表格。
    """
    config = dict(table_config or {})
    title_terms = tuple(str(item) for item in (config.get("title_terms") or ()) if str(item).strip())
    content_terms = tuple(
        str(item)
        for item in (config.get("content_terms") or config.get("completeness_terms") or ())
        if str(item).strip()
    )
    header_terms = tuple(
        str(item) for item in (config.get("header_terms") or ()) if str(item).strip()
    )
    exclude_terms = tuple(
        dict.fromkeys(
            [
                *(str(item) for item in (config.get("exclude_item_terms") or ())),
                *(str(item) for item in (config.get("profile_exclude_items") or ())),
                *(str(item) for item in (config.get("stop_terms") or ())),
            ]
        )
    )
    fingerprints: dict[str, ExtractedTable] = {}
    raw_index = 0
    for strategy, settings in _PAGE_STRATEGIES:
        try:
            tables = page.extract_tables(table_settings=settings) or []
        except Exception:
            tables = []
        for rows in tables:
            raw_index += 1
            cleaned_rows, wrap_repairs = _merge_wrapped_table_rows(
                _clean_rows(rows)
            )
            cleaned_rows = _reconstruct_logical_columns(cleaned_rows)
            cleaned = _slice_target_rows(table_id, cleaned_rows, config)
            if len(cleaned) < 2:
                continue
            signatures = TABLE_SIGNATURES.get(table_id, ())
            flat_text = re.sub(r"\s+", "", "".join("".join(row) for row in cleaned))
            if signatures and not all(signature in flat_text for signature in signatures):
                continue
            if table_id == "SOLVENCY_MAIN" and max(map(len, cleaned)) >= 6:
                cleaned = _repair_solvency_main(cleaned)
            elif table_id == "OPERATING_METRICS":
                cleaned = _repair_operating(cleaned)
            if len(cleaned) < 2:
                continue
            score, evidence = _candidate_quality(table_id, cleaned, strategy)
            if score < 0:
                continue
            if wrap_repairs:
                evidence = (
                    f"{evidence}；已合并{len(wrap_repairs)}个换行标签片段"
                )
            # ★ Profile 配置加权：一页多表时用目标表自己的表名/内容词/表头词
            #   把"真正的目标表"排在前面，并用排除项压掉相邻表的候选。
            if config:
                title_hits = [term for term in title_terms if term in flat_text]
                content_hits = [term for term in content_terms if term in flat_text]
                header_hits = [term for term in header_terms if term in flat_text]
                exclusion_hits = [term for term in exclude_terms if term and term in flat_text]
                score += (
                    len(title_hits) * 6.0
                    + len(content_hits) * 2.0
                    + len(header_hits) * 1.5
                    - len(exclusion_hits) * 10.0
                )
                evidence = "、".join(
                    dict.fromkeys([*title_hits, *content_hits, *header_hits, evidence])
                )
            candidate = ExtractedTable(
                table_id=table_id,
                table_name=table_name,
                page=page_number,
                table_index=raw_index,
                rows=cleaned,
                strategy=strategy,
                quality_score=score,
                evidence=evidence,
            )
            fingerprint = _candidate_fingerprint(cleaned)
            previous = fingerprints.get(fingerprint)
            if previous is None or candidate.quality_score > previous.quality_score:
                fingerprints[fingerprint] = candidate
    return sorted(
        fingerprints.values(),
        key=lambda item: (-item.quality_score, item.strategy != "线框识别", item.table_index),
    )


def extract_page_all_tables(page) -> tuple[list[list[list[str]]], str]:
    """提取页面内的全部表格，不按目标表首尾项目裁切。

    一页多表时，调用方应把返回的每个网格保留成独立表，而不是拼成一张
    大表。这样 Step3 仍能按每张表自己的表头识别期间口径，同时不会因为
    原先的边界裁剪漏掉目标表中的指标。
    """
    best_tables: list[list[list[str]]] = []
    best_score = -1.0
    best_strategy = ""
    wrap_repair_count = 0
    for strategy, settings in _PAGE_STRATEGIES:
        try:
            raw_tables = page.extract_tables(table_settings=settings) or []
        except Exception:
            raw_tables = []

        cleaned_tables: list[list[list[str]]] = []
        seen: set[str] = set()
        for raw_rows in raw_tables:
            rows = _clean_rows(raw_rows)
            if strategy == "文字对齐识别":
                rows = _merge_split_cjk_label(rows)
            rows, wrap_repairs = _merge_wrapped_table_rows(rows)
            rows = _reconstruct_logical_columns(rows)
            wrap_repair_count += len(wrap_repairs)
            if len(rows) < 2:
                continue
            width = max((len(row) for row in rows), default=0)
            if width < 2:
                continue
            fingerprint = _candidate_fingerprint(rows)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            cleaned_tables.append(rows)

        if not cleaned_tables:
            continue
        if strategy == "线框识别":
            usable_numeric_cells = sum(
                bool(re.search(r"\d", cell))
                for table in cleaned_tables
                for row in table
                for cell in row
                if cell
            )
            if usable_numeric_cells >= 2:
                # 线框结果与 PDF 的真实列边界一致；文字对齐结果容易把
                # 千分位金额和标签首字拆成独立列，因此不再让它仅凭“数字
                # 单元格更多”胜出。
                return (
                    cleaned_tables,
                    (
                        f"整页全表（线框识别｜共{len(cleaned_tables)}张表"
                        + (
                            f"｜已合并{wrap_repair_count}个换行标签片段"
                            if wrap_repair_count
                            else ""
                        )
                        + "）"
                    ),
                )
        numeric_cells = sum(
            bool(re.search(r"\d", cell))
            for table in cleaned_tables
            for row in table
            for cell in row
            if cell
        )
        # 优先保证“表数”完整，再比较数值单元格和行数；线框识别结果
        # 通常更规整，同等完整度下优先保留线框结果。
        score = (
            len(cleaned_tables) * 25
            + numeric_cells * 0.5
            + sum(len(table) for table in cleaned_tables) * 0.5
            + (3 if strategy == "线框识别" else 0)
        )
        if score > best_score:
            best_score = score
            best_tables = cleaned_tables
            best_strategy = strategy

    if not best_tables:
        return [], "整页全表提取未识别到可用表格"
    return (
        best_tables,
        (
            f"整页全表（{best_strategy}｜共{len(best_tables)}张表"
            + (
                f"｜已合并{wrap_repair_count}个换行标签片段"
                if wrap_repair_count
                else ""
            )
            + "）"
        ),
    )


def extract_table_candidates(
    pdf_bytes: bytes,
    matches: list[PageMatch],
) -> dict[str, list[ExtractedTable]]:
    """Return ranked table candidates for every target table and PDF page."""
    grouped: dict[str, list[ExtractedTable]] = {}
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for match in matches:
            for page_number in match.pages:
                if page_number < 1 or page_number > len(pdf.pages):
                    continue
                candidates = page_table_candidates(
                    pdf.pages[page_number - 1],
                    match.table_id,
                    match.table_name,
                    page_number,
                    dict(match.table_config),
                )
                if candidates:
                    grouped[f"{match.table_id}:{page_number}"] = candidates
    return grouped


def extract_tables(pdf_bytes: bytes, matches: list[PageMatch]) -> list[ExtractedTable]:
    """Compatibility API: automatically select the highest-scoring candidate."""
    groups = extract_table_candidates(pdf_bytes, matches)
    return [candidates[0] for candidates in groups.values() if candidates]

