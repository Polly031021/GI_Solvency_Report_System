"""源PDF完整性画像：表格行、章节顺序、条目召回率与最低资本尾段补齐。"""

from __future__ import annotations

import re
from typing import Mapping

from ..solvency_table_boundaries import boundary_items, item_hits_in_rows, line_has_item
from ..solvency_table_extractor import TABLE_HEADERS
from ..table_strategy_handlers import BoundaryRequest, CompletenessRequest
from ..table_strategy_registry import active_table_strategy

from .grid import _slice_grid_for_target
from .models import (
    AI_TABLE_END_MARKERS,
    ExtractionQualityError,
    OPERATING_SECTION_TITLES,
    PageGrid,
    TABLE_COMPLETENESS_TERMS,
    _clean,
    _compact,
)
from .units import _row_label_index

_TABLE_FOOTNOTE_START_RE = re.compile(r"^\s*(?:\d+\s*)?(?:注释|注|(?:填表)?说明)\s*[：:]")


def _source_table_lines_core(
    table_id: str,
    grids: list[PageGrid] | None,
    *,
    preserve_adjustment_after_footnote: bool = False,
    table_config: Mapping[str, object] | None = None,
) -> list[str]:
    """Return target-table lines without page-local footnotes and their continuations."""
    source_lines: list[str] = []
    for grid in grids or []:
        sliced_lines = _slice_grid_for_target(
            table_id,
            grid.grid_text,
            table_config,
        ).splitlines()
        index = 0
        while index < len(sliced_lines):
            line = sliced_lines[index]
            content = line.partition("|")[2]
            if _TABLE_FOOTNOTE_START_RE.match(content):
                if preserve_adjustment_after_footnote:
                    continuation_index = next(
                        (
                            candidate
                            for candidate in range(index + 1, len(sliced_lines))
                            if any(
                                term in _compact(sliced_lines[candidate].partition("|")[2])
                                for term in (
                                    _compact("核心一级资本调整表"),
                                    _compact("核心资本调整表"),
                                )
                            )
                        ),
                        None,
                    )
                    if continuation_index is not None:
                        index = continuation_index
                        continue
                break
            source_lines.append(line)
            index += 1
    return source_lines


def _source_table_lines(
    table_id: str,
    grids: list[PageGrid] | None,
    table_config: Mapping[str, object] | None = None,
) -> list[str]:
    strategy = active_table_strategy(table_id)
    return strategy.enforce_boundaries(
        BoundaryRequest(
            mode="source_lines",
            table_id=table_id,
            source_grids=grids,
            engine=_source_table_lines_core,
            table_config=dict(table_config or {}),
        )
    )


_GRID_VALUE_RE = re.compile(
    r"(?<![\d.])(?:[<＜]\s*不适用\s*[>＞]|不适用|N\s*/?\s*A|"
    r"--|—|-|[（(]?-?\d[\d,，]*(?:\.\d+)?%?[）)]?)(?![\d.])",
    flags=re.I,
)
_SOURCE_CELL_VALUE_RE = re.compile(
    r"(?:[<＜]\s*不适用\s*[>＞]|不适用|N\s*/?\s*A|" r"(?<!\d)[(（\-]?\d[\d,，.％%]*[)）]?)",
    flags=re.I,
)
_SOURCE_PLACEHOLDER_VALUE_RE = re.compile(r"(?<!\S)(?:--|—|-)(?!\S)")


def _source_value_matches(
    table_id: str,
    content: str,
) -> list[re.Match]:
    matches = list(_SOURCE_CELL_VALUE_RE.finditer(content))
    if table_id == "OPERATING_METRICS":
        matches.extend(_SOURCE_PLACEHOLDER_VALUE_RE.finditer(content))
    return sorted(matches, key=lambda match: match.start())


def _is_operating_section_only_line(
    content: str,
    value_matches: list[re.Match],
) -> bool:
    if not value_matches:
        return False
    label = _compact(content[: value_matches[0].start()])
    label = re.sub(
        r"^(?:[一二三四五六七八九十]+|\d+(?:\.\d+)*)",
        "",
        label,
    )
    non_business_titles = (
        *OPERATING_SECTION_TITLES,
        "主要经营指标",
        "主要经营情况",
    )
    return label in {_compact(item) for item in non_business_titles}


def _cell_has_disclosed_value(value: object) -> bool:
    text = str(value or "")
    return bool(
        re.search(r"\d", text)
        or re.fullmatch(
            r"\s*(?:[<＜]\s*不适用\s*[>＞]|不适用|N\s*/?\s*A)\s*",
            text,
            flags=re.I,
        )
    )


def _grid_business_row(line: str, width: int) -> list[str] | None:
    content = line.partition("|")[2].strip()
    value_matches = list(_GRID_VALUE_RE.finditer(content))
    if len(value_matches) < 3:
        return None
    current_match, previous_match = value_matches[-2:]
    prefix = content[: current_match.start()].strip()
    row_number_match = re.match(r"(?P<number>\d+(?:\.\d+)*\*?)\s+", prefix)
    row_number = row_number_match.group("number") if row_number_match else ""
    label = prefix[row_number_match.end() :].strip() if row_number_match else prefix
    if not label:
        return None
    parsed = (
        [
            row_number,
            label,
            current_match.group(0),
            previous_match.group(0),
        ]
        if width >= 4
        else [label, current_match.group(0), previous_match.group(0)]
    )
    parsed += [""] * max(0, width - len(parsed))
    return parsed[:width]


def _normalize_minimum_capital_total_alias_rows(
    rows: list[list[str]],
) -> tuple[list[list[str]], bool]:
    """Normalize a row-numbered final “4 合计” to canonical minimum capital."""
    normalized: list[list[str]] = []
    changed = False
    for row in rows:
        cells = list(row)
        if len(cells) >= 2 and _compact(cells[0]) == "4" and _compact(cells[1]).startswith("合计"):
            cells[1] = re.sub("合计", "最低资本", cells[1], count=1)
            changed = True
        elif cells and re.match(r"^4\s*合计", str(cells[0]).strip()):
            cells[0] = re.sub(
                r"^(4\s*)合计",
                r"\1最低资本",
                cells[0],
                count=1,
            )
            changed = True
        elif len(cells) >= 3 and _compact(cells[0]).startswith("合计"):
            cells[0] = re.sub("合计", "最低资本", cells[0], count=1)
            changed = True
        normalized.append(cells)
    return normalized, changed


def recover_minimum_capital_terminal_rows_core(
    table_id: str,
    rows: list[list[str]],
    grids: list[PageGrid] | None,
) -> tuple[list[list[str]], str]:
    """Recover the explicitly printed tail after the main additional-capital row.

    This is deliberately limited to the quantity-risk-first minimum-capital
    layout: both source and model output must show the main “附加资本” row,
    and the source must show a later terminal “最低资本”. Therefore it cannot
    move the summary-first layout's opening minimum-capital row.
    """
    rows, alias_normalized = _normalize_minimum_capital_total_alias_rows(rows)
    alias_note = "已将源表末行‘4 合计’标准化为‘4 最低资本’" if alias_normalized else ""
    if not rows or not grids:
        return rows, alias_note
    terminal_aliases = (
        "最低资本",
        "最低资本合计",
        "最低资本总额",
        "合计",
    )

    source_lines = _source_table_lines(table_id, grids)
    terminal_candidates: list[tuple[int, str, str]] = []
    for index, line in enumerate(source_lines):
        alias = next(
            (item for item in terminal_aliases if line_has_item(line, item, require_value=True)),
            "",
        )
        if alias:
            terminal_candidates.append((index, alias, line))
    if not terminal_candidates:
        return rows, alias_note

    terminal_index, alias, _ = terminal_candidates[-1]
    width = max(len(row) for row in rows)
    source_additional_indices = [
        index
        for index, line in enumerate(source_lines[:terminal_index])
        if (line_has_item(line, "附加资本") and _grid_business_row(line, width) is not None)
    ]
    output_additional_index = next(
        (index for index, row in enumerate(rows) if item_hits_in_rows([row], ("附加资本",))),
        None,
    )
    if not source_additional_indices or output_additional_index is None:
        return rows, alias_note

    source_additional_index = source_additional_indices[-1]
    source_tail = [
        parsed
        for line in source_lines[source_additional_index + 1 : terminal_index + 1]
        if (parsed := _grid_business_row(line, width)) is not None
    ]
    source_tail, _ = _normalize_minimum_capital_total_alias_rows(source_tail)
    canonical_terminal_aliases = (
        "最低资本",
        "最低资本合计",
        "最低资本总额",
    )
    if not source_tail or not item_hits_in_rows(source_tail[-1:], canonical_terminal_aliases):
        return rows, alias_note

    existing_tail = rows[output_additional_index + 1 :]
    if existing_tail == source_tail:
        return rows, alias_note
    recovered_rows = [*rows[: output_additional_index + 1], *source_tail]
    recovery_note = (
        f"模型遗漏最低资本尾段，已依据源PDF网格补回" f"{len(source_tail)}行（终止于“{alias}”）"
    )
    return recovered_rows, "；".join(note for note in (alias_note, recovery_note) if note)


def _source_completeness_profile_core(
    table_id: str,
    grids: list[PageGrid] | None,
    *,
    allow_single_value_boundary_rows: bool = False,
    table_config: Mapping[str, object] | None = None,
) -> tuple[int, tuple[str, ...]]:
    """Estimate source rows and the boundary items visibly present in the source."""
    config = dict(table_config or {})
    source_lines = _source_table_lines(table_id, grids, config)

    required_terms = [
        term
        for term in (config.get("completeness_terms") or TABLE_COMPLETENESS_TERMS.get(table_id, ()))
        if any(line_has_item(line, term, require_value=True) for line in source_lines)
    ]
    semantic_items = boundary_items(table_id, config)
    for item in semantic_items:
        if any(line_has_item(line, item, require_value=True) for line in source_lines):
            canonical_item = item
            if item == "合计":
                if table_id == "MINIMUM_CAPITAL":
                    canonical_item = "最低资本"
                elif table_id == "ACTUAL_CAPITAL":
                    canonical_item = "实际资本合计"
            required_terms.append(canonical_item)

    numeric_rows = 0
    for line in source_lines:
        content = line.partition("|")[2]
        compact_content = _compact(content)
        if any(
            compact_content.startswith(_compact(term))
            for term in (
                "公司名称",
                "报告期",
                "报表日期",
                "编制日期",
                "单位",
                "币种",
            )
        ):
            continue
        value_matches = _source_value_matches(table_id, content)
        if (
            bool(config.get("completeness_track_sections")) or table_id == "OPERATING_METRICS"
        ) and _is_operating_section_only_line(content, value_matches):
            continue
        if len(value_matches) >= 2:
            numeric_rows += 1
        elif (
            allow_single_value_boundary_rows
            and ("%" in content or "％" in content)
            and any(line_has_item(line, item) for item in semantic_items)
        ):
            numeric_rows += 1
    return numeric_rows, tuple(dict.fromkeys(required_terms))


def _source_completeness_profile(
    table_id: str,
    grids: list[PageGrid] | None,
    table_config: Mapping[str, object] | None = None,
) -> tuple[int, tuple[str, ...]]:
    strategy = active_table_strategy(table_id)
    return strategy.validate_completeness(
        CompletenessRequest(
            mode="source_profile",
            table_id=table_id,
            rows=[],
            validator=_source_completeness_profile_core,
            validator_kwargs={"grids": grids},
            table_config=dict(table_config or {}),
        )
    )


def _normalized_item_label(value: str) -> str:
    text = _compact(value)
    text = re.sub(r"^\d+(?:\.\d+)*\*?", "", text)
    text = re.sub(r"[（(](?:元|千元|万元|亿元|%|％|人|件|次)[）)]$", "", text)
    return text


def _source_item_labels(
    table_id: str,
    grids: list[PageGrid] | None,
    table_config: Mapping[str, object] | None = None,
) -> tuple[str, ...]:
    """Recover row-label fingerprints from the source PDF grid.

    Labels are deliberately conservative: a source line must contain a
    financial value and a meaningful text prefix. This avoids treating titles,
    page numbers, and narrative notes as required table rows.
    """
    labels: list[str] = []
    number_pattern = _SOURCE_CELL_VALUE_RE
    config = dict(table_config or {})
    header_terms = {
        _compact(term) for term in (config.get("header_terms") or TABLE_HEADERS.get(table_id, ()))
    }
    metadata_terms = tuple(
        map(
            _compact,
            ("公司名称", "报告期", "报表日期", "编制日期", "单位", "币种"),
        )
    )
    source_lines = _source_table_lines(table_id, grids, config)
    numbered_row_re = re.compile(r"^\s*(?:[（(]?[一二三四五六七八九十]+[）)]|" r"\d+(?:\.\d+)*\.?)")
    for line_index, line in enumerate(source_lines):
        content = line.partition("|")[2]
        compact_content = _compact(content)
        if any(compact_content.startswith(term) for term in metadata_terms):
            continue
        number_matches = _source_value_matches(table_id, content)
        if len(number_matches) < 2:
            continue
        if (
            bool(config.get("completeness_track_sections")) or table_id == "OPERATING_METRICS"
        ) and _is_operating_section_only_line(content, number_matches):
            continue
        if len(number_matches) == 2 and re.match(
            r"^\s*\d+(?:\.\d+)*\*?\s+[\u4e00-\u9fffA-Za-z]",
            content,
        ):
            # Row-number tables can legitimately disclose a single value,
            # notably the separate next-quarter forecast subtable.
            number_match = number_matches[-1]
        else:
            number_match = number_matches[-2]
        label_prefix = content[: number_match.start()].strip()
        recovered_wrapped_label = False
        if not numbered_row_re.match(label_prefix) and not re.search(
            r"[\u4e00-\u9fffA-Za-z]", label_prefix
        ):
            fragments: list[str] = []
            for previous_index in range(
                line_index - 1,
                max(-1, line_index - 3),
                -1,
            ):
                previous = source_lines[previous_index].partition("|")[2].strip()
                if not previous:
                    continue
                fragments.insert(0, previous)
                if numbered_row_re.match(previous):
                    label_prefix = "".join([*fragments, label_prefix])
                    recovered_wrapped_label = True
                    break
                if len(number_pattern.findall(previous)) >= 2:
                    break
        if recovered_wrapped_label:
            for next_index in range(
                line_index + 1,
                min(len(source_lines), line_index + 3),
            ):
                following = source_lines[next_index].partition("|")[2].strip()
                if number_pattern.search(following) or numbered_row_re.match(following):
                    break
                if following:
                    label_prefix += following
        label = _normalized_item_label(label_prefix)
        if len(label) < 2 or not re.search(r"[\u4e00-\u9fffA-Za-z]", label):
            continue
        if label in header_terms or any(
            header and header in label and len(label) <= len(header) + 2 for header in header_terms
        ):
            continue
        if any(label.startswith(term) for term in metadata_terms):
            continue
        labels.append(label)
    return tuple(dict.fromkeys(labels))


def _source_section_titles_core(
    table_id: str,
    grids: list[PageGrid] | None,
    *,
    track_operating_sections: bool = False,
    table_config: Mapping[str, object] | None = None,
) -> tuple[str, ...]:
    if not track_operating_sections:
        return ()
    ordered: list[str] = []
    config = dict(table_config or {})
    section_titles = tuple(config.get("section_titles") or OPERATING_SECTION_TITLES)
    for line in _source_table_lines(table_id, grids, config):
        compact_line = _compact(line.partition("|")[2])
        for title in section_titles:
            if _compact(title) in compact_line and title not in ordered:
                ordered.append(title)
    return tuple(ordered)


def _source_section_titles(
    table_id: str,
    grids: list[PageGrid] | None,
    table_config: Mapping[str, object] | None = None,
) -> tuple[str, ...]:
    strategy = active_table_strategy(table_id)
    return strategy.validate_completeness(
        CompletenessRequest(
            mode="source_sections",
            table_id=table_id,
            rows=[],
            validator=_source_section_titles_core,
            validator_kwargs={"grids": grids},
            table_config=dict(table_config or {}),
        )
    )


def _validate_source_section_order(
    rows: list[list[str]],
    source_section_titles: tuple[str, ...],
) -> None:
    if not source_section_titles:
        return
    output_order: list[str] = []
    for row in rows:
        row_text = _compact("".join(row))
        for title in source_section_titles:
            if _compact(title) in row_text and title not in output_order:
                output_order.append(title)
    missing = [title for title in source_section_titles if title not in output_order]
    if missing:
        raise ExtractionQualityError(f"遗漏源PDF类别标题：{'、'.join(missing)}。")
    if tuple(output_order) != source_section_titles:
        raise ExtractionQualityError(
            "类别顺序与源PDF不一致：原文为"
            f"{' → '.join(source_section_titles)}，输出为"
            f"{' → '.join(output_order)}。"
        )


def _output_item_labels(rows: list[list[str]]) -> tuple[str, ...]:
    labels: list[str] = []
    for row in rows[1:] if len(rows) > 1 else rows:
        cells = [_clean(cell) for cell in row]
        label_index = _row_label_index(cells)
        if label_index >= len(cells):
            continue
        label = _normalized_item_label(cells[label_index])
        if len(label) >= 2 and re.search(r"[\u4e00-\u9fffA-Za-z]", label):
            labels.append(label)
    return tuple(dict.fromkeys(labels))


def _item_recall_profile(
    rows: list[list[str]],
    source_labels: tuple[str, ...],
) -> tuple[float, tuple[str, ...]]:
    if not source_labels:
        return 1.0, ()
    output_labels = _output_item_labels(rows)
    missing = [
        source
        for source in source_labels
        if not any(
            source == output or source in output or output in source for output in output_labels
        )
    ]
    recall = (len(source_labels) - len(missing)) / len(source_labels)
    return recall, tuple(missing)


def _trim_adjacent_rows(
    table_id: str,
    rows: list[list[str]],
    table_config: Mapping[str, object] | None = None,
) -> tuple[list[list[str]], str]:
    config = dict(table_config or {})
    markers = tuple(config.get("stop_terms") or AI_TABLE_END_MARKERS.get(table_id, ()))
    if len(rows) < 2 or not markers:
        return rows, ""
    for index, row in enumerate(rows[1:], start=1):
        row_text = _compact("".join(row))
        hit = next((_compact(term) for term in markers if _compact(term) in row_text), "")
        if hit:
            trimmed = rows[:index]
            if len(trimmed) < 2:
                raise ExtractionQualityError(f"目标表边界出现在首个数据行：{hit}。")
            return trimmed, f"已在相邻表边界“{hit}”前自动截断"
    return rows, ""
