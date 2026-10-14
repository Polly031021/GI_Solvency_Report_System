from __future__ import annotations

"""Template-driven STEP3 standardization helpers.

The UI deliberately keeps the annual-report platform's target-template workflow,
while the output remains the canonical narrow table consumed by STEP5.
"""

import io
from dataclasses import dataclass
from typing import Mapping, Sequence

import pandas as pd

from .solvency_dataset_adapter import (
    append_derived_metrics,
    supported_metric_catalog,
    write_standard_workbook_sheets,
)
from .solvency_metric_registry import DERIVED_METRICS
from .solvency_normalizer import NARROW_TABLE_COLUMNS, STANDARD_COLUMNS, normalize_tables
from .solvency_table_extractor import ExtractedTable


TARGET_SHEET_NAME = "指标清单"
STANDARD_SHEET_NAME = "标准数据"
TARGET_TEMPLATE_COLUMNS = [
    "启用",
    "指标编码",
    "指标名称",
    "一级模块",
    "二级模块",
    "标准单位",
    "数据类型",
    "指标属性",
    "计算逻辑",
]

# 去重时把不同叫法归一到同一期间 key，仅用于去重，不改输出的"期间口径"字段
_PERIOD_DEDUP_ALIASES: dict[str, str] = {
    "本季度数": "本季度末数",
    "上季度数": "上季度末数",
    "期末数": "本季度末数",
    "期初数": "上季度末数",
    "本年度累计数": "本年累计数",
    "本年数": "本年累计数",          # ★ 新增
    "季度预测数": "下季度末预测数",
    "下季度预测数": "下季度末预测数",  # ★ 新增
    "本季度": "本季度末数",
    "上季度": "上季度末数",
    "期末": "本季度末数",
    "期初": "上季度末数",
    "近3年平均": "近三年平均",        # ★ 新增
    "三年平均": "近三年平均",         # ★ 新增
}


def _dedup_period(period: object) -> str:
    text = str(period or "").strip()
    return _PERIOD_DEDUP_ALIASES.get(text, text)

_CN_DIGITS = {
    # 小写
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    # ★ 财务大写
    "壹": 1, "贰": 2, "叁": 3, "肆": 4, "伍": 5,
    "陆": 6, "柒": 7, "捌": 8, "玖": 9,
    "〇": 0,
}
_CN_UNITS = {
    "十": 10, "百": 100, "千": 1000,
    "拾": 10, "佰": 100, "仟": 1000,
}


def _chinese_to_number(text: str) -> float:
    """把 '二百一十'、'叁拾亿零玖佰肆拾壹万陆仟' 之类的中文数字
    转成阿拉伯数字。支持"万/亿"节权位和财务大写。"""
    s = str(text or "").strip()
    if not s:
        raise ValueError("empty chinese number")

    total = 0
    section = 0
    current = 0
    for ch in s:
        if ch in _CN_DIGITS:
            if ch in {"零", "〇"}:
                continue
            current = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            unit = _CN_UNITS[ch]
            if current == 0:
                current = 1
            section += current * unit
            current = 0
        elif ch == "万":
            section = (section + current) * 10_000
            total += section
            section = 0
            current = 0
        elif ch == "亿":
            total = (total + section + current) * 100_000_000
            section = 0
            current = 0
        else:
            # 遇到非数字字符就停
            break
    return float(total + section + current)

@dataclass(frozen=True)
class Step3StandardizationResult:
    data: pd.DataFrame
    target_catalog: pd.DataFrame
    diagnostics: pd.DataFrame
    target_summary: pd.DataFrame
    warnings: tuple[str, ...]

def convert_amount_to_target_unit(
    raw_value,
    raw_unit: str | None,
    target_unit: str,
    metric_code: str = "",
) -> tuple[float | str | None, str]:
    """
    将原始数值转换为目标单位。
    返回 (转换后的值, 备注信息)

    注意：不再根据数值量级猜测原始单位。原始单位缺失或无法识别时，
    原样返回并写入备注，避免把“万元”误当“元”除以 10000。
    """
    if raw_value is None or pd.isna(raw_value):
        return None, ""

    # 非数值（如比率）直接返回
    if not isinstance(raw_value, (int, float)):
        return raw_value, ""

    unit_scale = {
        "元": 1,
        "万元": 10_000,
        "千元": 1_000,
        "百元": 100,
        "亿元": 100_000_000,
        "十亿元": 1_000_000_000,
    }

    raw_unit_norm = (raw_unit or "").strip()
    target_unit_norm = (target_unit or "").strip()

    if target_unit_norm not in unit_scale:
        return raw_value, f"目标单位'{target_unit_norm}'未知，未换算"

    if not raw_unit_norm or raw_unit_norm not in unit_scale:
        return raw_value, f"原始单位无法识别（'{raw_unit}'），未换算"

    raw_scale = unit_scale[raw_unit_norm]
    target_scale = unit_scale[target_unit_norm]

    if raw_scale == target_scale:
        return raw_value, ""

    converted = raw_value * raw_scale / target_scale
    return converted, f"由{raw_unit_norm}换算为{target_unit_norm}（×{raw_scale}/{target_scale}）"

def step3_metric_catalog(
    taxonomy: pd.DataFrame,
    *,
    include_derived: bool,
) -> pd.DataFrame:
    """Return the exact metric-code range supported by STEP5 mapping."""
    catalog = supported_metric_catalog(
        taxonomy,
        include_derived=include_derived,
        include_step3_only=True,
    ).copy()
    catalog.insert(0, "启用", "是")
    return catalog.reindex(columns=TARGET_TEMPLATE_COLUMNS)


def _find_header_row(preview: pd.DataFrame, required_header: str) -> int | None:
    for index, row in preview.iterrows():
        values = {str(value).strip() for value in row if not pd.isna(value)}
        if required_header in values:
            return int(index)
    return None


def _enabled(value: object) -> bool:
    text = str(value if value is not None else "").strip().lower()
    # ★ 兜底：把"是/启用/√/Y/1/true"都当作启用
    if text in {"是", "启用", "√", "✓", "y", "yes", "1", "true", "t"}:
        return True
    if text in {"否", "不启用", "×", "✗", "n", "no", "0", "false", "f"}:
        return False
    # 空值或其他无法识别的值，沿用当前默认：启用
    return text not in {"否", "不启用", "false", "0", "n", "no"}


def read_target_template(
    workbook_bytes: bytes,
    filename: str,
    supported_catalog: pd.DataFrame,
) -> tuple[pd.DataFrame, tuple[str, ...]]:
    """Read a target list and constrain it to STEP5-supported metric codes."""
    excel = pd.ExcelFile(io.BytesIO(workbook_bytes))
    sheet_name = (
        TARGET_SHEET_NAME
        if TARGET_SHEET_NAME in excel.sheet_names
        else STANDARD_SHEET_NAME
        if STANDARD_SHEET_NAME in excel.sheet_names
        else excel.sheet_names[0]
    )
    preview = pd.read_excel(excel, sheet_name=sheet_name, header=None, nrows=20)
    header_row = _find_header_row(preview, "指标编码")
    if header_row is None:
        raise ValueError(f"{filename} 未找到“指标编码”表头。")

    frame = pd.read_excel(excel, sheet_name=sheet_name, header=header_row).fillna("")
    frame.columns = [str(column).strip() for column in frame.columns]
    if "启用" in frame.columns:
        frame = frame[frame["启用"].map(_enabled)]
    raw_codes = [
        str(value).strip()
        for value in frame["指标编码"]
        if str(value).strip()
    ]
    codes = list(dict.fromkeys(raw_codes))
    if not codes:
        raise ValueError(f"{filename} 未配置任何已启用的目标指标。")

    supported_codes = set(supported_catalog["指标编码"].astype(str))
    unknown_codes = [code for code in codes if code not in supported_codes]
    if unknown_codes:
        raise ValueError(
            "目标表包含 STEP3 正式指标字典之外的编码：" + "、".join(unknown_codes)
        )

    indexed = supported_catalog.set_index("指标编码", drop=False)
    selected = pd.DataFrame([indexed.loc[code] for code in codes]).reset_index(drop=True)
    selected["启用"] = "是"

    warnings: list[str] = []
    duplicate_count = len(raw_codes) - len(codes)
    if duplicate_count > 0:
        warnings.append(f"目标表中 {duplicate_count} 条重复指标已按指标编码去重。")
    return selected.reindex(columns=TARGET_TEMPLATE_COLUMNS), tuple(warnings)


def _period_from_header(header_cells, col_index: int) -> str:
    """从表头列名判断期间口径名称（与 solvency_normalizer.PERIOD_ALIASES 一致）。"""
    if not header_cells or col_index >= len(header_cells):
        return ""
    text = str(header_cells[col_index]).replace(" ", "").replace("\u3000", "")
    if not text:
        return ""
    import re as _re_ph
    text = _re_ph.sub(r"[（(][^）)]*[）)]", "", text).strip()

    # ★ 新增（P1a 修复）：某些 PDF 的表头被 AI 拆成两行，例如：
    #   行A: | 行次 | 项目 | 期末数-审计后 | 期末数-审计前 | 期初数 | 基本情景下的下 |
    #   行B: |      |      |               |               |        | 季度预测数      |
    #   此时 col_index=5 只拿到"基本情景下的下"，匹配不到"下季度"或"预测"。
    #   这里尝试拼接右邻列的残片后再判断。
    if col_index + 1 < len(header_cells):
        _next_raw = str(header_cells[col_index + 1]).replace(" ", "").replace("\u3000", "")
        _next = _re_ph.sub(r"[（(][^）)]*[）)]", "", _next_raw).strip()
        _merged = text + _next
        # 只处理"下季度预测"这一类跨行表头；其他类型不拼接，避免误伤
        if (
            ("下季度" in _merged or "预测" in _merged or "下季" in _merged)
            and not any(k in text for k in ("下季度", "预测", "下季"))
            and len(_next) <= 8   # 残片要够短，避免把下一整列内容误拼
        ):
            return "下季度末预测数"

    if not text:
        # ★ 新增：当前列是纯括号说明（如"（审计后）"），看右邻列推断
        if col_index + 1 < len(header_cells):
            _next_raw = str(header_cells[col_index + 1]).replace(" ", "").replace("\u3000", "")
            _next = _re_ph.sub(r"[（(][^）)]*[）)]", "", _next_raw).strip()
            if any(k in _next for k in ("上季度末", "上季末")):
                return "本季度末数"
        return ""
    # 优先识别"近三年"类口径
    if "近三年" in text or "三年平均" in text or "3年平均" in text:
        return "近三年平均"
    if "下季度" in text or "下季" in text or "预测" in text:
        return "下季度末预测数"
    if "本年度累计" in text or "本年累计" in text or "本年数" in text:
        return "本年累计数"
    # ★ 修复：先匹配"本季末/上季末/本季度末/上季度末"等所有变体
    if any(k in text for k in ("本季度末", "本季末", "本季末数")):
        return "本季度末数"
    if any(k in text for k in ("上季度末", "上季末", "上季末数")):
        return "上季度末数"
    if any(k in text for k in ("本季度数", "本季度", "本期")):
        return "本季度数"
    if any(k in text for k in ("上季度数", "上季度", "上期")):
        return "上季度数"
    if "期末" in text:
        return "期末数"
    if "期初" in text:
        return "期初数"
    return ""

def _period_from_row_label(label: str) -> str:
    """当表头列名是"列2"之类的占位符时，用数据行的行标签推断期间。

    主要用于"近三年（综合）投资收益率"这类纵向表：
        | （四）近三年（综合）投资收益率 | 列2 |
        | 近三年平均投资收益率           | 2.34% |
        | 近三年平均综合投资收益率       | 3.11% |
    """
    text = str(label or "").replace(" ", "").replace("\u3000", "")
    if not text:
        return ""
    # 只有"近三年"+"平均"同时出现，才认为这一行是近三年平均值
    if "近三年" in text and "平均" in text:
        return "近三年平均"
    if "本年累计" in text or "本年度累计" in text:
        return "本年累计数"
    return ""

def _dependency_closure(target_codes: set[str]) -> set[str]:
    derived_by_code = {definition.code: definition for definition in DERIVED_METRICS}
    required = set(target_codes)
    pending = list(target_codes)
    while pending:
        code = pending.pop()
        definition = derived_by_code.get(code)
        if definition is None:
            continue
        for dependency in definition.dependencies:
            if dependency not in required:
                required.add(dependency)
                pending.append(dependency)
    return required


def _target_summary(
    target_catalog: pd.DataFrame,
    data: pd.DataFrame,
) -> pd.DataFrame:
    summary = target_catalog[
        ["指标编码", "指标名称", "一级模块", "二级模块", "指标属性"]
    ].copy()

    # ★ 新增：把 data 里已有、但 target_catalog 里没有的指标补进 summary。
    # 背景：REGISTERED_CAPITAL 是 standardize_to_target / app.py 在最后
    #       直接追加到 data 的，不在 target_catalog 里。若不补进来，
    #       summary 里永远看不到 REGISTERED_CAPITAL，最终报表会显示
    #       "未填报"，与实际数据不符。
    if not data.empty and "指标编码" in data.columns:
        _catalog_codes = set(
            summary["指标编码"].astype(str).str.strip()
        )
        _data_code_series = data["指标编码"].astype(str).str.strip()
        _extra_codes = sorted(set(_data_code_series) - _catalog_codes)
        if _extra_codes:
            _extra_rows = []
            for _code in _extra_codes:
                _subset = data[_data_code_series == _code]
                if _subset.empty:
                    continue
                _first = _subset.iloc[0]
                _extra_rows.append({
                    "指标编码": _code,
                    "指标名称": str(_first.get("指标名称", "")),
                    "一级模块": str(_first.get("一级模块", "")),
                    "二级模块": str(_first.get("二级模块", "")),
                    "指标属性": str(_first.get("指标属性", "披露")),
                })
            if _extra_rows:
                summary = pd.concat(
                    [summary, pd.DataFrame(_extra_rows)],
                    ignore_index=True,
                )

    if data.empty:
        summary["填报记录数"] = 0
        summary["已填期间"] = ""
        summary["填报状态"] = "未填报"
        return summary

    normalized = data.copy()
    normalized["_code"] = normalized["指标编码"].astype(str).str.strip()

    counts = normalized["_code"].value_counts()
    summary["填报记录数"] = (
        summary["指标编码"].astype(str).str.strip().map(counts).fillna(0).astype(int)
    )

    period_series = (
        normalized.groupby("_code")["期间口径"]
        .apply(lambda s: "、".join(sorted({str(v) for v in s if str(v).strip()})))
    )
    summary["已填期间"] = (
        summary["指标编码"].astype(str).str.strip().map(period_series).fillna("")
    )
    summary["填报状态"] = summary["填报记录数"].map(
        lambda value: "已填报" if value else "未填报"
    )
    # ★ 新增：记录同一指标是否存在多期间冲突
    if not data.empty:
        period_sets = (
            normalized.groupby("_code")["期间口径"]
            .apply(lambda s: {str(v).strip() for v in s if str(v).strip()})
        )
        summary["期间口径明细"] = (
            summary["指标编码"].astype(str).str.strip()
            .map(period_sets)
            .apply(lambda s: "、".join(sorted(s)) if isinstance(s, set) else "")
        )
        summary["口径冲突提示"] = summary["期间口径明细"].map(
            lambda s: "⚠ 多口径并存，请核对" if s and ("、" in s) else ""
        )
    return summary


def standardize_to_target(
    tables: list[ExtractedTable],
    taxonomy: pd.DataFrame,
    metadata: dict,
    company_type: str,
    target_catalog: pd.DataFrame,
    *,
    report_profile_id: str,
    allowed_company_types: Sequence[str],
    peer_group: str = "",
    peer_group_map: Mapping[str, str] | None = None,
    default_peer_group: str = "",
    include_derived: bool,
    template_warnings: Sequence[str] = (),
) -> Step3StandardizationResult:
    """Fill a target list and return the canonical STEP5-compatible narrow table."""
    # ★ 修复：报告期兜底 —— 从 metadata / 文件名 / 首文文本依次抓
    import re as _re_period
    _report_period = str(metadata.get("报告期", "") or "").strip()
    if not _report_period or "None" in _report_period:
        _name = str(metadata.get("来源文件", ""))
        _m = _re_period.search(r"(20\d{2})[-_ ]?Q?([1-4])", _name)
        if _m:
            _report_period = f"{_m.group(1)}Q{_m.group(2)}"
        else:
            _fp = str(metadata.get("first_page_text", ""))
            _m2 = _re_period.search(
                r"(20\d{2})\s*年\s*第\s*([一二三四1-4])\s*季", _fp
            )
            if _m2:
                _map = {"一": "1", "二": "2", "三": "3", "四": "4",
                        "1": "1", "2": "2", "3": "3", "4": "4"}
                _report_period = f"{_m2.group(1)}Q{_map.get(_m2.group(2), '4')}"
    if _report_period:
        metadata["报告期"] = _report_period
        metadata["报告年度"] = int(_report_period[:4])
        metadata["报告季度"] = _report_period[4:]

    target_codes = set(target_catalog["指标编码"].astype(str).str.strip())
    required_codes = _dependency_closure(target_codes)
    derived_codes = {definition.code for definition in DERIVED_METRICS}
    normalization_codes = required_codes - derived_codes
    scoped_taxonomy = taxonomy[
        taxonomy["指标编码"].astype(str).str.strip().isin(normalization_codes)
    ].copy()

    diagnostics: list[dict] = []
    warnings: list[str] = list(template_warnings)   # ★ 提前初始化，供补漏匹配使用
    normalized = normalize_tables(
        tables,
        scoped_taxonomy,
        metadata,
        company_type,
        report_profile_id=report_profile_id,
        diagnostics=diagnostics,
        allowed_company_types=allowed_company_types,
        peer_group=peer_group,
        peer_group_map=peer_group_map,
        default_peer_group=default_peer_group,
    )
    enriched = append_derived_metrics(normalized) if include_derived else normalized
    data = enriched[
        enriched["指标编码"].astype(str).str.strip().isin(target_codes)
    ].copy()
    if not data.empty:
        target_order = {
            code: index
            for index, code in enumerate(target_catalog["指标编码"].astype(str))
        }
        data["_目标顺序"] = data["指标编码"].map(target_order)
        data = data.sort_values(
            ["_目标顺序", "期间口径", "来源页码"],
            kind="stable",
        ).drop(columns="_目标顺序")
    data = data.reindex(columns=STANDARD_COLUMNS).reset_index(drop=True)

    
    # ===== 补漏匹配：对未填报的关键指标，用"清洗后名称"做二次精确匹配 =====
    import re as _re_supp
    _NOISE_RE_SUPP = _re_supp.compile(r"[\d,，.。%％:：;；()（）\[\]【】\s\-—_*+=/\\|]+")

    def _clean_label_supp(s):
        return _NOISE_RE_SUPP.sub("", str(s or "")).strip()

    _matched_codes = set()
    if not data.empty:
        _matched_codes = set(data["指标编码"].astype(str).str.strip())
    _missing_codes = target_codes - _matched_codes

    if _missing_codes:
        # 建立"清洗后名称 → 目标信息"映射
        _code_to_info = {}
        for _, _trow in target_catalog.iterrows():
            _code = str(_trow["指标编码"]).strip()
            if _code in _missing_codes:
                _cleaned_name = _clean_label_supp(_trow["指标名称"])
                if _cleaned_name:
                    _code_to_info[_cleaned_name] = {
                        "code": _code,
                        "name": str(_trow["指标名称"]).strip(),
                        "module1": str(_trow.get("一级模块", "")).strip(),
                        "module2": str(_trow.get("二级模块", "")).strip(),
                        "unit": str(_trow.get("标准单位", "万元")).strip() or "万元",
                        "dtype": str(_trow.get("数据类型", "金额")).strip() or "金额",
                    }

        # ===== ★ 新增：非车险 TOP1-TOP5 / 分渠道签单保费 的别名映射 =====
        # 场景：部分公司（如紫金财险）不写"第一大险种"，而直接写险种名；
        #      不写"各渠道签单保费"，而写"分渠道签单保费"。
        # 此处 _missing_codes 与 _code_to_info 均已就绪，可安全读写。
        _PREMIUM_ALIAS_MAP: dict[str, str] = {
            # 非车险前五大险种 —— 序号即排名（行业通用）
            "责任保险":   "SIGNED_PREMIUM_TOP1",
            "农业保险":   "SIGNED_PREMIUM_TOP2",
            "意外伤害险": "SIGNED_PREMIUM_TOP3",
            "健康险":     "SIGNED_PREMIUM_TOP4",
            "信用保险":   "SIGNED_PREMIUM_TOP5",
            # 分渠道签单保费 —— 与"各渠道签单保费"同义
            "分渠道签单保费":     "SIGNED_PREMIUM_ALL_CHANNEL",
            "各渠道签单保费合计": "SIGNED_PREMIUM_ALL_CHANNEL",
            "分渠道保费":         "SIGNED_PREMIUM_ALL_CHANNEL",
        }
        for _alias, _code in _PREMIUM_ALIAS_MAP.items():
            if _code in _missing_codes:
                _row = target_catalog[
                    target_catalog["指标编码"].astype(str).str.strip() == _code
                ]
                if not _row.empty:
                    _r = _row.iloc[0]
                    _info = {
                        "code": _code,
                        "name": str(_r["指标名称"]).strip(),
                        "module1": str(_r.get("一级模块", "")).strip(),
                        "module2": str(_r.get("二级模块", "")).strip(),
                        "unit": str(_r.get("标准单位", "万元")).strip() or "万元",
                        "dtype": str(_r.get("数据类型", "金额")).strip() or "金额",
                    }
                    # ★ P1b 修复：紫金财产 PDF 的原始标签形如
                    #   "--责任保险（万元）"，经 _clean_label_supp 清洗后
                    #   会保留"万元"两个字（正则只剥标点/空白，不剥中文），
                    #   变成 "责任保险万元"。如果只注册 "_alias" 这一个 key
                    #   （即 "责任保险"），永远匹配不上。
                    #   这里把所有常见变体都注册一遍。
                    for _variant in (
                        _alias,
                        _alias + "万元",
                        _alias + "（万元）",
                        _alias + "(万元)",
                        f"--{_alias}",
                        f"--{_alias}（万元）",
                        f"--{_alias}(万元)",
                    ):
                        _code_to_info[_clean_label_supp(_variant)] = _info
        # ===== 别名映射结束 =====

        _supp_rows = []
        _template = data.iloc[0].to_dict() if not data.empty else {}

        for _table in tables:
            _rows = getattr(_table, "rows", None) or []
            if len(_rows) < 2:
                continue
            _source_file = _template.get("来源文件", "")
            _source_sheet = getattr(_table, "table_name", "")
            _source_pages = getattr(_table, "source_pages", None) or []

            # ★ 太保 S05 表（以及其它双栏 PDF）常出现"标签与数值被拆到多行"
            #   的版式。下面这套合并逻辑覆盖两类结构：
            #
            #   结构一（标签被拆成两行）：
            #     行A: | 空 | 市场风险-境外固定收益类资产价格风险最低 | 空 | 空 | 空 |
            #     行B: | 3.4 | 空 | 5,645 | 5,869 | 空 |
            #     行C: | 空 | 资本 | 空 | 空 | 空 |
            #     → 合并为 | 3.4 | 市场风险-境外固定收益类资产价格风险最低资本 | 5,645 | 5,869 | 空 |
            #
            #   结构二（标签只被拆成一段在上一行）：
            #     行A: | 空 | 非寿险业务保险风险最低资本合计 | 空 | 空 | 空 |
            #     行B: | 1 | 空 | 2,298,668 | 2,280,150 | 空 |
            #     → 合并为 | 1 | 非寿险业务保险风险最低资本合计 | 2,298,668 | 2,280,150 | 空 |
            def _is_number_text(_s: str) -> bool:
                _s2 = str(_s).replace(",", "").replace("，", "").strip()
                if not _s2:
                    return False
                try:
                    float(_s2)
                    return True
                except ValueError:
                    return False

            def _is_label_like(_s: str, min_len: int = 4, max_len: int = 80) -> bool:
                _t = str(_s).strip()
                if not (min_len <= len(_t) <= max_len):
                    return False
                if any(ch.isdigit() for ch in _t):
                    return False
                return any("\u4e00" <= ch <= "\u9fff" for ch in _t)

            _rows_merged: list[list] = []
            _i = 0
            _n = len(_rows)
            while _i < _n:
                _row = list(_rows[_i]) if _rows[_i] else []
                _nonempty = [(k, str(c).strip()) for k, c in enumerate(_row) if str(c).strip()]

                # 判断：本行是否为"标签头"——只有一个非空单元格，
                # 位置在 _row[1] 或更后，是中文长句，不含数字
                _is_label_head = (
                    len(_nonempty) == 1
                    and _nonempty[0][0] >= 1
                    and _is_label_like(_nonempty[0][1], min_len=6)
                    and _i + 1 < _n
                )

                if not _is_label_head:
                    _rows_merged.append(_row)
                    _i += 1
                    continue

                _label_head = _nonempty[0][1]
                _next_row = list(_rows[_i + 1]) if _rows[_i + 1] else []
                _next_nonempty = [(k, str(c).strip()) for k, c in enumerate(_next_row) if str(c).strip()]

                # 判断：下一行是否以"短编号"开头（如 "1", "1.1", "3.4"）
                _is_value_row = (
                    bool(_next_nonempty)
                    and _next_nonempty[0][0] == 0
                    and len(_next_nonempty[0][1]) <= 6
                    and any(ch.isdigit() for ch in _next_nonempty[0][1])
                )
                if not _is_value_row:
                    _rows_merged.append(_row)
                    _i += 1
                    continue

                _row_no = _next_nonempty[0][1]
                # 判断下一行的第 2 个非空单元格是"标签尾"还是"数值"
                _tail_label = ""
                _values: list[str] = []
                if len(_next_nonempty) >= 2:
                    _second_val = _next_nonempty[1][1]
                    if _is_number_text(_second_val):
                        # 第 2 格就是数值，标签尾不在这行
                        _values = [v for _, v in _next_nonempty[1:]]
                    else:
                        # 第 2 格是标签尾
                        _tail_label = _second_val
                        _values = [v for _, v in _next_nonempty[2:]]

                _consumed = 2
                # 若标签尾不在这行，再看再下一行
                if not _tail_label and _i + 2 < _n:
                    _after = list(_rows[_i + 2]) if _rows[_i + 2] else []
                    _after_nonempty = [(k, str(c).strip()) for k, c in enumerate(_after) if str(c).strip()]
                    if (
                        len(_after_nonempty) == 1
                        and _after_nonempty[0][0] >= 1
                        and _is_label_like(_after_nonempty[0][1], min_len=1, max_len=20)
                    ):
                        _tail_label = _after_nonempty[0][1]
                        _consumed = 3

                _full_label = _label_head + _tail_label
                _merged_row = [_row_no, _full_label] + _values
                _rows_merged.append(_merged_row)
                _i += _consumed

            _rows = _rows_merged

            for _row in _rows[1:]:
                if not _row or len(_row) < 2:
                    continue
                for _label_idx in range(min(3, len(_row))):
                    _label = _clean_label_supp(str(_row[_label_idx]))
                    # ★ 空标签直接跳过，避免走 else 分支时把 "" 拼进 _combined
                    if not _label:
                        continue

                    # ★ 尝试三种匹配：
                    #   1) 单元格本身直接命中
                    #   2) 本单元格 + 下一个非空单元格拼接后命中
                    #   3) 本单元格 + 下一个非空单元格 + 再下一个拼接后命中
                    _info = None
                    _matched_label = None
                    if _label in _code_to_info:
                        _info = _code_to_info[_label]
                        _matched_label = _label
                    else:
                        # 收集本行中该单元格之后的非空单元格文本
                        _tail_cells = [
                            str(c).strip()
                            for c in _row[_label_idx + 1 :]
                            if str(c).strip()
                        ]
                        for _take in (1, 2):
                            if len(_tail_cells) < _take:
                                break
                            _combined = _label + "".join(
                                _clean_label_supp(t) for t in _tail_cells[:_take]
                            )
                            if _combined in _code_to_info:
                                _info = _code_to_info[_combined]
                                _matched_label = _combined
                                break

                    if _info is None:
                        continue
                    # 收集该行后续所有数值，并从表头判断期间口径
                    _header = _rows[0] if _rows else []
                    _vals_with_period: list[tuple[float, str]] = []
                    for _c in range(_label_idx + 1, len(_row)):
                        _cell = str(_row[_c]).replace(",", "").replace("，", "").strip()
                        # ★ 只对资本明细的 4 个编码把 "-" 当 0；
                        #   其他指标（比如财险的寿险业务风险）"-" 表示
                        #   "业务上不适用"，仍然按缺失跳过。
                        _CAPITAL_ZERO_CODES = {
                            "CORE_T1_CAPITAL",
                            "CORE_T2_CAPITAL",
                            "ANC_T1_CAPITAL",
                            "ANC_T2_CAPITAL",
                        }
                        if not _cell:
                            continue
                        if _cell in {"-", "--", "—", "–", "/"}:
                            if _info["code"] in _CAPITAL_ZERO_CODES:
                                _v = 0.0
                            else:
                                continue
                        else:
                            try:
                                _v = float(_cell)
                            except (ValueError, TypeError):
                                continue
                        # ★ 期间判断优先级：表头列 > 行标签 > 默认"本季度末数"
                        #   行标签优先级是为了兼容"近三年平均投资收益率"这类纵向表
                        _row_label = ""
                        if _label_idx < len(_row):
                            _row_label = str(_row[_label_idx]).strip()
                        _period = (
                            _period_from_header(_header, _c)
                            or _period_from_row_label(_row_label)
                            or "本季度末数"
                        )
                        _vals_with_period.append((_v, _period))
                    if not _vals_with_period:
                        continue
                    # ★ 修复：从源表拿单位，不要直接沿用目标标准单位
                    _KNOWN_UNITS = {"元", "千元", "万元", "百元", "亿元", "十亿元"}
                    _raw_src_unit = str(getattr(_table, "unit", "") or "").strip()
                    _source_unit = (
                        _raw_src_unit if _raw_src_unit in _KNOWN_UNITS
                        else _info["unit"]
                    )
                    for _v, _period in _vals_with_period:
                        _new = {c: None for c in STANDARD_COLUMNS}
                        for _key in [
                            "公司", "原始公司名称", "标准公司名称", "公司统一编码",
                            "公司类型", "同业分类", "报告类型", "报告年度",
                            "报告季度", "报告期", "披露日期",
                        ]:
                            _new[_key] = _template.get(_key, "")
                        _new["一级模块"] = _info["module1"]
                        _new["二级模块"] = _info["module2"]
                        _new["指标编码"] = _info["code"]
                        _new["指标名称"] = _info["name"]
                        _new["期间口径"] = _dedup_period(_period)
                        _new["数值"] = _v
                        _new["单位"] = _source_unit   # ★ 改用源表单位
                        _new["数据类型"] = _info["dtype"]
                        _new["来源类型"] = "报告提取"
                        _new["指标属性"] = "披露"
                        _new["来源文件"] = _source_file
                        _new["来源工作表"] = _source_sheet
                        if _source_pages:
                            _new["来源页码"] = "、".join(str(p) for p in _source_pages)
                        _new["备注"] = (
                            f"补漏匹配：原始标签'{_row[_label_idx]}'"
                            f"（来源表：{_source_sheet}）"
                        )
                        _supp_rows.append(_new)
                    # 每个未匹配编码只从表格中匹配一次
                    # ★ 真正命中的 key 是 _matched_label，删除时用 pop 更稳妥，
                    #   即使 key 不存在也不会抛 KeyError
                    _code_to_info.pop(_matched_label, None)
                    break
        if _supp_rows:
            _supp_df = pd.DataFrame(_supp_rows).reindex(columns=STANDARD_COLUMNS)
            data = pd.concat([data, _supp_df], ignore_index=True)
            warnings.append(f"补漏匹配了 {len(_supp_rows)} 条未自动匹配的关键指标。")
    # ===== 补漏匹配结束 =====

    # ===== 事后修正：来源工作表名或指标名含"近三年"的行，期间统一为"近三年平均" =====
    if not data.empty:
        _nt_mask = (
            data["来源工作表"].astype(str).str.contains("近三年", na=False)
            | data["指标名称"].astype(str).str.contains("近三年", na=False)
        )
        if _nt_mask.any():
            _before = data.loc[_nt_mask, "期间口径"].copy()
            data.loc[_nt_mask, "期间口径"] = "近三年平均"
            _changed = int((_before != data.loc[_nt_mask, "期间口径"]).sum())
            if _changed:
                warnings.append(
                    f"已修正 {_changed} 条近三年投资收益率记录的期间口径为'近三年平均'。"
                )
    # ===== 事后修正结束 =====
    
    # ---------- 强制单位换算（兜底） ----------
    if not data.empty and not scoped_taxonomy.empty:
        # ★ 兼容 "标准单位" 和 "单位" 两种列名
        _unit_col = "标准单位" if "标准单位" in scoped_taxonomy.columns else "单位"
        standard_unit_map = {}
        for _, row in scoped_taxonomy.iterrows():
            code = str(row["指标编码"]).strip()
            unit = str(row.get(_unit_col, "")).strip()
            if code and unit:
                standard_unit_map[code] = unit

        # ★ 豁免名单：这些指标原始单位是"元"（或"元/股"、"元/车"），
        #   不是整表的"万元"，不做换算
        _UNIT_CONVERT_EXEMPT_CODES = {
            "EPS",                # 基本每股收益（元/股）
            "MOTOR_AVG_PREMIUM",  # 车险车均保费（元/车）
        }

        for idx, row in data.iterrows():
            code = str(row["指标编码"]).strip()
            dtype = str(row.get("数据类型", "")).strip()
            if dtype != "金额" or code not in standard_unit_map:
                continue

            # ★ 豁免名单：跳过单位换算
            if code in _UNIT_CONVERT_EXEMPT_CODES:
                continue

            target_unit = standard_unit_map[code]
            current_unit = str(row.get("单位", "")).strip()
            raw_value = row["数值"]

            # 单位已正确：不换算，也不改写备注
            if current_unit and current_unit == target_unit:
                continue

            # 单位为空：不猜量级，直接跳过并留痕
            if not current_unit:
                old_note = (
                    str(data.at[idx, "备注"])
                    if pd.notna(data.at[idx, "备注"]) else ""
                )
                note = "原始单位缺失，未换算"
                data.at[idx, "备注"] = f"{old_note}；{note}" if old_note else note
                continue

            converted, note = convert_amount_to_target_unit(
                raw_value,
                current_unit,
                target_unit,
                metric_code=code,
            )
            if converted is not None and isinstance(converted, (int, float)):
                data.at[idx, "数值"] = converted
                data.at[idx, "单位"] = target_unit
                old_note = (
                    str(data.at[idx, "备注"])
                    if pd.notna(data.at[idx, "备注"]) else ""
                )
                if note:
                    data.at[idx, "备注"] = f"{old_note}；{note}" if old_note else note
    # ---------- 单位换算结束 ----------

    # ★ 剔除“资本工具一览表”发行金额误挂到 ANC_T1_CAPITAL 的记录
    if not data.empty:
        bad_mask = (
            (data["指标编码"].astype(str).str.strip() == "ANC_T1_CAPITAL")
            & (pd.to_numeric(data["数值"], errors="coerce") == 1_000_000)
            & (data["来源工作表"].astype(str).str.contains("S02", na=False))
        )
        if bad_mask.any():
            data = data[~bad_mask].reset_index(drop=True)
            warnings.append("已剔除资本工具表发行金额误挂到附属一级资本的记录。")
            
    # ★ 去重：同一公司/报告期/指标/归一化期间只保留一条
    if not data.empty:
        # 临时加一列归一化期间，仅用于去重
        data["_期间口径_去重"] = data["期间口径"].map(_dedup_period)

        # 优先保留"规范期间名"的记录：本季度末数 > 上季度末数 > 下季度末预测数 > 其他
        _PERIOD_SORT_PRIORITY = {
            "本季度末数": 0,
            "上季度末数": 1,
            "下季度末预测数": 2,
            "本年累计数": 3,
            "近三年平均": 4,
        }
        data["_期间排序"] = data["_期间口径_去重"].map(
            lambda p: _PERIOD_SORT_PRIORITY.get(p, 99)
        )
        data = data.sort_values("_期间排序", kind="stable").drop(columns=["_期间排序"])

        dedup_keys = [
            "公司统一编码",
            "报告期",
            "指标编码",
            "_期间口径_去重",
            "来源工作表",     # ★ 新增
        ]
        existing_keys = [k for k in dedup_keys if k in data.columns]
        if existing_keys:
            before = len(data)
            data = (
                data.drop_duplicates(subset=existing_keys, keep="first")
                .reset_index(drop=True)
            )
            removed = before - len(data)
            if removed:
                warnings.append(f"去重移除了 {removed} 条同粒度重复记录。")

        # 去重完成后删掉临时列
        data = data.drop(columns=["_期间口径_去重"])

    diagnostic_frame = pd.DataFrame(diagnostics)
    summary = _target_summary(target_catalog, data)
    


    # ---------- 去重：同一公司/报告期/指标/期间/来源只保留一条 ----------
    if not data.empty:
        # ★ 修复：第一次去重已删掉 _期间口径_去重，这里必须重算，
        #   否则 effective dedup key 会丢掉"期间"维度，
        #   把同一指标的本季度末数/上季度末数/下季度预测数误合并。
        data["_期间口径_去重"] = data["期间口径"].map(_dedup_period)
        dedup_keys = [
            "公司统一编码",
            "报告期",
            "指标编码",
            "_期间口径_去重",
            "来源工作表",
        ]
        existing_keys = [k for k in dedup_keys if k in data.columns]
        if existing_keys:
            before = len(data)
            data = (
                data.drop_duplicates(subset=existing_keys, keep="first")
                .reset_index(drop=True)
            )
            removed = before - len(data)
            if removed:
                warnings.append(f"去重移除了 {removed} 条同粒度重复记录。")
        # ★ 修复：用完后删掉临时列，避免污染输出
        if "_期间口径_去重" in data.columns:
            data = data.drop(columns=["_期间口径_去重"])

    # ---------- 统一模块/单位/数据类型，以 target_catalog 为准 ----------
    if not data.empty and not target_catalog.empty:
        catalog = target_catalog.copy()
        catalog["_code"] = catalog["指标编码"].astype(str).str.strip()

        def _code_map(column: str) -> dict[str, str]:
            if column not in catalog.columns:
                return {}
            return dict(zip(catalog["_code"], catalog[column]))

        code_norm = data["指标编码"].astype(str).str.strip()
        for target_col, source_col in (
            ("一级模块", "一级模块"),
            ("二级模块", "二级模块"),
            ("单位", "标准单位"),
            ("数据类型", "数据类型"),
        ):
            code_to_value = _code_map(source_col)
            if not code_to_value:
                continue
            mapped = code_norm.map(code_to_value)
            # 仅当映射有值时覆盖，避免把已正确的行抹成空
            data[target_col] = mapped.where(mapped.notna(), data[target_col])
        data["指标编码"] = code_norm

    diagnostic_frame = pd.DataFrame(diagnostics)
    summary = _target_summary(target_catalog, data)
    missing_count = int((summary["填报状态"] == "未填报").sum())

    if missing_count:
        warnings.append(
            f"目标表中有 {missing_count} 个指标未从本次提取表格或计算依赖中取得数值。"
        )

    # ===== 新增：从 PDF 第一页文本提取注册资本 =====
    import re
    first_page_text = metadata.get("first_page_text", "")
    
    if first_page_text and "注册资本" in first_page_text and not data.empty:
        # 取数据中已有的公司名称
        company_name_col = "原始公司名称"
        if company_name_col not in data.columns:
            company_name_col = "公司"
        target_company = data[company_name_col].iloc[0] if len(data) > 0 else metadata.get("公司名称", "")
        
        capital_value = None
        matched_text = ""

        # ---- 第 1 步：优先匹配阿拉伯数字 ----
        # ★ 同样允许标签和值之间存在分隔
        patterns = [
            (r'注册资本[\s\S]{0,800}?(?:人民币)?\s*([\d,，]+(?:\.\d+)?)\s*万亿元', 10000 * 10000),
            (r'注册资本[\s\S]{0,800}?(?:人民币)?\s*([\d,，]+(?:\.\d+)?)\s*亿元',   10000),
            # ★ 新增：支持"11.2亿"（不带"元"），例如富邦财险
            (r'注册资本[\s\S]{0,800}?(?:人民币)?\s*([\d,，]+(?:\.\d+)?)\s*亿(?!元)', 10000),
            (r'注册资本[\s\S]{0,800}?(?:人民币)?\s*([\d,，]+(?:\.\d+)?)\s*万元',   1),
            # ★ 新增：支持"xx万"（不带"元"）
            (r'注册资本[\s\S]{0,800}?(?:人民币)?\s*([\d,，]+(?:\.\d+)?)\s*万(?!元)', 1),
            (r'注册资本[\s\S]{0,800}?(?:人民币)?\s*([\d,，]+(?:\.\d+)?)\s*元',     1 / 10000),
        ]
        for pat, scale in patterns:
            match = re.search(pat, first_page_text)
            if not match:
                continue
            raw_num = match.group(1).replace(",", "").replace("，", "")
            try:
                num = float(raw_num)
            except ValueError:
                continue
            matched_text = match.group(0)
            capital_value = num * scale
            break

        # ---- 第 2 步：阿拉伯数字没匹配到，尝试中文数字（如"二百一十亿元"）----
        # ★ 平安、人保的 PDF 都是双栏布局，文本层会把所有"标签"排完再排
        #   所有"值"，所以"注册资本："和"二百一十亿元人民币"之间可能隔着
        #   400~500 个字符。用 [\s\S]{0,800}? 允许它们之间存在分隔。
        if capital_value is None:
            # ★ 字符集补上财务大写
            _CN = r"零一二三四五六七八九十百千万亿两〇壹贰叁肆伍陆柒捌玖拾佰仟"
            cn_patterns = [
                (rf'注册资本[\s\S]{{0,800}}?(?:人民币)?\s*([{_CN}]+)\s*万亿元', 10000 * 10000),
                (rf'注册资本[\s\S]{{0,800}}?(?:人民币)?\s*([{_CN}]+)\s*亿元',   10000),
                (rf'注册资本[\s\S]{{0,800}}?(?:人民币)?\s*([{_CN}]+)\s*亿(?!元)', 10000),
                (rf'注册资本[\s\S]{{0,800}}?(?:人民币)?\s*([{_CN}]+)\s*万元',   1),
                (rf'注册资本[\s\S]{{0,800}}?(?:人民币)?\s*([{_CN}]+)\s*万(?!元)', 1),
                (rf'注册资本[\s\S]{{0,800}}?(?:人民币)?\s*([{_CN}]+)\s*元',     1 / 10000),
            ]
            for pat, scale in cn_patterns:
                match = re.search(pat, first_page_text)
                if not match:
                    continue
                cn_text = match.group(1)
                try:
                    num = _chinese_to_number(cn_text)
                except Exception:
                    continue
                if num <= 0:
                    continue
                matched_text = match.group(0)
                # 统一换算为万元
                capital_value = num * scale
                break
        
        if capital_value is not None:
            cols = data.columns.tolist()
            new_row = {col: None for col in cols}
            # 复制现有数据的一行作为模板，取关键字段
            template_row = data.iloc[0].to_dict() if len(data) > 0 else {}
            # ★ 必须复制这些字段，否则 STEP5 会因"报告类型为空"把这条记录筛掉
            for key in [
                "公司", "原始公司名称", "标准公司名称", "公司统一编码",
                "公司类型", "同业分类", "报告类型", "报告年度", "报告季度",
                "报告期", "披露日期", "导入批次",
            ]:
                new_row[key] = template_row.get(key, "")
            # 覆盖或设置注册资本专属字段
            new_row["公司"] = template_row.get("公司", target_company)
            new_row["原始公司名称"] = template_row.get("原始公司名称", target_company)
            new_row["标准公司名称"] = template_row.get("标准公司名称", target_company)
            new_row["公司统一编码"] = template_row.get("公司统一编码", f"AUTO_{target_company}")
            new_row["公司类型"] = template_row.get("公司类型", company_type)
            new_row["报告期"] = template_row.get("报告期", metadata.get("报告期", ""))
            new_row["一级模块"] = "关键偿付数据概览"
            new_row["二级模块"] = "资本使用效率"
            new_row["指标编码"] = "REGISTERED_CAPITAL"
            new_row["指标名称"] = "注册资本"
            new_row["期间口径"] = _dedup_period("期末数")   # → "本季度末数"
            new_row["数值"] = round(capital_value, 2)
            new_row["单位"] = "万元"
            new_row["数据类型"] = "金额"
            new_row["来源类型"] = "报告提取"
            new_row["来源文件"] = metadata.get("来源文件", "")
            new_row["来源页码"] = "1"
            new_row["指标属性"] = "披露"
            new_row["备注"] = f"从PDF第一页文本自动提取：{matched_text}"
            
            clean_row = {k: v for k, v in new_row.items() if k in cols}
            data = pd.concat([data, pd.DataFrame([clean_row])], ignore_index=True)
            warnings.append("已从PDF第一页文本自动提取注册资本（REGISTERED_CAPITAL）。")
    # ===== 注册资本提取结束 =====

    # ★ 注册资本是上面才追加进 data 的，之前的 summary 没包含它，
    #   这里重新算一遍，保证"填报汇总"里 REGISTERED_CAPITAL 显示"已填报"。
    summary = _target_summary(target_catalog, data)

    # ===== ★ 新增：S05 表期间口径整体错位修正 =====
    # 场景：某些 PDF 的 S05 表列序与表头识别不一致，导致该表所有行
    #       都被误标为"上季度末数"（例如京东安联 2025Q4）。
    # 修正规则：如果某公司 S05 表的所有记录都是"上季度末数"，
    #          且该公司其他表的记录都是"本季度末数"（没有上季度末数），
    #          则把 S05 表的期间改回"本季度末数"。
    #
    # ⚠ 已禁用（2026-09 补丁 P0）：紫金财产 2025Q4 的 S05 表被 AI 提取
    #   时列序整体识别反了，导致所有行都被标为"上季度末数"。这条规则会
    #   把它们统一改成"本季度末数"，让真实的"本季度末数"被覆盖、并在
    #   去重时被抹掉，最终只剩一条错误记录。禁用后，S05 表数据按 AI 的
    #   原始输出保留，交给分析师人工核对。
    if False:  # 原条件: if not data.empty and "来源工作表" in data.columns:
        # ★ 修复：S05 表可能叫 "最低资本表" 或 "最低资本明细"
        _s05_pattern = r"S05"   # 只匹配 S05
        _s05_series = data["来源工作表"].astype(str)
        _s05_mask = _s05_series.str.contains(_s05_pattern, na=False, case=False, regex=True)
        if _s05_mask.any():
            _corrections: list[tuple[object, str, int]] = []
            for _code, _grp in data.groupby("公司统一编码", sort=False):
                _grp_s05_mask = _grp["来源工作表"].astype(str).str.contains(
                    _s05_pattern, na=False, case=False, regex=True
                )
                _g_s05 = _grp[_grp_s05_mask]
                _g_other = _grp[~_grp_s05_mask]
                if _g_s05.empty:
                    continue
                _s05_periods = set(_g_s05["期间口径"].astype(str).str.strip())
                _other_periods = set(_g_other["期间口径"].astype(str).str.strip())
                if (
                    _s05_periods == {"上季度末数"}
                    and "本季度末数" in _other_periods
                    and "上季度末数" not in _other_periods
                ):
                    _indices = list(_g_s05.index)
                    data.loc[_indices, "期间口径"] = "本季度末数"
                    _corrections.append((_code, "本季度末数", len(_indices)))
            for _code, _new_period, _count in _corrections:
                warnings.append(
                    f"检测到 {_code} 的 S05 表期间口径整体被误标为'上季度末数'，"
                    f"已修正为'{_new_period}'（{_count} 条记录）。"
                )
                
    # ===== ★ 新增：剔除非法期间口径 =====
    # 合法期间 = PERIOD_ALIASES 的所有 value + 少数带后缀的变体。
    # 任何不在此白名单、且明显是"表头噪声"的期间（如"附注编号""序号""备注"）直接丢弃。
    if not data.empty:
        _VALID_PERIODS = {
            # 主表/实际资本/最低资本
            "本季度末数", "上季度末数", "下季度末预测数",
            "本季度数", "上季度数",
            "本年累计数",
            "近三年平均",
            "期末数", "期初数",
            # 认可资产/负债表拆分列
            "账面价值", "非认可", "认可价值",
        }
        _NOISE_PERIOD_KEYS = ("附注", "序号", "备注", "编号", "行次")
        _p = data["期间口径"].astype(str).str.strip()
        _is_noise = _p.str.contains("|".join(_NOISE_PERIOD_KEYS), na=False)
        # 带 "-账面价值/-非认可" 后缀的视为合法（如 "本季度末数-账面价值"）
        _base_p = _p.str.split("-").str[0]
        _is_legal = _base_p.isin(_VALID_PERIODS)
        _drop_mask = _is_noise | (~_is_legal & ~_p.eq(""))
        if _drop_mask.any():
            _n = int(_drop_mask.sum())
            data = data[~_drop_mask].reset_index(drop=True)
            warnings.append(f"已剔除 {_n} 条期间口径非法的记录（附注编号/序号等表头噪声）。")
    # ===== 剔除结束 =====
    
    # ===== S05 表期间口径修正结束 =====

    return Step3StandardizationResult(
        data=data,
        target_catalog=target_catalog.copy(),
        diagnostics=diagnostic_frame,
        target_summary=summary,
        warnings=tuple(warnings),
    )



def _format_workbook(writer: pd.ExcelWriter) -> None:
    for worksheet in writer.book.worksheets:
        worksheet.freeze_panes = "A2"
        worksheet.auto_filter.ref = worksheet.dimensions
        for column_cells in worksheet.iter_cols():
            values = [str(cell.value or "") for cell in column_cells[:200]]
            width = min(max(max((len(value) for value in values), default=0) + 2, 10), 32)
            worksheet.column_dimensions[column_cells[0].column_letter].width = width


def target_template_workbook_bytes(
    target_catalog: pd.DataFrame,
    profile_name: str,
) -> bytes:
    output = io.BytesIO()
    instructions = pd.DataFrame(
        [
            ("用途", f"{profile_name} STEP3 目标指标配置；输出可直接进入 STEP5。"),
            ("选择指标", "在“指标清单”中将不需要的指标“启用”改为“否”，不要修改指标编码。"),
            ("窄表字段", "“标准数据”页仅展示 STEP3/STEP5 共用的 14 个标准窄表字段。"),
            ("单位口径", "金额按标准单位万元输出；百分比保留披露百分数，例如 130.98 表示 130.98%。"),
        ],
        columns=["项目", "说明"],
    )
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        instructions.to_excel(writer, sheet_name="使用说明", index=False)
        target_catalog.reindex(columns=TARGET_TEMPLATE_COLUMNS).to_excel(
            writer,
            sheet_name=TARGET_SHEET_NAME,
            index=False,
        )
        pd.DataFrame(columns=NARROW_TABLE_COLUMNS).to_excel(
            writer,
            sheet_name=STANDARD_SHEET_NAME,
            index=False,
        )
        _format_workbook(writer)
    return output.getvalue()

def result_workbook_bytes(result: Step3StandardizationResult) -> bytes:
    output = io.BytesIO()
    data_out = result.data.copy()
    if "备注" in data_out.columns:
        # 同一指标同一期间多条时，追加来源标记，避免下游当成完全重复
        dup_mask = data_out.duplicated(
            subset=["公司统一编码", "报告期", "指标编码", "期间口径"],
            keep=False,
        )
        if dup_mask.any():
            data_out.loc[dup_mask, "备注"] = (
                data_out.loc[dup_mask, "备注"].fillna("").astype(str)
                + "（多来源："
                + data_out.loc[dup_mask, "来源工作表"].astype(str)
                + "）"
            )
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        write_standard_workbook_sheets(writer, result.data)
        result.target_summary.to_excel(writer, sheet_name="填报汇总", index=False)
        if result.diagnostics.empty:
            pd.DataFrame(columns=["状态", "原因"]).to_excel(
                writer,
                sheet_name="匹配诊断",
                index=False,
            )
        else:
            result.diagnostics.to_excel(writer, sheet_name="匹配诊断", index=False)
        _format_workbook(writer)
    return output.getvalue()
