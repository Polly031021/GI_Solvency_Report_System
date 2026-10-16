import re

# ★ 新增这两个 import：append_registered_capital 需要 pandas，extract_registered_capital_from_pdf 需要 fitz
import fitz
import pandas as pd

CN_DIGITS = {
    # 小写
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    # ★ 财务大写（注册资本、合同金额等正式文本里几乎都用大写）
    "壹": 1, "贰": 2, "叁": 3, "肆": 4, "伍": 5,
    "陆": 6, "柒": 7, "捌": 8, "玖": 9,
    # ★ "〇" 偶尔出现，当 0 处理
    "〇": 0,
}
CN_UNITS = {
    # 小写
    "十": 10, "百": 100, "千": 1000,
    # ★ 财务大写
    "拾": 10, "佰": 100, "仟": 1000,
}

def _chinese_to_number(text: str) -> float | None:
    """中文数字转阿拉伯数字，支持"万/亿"节权位和财务大写。

    例：
      "叁拾亿零玖佰肆拾壹万陆仟" → 3_009_416_000
      "伍拾伍亿"              → 5_500_000_000
      "叁佰万"                → 3_000_000
      "壹佰零伍"              → 105
    """
    text = str(text).strip()
    if not text:
        return None
    # 纯阿拉伯数字直接返回
    try:
        return float(text.replace(",", ""))
    except ValueError:
        pass

    total = 0            # 已累加的"亿"部分
    section = 0          # 当前节（"万"以下）
    current = 0          # 当前数字字符
    for ch in text:
        if ch in CN_DIGITS:
            if ch in {"零", "〇"}:
                # ★ "零"只作占位，不清空 current
                continue
            current = CN_DIGITS[ch]
        elif ch in CN_UNITS:
            unit = CN_UNITS[ch]
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
            # 遇到不认识的字符返回 None，避免半截解析
            return None
    return float(total + section + current)

    
def parse_capital_to_wan(raw: str) -> float | None:
    """把"注册资本"后面的值解析为万元。

    关键修复：
      1) 一次性匹配"完整数字串 + 单位"，让 _chinese_to_number 处理整个
         "叁拾亿零玖佰肆拾壹万陆仟"，而不是被"叁拾亿"的第一个"亿"截断。
      2) 数字字符集里包含"亿"和"万"，正则贪婪匹配会把整个中文数字吞完。
      3) 单位按长度倒序匹配，避免"亿元"被"元"抢先。
    """
    text = str(raw).strip()
    text = text.replace(",", "").replace("，", "").replace(" ", "")
    text = text.replace("人民币元", "元").replace("人民币", "")

    # 数字：阿拉伯数字 或 中文数字（含财务大写、亿、万）
    _NUM = (
        r"[0-9]+(?:\.[0-9]+)?"
        r"|[零一二两三四五六七八九十百千万亿"
        r"〇壹贰叁肆伍陆柒捌玖拾佰仟]+"
    )

    # 单位 → 换算到"万元"的倍数
    _UNIT_SCALE = {
        "万亿元": 100_000_000,   # 1万亿元 = 1e12元 = 1e8万元
        "亿元":   10_000,        # 1亿元 = 1e8元 = 1e4万元
        "万元":   1,
        "千元":   0.1,
        "百元":   0.01,
        "元":     0.0001,
        "万亿":   100_000_000,
        "亿":     10_000,
        "万":     1,
    }
    # 单位按长度倒序，避免 "亿元" 被 "元" 抢先
    _units_pattern = "|".join(
        sorted(_UNIT_SCALE.keys(), key=len, reverse=True)
    )

    # 一次性匹配：完整数字 + 单位
    m = re.search(rf"({_NUM})({_units_pattern})", text)
    if m:
        num_str = m.group(1)
        unit = m.group(2)
        num = _chinese_to_number(num_str)
        if num is not None:
            return num * _UNIT_SCALE[unit]

    # 兜底：只有纯阿拉伯数字 + "元"（理论上上面已覆盖）
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)元", text)
    if m:
        try:
            return float(m.group(1)) / 10000
        except ValueError:
            return None

    return None


# ★ 新增：子公司关键词列表（模块级常量）
# 用途：在全文扫描"注册资本"时，跳过描述子公司/销售公司/参股公司的那一行，
#      避免把子公司的注册资本误当成本公司注册资本。
_SUBSIDIARY_HINTS = (
    "子公司", "销售公司", "参股公司", "下属公司", "全资子公司",
    "控股子公司", "保险经纪", "保险销售", "保险代理",
    "资产管理有限公司", "投资有限公司", "实业有限公司",
)


# ★ 新增：判断"注册资本"命中处附近是否出现子公司提示词
def _is_subsidiary_capital_line(
    text: str, match_start: int, match_end: int,
) -> bool:
    """判断"注册资本"命中处前后 60 字符内是否出现子公司关键词。

    取前后各 60 个字符作为上下文窗口——足够覆盖"销售公司注册资本"这种
    相邻词，又不会误伤远距离的"子公司"字样。
    """
    ctx_start = max(0, match_start - 60)
    ctx_end = min(len(text), match_end + 60)
    context = text[ctx_start:ctx_end]
    return any(hint in context for hint in _SUBSIDIARY_HINTS)


def extract_registered_capital_from_text(pdf_text: str) -> float | None:
    """从 PDF 全文文本中提取注册资本，统一返回万元。找不到返回 None。

    ★ 修复：
      1) 优先只在前 3000 字符（公司简介区域）匹配；
      2) 全文扫描时跳过包含子公司关键词的段落。
    """
    if not pdf_text:
        return None

    patterns = [
        r"注册资本[（(]?[^：:\n]*[）)]?\s*[:：]\s*([^\n]+)",
        r"注册资本[（(]?[^：:\n]*[）)]?\s*\n\s*([^\n]+)",
        r"注册资本[（(]?[^：:\n]*[）)]?\s*[:：]?\s*([^\n]+)",
    ]

    # ---------- 第一步：只在前 3000 字符里找（公司简介区域） ----------
    head_text = pdf_text[:3000]
    for pat in patterns:
        for m in re.finditer(pat, head_text):
            if _is_subsidiary_capital_line(head_text, m.start(), m.end()):
                continue
            value = parse_capital_to_wan(m.group(1))
            if value is not None:
                return value

    # ---------- 第二步：全文找，但跳过子公司段落 ----------
    for pat in patterns:
        for m in re.finditer(pat, pdf_text):
            if _is_subsidiary_capital_line(pdf_text, m.start(), m.end()):
                continue
            value = parse_capital_to_wan(m.group(1))
            if value is not None:
                return value

    return None


# ============================================================
# ★ 以下为新增：从 PDF 字节流提取 + 追加到标准窄表
#   之前这两个函数丢了，导致 batch_processor 里 
#   "cannot import name 'append_registered_capital'" 报错。
# ============================================================

def extract_registered_capital_from_pdf(pdf_bytes: bytes) -> float | None:
    """从 PDF 字节流中提取注册资本（作为 pdf_text 缺失时的 fallback）。"""
    if not pdf_bytes:
        return None
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        text = "\n".join((page.get_text() or "") for page in doc)
    finally:
        doc.close()
    return extract_registered_capital_from_text(text)


def append_registered_capital(
    standard_data: pd.DataFrame,
    *,
    pdf_bytes: bytes | None = None,
    pdf_text: str | None = None,
    pdf_name: str = "",
    company_name: str = "",
    report_period: str = "",
    company_type: str = "财险",
) -> pd.DataFrame:
    """如果标准窄表中没有 REGISTERED_CAPITAL，则自动提取并追加一行。

    优先使用 pdf_text；如果没有再调用 pdf_bytes。
    """
    if standard_data is None or standard_data.empty:
        return standard_data

    # 已有 REGISTERED_CAPITAL 就不重复追加
    if "指标编码" in standard_data.columns:
        if (standard_data["指标编码"].astype(str) == "REGISTERED_CAPITAL").any():
            return standard_data

    cap_wan = None
    if pdf_text:
        cap_wan = extract_registered_capital_from_text(pdf_text)
    if cap_wan is None and pdf_bytes:
        cap_wan = extract_registered_capital_from_pdf(pdf_bytes)

    if cap_wan is None:
        return standard_data

    # 从 report_period 解析出 报告年度 / 报告季度
    year: int | str = ""
    quarter: str = ""
    _period_text = str(report_period or "").strip()
    _m = re.match(r"^(20\d{2})(Q[1-4])$", _period_text, re.IGNORECASE)
    if _m:
        year = int(_m.group(1))
        quarter = _m.group(2).upper()

    # 取标准数据里首行作为"字段参照行"，继承公司身份/报告期/批次等字段
    ref_row = standard_data.iloc[0] if not standard_data.empty else None

    def _pick(col: str, default=""):
        if ref_row is not None and col in ref_row.index:
            v = ref_row[col]
            if pd.notna(v) and str(v).strip() not in {"", "nan", "None", "<NA>"}:
                return v
        return default

    new_row = {
        # ---- 公司身份字段：全部继承参照行 ----
        "公司": _pick("公司", company_name or company_type),
        "原始公司名称": _pick("原始公司名称", company_name or company_type),
        "标准公司名称": _pick("标准公司名称", company_name or company_type),
        "公司统一编码": _pick("公司统一编码", ""),
        "公司类型": _pick("公司类型", company_type),
        "同业分类": _pick("同业分类", ""),

        # ---- 报告期字段：优先参照行，其次从 report_period 解析 ----
        "报告类型": _pick("报告类型", ""),
        "报告年度": _pick("报告年度", year),
        "报告季度": _pick("报告季度", quarter),
        "报告期": _pick("报告期", report_period),
        "披露日期": _pick("披露日期", ""),

        # ---- 指标本体 ----
        "一级模块": "关键偿付数据概览",
        "二级模块": "资本使用效率",
        "行次": "",
        "指标编码": "REGISTERED_CAPITAL",
        "指标名称": "注册资本",
        "期间口径": "本季度末数",
        "数值": round(cap_wan, 2),
        "单位": "万元",
        "数据类型": "金额",
        "是否预测": "否",
        "来源类型": "报告提取",
        "指标属性": "披露",

        # ---- 溯源字段：能继承就继承 ----
        "来源页码": "1",
        "原始披露值": "",
        "备注": "从PDF文本自动提取注册资本并换算为万元",
        "来源文件": pdf_name or _pick("来源文件", ""),
        "来源工作表": _pick("来源工作表", ""),
        "导入批次": _pick("导入批次", ""),
        "计算逻辑": "",
    }

    # 补齐 standard_data 里其他列（避免 NaN 引起下游报错）
    for col in standard_data.columns:
        if col not in new_row:
            new_row[col] = ""

    return pd.concat(
        [standard_data, pd.DataFrame([new_row])],
        ignore_index=True,
    )
