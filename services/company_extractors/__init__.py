"""公司专属提取后处理。

通用规则负责"大多数公司的常见版式"，这里处理通用规则无法覆盖的
版式特例（合并单元格、跨章节指标、无表头续页等）。

约定：
* 只处理 raw_tables（``rows``），不进入标准化；
* 提取器必须容错：任何异常都不能阻断通用提取流程；
* 通过公司 YAML 的 ``company.extractor`` 启用，例如 ``extractor: pingan``。
"""

from __future__ import annotations

from typing import Any, Callable, Sequence

POSTPROCESSORS: dict[str, Callable[..., Sequence[Any]]] = {}


def register(name: str) -> Callable[[Callable[..., Sequence[Any]]], Callable[..., Sequence[Any]]]:
    def decorator(func: Callable[..., Sequence[Any]]) -> Callable[..., Sequence[Any]]:
        POSTPROCESSORS[str(name).strip().lower()] = func
        return func

    return decorator


def get_postprocessor(name: str) -> Callable[..., Sequence[Any]] | None:
    key = str(name or "").strip().lower()
    if not key:
        return None
    if key not in POSTPROCESSORS:
        # 延迟导入，避免启动时加载 fitz 之外的解析逻辑。
        if key == "pingan":
            from . import pingan  # noqa: F401
    return POSTPROCESSORS.get(key)


def apply_company_extractor(
    name: str,
    tables: Sequence[Any],
    *,
    pdf_bytes: bytes,
) -> Sequence[Any]:
    postprocessor = get_postprocessor(name)
    if postprocessor is None or not tables:
        return tables
    return postprocessor(tables, pdf_bytes=pdf_bytes)
