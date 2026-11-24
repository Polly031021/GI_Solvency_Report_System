"""富德产险专属提取后处理（2022–2025 四季度报告版式）。

YAML（``config/extraction/companies/富德产险.yaml``）已经解决了「找对页、找对锚点」，
剩下的是行级缺口。四份报告同构，通用规则 + Profile 版式变体裁不出来的部分：

1. ``NON_LIFE_OPERATING_METRICS``：Profile 变体「新会计准则经营指标」的终止项是
   ``其他渠道签单保费``，而本公司 2022–2023 把「保险服务收入」写成「保险业务收入」，
   于是表在「其他渠道签单保费」处被截断，车险签单保费 / 非车险前五大险种 /
   车均保费三类指标整体丢失（2022 只剩 21 行）。这里按 PDF 文本行重建到
   ``车险车均保费`` 为止。
2. ``MINIMUM_CAPITAL``：变体「最低资本共性版」的起止项都是「最低资本」，
   按包含匹配先命中首行「量化风险最低资本」，整表被裁成 2 行（只有表头 + 首行）。
   指示库候选本身是完整的 34 行，这里按文本行重建到末行「最低资本」。
3. ``ACTUAL_CAPITAL``：2025 年该表跨第 17/18 页，第 18 页首行「实际资本合计」被
   当作表外内容丢弃，随后整页认可资产明细被并入（36 行 3 块）。这里按文本行
   重建到「实际资本合计」，其余三年顺带修掉「递延所 得税资产」的折行空格。
4. ``THREE_YEAR_INVESTMENT_RETURN``：2025 年该表是「先两行标签、再两行数值」的
   两行两列版式，pdfplumber 把两张标签都当成了数据行，两个收益率一个都没取到；
   按出现顺序把「近三年平均（综合）投资收益率」与随后的百分比配对重建。
5. ``LIQUIDITY_MONITORING``：2022–2023 是「基本情景 + 压力情景 + 监测指标」三段，
   2024–2025 是带竖排「类别」列的 24 行大表，两种版式 pdfplumber 都会把类别碎片
   （流动/性风/险监/管指/标）当数据行。按文本行重建，LCR 标签还原成指示库名称
   ``LCR{1,2,3}未来{3,12}个月``；2025 年监管指标与主表同在第 10 页，定位器只锚到
   第 11 页，这里把前页补进 ``source_pages``。
6. ``SOLVENCY_MAIN``：2024 年「附加资本」三列为空，pdfplumber 把下一行标签并进了
   「控制风险最低资本」，得到「控制风险最低资本附加资本」，控制风险与附加资本
   两个指标都取不到；拆回两行并把附加资本补成占位符。

约定：只改 ``rows`` 与 ``table_blocks``，幂等；异常由 ``apply_company_extractor``
捕获，不允许阻断通用流程。
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from . import register

# 金额/百分比/占位符。允许「(184.41)」这种会计负数写法与「1,102,531,882.89」千分位。
_NUMBER_RE = re.compile(
    r"[（(]-?\d[\d,]*(?:\.\d+)?[）)]%?"
    r"|-?\d{1,3}(?:,\d{3})+(?:\.\d+)?%?"
    r"|-?\d+(?:\.\d+)?%?"
)
# 行次列：「1」「1.2」「1.2.1」「1*」
_ROW_INDEX_RE = re.compile(r"^\d+(?:\.\d+)*\*?$")
_PAGE_NO_RE = re.compile(r"^\d{1,3}$")
# 「1、基本情景」「2、压力情景」这类分节行
_SECTION_NO_RE = re.compile(r"^\d+、")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LCR_TAG_RE = re.compile(r"(LCR\s*[123])", re.IGNORECASE)
# 「一、经营活动净现金流」「十、持有关联方资产占比」这类监测指标标题行
_MONITOR_HEADING_RE = re.compile(r"^[一二三四五六七八九十]+、\s*")

_PLACEHOLDERS = {"-", "--", "—", "–", "－", "不适用", "未披露"}

# 竖排「类别」列被 fitz 拆成 1–2 字的碎片，拼起来等于下列名称时丢弃。
_LIQUIDITY_CATEGORIES = ("流动性风险监管指标", "流动性风险监测指标", "其他流动性指标")

_OPERATING_HEADER = ["指标名称", "本季度数", "本年度累计数"]
_ACTUAL_HEADER = ["项目", "期末数", "期初数"]
_MINIMUM_HEADER = ["指标名称", "期末数", "期初数"]
_THREE_YEAR_HEADER = ["项目", "近三年平均"]
_LIQUIDITY_HEADER = ["指标名称", "本季度数", "上季度数"]

_SKIP_TERMS = (
    "指标名称",
    "本季度数",
    "本季度末数",
    "上季度数",
    "上季度末数",
    "本年度累计数",
    "下季度末预测数",
    "下季度预测数",
    "期末数",
    "期初数",
    "项目",
    "类别",
    "序号",
    "数值",
    "监测指标",
    "单位：",
    "单位:",
    "金额单位",
    "公司名称",
)


def _clean(value: Any) -> str:
    return str(value or "").replace("\u3000", " ").strip()


def _is_number_token(token: str) -> bool:
    token = _clean(token)
    if not token:
        return False
    if token in _PLACEHOLDERS:
        return True
    return bool(_NUMBER_RE.fullmatch(token))


def _is_cjk(char: str) -> bool:
    return bool(_CJK_RE.match(char or ""))


def _clean_label(label: str) -> str:
    """去掉中文之间的折行空格（「递延所 得税资产」→「递延所得税资产」）。"""
    text = _clean(label)
    output: list[str] = []
    for index, char in enumerate(text):
        if char == " " and 0 < index < len(text) - 1:
            if _is_cjk(text[index - 1]) and _is_cjk(text[index + 1]):
                continue
        output.append(char)
    return "".join(output).strip()


def _unbalanced(label: str) -> bool:
    return label.count("（") + label.count("(") > label.count("）") + label.count(")")


def _explode_numbers(lines: Sequence[str]) -> list[str]:
    """把「242,609,621.53   267,090,772.12」这种两列挤在一行的数值拆成两行。

    fitz 在列宽足够时会把同一行的两个数值放在一行里输出（2024 最低资本表的
    「信用风险最低资本合计」「量化风险分散效应」「控制风险最低资本」
    「最低资本」四行），不拆开就会把整串当成标签、数值全部丢失。
    """
    output: list[str] = []
    for line in lines:
        tokens = line.split()
        if len(tokens) > 1 and all(_is_number_token(token) for token in tokens):
            output.extend(tokens)
        else:
            output.append(line)
    return output


def _doc_texts(pdf_bytes: bytes) -> list[str]:
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return [doc.load_page(index).get_text() or "" for index in range(len(doc))]
    finally:
        doc.close()


def _page_lines(texts: Sequence[str], page: int) -> list[str]:
    try:
        text = texts[int(page) - 1]
    except (IndexError, TypeError, ValueError):
        return []
    return [_clean(line) for line in text.splitlines() if _clean(line)]


def _pages_lines(texts: Sequence[str], pages: Sequence[int]) -> list[tuple[int, list[str]]]:
    return [(int(page), _page_lines(texts, page)) for page in pages]


def _sync(table: Any, rows: list[list[str]], page: int) -> None:
    """rows 与 table_blocks 必须同步，否则 STEP3 展开表块仍读旧值。"""
    table.rows = rows
    table.table_blocks = [
        {"page": int(page or 1), "rows": [list(row) for row in rows]}
    ]


def _drop_markers(rows: Sequence[Sequence[str]]) -> list[list[str]]:
    output: list[list[str]] = []
    for row in rows:
        cells = [_clean(cell) for cell in row]
        if any("表格分块" in cell for cell in cells):
            continue
        output.append(cells)
    return output


def _pad(row: list[str], width: int) -> list[str]:
    return row + [""] * max(0, width - len(row))


def _iter_rows(
    lines: Sequence[str],
    *,
    max_values: int = 2,
    stop_terms: Sequence[str] = (),
    skip_labels: Sequence[str] = (),
) -> list[list[str]]:
    """按 fitz「单元格逐行」版式重建行：标签行 + 最多 ``max_values`` 个数值行。

    只有标签括号未闭合（说明是折行）时才把下一行并回标签，否则两行相邻标签
    会被误合并——最低资本表里有大量「标签有值、下一行标签无值」的空行。
    """
    lines = _explode_numbers(list(lines))
    rows: list[list[str]] = []
    index = 0
    total = len(lines)
    while index < total:
        line = lines[index]
        if stop_terms and any(term in line for term in stop_terms):
            break
        if (
            _PAGE_NO_RE.match(line)
            or _SECTION_NO_RE.match(line)
            or _is_number_token(line)
            or any(term in line for term in _SKIP_TERMS)
            or any(term in line for term in skip_labels)
        ):
            index += 1
            continue
        label = _clean_label(line)
        values: list[str] = []
        cursor = index + 1
        while cursor < total:
            following = lines[cursor]
            if stop_terms and any(term in following for term in stop_terms):
                break
            if _is_number_token(following):
                values.append(following)
                cursor += 1
                if len(values) >= max_values:
                    break
                continue
            if not values and _unbalanced(label):
                label = _clean_label(f"{label}{following}")
                cursor += 1
                continue
            break
        if label:
            rows.append([label, *values])
        index = max(cursor, index + 1)
    return rows


def _rows_until(
    rows: Sequence[Sequence[str]],
    *,
    start_label: str = "",
    stop_label: str = "",
) -> list[list[str]]:
    """从 ``start_label`` 开始（含），到 ``stop_label`` 结束（含）截取。

    比较用前缀，兼容 2022–2023 带「（元）」后缀、2024–2025 去掉后缀两种写法。
    """
    output: list[list[str]] = []
    started = not start_label
    for row in rows:
        label = row[0] if row else ""
        if not started:
            if start_label and label.startswith(start_label):
                started = True
            else:
                continue
        output.append(list(row))
        if stop_label and label.startswith(stop_label):
            break
    return output


def _parse_operating(lines: Sequence[str]) -> list[list[str]]:
    """重建整张经营指标表（指标名称 + 本季度数 + 本年度累计数）。"""
    heading = 0
    for position, line in enumerate(lines):
        if "主要经营指标" in line:
            heading = position
            break
    start = heading
    for position in range(heading, len(lines)):
        if lines[position].startswith("指标名称"):
            start = position + 1
            break
    rows = _iter_rows(
        lines[start:],
        stop_terms=("偿付能力状况", "S01", "风险管理能力", "非寿险业务保险风险"),
    )
    # 截到「车险车均保费」为止：其后是 S01 偿付能力状况表。
    output: list[list[str]] = []
    for row in rows:
        output.append(row)
        if row and row[0].startswith("车险车均保费"):
            break
    return [_pad(row, 3) for row in output if row and row[0]]


def _parse_minimum(lines: Sequence[str]) -> list[list[str]]:
    """重建 S05 最低资本表（指标名称 + 期末数 + 期初数）。

    版式是「行次 / 项目 / 期末数 / 期初数」，行次单独成行（``1``、``1.2``、``1*``），
    先按「行次后紧跟非数值行」的规律把行次剔掉，再按标签 + 两个数值解析。
    """
    filtered: list[str] = []
    total = len(lines)
    for index, line in enumerate(lines):
        if _ROW_INDEX_RE.match(line):
            following = lines[index + 1] if index + 1 < total else ""
            if following and not _is_number_token(following):
                continue
        filtered.append(line)
    rows = _iter_rows(filtered, max_values=2)
    output = _rows_until(rows, start_label="量化风险最低资本", stop_label="最低资本")
    if not output or not output[-1][0].startswith("最低资本"):
        # 没有行次列的老版式（或首行标签不同）直接按标签找末行。
        output = _rows_until(rows, stop_label="最低资本")
    return [_pad(row, 3) for row in output if row and row[0]]


def _disambiguate_deferred_tax(rows: list[list[str]]) -> list[list[str]]:
    """给「附属一级资本」下那行递延所得税资产补上节标题。

    实际资本表里「递延所得税资产（由经营性亏损引起的递延所得税资产除外）」
    出现两次：一次在核心一级资本的调整项（负数），一次在附属一级资本明细
    （正数，即指标库里「附属一级资本-递延所得税资产…」那一条）。原文两行
    字面完全相同，匹配器会给两个候选同样满分而判为「歧义待确认」。这里按
    表格自身的结构补上「附属一级资本-」前缀，让两行各自唯一命中。
    """
    prefixed = False
    under_tier1 = False
    for row in rows:
        if not row or not row[0]:
            continue
        label = row[0]
        if label.startswith("附属一级资本"):
            under_tier1 = True
        elif under_tier1 and not prefixed and label.startswith("递延所得税资产"):
            row[0] = "附属一级资本-" + label
            prefixed = True
    return rows


def _parse_actual(lines: Sequence[str]) -> list[list[str]]:
    """重建实际资本指标表（项目 + 期末数 + 期初数），止于「实际资本合计」。"""
    rows = _iter_rows(
        lines,
        stop_terms=("认可资产指标", "认可负债指标"),
    )
    output = _rows_until(rows, start_label="核心一级资本", stop_label="实际资本合计")
    if not output or not output[-1][0].startswith("实际资本合计"):
        output = _rows_until(rows, start_label="核心一级资本", stop_label="实际资本")
    rows_out = [_pad(row, 3) for row in output if row and row[0]]
    return _disambiguate_deferred_tax(rows_out)


def _parse_three_year(lines: Sequence[str]) -> list[list[str]]:
    """把「近三年平均（综合）投资收益率」标签与随后的百分比按顺序配对。"""
    labels: list[str] = []
    values: list[str] = []
    started = False
    for line in lines:
        if not started:
            if "近三年" in line and "投资收益率" in line:
                started = True
            continue
        if "主要经营指标" in line:
            break
        if line.startswith("近三年平均") and "投资收益率" in line:
            labels.append(_clean_label(line))
            continue
        if _is_number_token(line) and "%" in line:
            values.append(line)
    return [[label, values[index]] for index, label in enumerate(labels) if index < len(values)]


def _normalize_liquidity_label(label: str) -> str | None:
    """把 PDF 标签还原成指标库名称；返回 None 表示该行不纳入标准表。"""
    if "自测" in label or "当期数" in label:
        # 「自测压力情景LCR2/LCR3」与监管口径 LCR2/LCR3 同名不同值，
        # 「LCR1（当期数）」同理，纳入会与监管指标撞码，统一不取。
        return None
    if label.startswith("上季度"):
        # 2025 年单独列出上一季度的 RDR，与本行的「上季度数」重复。
        return None
    match = _LCR_TAG_RE.search(label)
    if match:
        number = re.sub(r"\D", "", match.group(1))
        if "三个月" in label or "3个月" in label:
            window = "3"
        elif "一年" in label or "12个月" in label:
            window = "12"
        else:
            # 2022–2023 的「LCR1（基本情景下公司整体流动性覆盖率）」是 3 个月口径。
            window = "3"
        return f"LCR{number}未来{window}个月"
    if "回溯不利偏差率" in label or "RDR" in label.upper():
        return "经营活动净现金流回溯不利偏差率"
    return label


def _parse_liquidity(pages: Sequence[tuple[int, list[str]]]) -> list[list[str]]:
    """重建流动性监管 + 监测指标表。

    两种版式：
    * 2022–2023：`监管指标（基本情景）` 两列 + `监测指标` 三列；监测部分是
      「一、经营活动净现金流」标题行 + 「指标值」行 + 若干分解行，只有「指标值」
      是可用于标准化的一行，分解行（现金流入/流出等）不进标准表。
    * 2024–2025：一张 24 行大表，含竖排「类别」列（流动/性风/险监/管指/标）。
    """
    rows: list[list[str]] = []
    for _, lines in pages:
        start = 0
        for position, line in enumerate(lines):
            if any(name in line for name in _LIQUIDITY_CATEGORIES) or (
                "流动性风险" in line and "指标" in line and len(line) < 24
            ):
                start = position + 1
                break
        lines = _explode_numbers(lines)
        start = min(start, len(lines))
        buffer = ""
        heading = ""
        index = start
        total = len(lines)
        while index < total:
            line = lines[index]
            if any(
                term in line
                for term in ("近三年", "主要经营指标", "偿付能力状况", "风险管理能力", "填表说明")
            ):
                break
            if line.startswith("2、压力情景"):
                # 2022–2023 的「2、压力情景」是 4 列压力表（含 LCR2/LCR3 监管口径），
                # 与下方监测指标无关，整段跳过。
                break
            if len(line) <= 3 and all(_is_cjk(char) for char in line):
                candidate = buffer + line
                if any(name.startswith(candidate) for name in _LIQUIDITY_CATEGORIES):
                    buffer = candidate
                    index += 1
                    continue
                buffer = ""
            else:
                buffer = ""
            if (
                _PAGE_NO_RE.match(line)
                or _SECTION_NO_RE.match(line)
                or _is_number_token(line)
                or any(term in line for term in _SKIP_TERMS)
            ):
                index += 1
                continue
            label = _clean_label(line)
            values: list[str] = []
            cursor = index + 1
            while cursor < total:
                if _is_number_token(lines[cursor]):
                    values.append(lines[cursor])
                    cursor += 1
                    if len(values) >= 2:
                        break
                    continue
                break
            if _MONITOR_HEADING_RE.match(label):
                # 「一、经营活动净现金流」：只记住名字，数值在后面的「指标值」行。
                heading = _MONITOR_HEADING_RE.sub("", label)
                cursor = max(cursor, index + 1)
                # 标题会被折行（「AA 级（含）以下境内固定收益类」+「资产占比」）。
                while cursor < total:
                    following = _clean_label(lines[cursor])
                    if (
                        not following
                        or _is_number_token(following)
                        or following.startswith("指标值")
                        or _MONITOR_HEADING_RE.match(following)
                    ):
                        break
                    heading += following
                    cursor += 1
                index = cursor
                continue
            if heading:
                if label.startswith("指标值"):
                    rows.append([heading, *values])
                # 监测指标的分解行（现金流入/流出、赔付支出等）不进标准表，
                # 但要一直跳到下一个标题行，否则会当成独立指标落库。
                index = max(cursor, index + 1)
                continue
            normalized = _normalize_liquidity_label(label)
            if normalized:
                rows.append([normalized, *values])
            index = max(cursor, index + 1)
    # 2022–2023 的监测指标只有一列数值，补空到三列。
    return [_pad(row, 3) for row in rows]


def _fix_main(rows: Sequence[Sequence[str]]) -> list[list[str]]:
    """偿付能力主表：把 2024 年粘连的「控制风险最低资本附加资本」拆回两行。

    PDF 里「附加资本」三列全空，pdfplumber 把两条标签并进了一行，
    控制风险最低资本与附加资本两个指标因此都取不到值。
    """
    fixed: list[list[str]] = []
    for row in _drop_markers(rows):
        cells = [_clean(cell) for cell in row]
        label = cells[0] if cells else ""
        if label == "控制风险最低资本附加资本":
            rest = cells[1:]
            fixed.append(["控制风险最低资本", *rest])
            fixed.append(["附加资本", *["-"] * len(rest)])
        else:
            fixed.append(cells)
    return fixed


@register("fude")
def postprocess(tables: Sequence[Any], *, pdf_bytes: bytes) -> Sequence[Any]:
    """富德产险专属后处理入口；异常由调用方捕获。"""
    texts = _doc_texts(pdf_bytes)
    for table in tables:
        table_id = str(getattr(table, "table_id", "") or "")
        pages = [
            int(page)
            for page in (getattr(table, "source_pages", None) or [])
            if str(page).strip().isdigit()
        ] or [int(getattr(table, "page", 1) or 1)]
        first_page = pages[0]

        if table_id == "NON_LIFE_OPERATING_METRICS":
            parsed = _parse_operating(
                [line for _, lines in _pages_lines(texts, pages) for line in lines]
            )
            if len(parsed) >= 20:
                _sync(table, [_OPERATING_HEADER, *parsed], first_page)
                table.unit = "元"
                table.block_unit = ""

        elif table_id == "MINIMUM_CAPITAL":
            lines = [line for _, lines in _pages_lines(texts, pages) for line in lines]
            parsed = _parse_minimum(lines)
            if len(parsed) >= 20:
                _sync(table, [_MINIMUM_HEADER, *parsed], first_page)
                table.unit = "元"
                table.block_unit = ""

        elif table_id == "ACTUAL_CAPITAL":
            lines = [line for _, lines in _pages_lines(texts, pages) for line in lines]
            parsed = _parse_actual(lines)
            if len(parsed) >= 8:
                _sync(table, [_ACTUAL_HEADER, *parsed], first_page)
                table.unit = "元"
                table.block_unit = ""

        elif table_id == "THREE_YEAR_INVESTMENT_RETURN":
            lines = [line for _, lines in _pages_lines(texts, pages) for line in lines]
            parsed = _parse_three_year(lines)
            if len(parsed) == 2:
                _sync(table, [_THREE_YEAR_HEADER, *parsed], first_page)
                table.unit = "%"
                table.block_unit = ""

        elif table_id == "LIQUIDITY_MONITORING":
            joined = "\n".join(
                texts[page - 1] for page in pages if 0 < page <= len(texts)
            )
            if "流动性风险" not in joined and first_page > 1:
                # 2025 年「监管指标」与主表同在第 10 页，定位器只锚到第 11 页。
                pages = [first_page - 1, *pages]
                first_page = pages[0]
                table.source_pages = list(pages)
            parsed = _parse_liquidity(_pages_lines(texts, pages))
            if len(parsed) >= 10:
                _sync(table, [_LIQUIDITY_HEADER, *parsed], first_page)
                # 2024–2025 原表金额列标「（万元）」，2022–2023 为元。
                labels = " ".join(row[0] for row in parsed)
                table.unit = "万元" if "万元" in labels else "元"
                table.block_unit = ""

        elif table_id == "SOLVENCY_MAIN":
            table.rows = _fix_main(list(table.rows or []))
            for block in list(getattr(table, "table_blocks", None) or []):
                if isinstance(block, dict) and block.get("rows"):
                    block["rows"] = _fix_main(block["rows"])
    return tables

# Explicit company extraction hooks.
def locate_company_tables(pdf_bytes, table_configs=()):
    from ._company_hooks import locate_for_company
    return locate_for_company("fude", pdf_bytes, table_configs)


def extract_company_tables(pdf_bytes, table_configs):
    from ._company_hooks import extract_for_company
    return extract_for_company(
        "fude",
        pdf_bytes,
        table_configs,
        mode="legacy",
    )
