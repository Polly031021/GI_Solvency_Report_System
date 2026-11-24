# -*- coding: utf-8 -*-
"""众诚保险（众诚汽车保险股份有限公司）专属提取后处理。

版式背景（2022–2025 四年一致，仅页码漂移）：
* 六张目标表所在页没有逐行表格线，只有 0.5–1pt 的短横线段和 0.5pt 交点小方块；
  `page.find_tables()`/线框网格会把一行切成 9 个竖排碎片（“保险业 | | 务收入（元） 638,28”），
  金额也断成两截，主指标表还会多出空列。
* 同一页的文字层（`page.extract_text()`）却非常干净：一条指标一行，标签与金额同行
  （“认可资产（元） 5,285,249,192.84 5,666,381,273.55 5,349,837,575.17”）。
因此这里丢弃线框结果，按文字层的行结构重建 6 张表的表块（rows 与 table_blocks 同步）。

文字层的三种行形态都要吃下：
1. `行次 标签 期末 期初` 一行（2022/2023 的实际资本、大部分最低资本行）；
2. 标签行 / 纯行次行 / 纯数值行 三行分离（2024 最低资本 p28）；
3. 标签+数值一行，行次在下一行，标签续行再下一行（2022 实际资本、最低资本 p29）。

另外两处必要的收尾：
* 最低资本主表后紧跟 p30/p31 的“保费和准备金风险”“巨灾风险”明细表，
  它们不属于 MINIMUM_CAPITAL，遇到明细表标题即截断；
* 页脚孤立页码（“27”“29”“8”）与“2024年第4季度”“单位：元”等标题行要过滤。
"""

from __future__ import annotations

import io
import re

from . import register

# ---------------------------------------------------------------------------
# 行级正则
# ---------------------------------------------------------------------------
_AMOUNT_RE = re.compile(r"[（(]?[-+−]?[\d,，]+(?:\.\d+)?[）)]?%?")
_DASH_RE = re.compile(r"^(?:-{1,2}|—|–)$")
_CODE_RE = re.compile(r"^(\d+(?:\.\d+)*\*?)\s+(.*)$")
_PURE_CODE_RE = re.compile(r"^\d+(?:\.\d+)*\*?$")
_PAGE_NO_RE = re.compile(r"^\d{1,3}$")
_NOISE_RE = re.compile(
    r"指标名称|^行次|^项目$|^项目\s|公告编号|类指标|^注[：:]|单位[：:]|数据，非合并|预测数"
    r"|^基本情景下|^\d{4}\s*年|^第[一二三四五六七八九十]+节|^[一二三四五六七八九十]+、"
)
_SECTION_RE = re.compile(r"^[一二三四五六七八九十]+、")

# ---------------------------------------------------------------------------
# 6 张表的文字层定位规则
# ---------------------------------------------------------------------------
_TABLE_SPECS: dict[str, dict[str, object]] = {
    "SOLVENCY_MAIN": {
        "start": r"主要偿付能力充足率指标",
        "end": r"流动性风险(?:监管|监测)指标|主要经营指标",
        "stop": r"^综合偿付能力充足率",
        "ncols": 3,
        "header": ["指标名称", "本季度末数", "上季度末数", "预测数"],
    },
    "NON_LIFE_OPERATING_METRICS": {
        "start": r"主要经营指标",
        "end": r"近三年[（(]综合[）)]投资收益率",
        "stop": r"^其他渠道",
        "ncols": 2,
        "header": ["指标名称", "本季度数", "本年度累积数"],
    },
    "ACTUAL_CAPITAL": {
        "start": r"实际资本表",
        "end": r"最低资本表",
        "stop": r"^实际资本合计",
        "ncols": 2,
        "header": ["行次", "项目", "期末数", "期初数"],
        "with_code": True,
    },
    "MINIMUM_CAPITAL": {
        "start": r"最低资本表",
        "end": r"^十[、.]",
        "stop": r"^最低资本$",
        "boundary": r"财险和人身险公司非寿险业务保险风险",
        "ncols": 2,
        "header": ["行次", "项目", "期末数", "期初数"],
        "with_code": True,
    },
    "THREE_YEAR_INVESTMENT_RETURN": {
        "start": r"近三年[（(]综合[）)]投资收益率",
        "end": r"^[一二三四五六七八九十]+、",
        "stop": r"^近三年平均综合投资收益率",
        "ncols": 1,
        "header": ["指标名称", "本期数"],
    },
    "LIQUIDITY_MONITORING": {
        "start": r"流动性风险监测指标",
        "end": r"主要经营指标|近三年[（(]综合",
        "stop": r"^持有关联方资产",
        "ncols": 2,
        "header": ["指标名称", "本季度数", "上季度数"],
    },
}


def _is_value(token: str) -> bool:
    if not token:
        return False
    if _DASH_RE.match(token):
        return True
    return bool(re.fullmatch(r"[（(]?[-+−]?[\d,，]+(?:\.\d+)?[）)]?%?", token))


def _split_line(line: str) -> tuple[str, str, list[str]]:
    """把一行拆成 (行次, 标签, 数值列表)。"""
    text = line.strip()
    if _PURE_CODE_RE.match(text):
        return text, "", []
    code = ""
    match = _CODE_RE.match(text)
    if match and not re.match(r"^20\d{2}", text):
        code, text = match.group(1), match.group(2).strip()
    tokens = text.split()
    values: list[str] = []
    while tokens and _is_value(tokens[-1]):
        values.insert(0, tokens.pop())
    label = "".join(tokens).strip()
    return code, label, values


def _collect_lines(pages: list[str], start_page: int, spec: dict[str, object]) -> list[str]:
    start_re = re.compile(str(spec["start"]))
    end_re = re.compile(str(spec["end"])) if spec.get("end") else None
    boundary_re = re.compile(str(spec["boundary"])) if spec.get("boundary") else None
    lines: list[str] = []
    started = False
    for page_no in range(start_page, min(len(pages), start_page + 3) + 1):
        page_lines = [raw.strip() for raw in pages[page_no - 1].splitlines()]
        last_index = max(
            (index for index, text in enumerate(page_lines) if text), default=-1
        )
        for line_index, line in enumerate(page_lines):
            if not line:
                continue
            # 纯数字行既可能是行次（最低资本表的“1”“2”“3”），也可能是页脚页码；
            # 只有页面最后一条非空行才按页码丢弃。
            if _PAGE_NO_RE.match(line) and line_index == last_index:
                continue
            if not started:
                if start_re.search(line):
                    started = True
                continue
            if boundary_re is not None and boundary_re.search(line):
                return lines
            if end_re is not None and end_re.search(line):
                return lines
            lines.append(line)
    return lines


def _parse_rows(lines: list[str], spec: dict[str, object]) -> list[list[str]]:
    ncols = int(spec["ncols"])  # type: ignore[arg-type]
    stop_re = re.compile(str(spec["stop"])) if spec.get("stop") else None
    with_code = bool(spec.get("with_code"))
    label_index = 1 if with_code else 0

    items: list[tuple[str, str, list[str]]] = []
    for raw in lines:
        if _NOISE_RE.search(raw):
            continue
        code, label, values = _split_line(raw)
        if not code and not label and not values:
            continue
        items.append((code, label, values))

    rows: list[list[str]] = []
    pending: dict[str, object] | None = None
    carry = ""
    code_after_values = False

    def next_shape(index: int) -> str:
        """下一行的形状：code_only / code_values / other。"""
        if index + 1 >= len(items):
            return "other"
        nxt_code, nxt_label, nxt_values = items[index + 1]
        if not nxt_code or nxt_label:
            return "other"
        return "code_values" if nxt_values else "code_only"

    def emit(code: str, label: str, values: list[str]) -> bool:
        """写入一行；返回 True 表示已到表尾。"""
        padded = [str(v) for v in values[:ncols]] + [""] * max(0, ncols - len(values))
        rows.append([code, label, *padded] if with_code else [label, *padded])
        return bool(stop_re is not None and stop_re.search(label))

    for index, (code, label, values) in enumerate(items):
        if label and values:
            # 标签与数值同行：可能是 2022/2023 的“行次 标签 值”一体行，
            # 也可能是 2024 三行式的末行（标签续行 + 值）。
            actual = code or carry
            carry = ""
            code_after_values = False
            if pending is not None and pending.get("from_label"):
                merged_label = str(pending["label"]) + label
                merged_code = code or str(pending["code"] or "")
                pending = None
                if emit(merged_code, merged_label, values):
                    return rows
                continue
            if pending is not None:
                finished_code = str(pending["code"] or "")
                finished_label = str(pending["label"])
                pending = None
                if emit(finished_code, finished_label, []):
                    return rows
            if emit(actual, label, values):
                return rows
            continue

        if label and not values:
            if pending is not None:
                if (
                    not code
                    and pending["code"]
                    and not pending["from_label"]
                    and next_shape(index) == "code_only"
                ):
                    # 2024 表尾形态：“3.4 其他附加资本” /“最低资本”/“4”/数值：
                    # 本行是下一行的标签行，先把上一条出行（值留空）。
                    finished_code = str(pending["code"])
                    finished_label = str(pending["label"])
                    pending = {"code": "", "label": label, "from_label": True}
                    if emit(finished_code, finished_label, []):
                        return rows
                    continue
                if code and pending["code"] and not pending["from_label"]:
                    # 上一条是“行次+标签、无数值”的行，本行是另一条同类行
                    finished_code = str(pending["code"])
                    finished_label = str(pending["label"])
                    pending = {"code": code, "label": label, "from_label": False}
                    if emit(finished_code, finished_label, []):
                        return rows
                    continue
                pending["label"] = str(pending["label"]) + label
                if code and not pending["code"]:
                    pending["code"] = code
                continue
            if rows and (
                not (rows[-1][0] if with_code else "") or code_after_values
            ):
                rows[-1][label_index] += label
                if code and with_code and not rows[-1][0]:
                    rows[-1][0] = code
                code_after_values = False
                continue
            if code:
                pending = {"code": code, "label": label, "from_label": False}
            elif next_shape(index) in {"code_only", "code_values"}:
                # 2024 三行式：本行是标签行，下一行才是行次（可能同带数值）。
                # 若下一行同时带完整标签，则说明本行是上一行的续行，走下面的
                # “拼到末行”分支。
                pending = {"code": "", "label": label, "from_label": True}
            elif rows:
                rows[-1][label_index] += label
            else:
                pending = {"code": "", "label": label, "from_label": True}
            code_after_values = False
            continue

        if values and not label:  # 只有数值
            if pending is not None:
                row_code = str(pending["code"] or "")
                row_label = str(pending["label"])
                pending = None
                code_after_values = False
                if emit(row_code, row_label, values):
                    return rows
                continue
            if rows:
                padded = [str(v) for v in values[:ncols]] + [""] * max(
                    0, ncols - len(values)
                )
                if with_code:
                    rows[-1][2:] = padded
                else:
                    rows[-1][1:] = padded
            code_after_values = False
            continue

        # 只有行次
        if pending is not None:
            if not pending["code"]:
                pending["code"] = code
        elif with_code and rows and not rows[-1][0]:
            rows[-1][0] = code
            code_after_values = True
        else:
            carry = code

    if pending is not None:
        emit(str(pending["code"] or ""), str(pending["label"]), [])
    return rows


def _sniff_unit(pages: list[str], page_nos: list[int], rows: list[list[str]]) -> str:
    for page_no in page_nos:
        if not (1 <= page_no <= len(pages)):
            continue
        match = re.search(r"单位[：:]\s*(亿元|万元|千元|元)", pages[page_no - 1])
        if match:
            return match.group(1)
    for unit in ("亿元", "万元", "千元", "元"):
        if any(f"（{unit}）" in str(cell) for row in rows for cell in row):
            return unit
    return ""


def _apply(table, rows: list[list[str]], page: int, unit: str) -> None:
    table.rows = rows
    table.table_blocks = [
        {
            "page": int(page),
            "rows": [list(row) for row in rows],
            "unit": unit,
        }
    ]
    if unit:
        table.unit = unit


@register("zhongcheng")
def postprocess(tables, *, pdf_bytes):
    import pdfplumber

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        pages = [(page.extract_text() or "") for page in pdf.pages]
    for table in tables or []:
        table_id = str(getattr(table, "table_id", "") or "")
        spec = _TABLE_SPECS.get(table_id)
        if not spec:
            continue
        source_pages = [
            int(page)
            for page in (getattr(table, "source_pages", None) or [])
            if str(page).strip().isdigit()
        ]
        if not source_pages:
            page_no = getattr(table, "page", 0)
            if str(page_no).strip().isdigit() and int(page_no) > 0:
                source_pages = [int(page_no)]
        if not source_pages:
            continue
        lines = _collect_lines(pages, min(source_pages), spec)
        if not lines:
            continue
        rows = _parse_rows(lines, spec)
        if not rows:
            continue
        header = [str(item) for item in spec["header"]]  # type: ignore[index]
        table_rows = [header, *rows]
        unit = _sniff_unit(pages, source_pages, table_rows)
        _apply(table, table_rows, min(source_pages), unit)
    return tables

# Explicit company extraction hooks.
def locate_company_tables(pdf_bytes, table_configs=()):
    from ._company_hooks import locate_for_company
    return locate_for_company("zhongcheng", pdf_bytes, table_configs)


def extract_company_tables(pdf_bytes, table_configs):
    from ._company_hooks import extract_for_company
    return extract_for_company(
        "zhongcheng",
        pdf_bytes,
        table_configs,
        mode="legacy",
    )
