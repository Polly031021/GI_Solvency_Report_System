"""Bilingual labels used by the PDF front/back matter."""

from __future__ import annotations

LABELS = {
    "zh": {
        "page_label": "第 {current} 页 / 共 {total} 页",
        "generated": "生成日期：{date}",
        "appendix": "附：报告说明",
        "ending_title": "报告结束",
        "ending_body": "本报告由偿付能力报告系统自动生成，仅供内部研究参考。",
        "cover_default_title": "偿付能力公司对标报告",
        "no_content": "该模块暂无可导出的内容。",
    },
    "en": {
        "page_label": "Page {current} of {total}",
        "generated": "Generated: {date}",
        "appendix": "Appendix",
        "ending_title": "End of report",
        "ending_body": "This report was generated automatically by the Solvency Report System for internal research reference only.",
        "cover_default_title": "Insurance Company Solvency Benchmarking Report",
        "no_content": "No exportable content for this module.",
    },
}


def label(language: str, key: str) -> str:
    table = LABELS.get(language if language in LABELS else "zh", LABELS["zh"])
    return table.get(key, key)
