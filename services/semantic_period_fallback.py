"""Optional LLM fallback for period labels that local rules cannot resolve."""

from __future__ import annotations

import json
import re
from typing import Iterable, Mapping

from .solvency_hybrid_pipeline.llm import _call_chat, _parse_json_object
from .solvency_normalizer import infer_period_label


STANDARD_PERIODS = (
    "本季度末数",
    "本季度数",
    "上季度末数",
    "上季度数",
    "本年累计数",
    "下季度末预测数",
    "近三年平均",
    "期末数",
    "期初数",
)

_IGNORE_HEADER_CELLS = {
    "指标名称",
    "项目",
    "行次",
    "序号",
    "单位",
    "备注",
    "指标",
}


def collect_unresolved_period_labels(tables: Iterable[object]) -> list[str]:
    """Collect only header-like labels for which local semantics return empty."""
    labels: list[str] = []
    for table in tables:
        rows = getattr(table, "rows", None) or []
        if not rows:
            continue
        header_index = next(
            (
                index
                for index, row in enumerate(rows[:8])
                if any(
                    token in "".join(str(cell) for cell in row)
                    for token in (
                        "指标名称",
                        "项目",
                        "行次",
                        "本季度",
                        "上季度",
                        "期末",
                        "年度",
                        "预测",
                    )
                )
            ),
            0,
        )
        header_rows = [rows[header_index]]
        if header_index + 1 < len(rows):
            next_row = rows[header_index + 1]
            next_text = "".join(str(cell or "") for cell in next_row)
            if (
                any(
                    token in next_text
                    for token in ("本季度", "上季度", "期末", "年度", "预测", "累计")
                )
                and not any(
                    re.fullmatch(r"\(?[+-]?\d[\d,，.]*\)?", str(cell or "").strip())
                    for cell in next_row
                    if str(cell or "").strip()
                )
            ):
                header_rows.append(next_row)
        for row in header_rows:
            for cell in row:
                text = str(cell or "").strip()
                compact = re.sub(r"\s+", "", text)
                if (
                    not compact
                    or len(compact) < 2
                    or compact in _IGNORE_HEADER_CELLS
                    or not re.search(r"[\u4e00-\u9fff]", compact)
                    or re.fullmatch(r"[\d,，.％%()（）\-—–/]+", compact)
                ):
                    continue
                if infer_period_label(compact):
                    continue
                # Titles and section headings are not period candidates.
                if any(
                    token in compact
                    for token in ("指标名称", "主要指标", "单位：", "单位:")
                ):
                    continue
                if compact not in labels:
                    labels.append(compact)
    return labels


def resolve_period_labels_with_llm(
    tables: Iterable[object],
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout: int = 90,
    post_func=None,
) -> tuple[dict[str, str], str]:
    """Return a mapping from unknown header text to a standard period.

    One call is made per PDF/company and only unresolved labels are sent.
    Invalid model labels are ignored rather than guessed.
    """
    labels = collect_unresolved_period_labels(tables)
    if not labels:
        return {}, ""
    if not all(str(value or "").strip() for value in (api_key, base_url, model)):
        return {}, "未配置AI接口，已跳过期间语义兜底"
    prompt = f"""你是保险偿付能力报告的期间口径专家。
请把下列无法由本地规则识别的表头描述映射到标准期间。

标准期间只能从以下列表选择：
{json.dumps(list(STANDARD_PERIODS), ensure_ascii=False)}

待识别表头：
{json.dumps(labels, ensure_ascii=False)}

规则：
1. “本年度（末）”等年度/全年/累计描述映射到“本年累计数”。
2. 近三年平均、近3年平均、三年平均描述映射到“近三年平均”。
3. 上年度末/上年末映射到“上季度末数”，但下一年度预测不得映射为历史数。
4. 无法确定的表头不要猜测，不要输出该键。
5. 只输出JSON对象，键必须与待识别表头完全一致，值必须是标准期间之一。
"""
    try:
        content = _call_chat(
            api_key=api_key,
            base_url=base_url,
            model=model,
            messages=[{"role": "user", "content": prompt}],
            timeout=timeout,
            post_func=post_func,
            json_mode=True,
        )
        payload = _parse_json_object(content)
        result = {
            str(label): str(period)
            for label, period in payload.items()
            if str(label) in labels
            and str(period) in STANDARD_PERIODS
        }
        return result, (
            f"大模型期间语义映射 {len(result)} 个表头"
            if result
            else "大模型未返回可用的期间映射"
        )
    except Exception as exc:
        return {}, f"大模型期间语义映射失败：{exc}"
