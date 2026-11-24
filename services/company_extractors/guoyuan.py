"""国元农业 2022-2025 公司专属流动性及收益率重建。"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Sequence

from . import register

_LIQUIDITY_HEADER = ["指标名称", "本季度数", "上季度数"]
_RETURN_HEADER = ["项目", "数值"]
_LIQUIDITY_LABELS = (
    "经营活动净现金流",
    "百元保费经营活动净现金流",
    "特定业务现金流支出占比",
    "规模保费同比增速",
    "现金及流动性管理工具占比",
    "季均融资杠杆比例",
    "AA级（含）以下境内固定收益类资产占比",
    "持股比例大于5%的上市股票投资占比",
    "应收款项占比",
    "持有关联方资产占比",
)
_VALUE_TOKEN_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?%?|[-−–—]{1,2}")
_VALUE_LINE_RE = re.compile(
    r"^(?:[-+]?\d[\d,]*(?:\.\d+)?%?|[-−–—]{1,2})"
    r"(?:\s+(?:[-+]?\d[\d,]*(?:\.\d+)?%?|[-−–—]{1,2}))*$"
)
_RETURN_RE = re.compile(
    r"(?:近三年|三年)(?:的)?平均(?P<kind>综合)?投资收益率(?:为)?"
    r"(?P<value>[-−–—]{1,2}|[-+]?\d[\d,]*(?:\.\d+)?%?)"
)


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")))


def _page_text(pdf_bytes: bytes, pages: Sequence[int]) -> str:
    import fitz

    with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
        return "\n".join(
            pdf[int(page) - 1].get_text()
            for page in pages
            if 1 <= int(page) <= len(pdf)
        )


def _monitoring_lines(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    starts = [
        index
        for index, line in enumerate(lines)
        if "流动性风险监测指标" in _compact(line)
    ]
    if not starts:
        return []
    output: list[str] = []
    for line in lines[max(starts) :]:
        normalized = _compact(line)
        if output and any(
            marker in normalized
            for marker in ("近三年", "主要经营指标", "财产保险公司主要经营指标")
        ):
            break
        output.append(line)
    return output


def _values(line: str) -> list[str]:
    stripped = line.strip()
    if not _VALUE_LINE_RE.fullmatch(stripped):
        return []
    return _VALUE_TOKEN_RE.findall(stripped)


def _label_values(
    lines: Sequence[str], start: int, label_start: int, label_end: int
) -> list[str]:
    post: list[str] = []
    saw_indicator = False
    for line in lines[label_end + 1 :]:
        normalized = _compact(line)
        values = _values(line)
        if values and saw_indicator:
            post.extend(values)
            if len(post) >= 2:
                return post[:2]
            continue
        if values:
            break
        saw_indicator = saw_indicator or "指标值" in normalized
    for index in range(label_start - 1, start - 1, -1):
        if "指标值" not in _compact(lines[index]):
            continue
        before: list[str] = []
        for line in lines[index + 1 : label_start]:
            before.extend(_values(line))
            if len(before) >= 2:
                return before[:2]
        break
    return []


def _liquidity_rows(text: str) -> list[list[str]]:
    lines = _monitoring_lines(text)
    if not lines:
        return []
    rows: list[list[str]] = []
    cursor = 0
    for label in _LIQUIDITY_LABELS:
        target = _compact(label)
        buffer: list[str] = []
        run_start = cursor
        for index in range(cursor, len(lines)):
            if _values(lines[index]):
                run_start = index + 1
                continue
            buffer.append(_compact(lines[index]))
            if target not in "".join(buffer):
                continue
            label_start = run_start
            values = _label_values(lines, cursor, label_start, index)
            if len(values) >= 2:
                rows.append([label, values[0], values[1]])
            cursor = index + 1
            break
    return rows


def _return_rows(text: str) -> list[list[str]]:
    found: dict[str, str] = {}
    for match in _RETURN_RE.finditer(_compact(text)):
        kind = "综合" if match.group("kind") else ""
        found.setdefault(kind, match.group("value"))
    return [
        [f"近三年平均{kind}投资收益率", found[kind]]
        for kind in ("", "综合")
        if kind in found
    ]


def _replace(table: Any, rows: list[list[str]], strategy: str) -> None:
    page = int(getattr(table, "page", 1) or 1)
    table.rows = [list(row) for row in rows]
    table.table_blocks = [
        {"page": page, "rows": [list(row) for row in rows], "unit": table.unit}
    ]
    table.strategy = strategy


@register("guoyuan")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    for table in tables:
        table_id = str(getattr(table, "table_id", "") or "")
        if table_id not in {"LIQUIDITY_MONITORING", "THREE_YEAR_INVESTMENT_RETURN"}:
            continue
        pages = [
            int(page)
            for page in (getattr(table, "source_pages", None) or [])
            if int(page) > 0
        ]
        if not pages:
            pages = list(
                (getattr(table, "table_config", {}) or {}).get("_confirmed_pages") or []
            )
        if not pages:
            continue
        text = _page_text(pdf_bytes, pages)
        if table_id == "LIQUIDITY_MONITORING":
            body = _liquidity_rows(text)
            if body:
                _replace(
                    table,
                    [_LIQUIDITY_HEADER, *body],
                    "公司专属·流动性监测表文本重建",
                )
        else:
            body = _return_rows(text)
            if body:
                _replace(
                    table,
                    [_RETURN_HEADER, *body],
                    "公司专属·近三年投资收益率文本重建",
                )
    return tables
