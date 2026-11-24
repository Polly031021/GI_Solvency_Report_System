"""兴亚财险专属后处理。

2022 年实际资本表没有可识别的表格线，文本层按“行次 / 项目 / 本季度数 /
上季度数”逐行输出。这里按公司固定行次顺序重建 S02，续行标签保持原样，
短横占位符继续保留为短横。
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from . import register

_CAPITAL_HEADER = ["行次", "项目", "本季度数", "上季度数"]
_CODE_RE = re.compile(r"^\d+(?:\.\d+)*$")
_VALUE_RE = re.compile(
    r"^(?:[-－—–]+|[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?)$"
)


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value if value is not None else ""))


def _parse_actual_capital(text: str) -> list[list[str]]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines and lines[0].isdigit():
        lines = lines[1:]
    try:
        cursor = next(i for i, line in enumerate(lines) if _compact(line) == "行次") + 1
    except StopIteration:
        return []
    while cursor < len(lines) and _compact(lines[cursor]) in {
        "项目",
        "本季度数",
        "上季度数",
        "期末数",
        "期初数",
    }:
        cursor += 1

    rows: list[list[str]] = []
    code = ""
    label: list[str] = []
    values: list[str] = []

    def flush() -> None:
        nonlocal code, label, values
        text_label = "".join(label).strip()
        if code and text_label:
            rows.append([
                code,
                text_label,
                values[0] if values else "",
                values[1] if len(values) > 1 else "",
            ])
        code, label, values = "", [], []

    for line in lines[cursor:]:
        token = _compact(line)
        if token.startswith(("2.认可资产表", "（十一）", "(十一)", "最低资本表")):
            break
        if not code:
            if _CODE_RE.fullmatch(token):
                code = line
            continue
        if len(values) < 2:
            if _VALUE_RE.fullmatch(token):
                values.append(token)
            else:
                label.append(token)
            continue
        if _CODE_RE.fullmatch(token):
            flush()
            code = line
            continue
        label.append(token)

    flush()
    for index, row in enumerate(rows):
        if _compact(row[1]) == "实际资本合计":
            return rows[: index + 1]
    return rows


@register("xingya")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for table in tables:
            if str(getattr(table, "table_id", "") or "") != "ACTUAL_CAPITAL":
                continue
            pages = [
                int(page)
                for page in (getattr(table, "source_pages", None) or [])
                if str(page).isdigit() and int(page) > 0
            ]
            parsed: list[list[str]] = []
            for page in pages:
                if 1 <= page <= doc.page_count:
                    parsed.extend(_parse_actual_capital(doc[page - 1].get_text()))
            if len(parsed) >= 10:
                rows = [list(_CAPITAL_HEADER), *parsed]
                table.rows = rows
                table.table_blocks = [{
                    "page": pages[0] if pages else int(getattr(table, "page", 1) or 1),
                    "rows": [list(row) for row in rows],
                }]
                table.strategy = "公司专属·实际资本文本行重建"
        return tables
    finally:
        doc.close()
