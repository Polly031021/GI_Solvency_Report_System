"""经营表（主要经营指标）通用补全：按 PDF 文本补齐 pdfplumber 漏掉的行。

多数财险公司的经营表在 pdfplumber 里只识别出前半段（“1.主要指标”），
后半段“2.效益类指标/3.规模类指标/4.品质类指标”依赖文字层。这里提供
共享补全逻辑，供所有公司（含无 extractor 的公司）在提取后调用：

* 只在原 rows 缺少标签时追加，已有行优先；
* 允许 `--`/`-` 作为占位值（本季度不适用、累计列有值）；
* 过滤分组标题、页码、正文段落；
* 签单保费不在经营表时，从下一页“公司分类”表补。

公司专属差异（如平安的主表合并单元格、LCR 标签）仍放在各自 extractor。
"""

from __future__ import annotations

import re
from typing import Any, Sequence

_OPERATING_HEADER = ("指标名称", "本季度数", "本年度累计数")
_GROUP_TITLE_RE = re.compile(r"^(\d+[.．]\s*)?(主要|效益|规模|品质)类?指标")
# 行标签前缀：“（一）”“1.”“3.1 ”“（十一）”等；只用于补全，不影响原行。
_LABEL_PREFIX_RE = re.compile(
    r"^[（(]?[一二三四五六七八九十0-9]+(?:\.[0-9]+)?[）)、.．]?\s*"
)

_NUMBER_LINE_RE = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?$")
_PLACEHOLDER_VALUES = {"--", "-", "—", "–", "－"}
_SIGNED_PREMIUM_RE = re.compile(
    r"签单保费[（(]万元[）)][^\d\-]*(-?[\d,]+(?:\.\d+)?)"
)


def _clean(value: Any) -> str:
    return str(value or "").replace(" ", "").replace("\u3000", "").strip()


def _is_number_line(line: str) -> bool:
    return bool(_NUMBER_LINE_RE.match(_clean(line)))


def _is_value_line(line: str) -> bool:
    return _is_number_line(line) or _clean(line) in _PLACEHOLDER_VALUES


def _page_text(doc, page_number: int) -> str:
    if isinstance(doc, (list, tuple)):
        if page_number < 1 or page_number > len(doc):
            return ""
        return str(doc[page_number - 1] or "")
    if page_number < 1 or page_number > len(doc):
        return ""
    return doc.load_page(page_number - 1).get_text() or ""


def _strip_label(label: str) -> str:
    return _LABEL_PREFIX_RE.sub("", str(label or "")).strip()


def _is_label_fragment(line: str) -> bool:
    """换行拆开的标签片段：短、无数字、不是单位/注释。"""
    text = _clean(line)
    if not text or len(text) > 10:
        return False
    if re.search(r"\d", text):
        return False
    return not text.startswith(("（", "(", "单位", "注", "-", "—"))


def _parse_operating_rows(text: str) -> list[list[str]]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    start = None
    for index, line in enumerate(lines):
        if line.startswith(("指标名称", "项目")):
            start = index + 1
            break
    if start is None:
        for index, line in enumerate(lines):
            if "主要经营指标" in line:
                start = index + 1
                break
    if start is None:
        # 没有标准表头（如铁路自保用“（一）…（十一）效益类指标”），
        # 从第一个“综合成本率/综合费用率/保险业务收入”附近开始兜底。
        for index, line in enumerate(lines):
            if any(
                key in line
                for key in ("综合成本率", "综合费用率", "保险业务收入")
            ):
                start = max(0, index - 2)
                break
    if start is None:
        return []
    rows: list[list[str]] = []
    index = start
    while index < len(lines):
        line = lines[index]
        if line.startswith(("五、", "（三）流动性", "（四）流动性")):
            break
        if line in {"本季度数", "本年度累计数"}:
            index += 1
            continue
        if _is_number_line(line):
            index += 1
            continue
        label = _strip_label(line)
        if (
            not label
            or len(label) > 48
            or label.startswith(("单位：", "注："))
            or _GROUP_TITLE_RE.match(label)
        ):
            index += 1
            continue
        # 换行拆开的标签：“3.非车险前五大险种的签单” + “保费” + 数值。
        next_index = index + 1
        if (
            next_index + 1 < len(lines)
            and _is_label_fragment(lines[next_index])
            and _is_value_line(lines[next_index + 1])
        ):
            label = f"{label}{_strip_label(lines[next_index])}"
            next_index += 1
        values: list[str] = []
        cursor = next_index
        while cursor < len(lines) and _is_value_line(lines[cursor]) and len(values) < 2:
            values.append(_clean(lines[cursor]))
            cursor += 1
        if values:
            rows.append([label, *values])
            index = cursor
        else:
            index += 1
    return rows


def _normalize_rows(rows: list[list[str]]) -> list[list[str]]:
    if not rows:
        return rows
    original_header = [str(cell or "").strip() for cell in rows[0]]
    output: list[list[str]] = []
    if original_header and original_header[0] == "指标名称":
        output.append(original_header)
        data_rows = rows[1:]
    else:
        output.append(list(_OPERATING_HEADER))
        data_rows = rows
        if original_header and (
            _GROUP_TITLE_RE.match(original_header[0])
            or original_header[0].startswith("主要指标")
        ):
            data_rows = rows[1:]
    for row in data_rows:
        label = str(row[0] if row else "").strip()
        if not label or label in _PLACEHOLDER_VALUES:
            continue
        if _GROUP_TITLE_RE.match(label):
            continue
        output.append([str(cell or "").strip() for cell in row])
    return output


def _dedupe_rows(rows: list[list[str]]) -> list[list[str]]:
    """按清洗后的标签去重：原有行优先，补全行只补缺。"""
    seen: set[str] = set()
    output: list[list[str]] = []
    for row in rows:
        if not row:
            continue
        key = _strip_label(str(row[0]))
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(row)
    return output


def complete_operating_rows(table: Any, doc) -> list[list[str]]:
    rows = [list(row) for row in getattr(table, "rows", []) or []]
    pages = list(
        getattr(table, "source_pages", []) or [getattr(table, "page", 1)]
    )
    parsed: list[list[str]] = []
    for page_number in pages:
        parsed.extend(_parse_operating_rows(_page_text(doc, int(page_number))))

    existing = {
        str(row[0]).strip() for row in rows if row and str(row[0]).strip()
    }
    for row in parsed:
        label = str(row[0]).strip()
        if not label or label in existing:
            continue
        rows.append(row)
        existing.add(label)

    if not any("签单保费" in str(row[0]) for row in rows[1:] if row):
        next_page = int(pages[-1] if pages else getattr(table, "page", 1)) + 1
        match = _SIGNED_PREMIUM_RE.search(_page_text(doc, next_page))
        if match:
            rows.append(["签单保费（万元）", "", match.group(1)])
    return _normalize_rows(_dedupe_rows(rows))


def complete_operating_tables(
    tables: Sequence[Any],
    pdf_bytes: bytes,
    *,
    page_texts: Sequence[str] | None = None,
) -> Sequence[Any]:
    """对 NON_LIFE_OPERATING_METRICS 表做通用补全；其他表原样返回。"""
    targets = [
        table
        for table in tables
        if str(getattr(table, "table_id", "") or "") == "NON_LIFE_OPERATING_METRICS"
    ]
    if not targets or not pdf_bytes:
        return tables
    import fitz

    doc = (
        list(page_texts)
        if page_texts is not None
        else fitz.open(stream=pdf_bytes, filetype="pdf")
    )
    try:
        for table in targets:
            rows = complete_operating_rows(table, doc)
            table.rows = rows
            pages = list(
                getattr(table, "source_pages", []) or [getattr(table, "page", 1)]
            )
            table.table_blocks = [
                {
                    "page": int(pages[0] if pages else 1),
                    "rows": [list(row) for row in rows],
                }
            ]
    finally:
        if page_texts is None:
            doc.close()
    return tables
