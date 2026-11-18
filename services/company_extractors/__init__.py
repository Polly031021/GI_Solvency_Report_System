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


def extractor_from_matches(matches: Sequence[Any] | None) -> str:
    """从页定位结果的 table_config 里取公司专属 extractor 名称。

    STEP1 应用公司规则时会把 ``_extraction_rule.extractor`` 写进表配置，
    单文件与批量路径都依赖这个标记，保证两边行为一致。
    """
    for match in matches or []:
        config = getattr(match, "table_config", None)
        if not isinstance(config, dict):
            continue
        info = config.get("_extraction_rule")
        if isinstance(info, dict):
            name = str(info.get("extractor") or "").strip()
            if name:
                return name
    return ""


def apply_company_extractor_for_tables(
    tables: Sequence[Any],
    matches: Sequence[Any] | None = None,
    *,
    pdf_bytes: bytes,
    extractor_name: str = "",
) -> tuple[Sequence[Any], dict[str, str] | None]:
    """单文件与批量共用的入口；返回 (tables, 日志信息或 None)。"""
    name = str(extractor_name or "").strip() or extractor_from_matches(matches)
    if not name or not tables or not pdf_bytes:
        return tables, None
    if get_postprocessor(name) is None:
        return tables, None
    try:
        updated = list(apply_company_extractor(name, tables, pdf_bytes=pdf_bytes))
        return updated, {
            "extractor": name,
            "status": "成功",
            "message": "已应用公司专属提取后处理。",
        }
    except Exception as exc:  # 专属逻辑失败不能阻断通用提取
        return tables, {
            "extractor": name,
            "status": "失败",
            "message": f"专属后处理异常，已保留通用提取结果：{exc}",
        }
