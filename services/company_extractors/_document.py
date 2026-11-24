"""Document primitives for explicitly configured company disclosure layouts."""

from __future__ import annotations

import io
import re
from typing import Any, Mapping, Sequence

MAIN = ["指标名称", "本季度末数", "上季度末数", "下季度末预测数"]
OPERATING = ["指标名称", "本季度数", "本年度累计数"]
CAPITAL = ["指标名称", "期末数", "期初数"]
LIQUIDITY = ["指标名称", "本季度数", "上季度数"]
RETURN = ["项目", "数值"]
HEADERS = {"SOLVENCY_MAIN": MAIN, "NON_LIFE_OPERATING_METRICS": OPERATING,
           "ACTUAL_CAPITAL": CAPITAL, "MINIMUM_CAPITAL": CAPITAL,
           "LIQUIDITY_MONITORING": LIQUIDITY, "THREE_YEAR_INVESTMENT_RETURN": RETURN}
STANDARD_NON_LIFE_LAYOUT = {
    "SOLVENCY_MAIN": {
        "title": "(?:主要)?偿付能力充足率指标",
        "required": ["认可资产", "综合偿付能力充足率"],
        "unit": "元",
    },
    "NON_LIFE_OPERATING_METRICS": {
        "title": "(?:财产保险公司)?主要经营指标",
        "required": ["保险业务收入", "净利润"],
        "continuation": 2,
        "tail_terms": ["签单保费", "综合成本率", "车险车均保费"],
        "stop": "(?:实际资本表|偿付能力状况表|流动性风险)",
        "unit": "万元",
    },
    "ACTUAL_CAPITAL": {
        "title": "(?:S02[-—－]?)?实际资本表",
        "required": ["核心一级资本"],
        "continuation": 3,
        "tail_terms": ["附属一级资本", "附属二级资本", "实际资本合计", "核心一级资本"],
        "stop": "(?:认可资产表|认可资产明细表|最低资本表)",
        "unit": "元",
    },
    "MINIMUM_CAPITAL": {
        "title": "(?:S05[-—－]?)?最低资本表",
        "required": ["量化风险最低资本"],
        "continuation": 2,
        "tail_terms": ["附加资本", "风险最低资本", "最低资本"],
        "stop": "(?:保费风险最低资本表|巨灾风险最低资本表)",
        "unit": "元",
    },
    "LIQUIDITY_MONITORING": {
        "title": "流动性风险(?:监管|监测)指标",
        "required": [],
        "continuation": 1,
        "tail_terms": ["LCR", "融资杠杆", "持有关联方资产", "经营活动净现金流"],
        "stop": "(?:实际资本表|偿付能力状况表|主要经营指标)",
        "unit": "元",
    },
    "THREE_YEAR_INVESTMENT_RETURN": {
        "title": "近三年.{0,40}平均(?:综合)?投资收益率",
        "required": [],
        "unit": "%",
    },
}
STANDARD_LAYOUT_CONTRACTS = {
    "SOLVENCY_MAIN": {
        "headers": MAIN,
        "header_variants": [
            ["指标名称", "本季度数", "上季度数", "本季度比上季度增减", "下季度预测数"],
        ],
        "min_rows": 10,
        "required_rows": [],
        "header_aliases": {
            "指标名称": ["项目", "指标"],
            "本季度末数": ["本季度数", "本季度末", "本季度（末）数", "项目"],
            "上季度末数": ["上季度数", "上季度末", "上季度可比数"],
            "下季度末预测数": ["下季度预测数", "基本情景下季度预测数"],
        },
    },
    "NON_LIFE_OPERATING_METRICS": {
        "headers": OPERATING,
        "min_rows": 15,
        "required_rows": [],
        "header_aliases": {
            "指标名称": ["项目", "指标"],
            "本季度数": ["本季度末数", "本季度（末）数"],
            "本年度累计数": ["本年累计数", "本年累计"],
        },
    },
    "ACTUAL_CAPITAL": {
        "headers": CAPITAL,
        "header_variants": [
            ["行次", "项目", "期末数", "期初数"],
            ["项目", "行次", "期末数", "期初数"],
        ],
        "min_rows": 6,
        "required_rows": [],
        "allow_mixed_widths": True,
        "header_aliases": {
            "指标名称": ["项目", "指标"],
            "期末数": ["本季度末", "本季度末数", "本期末数", "本期末"],
            "期初数": ["上季度末", "上季度末数", "上期末数", "上期末"],
        },
    },
    "MINIMUM_CAPITAL": {
        "headers": CAPITAL,
        "header_variants": [
            ["行次", "项目", "期末数", "期初数"],
            ["项目", "行次", "期末数", "期初数"],
        ],
        "min_rows": 10,
        "required_rows": [],
        "allow_mixed_widths": True,
        "header_aliases": {
            "指标名称": ["项目", "指标"],
            "期末数": ["本季度末", "本季度末数", "本期末数", "本期末"],
            "期初数": ["上季度末", "上季度末数", "上期末数", "上期末"],
        },
    },
    "LIQUIDITY_MONITORING": {
        "headers": LIQUIDITY,
        "min_rows": 2,
        "required_rows": [],
        "header_aliases": {
            "指标名称": ["项目", "指标", "监测指标"],
            "本季度数": ["本季度", "本季度末数", "本季度（末）数"],
            "上季度数": ["上季度", "上季度末数", "上季度（末）数"],
        },
    },
    "THREE_YEAR_INVESTMENT_RETURN": {
        "headers": RETURN,
        "header_variants": [
            ["项目", "本季度"],
            ["项目", "本季度数"],
        ],
        "min_rows": 2,
        "required_rows": [],
        "header_aliases": {
            "项目": ["指标名称"],
            "数值": ["指标金额", "本季度", "近三年平均"],
        },
    },
}
NUMBER = r"(?:[-－—–]{1,2}|[-+]?\d[\d,]*(?:\.\d+)?%?)"
VALUES = re.compile(rf"^(.*?)\s+({NUMBER})(?:\s+({NUMBER}))?(?:\s+({NUMBER}))?\s*$")
SECTION = re.compile(r"^(?:[一二三四五六七八九十]{1,3}[、．.]|[（(][一二三四五六七八九十]{1,3}[）)])")
RETURN_LABEL = re.compile(
    r"(?:近三年|三年)(?:.{0,40}?)(?:平均)?(?P<kind>综合)?投资收益率"
    r"|(?:近三年|三年)(?:平均)?(?P<kind_direct>综合)?投资收益率"
)
LCR = re.compile(r"(?:未来\s*(3|12|三|十二)\s*个月.*?LCR([123])|LCR([123]).*?未来\s*(3|12|三|十二)\s*个月)")


def compact(value):
    return re.sub(r"\s+", "", str(value if value is not None else ""))


def _header_key(value) -> str:
    text = compact(value)
    return re.sub(
        r"[（(](?:亿元|万元|千元|元|%|％|百分点)[）)]",
        "",
        text,
    )


def contract_failure(table, rows):
    contract = getattr(table, "table_config", {}).get("_layout_contract") or {}
    if not contract:
        return "版式契约未配置"
    headers = list(contract.get("headers") or [])
    header_variants = [
        list(value)
        for value in (contract.get("header_variants") or [])
        if isinstance(value, (list, tuple))
    ]
    header_aliases = contract.get("header_aliases") or {}
    actual_headers = list(rows[0]) if rows else []
    matched_headers: list[Any] | None = None
    for candidate in [headers, *header_variants]:
        candidate_match = len(actual_headers) == len(candidate) and all(
            _header_key(actual) == _header_key(expected)
            or _header_key(actual) in {
                _header_key(alias)
                for alias in (header_aliases.get(expected) or [])
            }
            for expected, actual in zip(candidate, actual_headers)
        )
        if candidate_match:
            matched_headers = candidate
            break
    headers_match = matched_headers is not None
    if not headers_match:
        return f"版式契约：表头不匹配，预期 {headers}"
    expected_width = len(matched_headers or headers)
    if contract.get("allow_mixed_widths"):
        if any(len(row) not in {3, 4} for row in rows):
            return "版式契约：数据列数与表头不一致"
    elif any(len(row) != expected_width for row in rows):
        return "版式契约：数据列数与表头不一致"
    if len(rows) < int(contract.get("min_rows", 1)):
        return f"版式契约：行数不足（{len(rows)}）"
    max_rows = contract.get("max_rows")
    if max_rows is not None and len(rows) > int(max_rows):
        return f"版式契约：行数超限（{len(rows)} > {max_rows}）"
    labels = [
        compact("".join(row[:-2])) if len(row) > 2 else compact(row[0])
        for row in rows[1:]
        if row
    ]
    missing = [label for label in contract.get("required_rows", [])
               if not any(compact(label) in actual for actual in labels)]
    if missing:
        return f"版式契约：缺少必要行 {missing}"
    forbidden = [
        label for label in contract.get("forbidden_rows", [])
        if any(compact(label) in actual for actual in labels)
    ]
    if forbidden:
        return f"版式契约：出现禁止行 {forbidden}"
    duplicates = sorted({
        label for label in labels
        if label and labels.count(label) > 1
    })
    if contract.get("unique_rows") and duplicates:
        return f"版式契约：指标行重复 {duplicates}"
    return ""


def _title_pages(texts, pattern, required):
    return [number for number, text in enumerate(texts, 1)
            if re.search(pattern, compact(text)) and all(term in compact(text) for term in required)
            and not re.search(r"目\s*录", text)]


def locate_from_texts(texts, layout):
    result = {}
    for table_id, spec in layout.items():
        candidates = _title_pages(texts, spec["title"], spec.get("required", ()))
        if table_id == "THREE_YEAR_INVESTMENT_RETURN":
            candidates = [number for number in candidates if "%" in texts[number - 1]]
        pages = candidates[:1]
        if pages:
            first = pages[0]
            for number in range(first + 1, min(len(texts), first + spec.get("continuation", 0)) + 1):
                text = compact(texts[number - 1])
                if re.search(spec.get("stop", r"(?!)"), text):
                    break
                if any(term in text for term in spec.get("tail_terms", ())):
                    pages.append(number)
        result[table_id] = pages
    return result


def _regulatory_rows(grid, company):
    """Decode disclosed regulatory horizons, keeping mandatory and self stress separate."""
    output = []
    previous_lcr = ""
    adverse = {}
    for raw in grid:
        cells = [compact(cell) for cell in raw]
        label = "".join(cells[:3]) if len(cells) == 5 and company in {"太保产险", "太平财险"} else cells[0]
        if "自测" in label:
            continue
        match = re.search(r"LCR([123])", label, re.I)
        kind = match.group(1) if match else ""
        horizon = re.search(r"(?:未来)?(12|3|十二|三)个月", label)
        if not kind and horizon and previous_lcr:
            kind = previous_lcr
        if not kind and "流动性覆盖率" in label:
            kind = "3" if "不考虑资产变现" in label else "2" if "必测" in label else "1" if "基本" in label else ""
        if kind:
            previous_lcr = kind
            if company == "英大财产" and len(cells) == 8:
                offsets = (2, 3) if kind == "1" else (4, 5)
                output.extend([[f"LCR{kind}未来{month}个月", cells[offset], ""] for month, offset in zip((3, 12), offsets)])
            elif horizon:
                values = [cell for cell in cells[1:] if re.fullmatch(NUMBER, cell)]
                if len(values) == 2:
                    month = "12" if horizon.group(1) in {"12", "十二"} else "3"
                    output.append([f"LCR{kind}未来{month}个月", *values])
            elif len(cells) == 5 and all(re.fullmatch(NUMBER, cell) for cell in cells[1:]):
                output.extend([[f"LCR{kind}未来{month}个月", cells[cur], cells[prior]] for month, cur, prior in ((3, 1, 3), (12, 2, 4))])
        elif "回溯不利偏差率" in label:
            values = [cell for cell in cells[1:] if re.fullmatch(NUMBER, cell)]
            if len(values) == 2:
                output.append([label, *values])
            elif len(values) == 1:
                adverse["prior" if "上一季度" in label else "current"] = values[0]
    if adverse:
        output.append(["经营活动净现金流回溯不利偏差率", adverse.get("current", ""), adverse.get("prior", "")])
    return output


def _priority_rows(grid, table_id, *, company="", page_number=0):
    """Keep priority disclosure rows before legacy postprocessing loses them."""
    output = []
    group = ""
    for raw in grid:
        cells = [compact(cell) for cell in raw]
        if table_id == "MINIMUM_CAPITAL":
            if len(cells) == 4 and re.fullmatch(r"\d+(?:\.\d+)*", cells[0]):
                cells = cells[1:]
            if len(cells) == 3 and re.search(r"特定类(?:别)?保险合同损失吸收效应", cells[0]):
                output.append(["特定类别保险合同损失吸收效应", *cells[1:]])
        elif table_id == "LIQUIDITY_MONITORING":
            if len(cells) == 4:
                group = cells[0] or group
                label = group if cells[1].startswith("指标值") else cells[1]
                values = cells[2:]
            elif len(cells) == 3:
                label, values = cells[0], cells[1:]
            else:
                continue
            if ("规模保费同比增速" in label and all(re.fullmatch(NUMBER, v) for v in values)
                    and not (company == "阳光农业" and page_number == 16)):
                output.append(["规模保费同比增速", *values])
    return output


def _confirmed_blank_cells(pdf, pages, table_id):
    """Check empty grid cells against PDF words at their original coordinates."""
    from ..solvency_normalizer import _normalize_label
    evidence = []
    width = len(HEADERS[table_id])
    for page_number in sorted(set(pages)):
        page = pdf.pages[page_number - 1]
        words = page.extract_words()
        for source in page.find_tables({
            "vertical_strategy": "lines", "horizontal_strategy": "lines",
            "snap_tolerance": 4, "join_tolerance": 4, "intersection_tolerance": 5,
        }):
            grid = source.extract()
            group = ""
            for row, source_row in zip(grid, source.rows):
                cells = [compact(cell) for cell in row]
                if table_id in {"ACTUAL_CAPITAL", "MINIMUM_CAPITAL"} and len(cells) == width + 1 and re.fullmatch(r"\d+(?:\.\d+)*\*?", cells[0]):
                    if not re.fullmatch(r"\d+(?:\.\d+)*\*?", cells[0]):
                        continue
                    label, start = cells[1], 2
                elif table_id in {"ACTUAL_CAPITAL", "MINIMUM_CAPITAL"} and len(cells) == width and re.fullmatch(r"\d+(?:\.\d+)*\*?", cells[0]):
                    label, start = cells[1], 2
                elif len(cells) == width:
                    label, start = cells[0], 1
                elif len(cells) == width + 1 and table_id == "LIQUIDITY_MONITORING":
                    group = cells[0] or group
                    label = group if cells[1].startswith("指标值") else cells[1]
                    start = 2
                elif len(cells) == width and table_id == "MINIMUM_CAPITAL" and re.fullmatch(r"\d+(?:\.\d+)*\*?", cells[0]):
                    label, start = cells[1], 2
                else:
                    continue
                if not re.search(r"[\u4e00-\u9fff]", label) or label in {"项目", "指标名称", "监测指标"}:
                    continue
                label = label.replace("特定类保险合同", "特定类别保险合同")
                for index in range(start, len(cells)):
                    if cells[index] or index >= len(source_row.cells):
                        continue
                    bbox = source_row.cells[index]
                    if bbox is None:
                        continue
                    x0, top, x1, bottom = bbox
                    # Any PDF text overlapping the cell makes a blank ambiguous.
                    occupied = any(word["x0"] < x1 - .5 and word["x1"] > x0 + .5
                                   and word["top"] < bottom - .5 and word["bottom"] > top + .5
                                   for word in words)
                    if not occupied:
                        evidence.append({"page": page_number, "label": _normalize_label(label),
                                         "value_index": index - start, "bbox": list(bbox),
                                         "basis": "原PDF线框单元格为空，坐标范围内无文字"})
    return evidence


def locate_document(pdf_bytes, layout):
    import fitz
    with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
        return locate_from_texts([page.get_text() for page in pdf], layout)


def locate_document_with_rules(pdf_bytes, table_configs):
    """Use title-based location, then fall back to the year's YAML page window."""
    located = locate_document(pdf_bytes, STANDARD_NON_LIFE_LAYOUT)
    import fitz

    with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
        page_count = len(pdf)
    requested: set[str] = set()
    for raw in table_configs:
        config = dict(raw)
        table_id = str(config.get("table_id") or "").strip()
        if table_id == "OPERATING_METRICS":
            table_id = "NON_LIFE_OPERATING_METRICS"
        if not table_id:
            continue
        requested.add(table_id)
        if config.get("not_disclosed"):
            located[table_id] = []
            continue
        fallback = [
            int(page)
            for page in (config.get("expected_pages") or [])
            if int(page) > 0
        ]
        if fallback:
            # Company rules remain authoritative when a PDF has fewer pages
            # than the baseline layout. Clamp instead of silently dropping the
            # table and falling back to a title search on scanned summaries.
            located[table_id] = sorted(
                {
                    min(max(1, page), page_count)
                    for page in fallback
                }
            )
    if requested:
        located = {
            table_id: pages
            for table_id, pages in located.items()
            if table_id in requested
        }
    return located


def make_document_hooks(layout, company, postprocess):
    """Build company hooks while keeping each layout explicit and isolated."""

    def locate_company_tables(pdf_bytes, table_configs=()):
        return locate_document(pdf_bytes, layout)

    def extract_company_tables(pdf_bytes, table_configs):
        return extract_document(
            pdf_bytes,
            table_configs,
            layout,
            postprocess,
            company,
        )

    return locate_company_tables, extract_company_tables


def make_standard_document_hooks(company, postprocess):
    return make_document_hooks(STANDARD_NON_LIFE_LAYOUT, company, postprocess)


def _capital_rows_from_texts(texts: Sequence[str], pages: Sequence[int]):
    lines: list[str] = []
    for page in pages:
        if 1 <= int(page) <= len(texts):
            lines.extend(
                line.strip()
                for line in texts[int(page) - 1].splitlines()
                if line.strip()
            )
    start = None
    has_row_no = False
    for index in range(len(lines) - 2):
        window = lines[index:index + 5]
        compacted = [compact(value) for value in window]
        if "项目" in compacted and "期末数" in compacted and "期初数" in compacted:
            start = index + max(
                compacted.index("项目"),
                compacted.index("期初数"),
            ) + 1
            has_row_no = "行次" in compacted[:compacted.index("项目")]
            break
    if start is None:
        return []
    rows: list[list[str]] = []
    code = ""
    label: list[str] = []
    values: list[str] = []

    def flush():
        nonlocal code, label, values
        text = "".join(label).strip()
        if text and code:
            rows.append([code, text, values[0] if values else "", values[1] if len(values) > 1 else ""])
        elif text and not has_row_no:
            rows.append([text, values[0] if values else "", values[1] if len(values) > 1 else ""])
        code, label, values = "", [], []

    for line in lines[start:]:
        token = compact(line)
        if token.startswith("S0") or token.startswith("认可资产") or token.startswith("最低资本表"):
            break
        if re.fullmatch(r"\d+(?:\.\d+)*\*?", token):
            flush()
            code = line
            continue
        if re.fullmatch(NUMBER, token):
            values.append(line)
            continue
        if values and len(values) >= 2:
            flush()
        label.append(line)
        if not has_row_no and values:
            flush()
    flush()
    return rows


def _liquidity_rows_from_texts(texts: Sequence[str], pages: Sequence[int]):
    labels = (
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
    lines: list[str] = []
    for page in pages:
        if 1 <= int(page) <= len(texts):
            lines.extend(
                line.strip()
                for line in texts[int(page) - 1].splitlines()
                if line.strip()
            )
    rows = []
    for wanted in labels:
        key = compact(wanted)
        try:
            index = next(
                i
                for i, line in enumerate(lines)
                if key in compact(line)
            )
        except StopIteration:
            continue
        values = []
        for candidate in lines[index + 1:index + 10]:
            value = candidate.replace(" ", "").replace("\u3000", "")
            if not re.fullmatch(NUMBER, value):
                continue
            if re.fullmatch(r"\d{1,2}", value):
                continue
            values.append(candidate)
            if len(values) == 2:
                break
        if values:
            rows.append(
                [
                    wanted,
                    values[0],
                    values[1] if len(values) > 1 else "",
                ]
            )
    return rows


def _normalize_capital_width(rows: list[list[str]]) -> list[list[str]]:
    if not rows:
        return rows
    width = len(rows[0])
    output = [list(rows[0])]
    for row in rows[1:]:
        cells = list(row)
        if len(cells) > width and width in {3, 4}:
            if width == 4:
                cells = [
                    cells[0],
                    "".join(cells[1:-2]),
                    *cells[-2:],
                ]
            else:
                cells = ["".join(cells[:-2]), *cells[-2:]]
        if len(cells) < width:
            cells.extend([""] * (width - len(cells)))
        output.append(cells[:width])
    return output


def _number_rows(lines, width):
    rows = []
    for line in lines:
        match = VALUES.match(line.strip())
        if not match:
            continue
        label = match.group(1).strip()
        if not re.search(r"[\u4e00-\u9fffA-Za-z]", label):
            continue
        if label.startswith(("单位", "公司名称", "注：", "注:", "填表说明")):
            continue
        values = [value for value in match.groups()[1:] if value is not None]
        if len(values) == width - 1 and width == 3:
            values.append("")
        if len(values) != width:
            continue
        label = re.sub(r"^\d+(?:\.\d+)*\*?\s*", "", label)
        rows.append([label, *values])
    return rows


def _select_grids(pdf, pages, table_id, cache):
    selected = []
    signatures = {
        "SOLVENCY_MAIN": ("认可资产", "认可负债", "综合偿付能力充足率"),
        "NON_LIFE_OPERATING_METRICS": ("保险业务收入", "净利润"),
        "ACTUAL_CAPITAL": ("核心一级资本",),
        "MINIMUM_CAPITAL": ("量化风险最低资本",),
        "LIQUIDITY_MONITORING": ("经营活动净现金流",),
    }
    for page in pages:
        if page not in cache:
            cache[page] = pdf.pages[page - 1].extract_tables({
                "vertical_strategy": "lines", "horizontal_strategy": "lines",
                "snap_tolerance": 4, "join_tolerance": 4, "intersection_tolerance": 5,
            })
        for grid in cache[page]:
            rows = [[compact(cell) for cell in row] for row in grid if row]
            labels = "".join("".join(row[:2]) for row in rows)
            if all(term in labels for term in signatures.get(table_id, ())):
                selected.append((page, rows))
            elif table_id == "SOLVENCY_MAIN" and "综合偿付能力充足率" in labels:
                selected.append((page, rows))
            elif table_id == "MINIMUM_CAPITAL" and any(term in labels for term in (
                    "非寿险业务保险风险最低资本合计", "控制风险最低资本")):
                selected.append((page, rows))
            elif selected and table_id in {"ACTUAL_CAPITAL", "MINIMUM_CAPITAL", "NON_LIFE_OPERATING_METRICS"}:
                if any(term in labels for term in ("实际资本合计", "附属一级资本", "其他渠道签单保费", "控制风险最低资本", "非寿险业务保险风险最低资本合计")):
                    selected.append((page, rows))
    return selected


def _grid_rows(grid, table_id):
    width = len(HEADERS[table_id]) - 1
    output = []
    numbered = table_id in {"ACTUAL_CAPITAL", "MINIMUM_CAPITAL"}
    group = ""
    for row in grid:
        cells = [str(cell or "").strip() for cell in row]
        if len(cells) == width + 1:
            label, values = cells[0], cells[1:]
        elif table_id == "LIQUIDITY_MONITORING" and len(cells) == width + 2:
            group = cells[0] or group
            item = cells[1]
            label = group if item in {"指标值", "指标值（万元）", "指标值（元）"} else item
            values = cells[2:]
        elif numbered and len(cells) == width + 2:
            label, values = cells[1], cells[2:]
            if cells[0] and cells[0] not in {"行次", "序号"}:
                label = cells[0] + " " + label
        else:
            continue
        if not label or label in {"行次", "项目", "指标名称", "类型", "监测指标"}:
            continue
        if numbered and len(cells) == width + 2 and not re.fullmatch(r"\d+(?:\.\d+)*\*?", cells[0]):
            continue
        output.append([label, *values])
    return output


def _returns(page_text):
    found = {}
    lines = page_text.splitlines()
    for index, line in enumerate(lines):
        normalized = compact(line)
        for match in RETURN_LABEL.finditer(normalized):
            kind = "综合" if match.group("kind") or match.group("kind_direct") else ""
            if kind in found:
                continue
            tail = normalized[match.end():]
            value = re.search(r"[-－—–]{1,2}|[-+]?\d[\d,]*(?:\.\d+)?%?", tail)
            if value is None:
                value = re.search(
                    r"[-－—–]{1,2}|[-+]?\d[\d,]*(?:\.\d+)?%?",
                    " ".join(lines[index + 1:index + 4]),
                )
            if value:
                raw = value.group().strip()
                found.setdefault(kind, raw.rstrip("%") + ("%" if "%" in raw else ""))
        if "规模保费同比增速" in normalized:
            continue
    if len(found) < 2:
        for index, line in enumerate(lines):
            normalized = compact(line)
            if "近三年" not in normalized or "投资收益率" not in normalized:
                continue
            for kind, pattern in (("", r"近三年(?:平均)?投资收益率"),
                                  ("综合", r"近三年(?:平均)?综合投资收益率")):
                if kind in found:
                    continue
                match = re.search(pattern + r"\s*([-－—–]{1,2}|[-+]?\d[\d,]*(?:\.\d+)?%?)", normalized)
                if match:
                    raw = match.group(1)
                    found[kind] = raw.rstrip("%") + ("%" if "%" in raw else "")
    return [[f"近三年平均{kind}投资收益率", found[kind]] for kind in ("", "综合") if kind in found]


def _liquidity_text_rows(page_text, company):
    """Read only the confirmed monitoring-table section from PDF text order."""
    lines = [line.strip() for line in page_text.splitlines() if line.strip()]
    if company == "安盛天平":
        start = next((i for i, line in enumerate(lines)
                      if "流动性风险" in compact(line)), None)
        if start is None:
            return []
        lines = lines[start + 1:]
        rows = []
        for i, line in enumerate(lines):
            label = compact(line)
            if "规模保费同比增速" not in label:
                continue
            value = ""
            for candidate in lines[i + 1:i + 12]:
                candidate = compact(candidate)
                if re.fullmatch(NUMBER, candidate):
                    value = candidate
                    break
                if candidate.startswith(("规模保费", "当年累计规模保费", "去年同期累计规模保费", "五、")):
                    break
            if value:
                rows.append(["规模保费同比增速", value, ""])
            break
        return rows
    if company in {"pingan", "zhongan", "平安产险", "众安产险"}:
        # These Word/PDF reports expose the liquidity table as a sequence of
        # labels followed by two values, rather than reliable table grids.
        # Parse the entire confirmed section so the document contract can
        # validate all disclosed rows; do not infer zero from missing values.
        labels = (
            "经营活动净现金流", "百元保费经营活动净现金流",
            "特定业务现金流支出占比", "规模保费同比增速",
            "现金及流动性管理工具占比", "季均融资杠杆比例",
            "AA级（含）以下境内固定收益类资产占比",
            "持股比例大于5%的上市股票投资占比", "应收款项占比",
            "持有关联方资产占比",
        )
        # Ping An's page contains the regulatory section first and the
        # monitoring table later; both headings share this phrase. Anchor to
        # the last occurrence so regulatory rows cannot leak into the table.
        start = next((i for i in range(len(lines) - 1, -1, -1)
                      if "流动性风险监测指标" in compact(lines[i])), None)
        if start is None:
            return []
        section = lines[start + 1:]
        row_starts = []
        for index, line in enumerate(section):
            normalized = compact(line)
            label = next((item for item in labels if item in normalized), None)
            if label:
                row_starts.append((index, label, normalized))
        rows = []
        def add_row(label, raw_label, candidates):
            values = [compact(item) for item in candidates
                      if re.fullmatch(NUMBER, compact(item))]
            if len(values) < 2:
                return
            cleaned = re.sub(r"^[一二三四五六七八九十]+、", "", compact(raw_label))
            cleaned = re.sub(r"\(单位:[^)]*\)|（单位[:：]?[^）]*）", "", cleaned)
            # A PDF text line may contain a footnote marker between the row
            # label and its values; discard this non-value token explicitly.
            cleaned = re.sub(r"(?<=[占比])\d+$", "", cleaned)
            rows.append([cleaned or label, values[0], values[1]])
        for position, (index, label, normalized) in enumerate(row_starts):
            end = row_starts[position + 1][0] if position + 1 < len(row_starts) else len(section)
            add_row(label, normalized, section[index + 1:end])
        if not rows:
            # pdfplumber may collapse a complete row onto one line while
            # PyMuPDF exposes the same content as separate text blocks.
            text = "\n".join(lines)
            for match in re.finditer(
                    r"(?P<label>(?:[一二三四五六七八九十]+、)?[^\n]*?"
                    r"(?:经营活动净现金流|百元保费经营活动净现金流|特定业务现金流支出占比|"
                    r"规模保费同比增速|现金及流动性管理工具占比|季均融资杠杆比例|"
                    r"AA级（含）以下境内固定收益类资产占比|持股比例大于5%的上市股票投资占比|"
                    r"应收款项占比|持有关联方资产占比)[^\n]*?)"
                    r"\s+(?P<first>" + NUMBER + r")\s+(?P<second>" + NUMBER + r")",
                    text):
                raw_label = match.group("label")
                label = next((item for item in labels if item in compact(raw_label)), "")
                add_row(label, raw_label, [match.group("first"), match.group("second")])
        return rows
    start = next((i for i, line in enumerate(lines)
                  if "流动性风险" in compact(line)), None)
    if start is None:
        return []
    if company == "浙商财险":
        section = "".join(compact(line) for line in lines[start + 1:])
        rows = []
        for label in ("经营活动净现金流", "规模保费同比增速"):
            match = re.search(re.escape(label) + r"(?:（[^）]*）)?([-+]?[\d,]+(?:\.\d+)?%?)([-+]?[\d,]+(?:\.\d+)?%?)", section)
            if match:
                rows.append([label, match.group(1), match.group(2)])
        return rows
    if company == "国任保险":
        section = "".join(compact(line) for line in lines[start + 1:])
        rows = []
        for label in ("经营活动净现金流", "规模保费同比增速"):
            match = re.search(re.escape(label) + r"([-+]?[\d,]+(?:\.\d+)?%?)", section)
            if match:
                rows.append([label, match.group(1), ""])
        return rows
    if company == "铁路自保":
        section = "".join(compact(line) for line in lines[start + 1:])
        match = re.search(r"四、规模保费同比增速\s*\(%\)([-+]?[\d,]+(?:\.\d+)?%?)", section)
        return [["规模保费同比增速", match.group(1), ""]] if match else []
    if company == "长江财险":
        section = "".join(compact(line) for line in lines[start + 1:])
        rows = []
        for label in ("经营活动净现金流", "规模保费同比增速"):
            match = re.search(re.escape(label) + r"(?:（[^）]*）)?([-+]?[\d,]+(?:\.\d+)?%?)([-+]?[\d,]+(?:\.\d+)?%?)", section)
            if match:
                rows.append([label, match.group(1), match.group(2)])
        return rows
    if company == "黄河财险":
        section_lines = lines[start + 1:]
        for index, line in enumerate(section_lines):
            if "规模保费同比增速" not in compact(line):
                continue
            values = []
            for candidate in section_lines[index + 1:index + 12]:
                value = compact(candidate)
                if re.fullmatch(NUMBER, value):
                    values.append(value)
                    if len(values) == 2:
                        break
            if len(values) == 2:
                return [["规模保费同比增速", *values]]
        return []
    if company == "阳光农业":
        operating_heading = next((i for i, line in enumerate(lines[start + 1:], start + 1)
                                  if compact(line).startswith(("（四）主要经营指标", "(四)主要经营指标"))),
                                 len(lines))
        section_lines = lines[start + 1:operating_heading]
        rows = []
        for index, line in enumerate(section_lines):
            if compact(line) == "4":
                following = [compact(item) for item in section_lines[index + 1:index + 5]]
                if following and following[0] == "规模保费同比增速":
                    values = [item for item in following[1:] if re.fullmatch(NUMBER, item)]
                    if len(values) >= 2:
                        rows.append(["规模保费同比增速", values[0], values[1]])
            if compact(line) == "1":
                following = [compact(item) for item in section_lines[index + 1:index + 5]]
                if following and "经营活动净现金流" in following[0]:
                    values = [item for item in following[1:] if re.fullmatch(NUMBER, item)]
                    if len(values) >= 2:
                        rows.append(["经营活动净现金流", values[0], values[1]])
        return rows
    if company == "法巴天星":
        return []
    rows = []
    section_lines = lines[start + 1:]
    for index, line in enumerate(section_lines):
        label = compact(line)
        if label.startswith(("（四）", "(四)", "（五）", "(五)", "主要经营指标")):
            break
        match = re.match(r"^[一二三四五六七八九十]+、(.+?)(?:\s*[（(](?:万元|元|%)?[）)])?$", label)
        if not match:
            continue
        item = match.group(1).strip()
        if not re.search(r"(?:经营活动净现金流|规模保费同比增速)", item):
            continue
        values = []
        for candidate in section_lines[index + 1:index + 10]:
            candidate = compact(candidate)
            if re.fullmatch(NUMBER, candidate):
                values.append(candidate)
                if len(values) == 2:
                    break
            if re.match(r"^[一二三四五六七八九十]+、", candidate):
                break
        if "经营活动净现金流" in item:
            rows.append([f"{item}（元）" if "（元）" in item else item,
                         *(values[:2] + [""] * max(0, 2 - len(values)))])
        elif "规模保费同比增速" in item:
            rows.append(["规模保费同比增速", *(values[:2] + [""] * max(0, 2 - len(values)))])
    return rows


def _text_segment(text, table_id):
    lines = text.splitlines()
    start_patterns = {
        "SOLVENCY_MAIN": r"认可资产",
        "NON_LIFE_OPERATING_METRICS": r"保险业务收入",
        "ACTUAL_CAPITAL": r"(?:实际资本表|实际资本明细表)",
        "MINIMUM_CAPITAL": r"(?:十、最低资本|最低资本表|量化风险最低资本)",
        "LIQUIDITY_MONITORING": r"流动性风险监测指标",
    }
    start = next((index for index, line in enumerate(lines) if re.search(start_patterns[table_id], compact(line))), 0)
    result = []
    for line in lines[start:]:
        normalized = compact(line)
        if result and normalized.startswith(("填表说明", "填报说明", "注：", "备注：")):
            break
        if table_id == "SOLVENCY_MAIN" and result and ("流动性风险" in normalized or "主要经营指标" in normalized):
            break
        if table_id == "NON_LIFE_OPERATING_METRICS" and result and ("近三年" in normalized or "偿付能力状况表" in normalized):
            break
        if table_id == "LIQUIDITY_MONITORING" and result and ("主要经营指标" in normalized or "近三年" in normalized):
            break
        result.append(line)
    return result


def extract_document(
    pdf_bytes,
    configs,
    layout,
    postprocess,
    company,
    *,
    metadata=None,
    generic=False,
    enforce_contract: bool | None = None,
):
    import fitz
    import pandas as pd
    import pdfplumber
    from ..batch_extraction import FlexibleTable

    if enforce_contract is None:
        enforce_contract = not generic

    with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
        texts = [page.get_text() for page in pdf]
    has_return_disclosure = any(
        re.search(
            r"近三年.{0,16}(?:平均)?(?:（?综合）?)?投资收益率",
            compact(text),
        )
        for text in texts
    )
    # STEP1's year-aware page decisions are authoritative. Never silently search
    # elsewhere when a confirmed locator returned no page for a target table.
    pages_by_table = {config["table_id"]: list(config.get("_confirmed_pages") or [])
                      for config in configs}
    confirmed_pages = {table_id: set(pages) for table_id, pages in pages_by_table.items()}
    tables = []
    cache = {}
    regulatory_blocks = []
    priority_blocks = {"MINIMUM_CAPITAL": [], "LIQUIDITY_MONITORING": []}
    blank_evidence = {}
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page, text in enumerate(texts, 1):
            normalized = compact(text)
            for table_id, pattern in (("MINIMUM_CAPITAL", r"特定类(?:别)?保险合同损失吸收效应"),
                                      ("LIQUIDITY_MONITORING", r"规模保费同比增速")):
                if page not in confirmed_pages.get(table_id, set()):
                    continue
                if not re.search(pattern, normalized):
                    continue
                _select_grids(pdf, [page], table_id, cache)
                body = [row for grid in cache[page] for row in
                        _priority_rows(grid, table_id, company=company, page_number=page)]
                if body:
                    priority_blocks[table_id].append({"page": page, "rows": [HEADERS[table_id].copy(), *body],
                                                     "unit": "%" if table_id == "LIQUIDITY_MONITORING" else layout[table_id].get("unit", "万元")})
        for page, text in enumerate(texts, 1):
            if page not in confirmed_pages.get("LIQUIDITY_MONITORING", set()):
                continue
            if "LCR" not in text and "流动性覆盖率" not in compact(text):
                continue
            if not re.search(r"LCR[123]|流动性覆盖率", compact(text)):
                continue
            _select_grids(pdf, [page], "LIQUIDITY_MONITORING", cache)
            body = [row for grid in cache[page] for row in _regulatory_rows(grid, company)]
            if body:
                regulatory_blocks.append({"page": page, "rows": [LIQUIDITY.copy(), *body], "unit": "%"})
        for config in configs:
            table_id = config["table_id"]
            pages = pages_by_table.get(table_id, [])
            if not pages:
                continue
            blocks = []
            unit = "%" if table_id == "THREE_YEAR_INVESTMENT_RETURN" else layout[table_id].get("unit", "万元")
            for page in pages:
                text = pdf.pages[page - 1].extract_text() or ""
                declared = re.search(r"单位\s*[:：]?\s*(?:均为人民币)?\s*(万元|亿元|元)", text)
                if declared:
                    unit = declared.group(1)
                if table_id == "NON_LIFE_OPERATING_METRICS":
                    revenue_unit = re.search(r"保险业务收入\s*[（(]\s*(万元|亿元|元)\s*[）)]", text)
                    if revenue_unit:
                        unit = revenue_unit.group(1)
                if table_id == "THREE_YEAR_INVESTMENT_RETURN":
                    body = _returns(text)
                    if company == "法巴天星" and "无投资活动" in text:
                        body = [["近三年平均投资收益率", "-"],
                                ["近三年平均综合投资收益率", "-"]]
                elif table_id == "LIQUIDITY_MONITORING":
                    body = _liquidity_text_rows(text, company)
                    if not body:
                        grids = _select_grids(pdf, [page], table_id, cache)
                        body = [row for _, grid in grids for row in _grid_rows(grid, table_id)]
                else:
                    grids = _select_grids(pdf, [page], table_id, cache)
                    body = [row for _, grid in grids for row in _grid_rows(grid, table_id)]
                    if company == "太保产险" and table_id == "MINIMUM_CAPITAL":
                        for row in _number_rows(_text_segment(text, table_id), 2):
                            if "非寿险业务保险风险-风险分散效应" in row[0] and not any(row[0] in existing[0] for existing in body):
                                body.append(row)
                    if not body:
                        body = _number_rows(_text_segment(text, table_id), len(HEADERS[table_id]) - 1)
                if body:
                    blocks.append({"page": page, "rows": [list(HEADERS[table_id]), *body], "unit": unit})
            if table_id == "THREE_YEAR_INVESTMENT_RETURN" and not blocks:
                scan_pages = pages or list(range(1, len(texts) + 1))
                for page in scan_pages:
                    text = texts[page - 1]
                    normalized_text = compact(text)
                    if not ("近三年" in normalized_text and
                            re.search(r"(?:投资收益率|综合投资收益率)", normalized_text)):
                        continue
                    body = _returns(text)
                    if body:
                        blocks.append({"page": page, "rows": [list(RETURN), *body], "unit": "%"})
                        pages = [page]
                        break
            if generic and table_id == "THREE_YEAR_INVESTMENT_RETURN" and blocks:
                # Confirmed pages already contain the company's explicit rows.
                # If a hook emitted an incomplete numeric return table, fill only
                # its missing rows; dashes remain source text and are not zeros.
                seeded = {compact(row[0]) for block in blocks
                          for row in block.get("rows", [])[1:] if row}
                for page in pages:
                    for row in _returns(texts[page - 1]):
                        if compact(row[0]) not in seeded:
                            blocks.append({"page": page, "rows": [list(RETURN), row], "unit": "%"})
                            seeded.add(compact(row[0]))
            if table_id == "SOLVENCY_MAIN" and company == "dongwu" and blocks:
                for block in blocks:
                    block["rows"] = [[cell.replace(" ", "") if isinstance(cell, str) else cell
                                      for cell in row] for row in block["rows"]]
                    for row in block["rows"][1:]:
                        if len(row) == 5 and row[2] in {"元", "万元", "亿元"}:
                            row.pop(2)
                    block["rows"] = [row for row in block["rows"] if len(row) == len(MAIN)]
            if generic and table_id == "ACTUAL_CAPITAL" and not blocks and company == "浙商财险":
                # In this report S02 shares the confirmed S01 page and is
                # explicitly titled in the text layer, though it has no grid.
                for page in pages:
                    section = _text_segment(texts[page - 1], "ACTUAL_CAPITAL")
                    body = _number_rows(section, 2)
                    body = [row for row in body if any(term in row[0] for term in
                            ("核心一级资本", "净资产", "实际资本合计", "附属一级资本"))]
                    if body:
                        blocks.append({"page": page, "rows": [list(CAPITAL), *body], "unit": unit})
                        break
            if not blocks:
                # Keep the located target alive so a company postprocessor can
                # rebuild text-only or cross-page layouts from the PDF. A table
                # that remains header-only will fail its contract later.
                blocks = [
                    {
                        "page": pages[0],
                        "rows": [list(HEADERS[table_id])],
                        "unit": unit,
                    }
                ]
            rows = [row for block in blocks for row in block["rows"]]
            tables.append(FlexibleTable(table_id=table_id, table_name=config["table_name"],
                         page=pages[0], source_pages=pages, rows=rows, table_blocks=blocks,
                         table_config=dict(config), unit=unit, unit_source=unit,
                         strategy="公司专属规则", quality_score=1.0))
        for config in configs:
            table_id = config["table_id"]
            pages = list(pages_by_table.get(table_id, []))
            pages.extend(block["page"] for block in priority_blocks.get(table_id, []))
            if table_id in HEADERS and table_id != "THREE_YEAR_INVESTMENT_RETURN":
                blank_evidence[table_id] = _confirmed_blank_cells(pdf, pages, table_id)
    from ._operating_table import complete_operating_tables
    tables = list(complete_operating_tables(tables, pdf_bytes=pdf_bytes, page_texts=texts))
    # Company postprocessors run last so they can remove rows that the shared
    # operating-table completion recovered from an adjacent section.
    tables = list(postprocess(tables, pdf_bytes=pdf_bytes))
    by_id = {table.table_id: table for table in tables}
    for table_id, table in by_id.items():
        pages = [
            int(page)
            for page in (getattr(table, "source_pages", None) or [])
            if int(page) > 0
        ]
        if not pages:
            pages = [
                int(page)
                for page in (pages_by_table.get(table_id) or [])
                if int(page) > 0
            ]
        if table_id in {"ACTUAL_CAPITAL", "MINIMUM_CAPITAL"}:
            parsed = _capital_rows_from_texts(texts, pages)
            if parsed and len(table.rows or []) <= 1:
                header = (
                    ["行次", "项目", "期末数", "期初数"]
                    if any(len(row) == 4 for row in parsed)
                    else ["项目", "期末数", "期初数"]
                )
                table.rows = [header, *parsed]
                table.table_blocks = [
                    {
                        "page": pages[0] if pages else int(getattr(table, "page", 1) or 1),
                        "rows": [header, *parsed],
                    }
                ]
                table.strategy = "公司规则·资本表文本重建"
        elif table_id == "LIQUIDITY_MONITORING":
            parsed = _liquidity_rows_from_texts(texts, pages)
            if parsed and len(table.rows or []) <= 1:
                header = ["指标名称", "本季度数", "上季度数"]
                table.rows = [header, *parsed]
                table.table_blocks = [
                    {
                        "page": pages[0] if pages else int(getattr(table, "page", 1) or 1),
                        "rows": [header, *parsed],
                    }
                ]
                table.strategy = "公司规则·流动性文本重建"
    for table in tables:
        if table.table_id != "NON_LIFE_OPERATING_METRICS":
            continue
        width = len(OPERATING)
        for block in table.table_blocks:
            fixed = []
            for row in block.get("rows", []):
                cells = [str(cell or "").strip() for cell in row]
                if len(cells) < width:
                    cells.extend([""] * (width - len(cells)))
                fixed.append(cells[:width])
            block["rows"] = fixed
        table.rows = [
            list(row)
            for block in table.table_blocks
            for row in block.get("rows", [])
        ]
    if generic:
        by_id = {table.table_id: table for table in tables}
        for config in configs:
            table_id = config["table_id"]
            if table_id not in {"MINIMUM_CAPITAL", "LIQUIDITY_MONITORING"}:
                continue
            priority = priority_blocks.get(table_id, [])
            if not priority:
                continue
            table = by_id.get(table_id)
            if table is None:
                pages = sorted({block["page"] for block in priority})
                header = list(HEADERS[table_id])
                unit = "%" if table_id == "LIQUIDITY_MONITORING" else layout[table_id].get("unit", "万元")
                table = FlexibleTable(
                    table_id=table_id, table_name=config["table_name"], page=pages[0],
                    source_pages=pages, rows=[header.copy()], table_blocks=[],
                    table_config=dict(config), unit=unit, unit_source=unit,
                    strategy="原文指标行定位", quality_score=1.0,
                )
                tables.append(table)
                by_id[table_id] = table
            existing_rows = {tuple(compact(cell) for cell in row)
                             for block in table.table_blocks
                             for row in block.get("rows", [])[1:] if row}
            for block in priority:
                fresh = [row for row in block.get("rows", [])[1:]
                         if tuple(compact(cell) for cell in row) not in existing_rows]
                if fresh:
                    table.table_blocks.append({"page": block["page"],
                                               "rows": [list(HEADERS[table_id]), *fresh],
                                               "unit": block.get("unit", table.unit)})
                    existing_rows.update(tuple(compact(cell) for cell in row) for row in fresh)
            table.rows = [row for block in table.table_blocks for row in block.get("rows", [])]
            table.source_pages = sorted({block["page"] for block in table.table_blocks})
            table.page = table.source_pages[0]
        for table in tables:
            if table.table_id != "LIQUIDITY_MONITORING":
                continue
            for page in pages_by_table.get(table.table_id, []):
                for row in _liquidity_text_rows(texts[page - 1], company):
                    if any(compact(row[0]) == compact(existing[0]) and
                           compact(row[1]) == compact(existing[1])
                           for existing in table.rows if len(existing) > 1):
                        continue
                    table.rows.append(row)
                    block = next((item for item in table.table_blocks if item.get("page") == page), None)
                    if block is None:
                        block = {"page": page, "rows": [list(HEADERS[table.table_id])], "unit": table.unit}
                        table.table_blocks.append(block)
                    block["rows"].append(row)
            table.source_pages = sorted({block["page"] for block in table.table_blocks})
            if table.source_pages:
                table.page = table.source_pages[0]
        by_id = {table.table_id: table for table in tables}
        for table_id in ("MINIMUM_CAPITAL", "LIQUIDITY_MONITORING"):
            priority = priority_blocks.get(table_id, [])
            if not priority:
                continue
            table = by_id.get(table_id)
            if table is None:
                config = next((item for item in configs if item["table_id"] == table_id), None)
                if config is None:
                    continue
                pages = sorted({block["page"] for block in priority})
                header = list(HEADERS[table_id])
                unit = "%" if table_id == "LIQUIDITY_MONITORING" else layout[table_id].get("unit", "万元")
                table = FlexibleTable(
                    table_id=table_id, table_name=config["table_name"], page=pages[0],
                    source_pages=pages, rows=[header.copy()], table_blocks=[],
                    table_config=dict(config), unit=unit, unit_source=unit,
                    strategy="原文指标行定位", quality_score=1.0,
                )
                tables.append(table)
                by_id[table_id] = table
            table.table_blocks.extend(priority)
            table.rows = [row for block in table.table_blocks for row in block.get("rows", [])]
            table.source_pages = sorted({block["page"] for block in table.table_blocks})
            table.page = table.source_pages[0]
        for table in tables:
            table.blank_cell_evidence = blank_evidence.get(table.table_id, [])
            if table.table_id == "SOLVENCY_MAIN" and company == "dongwu":
                table.table_blocks = [{"page": table.page, "rows": [list(row) for row in table.rows],
                                       "unit": table.unit}]
            if table.table_id in {"ACTUAL_CAPITAL", "MINIMUM_CAPITAL"}:
                table.rows = _normalize_capital_width(
                    [list(row) for row in table.rows]
                )
                for block in table.table_blocks:
                    block["rows"] = _normalize_capital_width(
                        [list(row) for row in block.get("rows", [])]
                    )
            elif (
                table.table_id == "SOLVENCY_MAIN"
                and table.rows
                and len(table.rows[0]) in {4, 5}
            ):
                width = len(table.rows[0])
                table.rows = [
                    [*list(row), *([""] * max(0, width - len(row)))][:width]
                    for row in table.rows
                ]
                for block in table.table_blocks:
                    block["rows"] = [
                        [*list(row), *([""] * max(0, width - len(row)))][:width]
                        for row in block.get("rows", [])
                    ]
            elif (
                table.table_id == "LIQUIDITY_MONITORING"
                and table.rows
                and len(table.rows[0]) in {3, 4}
            ):
                width = len(table.rows[0])
                table.rows = [
                    [*list(row), *([""] * max(0, width - len(row)))][:width]
                    for row in table.rows
                ]
                for block in table.table_blocks:
                    block["rows"] = [
                        [*list(row), *([""] * max(0, width - len(row)))][:width]
                        for row in block.get("rows", [])
                    ]
        contract_failures = {}
        for table in tables:
            rows = [list(row) for row in (getattr(table, "rows", []) or [])]
            if len(rows) <= 1:
                continue
            failure = contract_failure(table, rows)
            if failure:
                contract_failures[table.table_id] = failure
        tables = [
            table
            for table in tables
            if len(getattr(table, "rows", []) or []) > 1
            and table.table_id not in contract_failures
        ]
        table_ids = {table.table_id for table in tables}
        logs = []
        for config in configs:
            table_id = config["table_id"]
            exists = table_id in table_ids
            has_pages = bool(pages_by_table.get(table_id))
            undisclosed_return = (table_id == "THREE_YEAR_INVESTMENT_RETURN"
                                  and not exists
                                  and not has_return_disclosure)
            logs.append({"目标表": config.get("table_name", table_id), "表编码": table_id,
                         "物理页码": "、".join(map(str, pages_by_table.get(table_id, []))),
                         "识别方式": f"公司规则文档适配:{company}",
                         "状态": "未披露" if undisclosed_return else "成功" if exists else "失败",
                         "说明": "原文未披露近三年投资收益率" if undisclosed_return else
                         "复用现有公司定位规则与后处理的整份文档提取" if exists else
                         contract_failures.get(table_id) if table_id in contract_failures else
                         "提取或公司后处理未生成该表" if has_pages else "公司规则定位未找到目标页"})
        return {"tables": tables, "logs": logs, "pages_by_table": pages_by_table}
    # Restore row provenance after legacy postprocessors rebuild a single block.
    for table in tables:
        if table.table_id != "NON_LIFE_OPERATING_METRICS":
            continue
        by_page = {}
        for row in table.rows:
            if not row or row[0] in OPERATING:
                continue
            label = re.sub(r"^\d+(?:\.\d+)*", "", compact(row[0]))
            label = re.sub(r"[（(].*?[）)]", "", label)
            page = next((p for p in table.source_pages if label and label in compact(texts[p - 1])), table.page)
            by_page.setdefault(page, [OPERATING.copy()]).append(row)
        table.table_blocks = [{"page": page, "rows": rows, "unit": table.unit}
                              for page, rows in sorted(by_page.items())]
    if regulatory_blocks:
        for table in tables:
            if table.table_id != "LIQUIDITY_MONITORING":
                continue
            for block in table.table_blocks:
                block["rows"] = [row for row in block.get("rows", [])
                                 if not row or not re.search(r"LCR[123]|流动性覆盖率|回溯不利偏差率", compact(row[0]))]
            table.table_blocks.extend(regulatory_blocks)
    for table in tables:
        blocks = priority_blocks.get(table.table_id, [])
        if not blocks:
            continue
        pattern = r"特定类(?:别)?保险合同损失吸收效应" if table.table_id == "MINIMUM_CAPITAL" else r"规模保费同比增速"
        for block in table.table_blocks:
            block["rows"] = [row for row in block.get("rows", []) if not row or not re.search(pattern, compact(row[0]))]
        table.table_blocks.extend(blocks)
    for table in tables:
        if (
            str(getattr(table, "table_id", ""))
            not in {"ACTUAL_CAPITAL", "MINIMUM_CAPITAL"}
            or not table.rows
        ):
            continue
        if (
            len(table.rows[0]) == 3
            and any(
                len(row) == 4
                and re.fullmatch(r"\d+(?:\.\d+)*\*?", compact(row[0]))
                for row in table.rows[1:]
            )
        ):
            table.rows[0] = ["行次", "项目", "期末数", "期初数"]
        table.rows = _normalize_capital_width(table.rows)
        width = len(table.rows[0])
        for block in table.table_blocks:
            header = list(block.get("rows", [[]])[0])
            if (
                len(header) == 3
                and any(
                    len(row) == 4
                    and re.fullmatch(r"\d+(?:\.\d+)*\*?", compact(row[0]))
                    for row in block.get("rows", [])[1:]
                )
            ):
                header = ["行次", "项目", "期末数", "期初数"]
            block["rows"] = _normalize_capital_width(
                [header, *list(block.get("rows", []))[1:]]
            )
    logs = []
    accepted = []
    by_id = {table.table_id: table for table in tables}
    for config in configs:
        table_id = config["table_id"]
        table = by_id.get(table_id)
        failure = ""
        if table is None:
            undisclosed_return = (table_id == "THREE_YEAR_INVESTMENT_RETURN"
                                  and not pages_by_table.get(table_id)
                                  and not has_return_disclosure)
            failure = "" if undisclosed_return else (
                "公司专属定位未找到目标表" if not pages_by_table.get(table_id)
                else "公司专属解析未取得表体"
            )
        else:
            table.table_config = dict(config)
            table.blank_cell_evidence = blank_evidence.get(table_id, [])
            contract = config.get("_layout_contract") or {}
            source_header = list(table.rows[0]) if table.rows else []
            expected_header = list(HEADERS[table_id])
            if (
                table_id == "THREE_YEAR_INVESTMENT_RETURN"
                and source_header
                and "近三年" in compact(source_header[0])
            ):
                expected_header = list(HEADERS[table_id])
            elif (
                source_header
                and len(source_header) != len(expected_header)
                and any(
                    len(variant) == len(source_header)
                    for variant in (contract.get("header_variants") or [])
                )
            ):
                expected_header = source_header
            expected_width = len(expected_header)
            rows = [list(expected_header)]
            normalized_blocks = []
            seen = set()
            for block in table.table_blocks:
                body = []
                source = block.get("rows") or []
                for row in source:
                    cells = [str(cell if cell is not None else "").strip() for cell in row]
                    if not cells or not cells[0] or "表格分块" in cells[0]:
                        continue
                    if cells[0] in {"项目", "类型", "指标名称", "行次", "监测指标"}:
                        continue
                    if len(cells) == 2 and table_id == "NON_LIFE_OPERATING_METRICS" and "近三年" in cells[0]:
                        continue
                    if table_id == "NON_LIFE_OPERATING_METRICS" and "规模保费同比增速" in compact(cells[0]):
                        continue
                    if table_id == "SOLVENCY_MAIN" and len(cells) == len(HEADERS[table_id]) + 1:
                        # Some company templates prefix the main table with a
                        # row-number column or leave a trailing empty column.
                        if (
                            compact(cells[0]) in {"行次", "序号"}
                            or re.fullmatch(r"\d+(?:\.\d+)*", compact(cells[0]))
                        ):
                            cells = cells[1:]
                        elif not compact(cells[-1]):
                            cells = cells[:-1]
                    if (
                        expected_width == len(HEADERS[table_id])
                        and len(cells) == expected_width + 1
                        and table_id in {"ACTUAL_CAPITAL", "MINIMUM_CAPITAL", "LIQUIDITY_MONITORING"}
                    ):
                        cells = [cells[0] + " " + cells[1], *cells[2:]]
                    if (
                        generic
                        and table_id in {"ACTUAL_CAPITAL", "MINIMUM_CAPITAL"}
                        and len(cells) < expected_width
                    ):
                        cells.extend([""] * (expected_width - len(cells)))
                    if table_id == "LIQUIDITY_MONITORING" and len(cells) == 3 and not re.fullmatch(NUMBER, compact(cells[1])) and re.fullmatch(NUMBER, compact(cells[2])):
                        cells = [cells[0] if cells[1].startswith("指标值") else cells[1], cells[2], ""]
                    if len(cells) != expected_width and not generic:
                        failure = f"版式契约：数据列数不符（{len(cells)}）"
                        break
                    if len(cells) != expected_width and generic:
                        # Existing company postprocessors sometimes emit numbered
                        # four-column rows or merged multi-value layouts. Preserve
                        # them for the established STEP3 table parser.
                        body.append(cells)
                        continue
                    if table_id == "NON_LIFE_OPERATING_METRICS" and not any(re.fullmatch(NUMBER, compact(value)) for value in cells[1:]):
                        continue
                    key = tuple(cells)
                    if key in seen:
                        continue
                    seen.add(key)
                    body.append(cells)
                if failure:
                    break
                if body:
                    normalized_blocks.append({**block, "rows": [list(expected_header), *body]})
                    rows.extend(body)
            if table_id in {"ACTUAL_CAPITAL", "MINIMUM_CAPITAL"} and rows:
                rows = _normalize_capital_width(rows)
                for block in normalized_blocks:
                    block["rows"] = _normalize_capital_width(
                        list(block.get("rows", []))
                    )
            if enforce_contract:
                failure = failure or contract_failure(table, rows)
            if not failure:
                table.rows = rows
                table.table_blocks = normalized_blocks
                table.source_pages = sorted({block["page"] for block in normalized_blocks})
                table.page = table.source_pages[0]
                frame = pd.DataFrame(rows[1:], columns=rows[0])
                table.to_frame = lambda *args, _frame=frame, **kwargs: _frame.copy()
                accepted.append(table)
        undisclosed = (table_id == "THREE_YEAR_INVESTMENT_RETURN"
                       and table is None
                       and not has_return_disclosure)
        logs.append({"目标表": config["table_name"], "表编码": table_id,
                     "物理页码": "、".join(map(str, pages_by_table.get(table_id, []))),
                     "识别方式": f"公司专属规则:{company}", "状态": "未披露" if undisclosed else "失败" if failure else "成功",
                     "说明": "原文未披露近三年投资收益率" if undisclosed else failure or f"公司专属解析 {len(table.rows) - 1} 行"})
    return {"tables": accepted, "logs": logs, "pages_by_table": pages_by_table}
