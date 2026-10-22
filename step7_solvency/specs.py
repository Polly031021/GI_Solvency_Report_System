"""STEP7 图表规格常量：配色、面板宽度、构成堆叠口径与能力五边形基准。"""

from __future__ import annotations

from pathlib import Path

from services.solvency_navigation import (
    CREDIT_RISK_ASSET_SCATTER,
    INSURANCE_RISK_LIABILITY_SCATTER,
    KPMG_BRIGHT_CHART_COLORS,
    KPMG_QUANT_RISK_COLORS,
    MARKET_RISK_ASSET_SCATTER,
)
from services.solvency_step7_charts import CAPITAL_STRUCTURE_COLORS

# 图片资源目录位于项目根目录（本文件在包内，需要多上一级）
PICTURE_DIR = Path(__file__).resolve().parents[1] / "picture"
ALL_COMPANY_TYPES = "全部"
DEFAULT_SORT_LABEL = "默认（按列表原始顺序）"
SORT_DESCENDING = "降序（从大到小）"
SORT_ASCENDING = "升序（从小到大）"
TRACKED_COMPANY_COLOR = "#00338D"
RISK_DIVERSIFICATION_EFFECT_COLOR = KPMG_BRIGHT_CHART_COLORS[6]
RISK_SCATTER_DISPLAY_WIDTH = 750
RISK_SCATTER_FULL_PANEL_WIDTH = 570
RISK_SCATTER_ZOOM_PANEL_WIDTH = 330
BUBBLE_FULL_PANEL_WIDTH = 570
BUBBLE_ZOOM_PANEL_WIDTH = 330
BUBBLE_DISTINCT_COLORS = (
    "#1FDB1F",
    "#DB651F",
    "#1FDBDB",
    "#DB1F89",
    "#84943E",
    "#A01FDB",
    "#3E7494",
    "#943E43",
    "#1FDB89",
    "#DBAC1F",
    "#DB1F42",
    "#A44DB8",
    "#29946C",
    "#259415",
    "#B8824D",
    "#5C84DB",
    "#1F42DB",
    "#9AB81A",
    "#DB1FC4",
    "#943E74",
    "#5CBCDB",
    "#944415",
    "#DB5C84",
    "#DB6C5C",
)
COMPONENT_STACK_SPECS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "计入各级资本的保单未来盈余构成占比": (
        ("POLICY_SURPLUS_CORE_T1", "计入核心一级资本", CAPITAL_STRUCTURE_COLORS["核心一级资本"]),
        ("POLICY_SURPLUS_CORE_T2", "计入核心二级资本", CAPITAL_STRUCTURE_COLORS["核心二级资本"]),
        ("POLICY_SURPLUS_ANC_T1", "计入附属一级资本", CAPITAL_STRUCTURE_COLORS["附属一级资本"]),
        ("POLICY_SURPLUS_ANC_T2", "计入附属二级资本", CAPITAL_STRUCTURE_COLORS["附属二级资本"]),
    ),
    "量化风险最低资本构成": (
        ("NON_LIFE_INSURANCE_RISK_CAPITAL", "非寿险业务保险风险", KPMG_QUANT_RISK_COLORS[1]),
        ("MARKET_RISK_CAPITAL", "市场风险", KPMG_QUANT_RISK_COLORS[2]),
        ("CREDIT_RISK_CAPITAL", "信用风险", KPMG_QUANT_RISK_COLORS[3]),
        ("QUANT_RISK_DIVERSIFICATION_EFFECT", "风险分散", KPMG_QUANT_RISK_COLORS[4]),
    ),
    "保险风险构成": (
        (
            "NON_LIFE_PREMIUM_RESERVE_RISK_CAPITAL",
            "保费及准备金风险",
            KPMG_QUANT_RISK_COLORS[1],
        ),
        ("NON_LIFE_CATASTROPHE_RISK_CAPITAL", "巨灾风险", KPMG_QUANT_RISK_COLORS[0]),
        (
            "NON_LIFE_INSURANCE_RISK_DIVERSIFICATION_EFFECT",
            "风险分散",
            KPMG_QUANT_RISK_COLORS[4],
        ),
    ),
    "各类保险风险（寿）占比": (
        ("LOSS_OCCURRENCE_RISK_CAPITAL", "损失发生风险", KPMG_BRIGHT_CHART_COLORS[0]),
        ("SURRENDER_RISK_CAPITAL", "退保风险", KPMG_BRIGHT_CHART_COLORS[1]),
        ("EXPENSE_RISK_CAPITAL", "费用风险", KPMG_BRIGHT_CHART_COLORS[2]),
        ("LIFE_INSURANCE_RISK_DIVERSIFICATION_EFFECT", "风险分散效应", KPMG_BRIGHT_CHART_COLORS[3]),
    ),
    "各类市场风险占比": (
        ("INTEREST_RATE_RISK_CAPITAL", "利率风险", KPMG_BRIGHT_CHART_COLORS[0]),
        ("EQUITY_RISK_CAPITAL", "权益价格风险", KPMG_BRIGHT_CHART_COLORS[1]),
        ("REAL_ESTATE_RISK_CAPITAL", "房地产价格风险", KPMG_BRIGHT_CHART_COLORS[2]),
        (
            "OVERSEAS_FIXED_INCOME_RISK_CAPITAL",
            "境外固定收益类资产价格风险",
            KPMG_BRIGHT_CHART_COLORS[3],
        ),
        ("OVERSEAS_EQUITY_RISK_CAPITAL", "境外权益类资产价格风险", KPMG_BRIGHT_CHART_COLORS[4]),
        ("FOREIGN_EXCHANGE_RISK_CAPITAL", "汇率风险", KPMG_BRIGHT_CHART_COLORS[5]),
        ("MARKET_RISK_DIVERSIFICATION_EFFECT", "风险分散效应", RISK_DIVERSIFICATION_EFFECT_COLOR),
    ),
    "各类信用风险占比": (
        ("SPREAD_RISK_CAPITAL", "利差风险", KPMG_BRIGHT_CHART_COLORS[0]),
        ("COUNTERPARTY_RISK_CAPITAL", "交易对手违约风险", KPMG_BRIGHT_CHART_COLORS[1]),
        ("CREDIT_RISK_DIVERSIFICATION_EFFECT", "风险分散效应", RISK_DIVERSIFICATION_EFFECT_COLOR),
    ),
}
QUANT_RISK_STACK_RATIO_CODES = {
    "INSURANCE_RISK_CAPITAL": "LIFE_INSURANCE_RISK_TO_QUANT_CAPITAL",
    "NON_LIFE_INSURANCE_RISK_CAPITAL": "NON_LIFE_INSURANCE_RISK_TO_QUANT_CAPITAL",
    "MARKET_RISK_CAPITAL": "MARKET_RISK_TO_QUANT_CAPITAL",
    "CREDIT_RISK_CAPITAL": "CREDIT_RISK_TO_QUANT_CAPITAL",
    "QUANT_RISK_DIVERSIFICATION_EFFECT": "DIVERSIFICATION_EFFECT_TO_QUANT_CAPITAL",
    "CONTRACT_LOSS_ABSORPTION_EFFECT": "LOSS_ABSORPTION_TO_QUANT_CAPITAL",
}
COMPONENT_STACK_DENOMINATOR_CODES = {
    "量化风险最低资本构成": "QUANT_RISK_CAPITAL",
    "保险风险构成": "NON_LIFE_INSURANCE_RISK_CAPITAL",
    "各类市场风险占比": "MARKET_RISK_CAPITAL",
    "各类信用风险占比": "CREDIT_RISK_CAPITAL",
}
RISK_RATIO_SCATTER_SPECS = {
    MARKET_RISK_ASSET_SCATTER: (
        "INTEREST_RATE_RISK_TO_ASSETS",
        "EQUITY_RISK_TO_ASSETS",
        "RECOGNIZED_ASSETS",
        "认可资产",
    ),
    CREDIT_RISK_ASSET_SCATTER: (
        "SPREAD_RISK_TO_ASSETS",
        "COUNTERPARTY_RISK_TO_ASSETS",
        "RECOGNIZED_ASSETS",
        "认可资产",
    ),
    INSURANCE_RISK_LIABILITY_SCATTER: (
        "LIFE_INSURANCE_RISK_TO_LIABILITIES",
        "NON_LIFE_INSURANCE_RISK_TO_LIABILITIES",
        "RECOGNIZED_LIABILITIES",
        "认可负债",
    ),
}
RISK_SCATTER_DUAL_VIEW_CHARTS = frozenset(
    {
        MARKET_RISK_ASSET_SCATTER,
        CREDIT_RISK_ASSET_SCATTER,
        INSURANCE_RISK_LIABILITY_SCATTER,
    }
)
# ★ 新增：偿付能力阶梯打分基准（默认兜底）
# 只用于雷达图顶点"偿付能力"（COMBINED_SOLVENCY_RATIO），不参与其他图表。
# 分数采用 100 分制，与 build_capability_radar_chart 的 max_score=100 一致。
# 若某天想从 Excel 里覆盖这些档位，把 steps 写进 chart_config.axes 即可，
# 此处仅作为"未配置时"的兜底。
CAPABILITY_STEP_SCORE_FALLBACK: dict[str, list[dict]] = {
    "COMBINED_SOLVENCY_RATIO": [
        {"min": 200.0, "score": 100.0},  # ≥200%        → 100 分
        {"min": 150.0, "score": 70.0},  # 150% ~ 200%  → 70 分
        {"min": 120.0, "score": 50.0},  # 120% ~ 150%  → 50 分
        {"min": 100.0, "score": 30.0},  # 100% ~ 120%  → 30 分
        {"min": float("-inf"), "score": 0.0},  # <100%        → 0 分
    ],
}
# ★ 新增：能力五边形的默认打分基准
# 只在这些指标出现在雷达图 axes 里且 Excel 未提供 lower/upper 时兜底。
# 金额类基准使用"万元"（Step3 输出的标准单位是万元）。
DEFAULT_CAPABILITY_AXES: list[dict] = [
    {
        "code": "PREMIUM_GROWTH",
        "label": "保费增速",
        "period": "本季度数",
        "lower": -10.0,
        "upper": 40.0,
    },
    {"code": "ROE", "label": "ROE", "period": "本年累计数", "lower": 0.0, "upper": 12.0},
    {
        "code": "TOTAL_ASSETS",
        "label": "总资产",
        "period": "本季度数",
        "lower": 500_000,
        "upper": 30_000_000,
    },
    {
        "code": "ACTUAL_CAPITAL",
        "label": "实际资本",
        "period": "本季度末数",
        "lower": 200_000,
        "upper": 15_000_000,
    },
    # ★ 偿付能力改为"阶梯打分"：不再使用 lower/upper 线性映射，
    #   而是按 ≥200/150/120/100 的档位给分。
    {
        "code": "COMBINED_SOLVENCY_RATIO",
        "label": "偿付能力",
        "period": "本季度末数",
        "steps": CAPABILITY_STEP_SCORE_FALLBACK["COMBINED_SOLVENCY_RATIO"],
    },
]
