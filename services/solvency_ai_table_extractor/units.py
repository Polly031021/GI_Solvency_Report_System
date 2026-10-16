"""单位记录：识别、合并、按行匹配与视觉兜底恢复。"""

from __future__ import annotations

import json
import re
from typing import Callable

from ..solvency_table_extractor import UnitRecord

from .grid import _slice_grid_for_target
from .llm import _call_model, _parse_json
from .models import PageGrid, _clean, _compact

_UNIT_TOKEN = (
    r"(?:\u4eba\u6c11\u5e01\s*)?"
    r"(?:\u4ebf\u5143|\u4e07\u5143|\u5343\u5143|\u5143|%|\uff05|"
    r"\u767e\u5206\u6bd4|\u767e\u5206\u70b9|\u4eba|\u6237|\u4ef6|\u6b21|\u7ea7)"
)
_UNIT_DECLARATION_RE = re.compile(
    rf"(?:\u91d1\u989d)?\u5355\u4f4d\s*(?:[\uff1a:]\s*)?(?P<unit>{_UNIT_TOKEN})",
    flags=re.I,
)
_UNIT_PAREN_RE = re.compile(
    rf"[\uff08(]\s*(?P<unit>{_UNIT_TOKEN})\s*[\uff09)]",
    flags=re.I,
)
_UNIT_GROUP_PAREN_RE = re.compile(
    r"[\uff08(](?P<body>[^\uff08\uff09()]{1,40})[\uff09)]",
    flags=re.I,
)
_SUPPORTED_UNITS = {
    "\u4ebf\u5143",
    "\u4e07\u5143",
    "\u5343\u5143",
    "\u5143",
    "%",
    "\u767e\u5206\u70b9",
    "\u4eba",
    "\u6237",
    "\u4ef6",
    "\u6b21",
    "\u7ea7",
}
_PERCENT_LABEL_TERMS = (
    "\u7387",
    "\u6bd4\u4f8b",
    "\u5360\u6bd4",
    "\u589e\u901f",
    "\u589e\u957f",
)


def _normalize_unit(value: str) -> str:
    text = re.sub(r"[\s\uff1a:\uff08\uff09()]", "", str(value or ""))
    text = text.replace("\uff05", "%")
    if "\u767e\u5206\u6bd4" in text:
        return "%"
    if "\u767e\u5206\u70b9" in text:
        return "\u767e\u5206\u70b9"
    for unit in (
        "\u4ebf\u5143",
        "\u4e07\u5143",
        "\u5343\u5143",
        "\u5143",
        "%",
        "\u4eba",
        "\u6237",
        "\u4ef6",
        "\u6b21",
        "\u7ea7",
    ):
        if text.endswith(unit):
            return unit
    return text


def _unit_raw(value: str) -> str:
    text = re.sub(r"\s+", "", str(value or "")).strip("\uff1a:")
    return text or str(value or "").strip()


def _units_from_parenthetical(value: str) -> list[str]:
    """Read one or more explicit units from a pure parenthetical unit group."""
    units: list[str] = []
    for group in _UNIT_GROUP_PAREN_RE.finditer(str(value or "")):
        body = group.group("body")
        matches = list(re.finditer(_UNIT_TOKEN, body, flags=re.I))
        if not matches:
            continue
        remainder = re.sub(_UNIT_TOKEN, "", body, flags=re.I)
        # Only accept a parenthesis made entirely of units and separators.
        # This excludes narrative parentheses and section labels.
        if re.sub(r"[\s,，、/；;和及]+", "", remainder):
            continue
        units.extend(_unit_raw(match.group(0)) for match in matches)
    return list(dict.fromkeys(units))


def _unit_target_key(value: str) -> str:
    text = _UNIT_PAREN_RE.sub("", str(value or ""))
    normalized = _normalize_unit(text)
    if normalized in _SUPPORTED_UNITS:
        text = re.sub(
            rf"(?:{_UNIT_TOKEN})\s*$",
            "",
            text,
            flags=re.I,
        )
    return _compact(text)


def _row_label_index(row: list[str]) -> int:
    return 1 if len(row) > 1 and re.fullmatch(r"\d+(?:\.\d+)*\*?", row[0].strip()) else 0


def _row_label(row: list[str]) -> str:
    if not row:
        return ""
    label_index = _row_label_index(row)
    return _clean(row[label_index] if label_index < len(row) else "")


def merge_unit_records(*groups: list[UnitRecord]) -> list[UnitRecord]:
    unique: list[UnitRecord] = []
    seen: set[tuple] = set()
    for records in groups:
        for record in records:
            key = record.dedupe_key()
            if key not in seen:
                seen.add(key)
                unique.append(record)
    return unique


def _resolve_unit_target(
    scope: str,
    target: str,
    rows: list[list[str]],
) -> str | None:
    if scope == "\u8868\u7ea7":
        return ""
    candidates = (
        [_clean(cell) for cell in rows[0] if _clean(cell)]
        if scope == "\u5217\u7ea7" and rows
        else [_row_label(row) for row in rows[1:]]
    )
    candidates = [candidate for candidate in candidates if candidate]
    target_key = _unit_target_key(target)
    if not target_key:
        return None
    exact = next(
        (candidate for candidate in candidates if _unit_target_key(candidate) == target_key),
        None,
    )
    if exact:
        return exact
    contained = [
        candidate
        for candidate in candidates
        if (target_key in _unit_target_key(candidate) or _unit_target_key(candidate) in target_key)
    ]
    if not contained:
        return None
    return min(
        contained,
        key=lambda candidate: abs(len(_unit_target_key(candidate)) - len(target_key)),
    )


def unit_records_from_payload(
    payload: dict,
    rows: list[list[str]],
    pages: list[int],
    *,
    source_type: str = "\u56fe\u7247\u7ed3\u6784\u5316\u5355\u4f4d",
) -> list[UnitRecord]:
    raw_records = payload.get("unit_records", payload.get("units", []))
    if not isinstance(raw_records, list):
        return []
    page_set = set(pages)
    default_page = pages[0] if pages else 0
    scope_aliases = {
        "\u8868": "\u8868\u7ea7",
        "\u8868\u7ea7": "\u8868\u7ea7",
        "table": "\u8868\u7ea7",
        "\u5217": "\u5217\u7ea7",
        "\u5217\u7ea7": "\u5217\u7ea7",
        "column": "\u5217\u7ea7",
        "\u884c": "\u884c\u7ea7",
        "\u884c\u7ea7": "\u884c\u7ea7",
        "row": "\u884c\u7ea7",
    }
    records: list[UnitRecord] = []
    for item in raw_records:
        if not isinstance(item, dict):
            continue
        scope = scope_aliases.get(
            str(item.get("scope", item.get("\u4f5c\u7528\u8303\u56f4", ""))).strip().lower()
        )
        if not scope:
            continue
        raw = _unit_raw(
            item.get(
                "raw_unit",
                item.get(
                    "unit",
                    item.get("\u539f\u59cb\u5355\u4f4d", item.get("normalized_unit", "")),
                ),
            )
        )
        normalized = _normalize_unit(raw)
        if normalized not in _SUPPORTED_UNITS:
            continue
        target = str(
            item.get("target", item.get("\u5bf9\u8c61", item.get("label", ""))) or ""
        ).strip()
        resolved_target = _resolve_unit_target(scope, target, rows)
        if resolved_target is None:
            continue
        try:
            source_page = int(
                item.get("source_page", item.get("\u7269\u7406\u9875\u7801", default_page))
                or default_page
            )
        except (TypeError, ValueError):
            source_page = default_page
        if page_set and source_page not in page_set:
            source_page = default_page
        records.append(
            UnitRecord(
                scope=scope,
                target=resolved_target,
                raw_unit=raw,
                normalized_unit=normalized,
                source_page=source_page,
                source_type=source_type,
                source_text=str(
                    item.get("source_text", item.get("\u6765\u6e90\u539f\u6587", "")) or ""
                ).strip(),
                confidence=str(
                    item.get("confidence", item.get("\u7f6e\u4fe1\u5ea6", "\u9ad8")) or "\u9ad8"
                ).strip(),
            )
        )
    return merge_unit_records(records)


def unit_recovery_needed(
    rows: list[list[str]],
    records: list[UnitRecord],
) -> bool:
    if len(rows) < 2:
        return False
    table_units = {record.normalized_unit for record in records if record.scope == "\u8868\u7ea7"}
    column_units = {record.normalized_unit for record in records if record.scope == "\u5217\u7ea7"}
    for row in rows[1:]:
        label = _row_label(row)
        if not label:
            continue
        label_index = _row_label_index(row)
        value_cells = row[label_index + 1 :]
        if not any(re.search(r"\d", cell) for cell in value_cells):
            continue
        row_key = _unit_target_key(label)
        available = {
            record.normalized_unit
            for record in records
            if (record.scope == "\u884c\u7ea7" and _unit_target_key(record.target) == row_key)
        }
        available.update(table_units)
        available.update(column_units)
        expects_percent = any(term in label for term in _PERCENT_LABEL_TERMS)
        if expects_percent:
            if "%" not in available and "\u767e\u5206\u70b9" not in available:
                return True
        elif not available:
            return True
    return False


def _unit_vision_messages(
    table_id: str,
    table_name: str,
    pages: list[int],
    rows: list[list[str]],
    images: list[tuple[int, str]],
) -> list[dict]:
    columns = rows[0] if rows else []
    row_targets = [_row_label(row) for row in rows[1:] if _row_label(row)]
    prompt = f"""你是保险公司偿付能力报告单位核对专家。
目标表ID：{table_id}
目标表名称：{table_name}
PDF物理页码：{pages}

系统已经提取的列名：
{json.dumps(columns, ensure_ascii=False)}
系统已经提取的项目名：
{json.dumps(row_targets, ensure_ascii=False)}

请只读取随后页面图片中明确出现的单位，不得依据数值大小、行业惯例或项目含义猜测。
1. 表格上方统一单位，或表格标题括号中的单位，使用scope="表级"，target=""。
   标题如“其他经营指标（元，%）”时必须分别输出“元”和“%”两条表级记录；
   金额项目使用元、率类项目使用%，不得把两个单位合成一个字符串。
2. 表头单位使用scope="列级"，target必须从上述列名中原样选择。
3. 项目名内单位使用scope="行级"，target必须从上述项目名中原样选择；即使图片原文项目名与系统项目名略有差异，也要映射到系统项目名。
4. source_text必须保留图片中实际看到的含单位原文，例如“实际资本（元）”。
5. 支持的单位：亿元、万元、千元、元、%、百分点、人、户、件、次、级。
6. 图片中没有明确单位时不要输出该记录。
7. 只输出JSON对象，不输出Markdown或解释文字。

JSON格式：
{{"unit_records":[
  {{"scope":"行级","target":"实际资本合计","raw_unit":"元",
    "source_page":8,"source_text":"实际资本（元）","confidence":"高"}}
]}}
"""
    content: list[dict] = [{"type": "text", "text": prompt}]
    for page_number, data_url in images:
        content.extend(
            [
                {"type": "text", "text": f"PDF物理第 {page_number} 页"},
                {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
            ]
        )
    return [
        {
            "role": "system",
            "content": "\u4f60\u53ea\u8d1f\u8d23\u4ece\u9875\u9762\u56fe\u7247\u4e2d\u6284\u5f55\u660e\u786e\u5355\u4f4d\uff0c\u4e0d\u5f97\u63a8\u65ad\u5355\u4f4d\u3002",
        },
        {"role": "user", "content": content},
    ]


def _line_content(line: str) -> str:
    return str(line or "").partition("|")[2] if "|" in str(line or "") else str(line or "")


def _unit_from_label(
    label: str,
    *,
    scope: str,
    target: str,
    source_page: int,
    source_type: str,
) -> list[UnitRecord]:
    records: list[UnitRecord] = []
    for raw in _units_from_parenthetical(label):
        records.append(
            UnitRecord(
                scope=scope,
                target=target,
                raw_unit=raw,
                normalized_unit=_normalize_unit(raw),
                source_page=source_page,
                source_type=source_type,
                source_text=str(label or "").strip(),
                confidence="\u9ad8",
            )
        )
    return records


def _unit_records_from_grid(
    table_id: str,
    page_number: int,
    grid_text: str,
) -> list[UnitRecord]:
    lines = str(grid_text or "").splitlines()
    if not lines:
        return []
    sliced_lines = _slice_grid_for_target(table_id, grid_text).splitlines()
    if not sliced_lines:
        return []
    first = next((index for index, line in enumerate(lines) if line == sliced_lines[0]), 0)
    # Standalone unit labels normally sit between the table title and its header.
    context_start = max(0, first - 6)
    context_end = min(len(lines), first + 7)
    records: list[UnitRecord] = []
    for line in lines[context_start:context_end]:
        content = _line_content(line).strip()
        for match in _UNIT_DECLARATION_RE.finditer(content):
            raw = _unit_raw(match.group("unit"))
            records.append(
                UnitRecord(
                    scope="\u8868\u7ea7",
                    target="",
                    raw_unit=raw,
                    normalized_unit=_normalize_unit(raw),
                    source_page=page_number,
                    source_type="\u8868\u683c\u4e0a\u65b9\u6807\u6ce8",
                    source_text=content,
                    confidence="\u9ad8",
                )
            )
    # Some reports put units directly in the table title, for example
    # “S02-实际资本（元）” or “其他经营指标（元，%）”. Only inspect
    # title-side context through the first target line so later row/column
    # parentheses cannot be promoted to table-level units.
    for line in lines[context_start : first + 1]:
        content = _line_content(line).strip()
        records.extend(
            _unit_from_label(
                content,
                scope="\u8868\u7ea7",
                target="",
                source_page=page_number,
                source_type="\u8868\u683c\u6807\u9898\u62ec\u53f7\u5355\u4f4d",
            )
        )
    return records


def extract_unit_records(
    table_id: str,
    rows: list[list[str]],
    source_grids: list[PageGrid] | None = None,
) -> list[UnitRecord]:
    records: list[UnitRecord] = []
    for grid in source_grids or []:
        records.extend(_unit_records_from_grid(table_id, grid.page_number, grid.grid_text))

    first_grid = next(iter(source_grids or []), None)
    source_page_number = first_grid.page_number if first_grid else 0
    if rows:
        for cell in rows[0]:
            text = _clean(cell)
            records.extend(
                _unit_from_label(
                    text,
                    scope="\u5217\u7ea7",
                    target=text,
                    source_page=source_page_number,
                    source_type="\u8868\u5934\u9644\u5e26\u5355\u4f4d",
                )
            )
        for row in rows[1:]:
            if not row:
                continue
            label_index = (
                1 if len(row) > 1 and re.fullmatch(r"\d+(?:\.\d+)*\*?", row[0].strip()) else 0
            )
            label = _clean(row[label_index] if label_index < len(row) else "")
            if label:
                records.extend(
                    _unit_from_label(
                        label,
                        scope="\u884c\u7ea7",
                        target=label,
                        source_page=source_page_number,
                        source_type="\u9879\u76ee\u540d\u79f0\u9644\u5e26\u5355\u4f4d",
                    )
                )
            value_cells = row[label_index + 1 :]
            has_percent = any(
                re.search(r"(?:%|\uff05)\s*$", cell.strip()) for cell in value_cells if cell.strip()
            )
            if label and has_percent:
                records.append(
                    UnitRecord(
                        scope="\u884c\u7ea7",
                        target=label,
                        raw_unit="%",
                        normalized_unit="%",
                        source_page=source_page_number,
                        source_type="\u6570\u503c\u683c\u5f0f",
                        source_text="\uff1b".join(
                            cell.strip() for cell in value_cells if cell.strip()
                        ),
                        confidence="\u9ad8",
                    )
                )
    return merge_unit_records(records)


def recover_unit_records_from_images(
    *,
    table_id: str,
    table_name: str,
    pages: list[int],
    rows: list[list[str]],
    images: list[tuple[int, str]],
    api_key: str,
    base_url: str,
    model: str,
    timeout: int,
    post_func: Callable | None,
) -> list[UnitRecord]:
    payload = _parse_json(
        _call_model(
            api_key=api_key,
            base_url=base_url,
            model=model,
            messages=_unit_vision_messages(
                table_id,
                table_name,
                pages,
                rows,
                images,
            ),
            timeout=timeout,
            post_func=post_func,
        )
    )
    return unit_records_from_payload(
        payload,
        rows,
        pages,
        source_type="\u56fe\u7247\u5355\u4f4d\u4e13\u7528\u8865\u63d0\u53d6",
    )
