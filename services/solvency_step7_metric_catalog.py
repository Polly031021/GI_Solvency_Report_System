from __future__ import annotations

"""STEP7 指标需求目录：统一维护公司报告真正需要的指标、图表映射与单位口径。

这个模块是 STEP7 指标清单的唯一代码来源：
1. step7_metric_requirement_catalog 生成“指标需求清单”；
2. step7_module_metric_rows 生成“图表-指标映射”；
3. build_step7_metric_catalog_workbook_bytes 生成可维护的 Excel；
4. STEP3 的指标缺口检查也复用同一份目录，避免两处口径不一致。

维护时只需要改 STEP7 注释表或本文件顶部的常量，然后重新生成
config/Step7_指标需求清单.xlsx。
"""

import io
from pathlib import Path

import pandas as pd
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from .solvency_dataset_adapter import supported_metric_catalog
from .solvency_metric_registry import DERIVED_METRICS
from .step7_excel_config import Step7Module, load_step7_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STEP7_TEMPLATE = PROJECT_ROOT / "templates" / "Step7-偿付能力公司报告展示模块.xlsx"
DEFAULT_STEP7_CATALOG_PATH = PROJECT_ROOT / "config" / "Step7_指标需求清单.xlsx"
DEFAULT_TAXONOMY_PATH = PROJECT_ROOT / "config" / "solvency_taxonomy.xlsx"

# 图表代码里直接写死、Excel“指标编码”列没有列出的指标。
# 目前只有“关键偿付数据概览”表：dashboard_components.render_key_solvency_overview
# 固定读取以下编码。
IMPLICIT_CHART_METRICS: dict[str, tuple[str, ...]] = {
    # 量化风险堆叠图在 renderer/specs 中还读取以下构成项与占比标签，
    # 模板的“指标编码”列只列了非寿险/市场/信用三项。
    "量化风险最低资本构成": (
        "INSURANCE_RISK_CAPITAL",
        "QUANT_RISK_DIVERSIFICATION_EFFECT",
        "CONTRACT_LOSS_ABSORPTION_EFFECT",
        "LIFE_INSURANCE_RISK_TO_QUANT_CAPITAL",
        "NON_LIFE_INSURANCE_RISK_TO_QUANT_CAPITAL",
        "MARKET_RISK_TO_QUANT_CAPITAL",
        "CREDIT_RISK_TO_QUANT_CAPITAL",
        "DIVERSIFICATION_EFFECT_TO_QUANT_CAPITAL",
        "LOSS_ABSORPTION_TO_QUANT_CAPITAL",
    ),
    "关键偿付数据概览": (
        "CORE_SOLVENCY_RATIO",
        "COMBINED_SOLVENCY_RATIO",
        "ACTUAL_CAPITAL",
        "POLICY_SURPLUS_CORE_T1",
        "POLICY_SURPLUS_CORE_T2",
        "POLICY_SURPLUS_ANC_T1",
        "POLICY_SURPLUS_ANC_T2",
        "POLICY_SURPLUS_CORE_TO_CORE_CAPITAL",
        "MARKET_RISK_TO_QUANT_CAPITAL",
        "LIFE_INSURANCE_RISK_TO_QUANT_CAPITAL",
        "RECOGNIZED_LIABILITIES",
    ),
}

# 图表真正优先读取的期间口径。仅作为缺填模板的“建议期间”，
# 用户可以在补录表里修改；STEP7 自身会按 本年累计数 > 本季度数 >
# 本季度末数 的顺序选取可用值。
EXPECTED_PERIODS: dict[str, str] = {
    "SIGNED_PREMIUM": "本年累计数",
    "SIGNED_PREMIUM_MOTOR": "本年累计数",
    "NET_PROFIT": "本年累计数",
    "TOTAL_ASSETS": "本季度末数",
    "COMBINED_RATIO": "本年累计数",
    "LOSS_RATIO": "本年累计数",
    "EXPENSE_RATIO": "本年累计数",
    "INVESTMENT_RETURN": "本年累计数",
    "PREMIUM_GROWTH": "本年累计数",
    "ROE": "本年累计数",
}

DEFAULT_AMOUNT_PERIOD = "本季度末数"

# 不纳入“缺失指标补录表”的指标：派生指标由系统计算，校验/文本指标不填数值。
_SKIP_FILL_DATA_TYPES = {"文本", "校验"}
_SKIP_FILL_CODES = {
    "POLICY_SURPLUS_CORE_BAND",
    "FEATURE_FACTOR_CHECK",
}

_CATALOG_COLUMNS = [
    "序号",
    "指标编码",
    "指标名称",
    "一级模块",
    "二级模块",
    "标准单位",
    "数据类型",
    "指标属性",
    "计算逻辑",
    "是否派生",
    "Step7使用图表数",
    "Step7使用图表",
    "图表是否要求全部指标",
    "建议期间口径",
    "补录方式",
    "维护说明",
]

_MAPPING_COLUMNS = [
    "模块序号",
    "模块ID",
    "一级分类",
    "二级分类",
    "图表名称",
    "图表类型",
    "指标编码",
    "指标名称",
    "标准单位",
    "数据类型",
    "是否必须全部指标",
    "指标来源",
    "使用说明",
]


def _text(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _config_metric_codes(config: object) -> tuple[str, ...]:
    """递归读取图表配置中的指标编码（含 denominator/axes/components 等）。"""
    found: list[str] = []
    code_keys = {
        "code",
        "denominator_code",
        "x_metric",
        "total_code",
        "motor_code",
        "revenue_code",
        "profit_code",
    }

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in code_keys and isinstance(item, str) and item.strip():
                    found.append(item.strip())
                elif key == "ratio_codes" and isinstance(item, dict):
                    found.extend(
                        str(code).strip()
                        for code in item.values()
                        if str(code).strip()
                    )
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    walk(config)
    return tuple(dict.fromkeys(found))


def _module_display_name(module: Step7Module) -> str:
    name = module.chart_name or module.module_id
    # 模板里部分新增图表没有填“对应图表名称”，load_step7_config 会回退成模块 ID。
    # 用二级分类作为可读名称，避免维护表里出现 actual_capital_trend 这类英文键。
    if name == module.module_id and module.level_two:
        return module.level_two
    return name


def step7_module_metric_rows(
    template_source: object | None = None,
) -> list[dict]:
    """Return one row per chart x required metric, preserving Excel order."""
    source = template_source or DEFAULT_STEP7_TEMPLATE
    modules = load_step7_config(source)
    rows: list[dict] = []
    for index, module in enumerate(modules, start=1):
        direct = tuple(
            dict.fromkeys(
                code.strip() for code in module.metric_codes if code.strip()
            )
        )
        config_codes = _config_metric_codes(module.chart_config)
        implicit = IMPLICIT_CHART_METRICS.get(_module_display_name(module), ())
        all_codes = list(dict.fromkeys([*direct, *config_codes, *implicit]))
        if not all_codes:
            rows.append(
                {
                    "模块序号": index,
                    "模块ID": module.module_id,
                    "一级分类": module.level_one,
                    "二级分类": module.level_two,
                    "图表名称": _module_display_name(module),
                    "图表类型": module.chart_kind,
                    "指标编码": "",
                    "指标名称": "",
                    "标准单位": "",
                    "数据类型": "",
                    "是否必须全部指标": "是" if module.requires_all else "否",
                    "指标来源": "无指标",
                    "使用说明": "该图表不依赖标准指标（表格/融资/待定模块）。",
                }
            )
            continue
        for code in all_codes:
            if code in direct:
                origin = "指标列"
                note = "图表直接展示或计算该指标。"
            elif code in config_codes:
                origin = "图表配置"
                note = "图表配置中的计算依赖（如分母、雷达轴、堆叠构成）。"
            else:
                origin = "隐含依赖"
                note = "图表代码固定读取该指标，Excel 指标列未列出。"
            if module.requires_all and code in direct:
                note += " 该图要求指标列全部可用。"
            rows.append(
                {
                    "模块序号": index,
                    "模块ID": module.module_id,
                    "一级分类": module.level_one,
                    "二级分类": module.level_two,
                    "图表名称": _module_display_name(module),
                    "图表类型": module.chart_kind,
                    "指标编码": code,
                    "指标名称": "",
                    "标准单位": "",
                    "数据类型": "",
                    "是否必须全部指标": "是" if module.requires_all else "否",
                    "指标来源": origin,
                    "使用说明": note,
                }
            )
    return rows


def load_taxonomy_workbook(path: object | None = None) -> pd.DataFrame:
    """Read the taxonomy workbook, auto-detecting the 指标编码 header row."""
    source = path or DEFAULT_TAXONOMY_PATH
    preview = pd.read_excel(source, header=None)
    header_row = None
    for index in range(min(20, len(preview))):
        values = {str(value).strip() for value in preview.iloc[index].tolist()}
        if "指标编码" in values:
            header_row = index
            break
    if header_row is None:
        raise ValueError(f"{source} 未找到“指标编码”表头。")
    frame = pd.read_excel(source, header=header_row).fillna("")
    frame.columns = [str(column).strip() for column in frame.columns]
    return frame


def _catalog_lookup(taxonomy: pd.DataFrame | None) -> dict[str, dict]:
    if taxonomy is None:
        taxonomy = load_taxonomy_workbook()
    catalog = supported_metric_catalog(
        taxonomy,
        include_derived=True,
        include_step3_only=True,
    )
    lookup: dict[str, dict] = {}
    for _, row in catalog.iterrows():
        code = _text(row.get("指标编码"))
        if not code:
            continue
        lookup[code] = {
            "指标名称": _text(row.get("指标名称")),
            "一级模块": _text(row.get("一级模块")),
            "二级模块": _text(row.get("二级模块")),
            "标准单位": _text(row.get("标准单位")),
            "数据类型": _text(row.get("数据类型")),
            "指标属性": _text(row.get("指标属性")),
            "计算逻辑": _text(row.get("计算逻辑")),
        }
    for definition in DERIVED_METRICS:
        lookup.setdefault(
            definition.code,
            {
                "指标名称": definition.name,
                "一级模块": definition.level1,
                "二级模块": definition.level2,
                "标准单位": definition.unit,
                "数据类型": definition.data_type,
                "指标属性": definition.attribute,
                "计算逻辑": definition.formula,
            },
        )
    return lookup


def step7_metric_requirement_catalog(
    taxonomy: pd.DataFrame | None = None,
    *,
    template_source: object | None = None,
) -> pd.DataFrame:
    """Return the canonical STEP7 metric catalog used by reports and STEP3 checks."""
    mapping = step7_module_metric_rows(template_source)
    lookup = _catalog_lookup(taxonomy)
    derived_codes = {definition.code for definition in DERIVED_METRICS}

    ordered_codes: list[str] = []
    module_names: dict[str, list[str]] = {}
    requires_all: dict[str, bool] = {}
    for row in mapping:
        code = _text(row.get("指标编码"))
        if not code:
            continue
        if code not in module_names:
            ordered_codes.append(code)
            module_names[code] = []
            requires_all[code] = False
        chart_name = _text(row.get("图表名称"))
        if chart_name and chart_name not in module_names[code]:
            module_names[code].append(chart_name)
        if _text(row.get("是否必须全部指标")) == "是":
            requires_all[code] = True

    records: list[dict] = []
    for index, code in enumerate(ordered_codes, start=1):
        meta = lookup.get(code, {})
        data_type = meta.get("数据类型", "")
        unit = meta.get("标准单位", "")
        is_derived = code in derived_codes
        if is_derived:
            fill_mode = "自动计算"
        elif data_type in _SKIP_FILL_DATA_TYPES or code in _SKIP_FILL_CODES:
            fill_mode = "仅展示/校验"
        else:
            fill_mode = "直接补录"

        note_parts: list[str] = []
        if not meta:
            note_parts.append("指标字典未收录该编码，请补充 taxonomy。")
        if is_derived and meta.get("计算逻辑"):
            note_parts.append(f"派生公式：{meta['计算逻辑']}")
        if code == "LIFE_INSURANCE_RISK_TO_QUANT_CAPITAL":
            note_parts.append(
                "该指标用于“关键偿付数据概览”的保险风险占比列；非寿险公司可能不披露。"
            )
        if unit in {"万元", "元", "亿元", "千元"}:
            note_parts.append(
                "金额类标准单位为万元；补录时可在单位列改为元，合并会自动换算。"
            )

        records.append(
            {
                "序号": index,
                "指标编码": code,
                "指标名称": meta.get("指标名称", code),
                "一级模块": meta.get("一级模块", ""),
                "二级模块": meta.get("二级模块", ""),
                "标准单位": unit,
                "数据类型": data_type,
                "指标属性": meta.get("指标属性", ""),
                "计算逻辑": meta.get("计算逻辑", ""),
                "是否派生": "是" if is_derived else "否",
                "Step7使用图表数": len(module_names[code]),
                "Step7使用图表": "；".join(module_names[code]),
                "图表是否要求全部指标": "是" if requires_all[code] else "否",
                "建议期间口径": EXPECTED_PERIODS.get(code, DEFAULT_AMOUNT_PERIOD),
                "补录方式": fill_mode,
                "维护说明": " ".join(note_parts),
            }
        )
    return pd.DataFrame(records, columns=_CATALOG_COLUMNS)


def step7_required_codes(
    taxonomy: pd.DataFrame | None = None,
    *,
    only_fillable: bool = False,
    template_source: object | None = None,
) -> tuple[str, ...]:
    catalog = step7_metric_requirement_catalog(
        taxonomy,
        template_source=template_source,
    )
    if only_fillable:
        catalog = catalog[catalog["补录方式"].eq("直接补录")]
    return tuple(catalog["指标编码"].astype(str).str.strip())


def _mapping_frame(
    catalog: pd.DataFrame,
    template_source: object | None = None,
) -> pd.DataFrame:
    rows = step7_module_metric_rows(template_source)
    lookup = {
        str(row["指标编码"]).strip(): row
        for _, row in catalog.iterrows()
        if str(row["指标编码"]).strip()
    }
    output: list[dict] = []
    for row in rows:
        code = _text(row.get("指标编码"))
        meta = lookup.get(code, {})
        item = dict(row)
        item["指标名称"] = meta.get("指标名称", code)
        item["标准单位"] = meta.get("标准单位", "")
        item["数据类型"] = meta.get("数据类型", "")
        output.append(item)
    return pd.DataFrame(output, columns=_MAPPING_COLUMNS)


def _instructions_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            (
                "用途",
                "维护 STEP7 公司报告真正需要的指标，并作为 STEP3 缺失指标检查的唯一口径。",
            ),
            (
                "维护入口",
                "优先修改 templates/Step7-偿付能力公司报告展示模块.xlsx 的“指标编码/图表配置”列；"
                "代码中固定读取的指标见 services/solvency_step7_metric_catalog.py 的 IMPLICIT_CHART_METRICS。",
            ),
            (
                "重新生成",
                "运行 python tools/generate_step7_metric_catalog.py，或调用 "
                "services.solvency_step7_metric_catalog.write_step7_metric_catalog()。",
            ),
            (
                "指标需求清单",
                "一行一个指标；“补录方式=直接补录”的指标会进入 STEP3 缺失补录表。",
            ),
            (
                "图表指标映射",
                "一行一个“图表 x 指标”；指标来源区分 指标列 / 图表配置 / 隐含依赖。",
            ),
            ("标准单位", "金额统一为万元；百分比为 %；倍数为 倍。"),
            (
                "无单位默认口径",
                "源表未声明金额单位时，STEP3 按“元”作为默认原始单位，再换算为万元。",
            ),
            (
                "补录合并",
                "在 STEP3 导出的缺失补录表中填写“数值”，上传后系统按 "
                "公司统一编码 + 报告期 + 指标编码 + 期间口径 合并，并自动补算派生指标。",
            ),
        ],
        columns=["项目", "说明"],
    )


def _unit_notes_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            (
                "金额",
                "万元",
                "STEP3/STEP5 标准窄表的金额统一为万元。",
                "源表单位：元/千元/百元/亿元/十亿元时自动换算。",
            ),
            (
                "金额（无单位）",
                "万元",
                "源表未声明单位时，默认原始单位为元，除以 10000 换算为万元。",
                "备注列会写明“未识别原始单位，按默认单位元换算”。",
            ),
            (
                "比率",
                "%",
                "保留披露百分数，例如 130.98 表示 130.98%。",
                "不使用小数形式 1.3098。",
            ),
            ("倍数", "倍", "资本/资产、资本/负债等比值。", "不做百分比换算。"),
            (
                "期间-存量",
                "本季度末数",
                "实际资本、最低资本、认可资产/负债等时点值。",
                "上季度末数用于同比/环比。",
            ),
            (
                "期间-流量",
                "本年累计数",
                "保费、净利润、综合成本率等期间值。",
                "缺失时 STEP7 会回退到本季度数/本季度末数。",
            ),
        ],
        columns=["指标类型", "标准单位", "口径", "说明"],
    )


def _auto_width(sheet, *, max_width: int = 56) -> None:
    for column_cells in sheet.iter_cols(
        min_row=1,
        max_row=min(sheet.max_row, 300),
    ):
        values = []
        for cell in column_cells:
            value = "" if cell.value is None else str(cell.value)
            values.append(sum(2 if ord(char) > 127 else 1 for char in value))
        width = min(max(max(values, default=8) + 3, 10), max_width)
        sheet.column_dimensions[get_column_letter(column_cells[0].column)].width = width


def _style_table_sheet(
    sheet,
    *,
    header_fill: str = "00338D",
    freeze: str = "A2",
    wrap_headers: bool = True,
) -> None:
    header_fill_obj = PatternFill("solid", fgColor=header_fill)
    header_font = Font(name="Microsoft YaHei", size=10, bold=True, color="FFFFFF")
    body_font = Font(name="Microsoft YaHei", size=10)
    thin = Side(style="thin", color="D9E2EC")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    sheet.freeze_panes = freeze
    if sheet.max_row >= 1 and sheet.max_column >= 1:
        sheet.auto_filter.ref = sheet.dimensions
    for cell in sheet[1]:
        cell.fill = header_fill_obj
        cell.font = header_font
        cell.alignment = Alignment(
            horizontal="center",
            vertical="center",
            wrap_text=wrap_headers,
        )
        cell.border = border
    sheet.row_dimensions[1].height = 34
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.font = body_font
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            cell.border = border
    _auto_width(sheet)


def _write_frame(writer: pd.ExcelWriter, frame: pd.DataFrame, sheet_name: str) -> None:
    frame.to_excel(writer, sheet_name=sheet_name, index=False)


def build_step7_metric_catalog_workbook_bytes(
    taxonomy: pd.DataFrame | None = None,
    *,
    template_source: object | None = None,
) -> bytes:
    catalog = step7_metric_requirement_catalog(
        taxonomy,
        template_source=template_source,
    )
    mapping = _mapping_frame(catalog, template_source)
    required_codes = set(catalog["指标编码"].astype(str))
    derived = pd.DataFrame(
        [
            {
                "派生指标编码": definition.code,
                "派生指标名称": definition.name,
                "标准单位": definition.unit,
                "计算公式": definition.formula,
                "依赖指标": "、".join(definition.dependencies),
                "是否在STEP7需求内": "是" if definition.code in required_codes else "否",
            }
            for definition in DERIVED_METRICS
            if definition.code in required_codes
        ]
    )

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        _write_frame(writer, _instructions_frame(), "使用说明")
        _write_frame(writer, catalog, "指标需求清单")
        _write_frame(writer, mapping, "图表指标映射")
        _write_frame(writer, derived, "派生指标依赖")
        _write_frame(writer, _unit_notes_frame(), "单位与口径")

        for name in writer.book.sheetnames:
            sheet = writer.book[name]
            if name == "使用说明":
                _style_table_sheet(sheet, freeze="A2")
                sheet.column_dimensions["A"].width = 20
                sheet.column_dimensions["B"].width = 110
            else:
                _style_table_sheet(sheet)

        catalog_sheet = writer.book["指标需求清单"]
        fill_col = _CATALOG_COLUMNS.index("补录方式") + 1
        validation = DataValidation(
            type="list",
            formula1='"直接补录,自动计算,仅展示/校验"',
            allow_blank=False,
        )
        catalog_sheet.add_data_validation(validation)
        validation.add(
            f"{get_column_letter(fill_col)}2:"
            f"{get_column_letter(fill_col)}{max(catalog_sheet.max_row, 2)}"
        )
    return output.getvalue()


def write_step7_metric_catalog(
    path: object | None = None,
    *,
    taxonomy: pd.DataFrame | None = None,
    template_source: object | None = None,
) -> Path:
    target = Path(path or DEFAULT_STEP7_CATALOG_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(
        build_step7_metric_catalog_workbook_bytes(
            taxonomy,
            template_source=template_source,
        )
    )
    return target
