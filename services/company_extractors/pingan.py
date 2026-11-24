"""平安产险专属提取后处理（2022 版式试点）。

已覆盖的通用规则缺口：

1. ``偿付能力充足率指标``：pdfplumber 把“本季度末数/上季度末数”两列
   合并成 ``50,456,00551,044,518`` 且首列留空，导致认可资产/认可负债/
   量化风险最低资本等被记成 0 或 1e15 量级错误值。这里按逗号分组拆分。
2. ``主要经营指标``：2022 年第 17 页的效益类/规模类/品质类指标不在
   pdfplumber 识别出的表格块里，导致综合成本率、费用率等缺失。这里用
   PDF 文本行重建“指标名 + 本季度数 + 本年度累计数”。
3. 签单保费不在经营表内，而在下一页“公司分类”表中；单独提取并追加。
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from . import register

# 支持：金额粘连（50,456,00551,044,518）、负数粘连（-132,042-129,107）、
# 百分比粘连（177.6%191.2%）。
MERGED_NUMBER_RE = re.compile(
    r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?%?|-?\d+(?:\.\d+)?%?"
)
NUMBER_LINE_RE = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?$")
SIGNED_PREMIUM_RE = re.compile(
    r"签单保费[（(]万元[）)][^\d\-]*(-?[\d,]+(?:\.\d+)?)"
)

_OPERATING_HEADER = ("指标名称", "本季度数", "本年度累计数")
_GROUP_TITLE_RE = re.compile(r"^\d+[.．]\s*(主要|效益|规模|品质)类?指标")
# 平安报告写“基本情境下，未来3个月LCR1”，标准库写“LCR1未来3个月”，
# 顺序相反导致子串匹配失败；这里按正则重新拼成标准标签。
_LCR_LABEL_RE = re.compile(r"未来\s*(3|12)\s*个月\s*LCR([123])")
_LOSS_ABSORPTION_RE = re.compile(r"特定类(?:别)?保险合同损失吸收效应")
_LOSS_ABSORPTION_LABEL = "特定类别保险合同损失吸收效应"


def _fix_liquidity_rows(rows: list[list[str]]) -> list[list[str]]:
    """把 “未来3个月LCR1” 统一成 “LCR1未来3个月”。"""
    fixed: list[list[str]] = []
    for row in rows:
        cells = [str(cell or "").strip() for cell in row]
        if len(cells) >= 2:
            match = _LCR_LABEL_RE.search(cells[1])
            if match:
                cells[1] = f"LCR{match.group(2)}未来{match.group(1)}个月"
        fixed.append(cells)
    return fixed


def _normalize_operating_rows(rows: list[list[str]]) -> list[list[str]]:
    """统一经营表表头和行结构。

    2022/2023 表头是标准三列；2024/2025 的首行是“1.主要指标”分组标题，
    pdfplumber 把真正的表头吞掉了，这里统一补齐，并清掉分组标题、占位行。
    """
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
        if not label or label in {"--", "-", "—", "–"}:
            continue
        if _GROUP_TITLE_RE.match(label):
            continue
        output.append([str(cell or "").strip() for cell in row])
    return output

# 2023 版式把首列表头与期间粘在一起（“类型本季度末”），需要拆回两列。
_HEADER_PERIOD_TERMS = ("本季度末数", "本季度末", "本季度数", "本季度", "上季度末", "上季度数")


def _fix_solvency_header(rows: list[list[str]]) -> list[list[str]]:
    if not rows:
        return rows
    header = [str(cell or "").strip() for cell in rows[0]]
    if len(header) >= 2 and header[0]:
        for term in _HEADER_PERIOD_TERMS:
            if term in header[0] and not header[0].startswith(term):
                prefix = header[0].replace(term, "", 1).strip()
                if prefix:
                    rows[0] = [prefix, term, *header[1:]]
                    break
    return rows


def _clean_cell(value: Any) -> str:
    return str(value or "").replace(" ", "").replace("\u3000", "").strip()


def _split_merged_numbers(text: Any) -> list[str] | str | None:
    compact = _clean_cell(text)
    if not compact:
        return None
    parts = MERGED_NUMBER_RE.findall(compact)
    if len(parts) >= 2 and "".join(parts) == compact:
        return parts
    return None


def _is_number_line(line: str) -> bool:
    return bool(NUMBER_LINE_RE.match(_clean_cell(line)))


_PLACEHOLDER_VALUES = {"--", "-", "—", "–", "－"}


def _is_value_line(line: str) -> bool:
    """数值行或占位符（“--” 表示本季度不适用，累计列仍有值）。"""
    return _is_number_line(line) or _clean_cell(line) in _PLACEHOLDER_VALUES


def _looks_like_label(value: Any) -> bool:
    """中文标签：非空、无数字、不是占位符。"""
    text = str(value or "").strip()
    if not text:
        return False
    if re.search(r"\d", text):
        return False
    return not text.startswith(("-", "—", "–"))


def _fix_solvency_main(rows: list[list[str]]) -> list[list[str]]:
    """修复平安偿付能力主表的两类版式问题。

    1. 行标签被换行拆成两列（``核心一 | 级资本``），先把前两列拼回标签；
    2. “本季度末/上季度末”两列金额粘连（``5,829,6865,700,074``），
       按数字模式重新拆开。
    """
    fixed: list[list[str]] = []
    for row_index, row in enumerate(rows):
        cells = [str(cell or "").strip() for cell in row]
        if row_index == 0:
            fixed.append(cells)
            continue
        if (
            len(cells) >= 3
            and _looks_like_label(cells[0])
            and _looks_like_label(cells[1])
            and not cells[0].startswith(("指标名称", "项目"))
        ):
            cells = [f"{cells[0]}{cells[1]}", *cells[2:]]
        for index in range(1, len(cells)):
            parts = _split_merged_numbers(cells[index])
            if isinstance(parts, list) and len(parts) >= 2:
                if index > 1 and not cells[index - 1]:
                    # 前一列是空占位（认可资产/认可负债行常见），
                    # 用拆出的第一个值填掉它，避免多出一列导致整体错位。
                    cells = [
                        *cells[: index - 1],
                        *parts,
                        *cells[index + 1:],
                    ]
                else:
                    cells = [*cells[:index], *parts, *cells[index + 1:]]
                break
        fixed.append(cells)
    return fixed


def _page_text(doc, page_number: int) -> str:
    if page_number < 1 or page_number > len(doc):
        return ""
    return doc.load_page(page_number - 1).get_text() or ""


def _loss_absorption_row(cells: Sequence[Any]) -> list[str] | None:
    values = [str(cell or "").strip() for cell in cells]
    for index, cell in enumerate(values[:2]):
        if not _LOSS_ABSORPTION_RE.search(_clean_cell(cell)):
            continue
        measures = values[index + 1:index + 3]
        while len(measures) < 2:
            measures.append("")
        return [_LOSS_ABSORPTION_LABEL, *measures]
    return None


def _ensure_loss_absorption_rows(
    table: Any,
    doc,
) -> None:
    rows = [list(row) for row in (getattr(table, "rows", None) or [])]
    found_index = None
    canonical = None
    for index, row in enumerate(rows):
        canonical = _loss_absorption_row(row)
        if canonical:
            found_index = index
            break

    pages = [
        int(page)
        for page in (
            getattr(table, "source_pages", None)
            or [getattr(table, "page", 1)]
        )
        if str(page).isdigit()
    ]
    has_source_label = any(
        _LOSS_ABSORPTION_RE.search(_clean_cell(_page_text(doc, page)))
        for page in pages
    )
    if canonical is None:
        if not has_source_label:
            return
        canonical = [_LOSS_ABSORPTION_LABEL, "-", "-"]
    if found_index is None:
        insert_at = len(rows)
        for index, row in enumerate(rows):
            if "损失吸收效应调整上限" in _clean_cell("".join(map(str, row))):
                insert_at = index + 1
                break
        rows.insert(insert_at, canonical)
    else:
        rows[found_index] = canonical
    table.rows = rows

    blocks = list(getattr(table, "table_blocks", None) or [])
    block_found = False
    for block in blocks:
        block_rows = [list(row) for row in (block.get("rows") or [])]
        for index, row in enumerate(block_rows):
            replacement = _loss_absorption_row(row)
            if replacement:
                block_rows[index] = replacement
                block_found = True
                break
        if block_found:
            block["rows"] = block_rows
            break
    if not block_found:
        page = pages[0] if pages else int(getattr(table, "page", 1) or 1)
        if not blocks:
            blocks = [{"page": page, "rows": []}]
        blocks[-1].setdefault("rows", []).append(canonical)
    table.table_blocks = blocks


def _parse_operating_rows(text: str) -> list[list[str]]:
    """从“指标名称/本季度数/本年度累计数”表重建数据行。"""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    try:
        start = next(
            index for index, line in enumerate(lines) if line.startswith("指标名称")
        )
    except StopIteration:
        return []
    rows: list[list[str]] = []
    index = start + 1
    while index < len(lines):
        line = lines[index]
        if line.startswith(("五、", "（四）", "（三）流动性")):
            break
        if line in {"本季度数", "本年度累计数"}:
            index += 1
            continue
        if _is_number_line(line):
            index += 1
            continue
        # 只接受像指标名的行：短、不以括号/冒号开头。
        if len(line) > 48 or line.startswith(("（", "(", "单位：", "注：")):
            index += 1
            continue
        values: list[str] = []
        cursor = index + 1
        while cursor < len(lines) and _is_value_line(lines[cursor]) and len(values) < 2:
            values.append(_clean_cell(lines[cursor]))
            cursor += 1
        if values:
            rows.append([line, *values])
            index = cursor
        else:
            index += 1
    return rows

def _complete_operating(table: Any, doc) -> list[list[str]]:
    # 通用补全（含 -- 占位符处理）已抽到 _operating_table，平安只需复用。
    from ._operating_table import complete_operating_rows

    return complete_operating_rows(table, doc)


def _sync_blocks(table: Any, rows: list[list[str]], page: int) -> None:
    """rows 与 table_blocks 必须同步：STEP3 展开表块时优先读 blocks。"""
    table.rows = rows
    table.table_blocks = [
        {"page": int(page or 1), "rows": [list(row) for row in rows]}
    ]


@register("pingan")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """平安专属后处理入口。异常由调用方捕获，不在这里吞掉。"""
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for table in tables:
            table_id = str(getattr(table, "table_id", "") or "")
            if table_id == "SOLVENCY_MAIN":
                fixed_rows = _fix_solvency_main(
                    _fix_solvency_header(list(table.rows or []))
                )
                # 页内表块同样需要拆分，否则 STEP3 仍会读到旧值。
                for block in list(getattr(table, "table_blocks", None) or []):
                    if isinstance(block, dict) and block.get("rows"):
                        block["rows"] = _fix_solvency_main(
                            _fix_solvency_header(
                                [list(row) for row in block["rows"]]
                            )
                        )
                table.rows = fixed_rows
            elif table_id == "NON_LIFE_OPERATING_METRICS":
                rows = _complete_operating(table, doc)
                pages = list(
                    getattr(table, "source_pages", [])
                    or [getattr(table, "page", 1)]
                )
                _sync_blocks(table, rows, int(pages[0] if pages else 1))
            elif table_id == "LIQUIDITY_MONITORING":
                table.rows = _fix_liquidity_rows(list(table.rows or []))
                for block in list(getattr(table, "table_blocks", None) or []):
                    if isinstance(block, dict) and block.get("rows"):
                        block["rows"] = _fix_liquidity_rows(
                            [list(row) for row in block["rows"]]
                        )
            elif table_id == "MINIMUM_CAPITAL":
                _ensure_loss_absorption_rows(table, doc)
    finally:
        doc.close()
    return tables


_DOCUMENT_LAYOUT = {
    "SOLVENCY_MAIN": {
        "title": "偿付能力充足率指标",
        "required": [
            "认可资产",
            "认可负债",
            "综合偿付能力充足率"
        ],
        "unit": "元"
    },
    "NON_LIFE_OPERATING_METRICS": {
        "title": "[（(]三[）)]主要经营指标",
        "required": [
            "保险业务收入",
            "净利润"
        ],
        "continuation": 2,
        "tail_terms": [
            "签单保费",
            "综合成本率",
            "车险车均保费"
        ],
        "stop": "(?:实际资本表|偿付能力状况表)",
        "unit": "元"
    },
    "ACTUAL_CAPITAL": {
        "title": "实际资本表",
        "required": [
            "核心一级资本"
        ],
        "continuation": 3,
        "tail_terms": [
            "附属一级资本",
            "附属二级资本",
            "实际资本合计",
            "核心一级资本"
        ],
        "stop": "(?:认可资产表|认可资产明细表)",
        "unit": "元"
    },
    "MINIMUM_CAPITAL": {
        "title": "最低资本表",
        "required": [
            "量化风险最低资本"
        ],
        "continuation": 2,
        "tail_terms": [
            "附加资本",
            "风险最低资本",
            "最低资本"
        ],
        "stop": "(?:保费风险最低资本表|巨灾风险最低资本表)",
        "unit": "元"
    },
    "LIQUIDITY_MONITORING": {
        "title": "流动性风险监管、监测指标",
        "required": [
            "经营活动净现金流"
        ],
        "continuation": 1,
        "tail_terms": [
            "融资杠杆",
            "持有关联方资产",
            "经营活动现金流",
            "AA级"
        ],
        "stop": "(?:实际资本表|偿付能力状况表)",
        "unit": "元"
    },
    "THREE_YEAR_INVESTMENT_RETURN": {
        "title": "近三年.{0,35}平均(?:综合)?投资收益率",
        "required": [],
        "unit": "%"
    }
}


def locate_company_tables(pdf_bytes, table_configs=()):
    from ._document import locate_document
    return locate_document(pdf_bytes, _DOCUMENT_LAYOUT)

def extract_company_tables(pdf_bytes, table_configs):
    from ._document import extract_document
    return extract_document(pdf_bytes, table_configs, _DOCUMENT_LAYOUT, postprocess, "平安产险")
