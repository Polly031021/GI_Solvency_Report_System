"""管线的模式常量与并发/阈值配置。"""

from __future__ import annotations

PAGE_TEXT_MODE = "逐页文本结构化提取"
PAGE_VISION_MODE = "逐页图像结构化提取"
PAGE_HIGH_RES_VISION_MODE = "逐页3倍高清图像结构化提取"
PAGE_RETRY_MODE = "逐页网格纠错"
CROSS_PAGE_MERGE_MODE = "跨页一致性拼接"
FULL_TABLE_RECONSTRUCTION_MODE = "多页整表结构化重构"
LOCATOR_VISION_BATCH_SIZE = 6
LOW_TEXT_MIN_CHARACTERS = 24
TABLE_EXTRACTION_MAX_WORKERS = 2
