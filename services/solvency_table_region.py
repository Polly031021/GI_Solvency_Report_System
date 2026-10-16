"""页内目标表格区域定位：一页多表时锁定目标表的纵向范围。

STEP1 只给出物理页码，但同一页经常并排/上下排列多张表（目标表可能是夹在中间
的那一张）。仅靠页码无法区分，模型也容易把相邻表一起读进来。

本模块用 Profile 已配置（或内置）的目标表首尾项目，在页面的文字行里定位目标表，
返回它在页面上的精确矩形区域（PDF 点、左上原点）。该区域随后被用于：

* 规则法：把 pdfplumber 的表格识别限制在该区域内，避免选到相邻表；
* 大模型视觉模式：把渲染图片裁切到该区域，让模型只看到目标表。

定位完全离线、可复现；找不到边界（例如扫描页没有文字层）时返回 None，
由调用方退回原有整页处理。
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import pdfplumber

from .solvency_table_boundaries import (
    boundary_variants,
    end_item_groups,
    line_has_item,
)
from .solvency_table_extractor import TABLE_HEADERS


# 行内单词聚合成一行的纵向容差，与文字网格保持一致
_LINE_TOLERANCE = 3.5
# 表头行的判定：命中表头词的数量
_HEADER_HIT_MINIMUM = 2
# 从起始项目向上寻找表头/表名的最大行数
_HEADER_SEARCH_LINES = 8


@dataclass(frozen=True)
class TableRegion:
    """目标表在某一页上的纵向范围。"""

    page_number: int
    bbox: tuple[float, float, float, float]
    start_item: str
    end_item: str
    variant_name: str
    header_found: bool
    note: str

    @property
    def height(self) -> float:
        return max(0.0, self.bbox[3] - self.bbox[1])


def _page_lines(page) -> list[dict]:
    """把页面文字按 y 坐标聚合成行：(top, bottom, text)。"""
    words = page.extract_words(
        x_tolerance=2,
        y_tolerance=3,
        keep_blank_chars=False,
        use_text_flow=False,
    )
    if not words:
        return []
    lines: list[dict] = []
    for word in sorted(words, key=lambda item: (float(item["top"]), float(item["x0"]))):
        top = float(word["top"])
        text = str(word.get("text", ""))
        if lines and abs(top - lines[-1]["top"]) <= _LINE_TOLERANCE:
            line = lines[-1]
            line["parts"].append(text)
            count = len(line["parts"])
            line["top"] = (line["top"] * (count - 1) + top) / count
            line["bottom"] = max(line["bottom"], float(word["bottom"]))
        else:
            lines.append(
                {
                    "top": top,
                    "bottom": float(word["bottom"]),
                    "parts": [text],
                }
            )
    for line in lines:
        line["text"] = "".join(line.pop("parts"))
    return lines


def _header_terms(table_id: str, table_config: dict) -> tuple[str, ...]:
    configured = tuple(str(item) for item in table_config.get("header_terms", ()) or ())
    if configured:
        return configured
    return tuple(TABLE_HEADERS.get(table_id, ())) or (
        "指标名称",
        "项目",
        "行次",
        "序号",
        "本季度",
        "上季度",
        "期末",
        "期初",
    )


def _find_header_line(
    lines: list[dict],
    start_index: int,
    table_id: str,
    table_config: dict,
) -> int | None:
    """在起始项目上方寻找表头行；找不到时返回 None（调用方补标准表头）。"""
    terms = _header_terms(table_id, table_config)
    title_terms = tuple(
        str(item) for item in (table_config.get("title_terms") or ()) if str(item).strip()
    )
    best: int | None = None
    for offset in range(1, _HEADER_SEARCH_LINES + 1):
        index = start_index - offset
        if index < 0:
            break
        text = lines[index]["text"]
        header_hits = sum(1 for term in terms if term and term in text)
        title_hit = any(term in text for term in title_terms)
        if header_hits >= _HEADER_HIT_MINIMUM or title_hit:
            best = index
        elif best is not None:
            # 继续向上会跨出表头区域（中间已出现无关行）
            break
    return best


def _snap_to_table_rules(
    page,
    bbox: tuple[float, float, float, float],
    tolerance: float = 14.0,
) -> tuple[float, float, float, float]:
    """把区域边界吸附到最近的表格横线，保证裁切后仍能按线框还原表格。"""
    try:
        edges = list(page.edges or [])
    except Exception:
        return bbox
    horizontal = sorted(
        {
            float(edge["top"])
            for edge in edges
            if abs(float(edge["top"]) - float(edge["bottom"])) < 1.0
        }
    )
    if not horizontal:
        return bbox
    x0, top, x1, bottom = bbox
    above = [value for value in horizontal if value <= top + tolerance]
    below = [value for value in horizontal if value >= bottom - tolerance]
    if above:
        top = min(top, above[-1])
    if below:
        bottom = max(bottom, below[0])
    return (x0, max(0.0, top), x1, min(float(page.height), bottom))


def _variant_profile(
    lines: list[dict],
    boundary: dict,
    table_id: str,
) -> tuple[int, int | None, int | None]:
    """给一个版式打分，返回 (证据等级, 起始行, 终止行)。"""
    texts = [line["text"] for line in lines]
    start_items = tuple(boundary.get("start_items", ()))
    end_groups = end_item_groups(table_id, boundary)
    end_items = tuple(item for group in end_groups[:1] for item in group)

    def first_hit(items: tuple[str, ...], *, require_value: bool) -> int | None:
        for index, text in enumerate(texts):
            if any(line_has_item(text, item, require_value=require_value) for item in items):
                return index
        return None

    start_index = first_hit(start_items, require_value=True)
    if start_index is None:
        start_index = first_hit(start_items, require_value=False)
    end_index = None
    search_from = (start_index + 1) if start_index is not None else 0
    for index in range(search_from, len(texts)):
        if any(line_has_item(texts[index], item) for item in end_items):
            end_index = index
            break

    if start_index is not None and end_index is not None and end_index >= start_index:
        return 4, start_index, end_index
    if start_index is None and end_index is not None:
        # 续页：本页只有表格尾部，从页首取到终止项目
        first_data = next(
            (
                index
                for index, text in enumerate(texts)
                if sum(character.isdigit() for character in text) >= 2
            ),
            0,
        )
        return 3, first_data, end_index
    if start_index is not None:
        return 2, start_index, None
    return 0, None, None


def locate_table_region(
    pdf_bytes: bytes,
    page_number: int,
    table_id: str,
    table_config: dict | None = None,
) -> TableRegion | None:
    """定位目标表在页内的纵向区域；无法确定时返回 None。"""
    config = dict(table_config or {})
    variants = boundary_variants(table_id, config)
    if not variants:
        return None
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            if page_number < 1 or page_number > len(pdf.pages):
                return None
            page = pdf.pages[page_number - 1]
            if int(getattr(page, "rotation", 0) or 0) % 360:
                # 旋转页的坐标系换算容易出错，直接退回整页处理
                return None
            lines = _page_lines(page)
            if not lines:
                return None
            page_width = float(page.width)

            profiles: list[tuple[int, int, int | None, int | None, dict]] = []
            for variant_index, boundary in enumerate(variants):
                rank, start_index, end_index = _variant_profile(lines, boundary, table_id)
                if rank:
                    profiles.append((rank, -variant_index, start_index, end_index, boundary))
            if not profiles:
                return None
            _, _, start_index, end_index, boundary = max(
                profiles,
                key=lambda item: (item[0], item[1]),
            )
            if start_index is None or end_index is None or end_index <= start_index:
                return None

            header_index = _find_header_line(lines, start_index, table_id, config)
            top_index = header_index if header_index is not None else start_index
            line_height = max(
                6.0,
                sum(
                    max(1.0, lines[index]["bottom"] - lines[index]["top"])
                    for index in range(top_index, end_index + 1)
                )
                / max(1, end_index - top_index + 1),
            )
            margin = max(4.0, line_height * 0.5)
            bbox = (
                0.0,
                max(0.0, float(lines[top_index]["top"]) - margin),
                page_width,
                min(
                    float(page.height),
                    float(lines[end_index]["bottom"]) + margin,
                ),
            )
            bbox = _snap_to_table_rules(page, bbox)
    except Exception:
        return None

    if bbox[3] - bbox[1] <= 0:
        return None
    start_items = tuple(boundary.get("start_items", ()))
    matched_end = tuple(
        item
        for group in end_item_groups(table_id, boundary)[:1]
        for item in group
        if line_has_item(lines[end_index]["text"], item)
    )
    note = (
        f"页内区域定位：第{page_number}页 从“{start_items[0] if start_items else '首行'}”"
        f"到“{matched_end[0] if matched_end else '末行'}”"
        f"（版式：{boundary.get('name', '标准版式')}，"
        f"{'含表头' if header_index is not None else '未识别到表头'}）"
    )
    return TableRegion(
        page_number=page_number,
        bbox=bbox,
        start_item=start_items[0] if start_items else "",
        end_item=matched_end[0] if matched_end else "",
        variant_name=str(boundary.get("name", "标准版式")),
        header_found=header_index is not None,
        note=note,
    )
