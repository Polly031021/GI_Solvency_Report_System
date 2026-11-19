"""按 PDF 文本词坐标重建偿付能力主表，供各公司 extractor 共用。

部分公司的偿付能力主表在文本层里被拆碎（金额与标签粘连、表头被拆成
两三个词、行次单独成列），通用的区域/线框识别会把数值映射到错误的
期间列。这里绕过表格结构，直接按“行内词”重建：

    指标名称 | 本季度数 | 上季度数 | 本季度比上季度增减（%） | 下季度预测数

只读取 PDF 词坐标，不写死年份和页码。
"""

from __future__ import annotations

import re
from typing import Iterable, Sequence

_AMOUNT_RE = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")
_PERCENT_RE = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%$")
_SERIAL_RE = re.compile(r"^\d{1,2}(?:\.\d{1,2})*$")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

HEADER = [
    "指标名称",
    "本季度数（万元）",
    "上季度数（万元）",
    "本季度比上季度增减（%）",
    "下季度预测数（万元）",
]


def _is_amount(text: str) -> bool:
    return bool(_AMOUNT_RE.match(text.replace(" ", "")))


def _is_percent(text: str) -> bool:
    return bool(_PERCENT_RE.match(text.replace(" ", "")))


def _group_lines(words: Iterable[Sequence], tol: float = 3.0) -> list[list[tuple[float, str]]]:
    buckets: list[list[tuple[float, float, str]]] = []
    for word in words:
        x0, y0, text = float(word[0]), float(word[1]), str(word[4])
        placed = False
        for bucket in buckets:
            if abs(bucket[0][0] - y0) <= tol:
                bucket.append((y0, x0, text))
                placed = True
                break
        if not placed:
            buckets.append([(y0, x0, text)])
    buckets.sort(key=lambda bucket: bucket[0][0])
    return [[(x0, text) for _y0, x0, text in sorted(bucket, key=lambda item: item[1])] for bucket in buckets]


def rebuild_main_rows(
    doc,
    pages: Sequence[int],
    *,
    start_label: str = "认可资产",
    stop_labels: Sequence[str] = ("综合偿付能力充足率",),
) -> list[list[str]]:
    """从指定物理页重建主表行；返回 [header, ...] 或 []。"""
    parsed: list[tuple[str, list[str], list[str]]] = []
    started = False
    for page_no in pages:
        if not isinstance(page_no, int) or page_no < 1 or page_no > doc.page_count:
            continue
        for line in _group_lines(doc[page_no - 1].get_text("words")):
            label_parts: list[str] = []
            numbers: list[str] = []
            percents: list[str] = []
            for x0, text in line:
                compact = text.strip()
                if not compact:
                    continue
                if _is_percent(compact):
                    percents.append(compact)
                elif _is_amount(compact) and x0 >= 150:
                    numbers.append(compact.replace(" ", ""))
                elif _SERIAL_RE.match(compact) and x0 < 170:
                    continue  # 行次列
                elif _CJK_RE.search(compact):
                    label_parts.append(compact)
            label = "".join(label_parts)
            if not started:
                if label.startswith(start_label):
                    started = True
                else:
                    continue
            if not label:
                continue
            parsed.append((label, numbers, percents))
            if any(label.startswith(stop) for stop in stop_labels):
                break
        if parsed and any(label.startswith(stop) for label, _, _ in parsed[-1:] for stop in stop_labels):
            break
    if not parsed:
        return []

    rows: list[list[str]] = [list(HEADER)]
    for label, numbers, percents in parsed:
        current = numbers[0] if len(numbers) >= 1 else ""
        previous = numbers[1] if len(numbers) >= 2 else ""
        forecast = numbers[2] if len(numbers) >= 3 else ""
        change = percents[0] if percents else ""
        rows.append([label, current, previous, change, forecast])
    return rows
