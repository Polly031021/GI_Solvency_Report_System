"""从 app.py 抽离的纯函数工具集：PDF 逐页提取、单位嗅探、类型净化。

本模块不依赖 Streamlit，可被 app.py 与命令行批处理脚本（batch_run.py）共用。
"""
from __future__ import annotations

import base64
import io
import re
from types import SimpleNamespace

import fitz
import pandas as pd
import pdfplumber
from openai import OpenAI


# ============================================================
# 通用文本工具
# ============================================================
_LABEL_NOISE_RE = re.compile(r"[\d,，.。%％:：;；()（）\[\]【】\s\-—_*+=/\\|]+")


def clean_label(s) -> str:
    """剥离标签里的数字、标点、空白，只留中文字符，用于匹配。"""
    return _LABEL_NOISE_RE.sub("", str(s or "")).strip()


def get_report_unit(text):
    """从文本中嗅探金额单位"""
    if not text:
        return None
    m1 = re.search(r'(?:金额)?单位[：:\s]*((?:人民币)?[元十百千万亿]+)', text)
    if m1:
        return m1.group(1).strip()
    m2 = re.search(r'(人民币[十百千万亿]+元)', text)
    if m2:
        return m2.group(1).strip()
    if "人民币元" in text:
        return "人民币元"
    return None


def normalize_field(s):
    if not isinstance(s, str):
        return ""
    s = s.replace('\xa0', '').replace('\u3000', '').replace(' ', '').strip()
    s = s.replace('（', '(').replace('）', ')')
    return s


# ============================================================
# 兜底表格对象
# ============================================================
class FlexibleTable(SimpleNamespace):
    """兜底表格对象：下游 standardizer/normalizer 读取任何未显式赋值的
    元数据字段（unit_records、header_records 等）时返回安全默认值，
    避免 AttributeError 反复出现。"""

    _LIST_FIELDS = {
        "unit_records", "header_records", "footer_records",
        "note_records", "cross_page_records", "keyword_records",
        "warnings", "sources", "pages", "keywords",
    }
    _STR_FIELDS = {"strategy", "evidence", "note", "unit", "sheet_name", "remark"}
    _NUM_FIELDS = {"quality_score", "score", "confidence", "row_count"}

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        if name == "source_pages":
            page = getattr(self, "page", None)
            return [page] if page else []
        if name in self._LIST_FIELDS or name.endswith(
            ("records", "_rows", "items", "_list")
        ):
            return []
        if name in self._STR_FIELDS or name.endswith(("name", "text", "label")):
            return ""
        if name in self._NUM_FIELDS or name.endswith("count"):
            return 0.0
        return None


# ============================================================
# 单位嗅探与万元换算
# ============================================================
_UNIT_PATTERNS = [
    (re.compile(r"单位[：:]\s*万元"), "万元"),
    (re.compile(r"单位[：:]\s*元或%"), "元"),
    (re.compile(r"单位[：:]\s*元(?![或万])"), "元"),
    (re.compile(r"单位[：:]\s*千元"), "千元"),
    (re.compile(r"单位[：:]\s*百元"), "百元"),
    (re.compile(r"单位[：:]\s*亿元"), "亿元"),
    (re.compile(r"金额单位[：:]\s*元"), "元"),
]


def detect_table_unit(rows) -> str | None:
    """扫描表格所有单元格文本，返回最后一次出现的'单位：X'。

    支持多种写法：
      - "单位：元" / "单位:元" / "单位 元"
      - "单位：人民币元" / "单位：人民币万元"
      - "（单位：元）" / "（单位：人民币元）"
      - "（万元）" / "(万元)" / "（亿元）" 等嵌入式写法
      - "金额单位：元" / "货币单位：元" 等带修饰写法
    """
    import re as _re_unit

    # ★ 修复：单位关键字 + 冒号/空格 + 单位名，一次正则匹配所有变体
    #   允许："单位" 或 "金额单位" 或 "货币单位" 或 "计量单位"
    #   允许冒号："：" ":" "  " 或 无
    #   单位名：人民币 / RMB / 空
    #   单位：元 / 千元 / 万元 / 百元 / 亿元 / 十亿元
    _UNIT_REGEX = _re_unit.compile(
        r"(?:金额|货币|计量)?单位[\s:：]*"
        r"(?:人民币|RMB|rmb)?\s*"
        r"(十亿元|亿元|万元|千元|百元|元)"
    )

    # ★ 修复：不再只扫前 5 行，改为全量扫描（表格一般不会超过几百行，性能可接受）
    found = None
    for row in (rows or []):
        for cell in row:
            if not isinstance(cell, str):
                continue
            # 1) 优先匹配 "单位：X" 类写法
            m = _UNIT_REGEX.search(cell)
            if m:
                found = m.group(1)
            # 2) 兼容 _UNIT_PATTERNS 里已有的正则（保留原有能力）
            for pat, unit in _UNIT_PATTERNS:
                if pat.search(cell):
                    found = unit
            # 3) 兼容嵌入式写法（如"车险车均保费（元）"）
            if "（万元）" in cell or "(万元)" in cell:
                found = "万元"
            elif "（亿元）" in cell or "(亿元)" in cell:
                found = "亿元"
            elif "（千元）" in cell or "(千元)" in cell:
                found = "千元"
            elif "（百元）" in cell or "(百元)" in cell:
                found = "百元"
            elif "（元）" in cell or "(元)" in cell:
                found = "元"
    return found

# 匹配 "单位：元" / "单位：万元" / "（单位：元或%）" 等
_UNIT_TEXT_RE = re.compile(
    r"单位[：:\s]*((?:人民币)?(?:亿元|万元|千元|百元|元))(?:或[%％])?"
)

_UNIT_PRIORITY = {"亿元": 5, "万元": 4, "千元": 3, "百元": 2, "元": 1}


def sniff_unit_from_text(text: str) -> str | None:
    """从 PDF 原始文本（pdfplumber 提取的 raw_text）中嗅探金额单位。

    用途：当 detect_table_unit 在表格单元格里扫不到单位时，从表格外的
    段落文字（如"（单位：元或%）"）兜底。返回 None 表示未找到。
    """
    if not text or not isinstance(text, str):
        return None
    matches = _UNIT_TEXT_RE.findall(text)
    if not matches:
        return None
    return max(matches, key=lambda u: _UNIT_PRIORITY.get(u, 0))

_VALUE_HEADER_HINTS = (
    '本季度末', '上季度末', '下季度末',
    '本季末', '上季末', '下季末',
    '本期末', '上期末',
    '期末', '期初', '期末数', '期初数',
    '本季度数', '上季度数',
    '本年累计', '本年度累计', '本年数', '本年度数',
    '近三年平均', '数值', '金额', '本期', '上期',
    '季度预测数', '季度预测', '下季度预测数', '下季度预测',
    '情景',          # ★ 新增：匹配“基本情景下的下季度预测数 / 基本情景下的下 / 压力情景下的预测数”
)

_NUM_RE = re.compile(
    r"^\(?\s*-?\s*\d{1,3}(?:,\d{3})+(?:\.\d+)?\s*\)?$"
    r"|^\(?\s*-?\s*\d+(?:\.\d+)?\s*\)?$"
)


def _convert_amount_cell(text, factor) -> str:
    """纯数字金额按 factor 缩放；带%或解析失败的原样返回。"""
    s = (text or "").strip().replace("（", "(").replace("）", ")").replace("，", ",")
    if not s or "%" in s or "％" in s:
        return text
    if not _NUM_RE.match(s):
        return text
    neg = s.startswith("(") and s.endswith(")")
    core = s[1:-1].replace(",", "").strip() if neg else s.replace(",", "")
    try:
        val = float(core) * factor
    except ValueError:
        return text
    out = ("%.6f" % val).rstrip("0").rstrip(".")
    return f"({out})" if neg else out


_RATIO_LABEL_MARKERS = (
    '%', '％', '倍', '占比',
    '每股收益', '车均保费', '每股', '车均',
    # ★ 修复：把笼统的"率"拆成具体比率词，避免"利率风险""汇率风险"被误判
    '赔付率', '费用率', '成本率', '收益率', '比率', '充足率',
    '增长率', '增幅', '降幅', '降速', '同比', '环比',
)


def convert_table_rows_to_wan(rows, unit):
    """按表头识别金额列，把源单位换算为万元；比率行与%单元格不换算（防混合表误伤）。"""
    if not rows or not unit or unit == "万元":
        return rows
    factor = {"元": 1 / 10000, "千元": 1 / 10}.get(unit)
    if not factor:
        return rows
    header = rows[0]
    value_cols = [
        i for i, h in enumerate(header)
        if isinstance(h, str) and any(k in h for k in _VALUE_HEADER_HINTS)
    ]
    if not value_cols:
        return rows
    out = [list(r) for r in rows]
    for r in out[1:]:
        label = str(r[0]) if r else ""
        if any(m in label for m in _RATIO_LABEL_MARKERS):
            continue
        for i in value_cols:
            if i < len(r):
                r[i] = _convert_amount_cell(str(r[i]), factor)
    return out


# ============================================================
# 类型净化
# ============================================================
def sanitize_scalar(value):
    """任意值 → 安全标量；None 和数值保持原样，list/dict 转字符串。"""
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, (list, tuple, set)):
        return "、".join(str(sanitize_scalar(item)) for item in value)
    if isinstance(value, dict):
        return "；".join(f"{k}={sanitize_scalar(v)}" for k, v in value.items())
    return str(value)


def sanitize_tree(obj):
    """递归净化 dict / SimpleNamespace，把内部所有 list 字段改为字符串。"""
    if isinstance(obj, dict):
        return {k: sanitize_tree(v) for k, v in obj.items()}
    if hasattr(obj, "__dict__"):
        for attr, value in vars(obj).items():
            if callable(value):
                continue
            setattr(obj, attr, sanitize_tree(value))
        return obj
    return sanitize_scalar(obj)


# ============================================================
# PDF 视觉提取引擎
# ============================================================
def extract_single_page_vision(pdf_bytes, page_num, expected_name, api_key, base_url, model_name):
    """将 PDF 页面转为高清图片，使用视觉大模型提取表格"""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        if page_num < 1 or page_num > len(doc):
            return None, ""
        page = doc.load_page(page_num - 1)

        zoom_matrix = fitz.Matrix(3.0, 3.0)
        pix = page.get_pixmap(matrix=zoom_matrix, alpha=False)
        img_data = pix.tobytes("png")
        base64_image = base64.b64encode(img_data).decode('utf-8')
        doc.close()

        prompt = f"""你是一个四大会计师事务所的资深财险数字化审计专家。
我为你提供了一张【保险公司年报的单页高清截图】，目标是精准提取：【{expected_name}】。
表格中的【数字是由图片构成的】，请发挥强大的视觉识别能力，提取所有文字和表格。
【强制要求】：
1. 所有的科目名、数字金额之间，必须被 "|" 隔开。严禁使用连续空格。
2. 精准对齐多级表头！如果有留白单元格，必须用 "|" 占位补齐！
3. 绝不能漏掉任何一行数据，保留金额里的逗号和括号。
4. 纯文本输出，不要使用 Markdown 代码块。不需要输出 SHEET_NAME 标签，直接排版。"""

        client = OpenAI(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_image}"}}
                    ]
                }
            ],
            temperature=0.0
        )

        result_text = response.choices[0].message.content.strip()
        result_text = re.sub(r'^```(csv|text)?\n?', '', result_text, flags=re.MULTILINE).replace("```", "").strip()

        lines = result_text.split('\n')
        parsed_data = []
        max_cols = 0
        for row in lines:
            clean_row = row.strip()
            if not clean_row:
                continue
            if re.match(r'^[\s\|-]+$', clean_row) and '-' in clean_row:
                continue
            if clean_row.startswith('|'):
                clean_row = clean_row[1:]
            if clean_row.endswith('|'):
                clean_row = clean_row[:-1]
            cols = [col.strip() for col in clean_row.split('|')] if '|' in clean_row else [clean_row]
            parsed_data.append(cols)
            max_cols = max(max_cols, len(cols))

        for row in parsed_data:
            if len(row) < max_cols:
                row.extend([''] * (max_cols - len(row)))

        return pd.DataFrame(parsed_data), "【提示：该页已使用 OCR 视觉引擎处理，原文本为图片结构】"

    except Exception as e:
        return None, f"图像处理失败: {str(e)}"


# ============================================================
# PDF 文本提取引擎
# ============================================================
def extract_single_page(pdf_bytes, page_num, expected_name, api_key, base_url, model_name):
    """使用 pdfplumber 提取文本，并用 LLM 转换为结构化数据"""
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            if page_num < 1 or page_num > len(pdf.pages):
                return None, ""
            page = pdf.pages[page_num - 1]
            raw_text = page.extract_text(layout=True)

        if not raw_text:
            return None, ""

        prompt = f"""你是一个四大会计师事务所的资深财险数字化审计专家。
当前任务：提取【{expected_name}】。
请将以下纯文本中的【段落文字】原样保留为一行，将【财务表格】转化为用 "|" 严格分隔的标准网格格式。
【强制要求】：
1. 所有的科目名、附注编号、数字金额之间，必须被 "|" 隔开。严禁使用连续空格。
2. 精准对齐多级表头！如果某个单元格是空的，必须用 "|" 占位补齐！
3. 绝不能漏掉任何一行数据，保留金额里的逗号和括号。
4. 纯文本输出，不要使用 Markdown 代码块。不需要输出 SHEET_NAME，直接输出转化后的数据。

以下是原始文本，请开始输出：
{raw_text}"""

        client = OpenAI(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0
        )

        result_text = response.choices[0].message.content.strip()
        result_text = re.sub(r'^```(csv|text)?\n?', '', result_text, flags=re.MULTILINE)
        result_text = result_text.replace("```", "").strip()

        lines = result_text.split('\n')
        parsed_data = []
        max_cols = 0
        for row in lines:
            clean_row = row.strip()
            if not clean_row:
                continue
            if re.match(r'^[\s\|-]+$', clean_row) and '-' in clean_row:
                continue
            if clean_row.startswith('|'):
                clean_row = clean_row[1:]
            if clean_row.endswith('|'):
                clean_row = clean_row[:-1]
            if '|' not in clean_row:
                cols = [clean_row]
            else:
                cols = [col.strip() for col in clean_row.split('|')]
            parsed_data.append(cols)
            max_cols = max(max_cols, len(cols))

        for row in parsed_data:
            if len(row) < max_cols:
                row.extend([''] * (max_cols - len(row)))

        return pd.DataFrame(parsed_data), raw_text

    except Exception as e:
        return None, f"提取失败: {str(e)}"