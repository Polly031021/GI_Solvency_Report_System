"""长江财险专属提取后处理：重排偿付能力主表。

长江财险 2024 年（及同版式的其他年份）主表在 PDF 文本层里被拆成大量
碎单元格：表头 ``本季度数`` 被拆成 ``本季``/``度比上季``/``下季``，
金额被拆成 ``29,1``/``77.35``，并且区域从页首正文（董监高信息）开始，
导致标准化把数值映射到错误的期间列、金额再被当作“元”缩小 10^4。

专属逻辑只重写 ``SOLVENCY_MAIN`` 的 ``rows``：以“认可资产”为起点截取，
把碎单元格按“标签 + 金额（可碎） + 百分比”重新拼回四列
（指标名称 / 本季度数 / 上季度数 / 本季度比上季度增减 / 下季度预测数），
数据全部来自已提取的 raw rows，不写死年份和页码。
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from . import register

_AMOUNT_RE = re.compile(r"^-?\d[\d,]*(?:\.\d+)?$")
_PERCENT_RE = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%$")
_FRAGMENT_RE = re.compile(r"^\d{1,3}(?:,\d{1,3})?$|^\d{1,3}$")

_GLUED_RE = re.compile(r"^(.*?[\u4e00-\u9fff\uff09\)])(-?\d[\d,]*(?:\.\d+)?)$")

_MAIN_HEADER = [
    "指标名称",
    "本季度数（万元）",
    "上季度数（万元）",
    "本季度比上季度增减（%）",
    "下季度预测数（万元）",
]
_START_LABEL = "认可资产"
_STOP_LABEL = "综合偿付能力充足率"


def _cell(value: Any) -> str:
    return str(value or "").strip()


def _is_amount(value: str) -> bool:
    compact = value.replace(" ", "")
    if not _AMOUNT_RE.match(compact):
        return False
    # 行次（1、1.1、2.4）不是金额；金额要有千分位、三位以上整数或两位以上小数
    if "," in compact:
        return True
    if "." in compact:
        return len(compact.split(".", 1)[1]) >= 2
    return len(compact.lstrip("-")) >= 3


def _is_percent(value: str) -> bool:
    return bool(_PERCENT_RE.match(value.replace(" ", "")))


def _merge_fragments(label: str, fragments: list[str]) -> list[str]:
    """把 ``29,1`` + ``77.35`` 这类被拆开的金额拼回一个。"""
    merged: list[str] = []
    buffer = ""
    for frag in fragments:
        compact = frag.replace(" ", "")
        if not buffer:
            buffer = compact
            continue
        # 前一段以逗号结尾（29,187）或缺少数位（29,1）时继续拼接
        if re.search(r"\d,\d{0,2}$", buffer) or re.search(r"\.\d$", buffer):
            buffer += compact
        elif compact.startswith(("0", "1", "2", "3", "4", "5", "6", "7", "8", "9")) and re.search(r"\d$", buffer):
            # 数字被切成两段且没有逗号/小数点：只在长度明显不足时拼接
            if len(buffer) <= 3 and buffer[-3:].isdigit() and not buffer.startswith("-"):
                buffer += compact
            else:
                merged.append(buffer)
                buffer = compact
        else:
            merged.append(buffer)
            buffer = compact
    if buffer:
        merged.append(buffer)
    return merged


@register("changjiang")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:  # noqa: ARG001
    """只重排偿付能力主表。"""
    for table in tables:
        if str(getattr(table, "table_id", "") or "") != "SOLVENCY_MAIN":
            continue
        rows = getattr(table, "rows", None) or []
        rebuilt = _rebuild_main(rows)
        if rebuilt:
            try:
                table.rows = rebuilt
            except Exception:  # pragma: no cover - 容错
                pass
            blocks = getattr(table, "table_blocks", None) or []
            if blocks:
                try:
                    blocks[0]["rows"] = rebuilt
                except Exception:  # pragma: no cover
                    pass
    return tables


def _rebuild_main(rows: Sequence[Sequence[Any]]) -> list[list[str]]:
    parsed: list[tuple[str, list[str], list[str]]] = []
    started = False
    for row in rows:
        cells = [_cell(c) for c in row]
        if not any(cells):
            continue
        label_parts: list[str] = []
        numbers: list[str] = []
        percentages: list[str] = []
        for cell in cells:
            if not cell:
                continue
            glued = _GLUED_RE.match(cell)
            if glued:
                label_parts.append(glued.group(1))
                numbers.append(glued.group(2))
                continue
            if _is_percent(cell):
                percentages.append(cell)
            elif _is_amount(cell):
                numbers.append(cell)
            else:
                label_parts.append(cell)
        label = "".join(label_parts)
        if not started:
            if label.startswith(_START_LABEL):
                started = True
            else:
                continue
        if not label and not numbers:
            continue
        parsed.append((label, numbers, percentages))
        if label.startswith(_STOP_LABEL):
            break

    if not parsed:
        return []

    rebuilt: list[list[str]] = [list(_MAIN_HEADER)]
    for label, numbers, percentages in parsed:
        amounts = _merge_fragments(label, numbers)
        current = amounts[0] if len(amounts) >= 1 else ""
        previous = amounts[1] if len(amounts) >= 2 else ""
        forecast = amounts[2] if len(amounts) >= 3 else ""
        change = percentages[0] if percentages else ""
        rebuilt.append([label, current, previous, change, forecast])
    return rebuilt
