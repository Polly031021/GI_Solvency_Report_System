"""集中定义平台级常量：路径、Profile 默认值、工作流标签与登录用 AI 预设。"""

from __future__ import annotations

from pathlib import Path


# 项目根目录：本文件位于 ui/ 包内，因此取上一级目录
ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
PROFILE_DIR = CONFIG_DIR / "report_profiles"
NON_LIFE_PROFILE_FILE = PROFILE_DIR / "NON_LIFE_SOLVENCY_profile.xlsx"
# ★ 默认 profile 改为财险，登录后直接进入 NON_LIFE_SOLVENCY
DEFAULT_PROFILE_ID = "NON_LIFE_SOLVENCY"
STANDARD_SCHEMA_VERSION = 2
GOLD_MANIFEST = ROOT / "gold_standard" / "manifest.json"
PEER_GROUP_CONFIG = CONFIG_DIR / "solvency_peer_groups.json"
# 财险公司分类主数据：短名称 + 公司类别（前十大/大型/中型/小型/外资系等）。
NON_LIFE_COMPANY_LIST = ROOT / "财险公司列表.xlsx"
# 财险公司全称与披露地址，用于把短名称别名关联到同一家公司。
NON_LIFE_COMPANY_URLS = CONFIG_DIR / "non_life_solvency_company_urls.xlsx"
WORKFLOW_TAB_LABELS = (
    "STEP0 报告监控",
    "STEP1 页码定位",
    "STEP2 表格提取",
    "STEP3 标准化",
    "STEP4 勾稽检查",
    "STEP5 数据集成",
    "STEP6 自定义分析",
    "STEP7 公司报告",
    "STEP8 行业分析",
)
ANALYSIS_TAB_LABELS = WORKFLOW_TAB_LABELS[6:]
LOGIN_AI_PROVIDERS = {
    "阿里云百炼 (通义千问)": (
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen3.7-plus",
    ),
    "DeepSeek (深度求索)": ("https://api.deepseek.com/v1", "deepseek-v4-flash"),
    "月之暗面 (Kimi)": ("https://api.moonshot.cn/v1", "moonshot-v1-8k"),
    "智谱AI (GLM-4)": ("https://open.bigmodel.cn/api/paas/v4", "glm-4"),
    "OpenAI (ChatGPT)": ("https://api.openai.com/v1", "gpt-4o"),
    "自定义私有化节点": ("", ""),
}
DEFAULT_LOGIN_AI_PROVIDER = "阿里云百炼 (通义千问)"
NORMAL_USER_ROLE = "普通用户"
PROJECT_MEMBER_ROLE = "项目组成员"
