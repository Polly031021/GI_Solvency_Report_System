"""Portable STEP0-STEP8 workflow settings and page-selection restore."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from .solvency_pdf_locator import PageMatch


SETTINGS_SCHEMA_VERSION = 1

SCOPE_PREFIXES: dict[str, tuple[str, ...]] = {
    "STEP0 报告监控": ("monitor_",),
    "STEP1 页码定位": (
        "locator_method_",
        "batch_locate_",
        "solvency_preview_",
        "edited_pages",
        "pages_confirmed",
    ),
    "STEP2 表格提取": (
        "extraction_method",
        "extract_whole_page",
        "allow_llm_fallback",
        "auto_vision_retry",
        "force_vision_mode",
        "batch_extraction_method",
        "batch_allow_llm_fallback",
        "batch_scan_vision_fallback",
        "batch_whole_page",
        "batch_workers",
        "batch_document_workers",
    ),
    "STEP3 标准化": (
        "step3_",
        "batch_standardization_workers",
        "batch_step3_",
    ),
    "STEP4 勾稽检查": ("step4_",),
    "STEP5 数据集成": ("integration_mode",),
    "STEP6 自定义分析": ("step6_",),
    "STEP7 公司报告": (
        "s7_",
        "company_nav_",
        "step7_config_source_",
        "step7_notes_bytes_",
    ),
    "STEP8 行业分析": ("industry_nav_",),
}

FORBIDDEN_SETTING_KEYS = {
    "llm_api_key",
    "login_api_key_input",
    "login_security_code",
    "_session_token",
}

# Keys that belong to action widgets (st.button / st.file_uploader) in the UI.
# Some of them share a prefix with real settings, so a prefix-based export used
# to capture them. Restoring such a key either re-fires an action or makes
# Streamlit raise StreamlitValueAssignmentNotAllowedError when the widget is
# created (buttons and uploaders do not accept values written through
# st.session_state).
ACTION_WIDGET_KEYS = frozenset(
    {
        "batch_locate_all",
        "monitor_all",
        "monitor_one",
        "step3_standardize_target",
        "step3_target_upload",
    }
)
ACTION_WIDGET_PREFIXES = (
    "step4_goto_step3_",
    "step6_integrated_upload_",
    "step7_reset_config_",
)


def is_restorable_setting_key(key: str) -> bool:
    """Return whether a session-state key may travel in a settings bundle."""
    text = str(key)
    if text in FORBIDDEN_SETTING_KEYS or text.startswith("_"):
        return False
    if text in ACTION_WIDGET_KEYS:
        return False
    return not any(text.startswith(prefix) for prefix in ACTION_WIDGET_PREFIXES)


def _encode_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {
            "__type__": "bytes",
            "data": base64.b64encode(value).decode("ascii"),
        }
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple, set)):
        return [_encode_value(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _encode_value(item)
            for key, item in value.items()
        }
    return None


def _decode_value(value: Any) -> Any:
    if isinstance(value, list):
        return [_decode_value(item) for item in value]
    if isinstance(value, dict):
        if value.get("__type__") == "bytes":
            return base64.b64decode(str(value.get("data", "")))
        return {
            str(key): _decode_value(item)
            for key, item in value.items()
        }
    return value


def _sha256(data: bytes | None) -> str:
    return hashlib.sha256(data or b"").hexdigest() if data else ""


def _file_size(document: Mapping[str, Any]) -> int:
    data = document.get("pdf_bytes")
    if isinstance(data, (bytes, bytearray)) and data:
        return len(data)
    uploaded = document.get("uploaded_file")
    try:
        return int(getattr(uploaded, "size", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _serialize_matches(matches: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            key: _encode_value(value)
            for key, value in (
                match.to_dict().items()
                if hasattr(match, "to_dict")
                else vars(match).items()
            )
        }
        for match in matches
    ]


def _deserialize_matches(records: Any) -> list[PageMatch]:
    if not isinstance(records, list):
        return []
    matches: list[PageMatch] = []
    for item in records:
        if not isinstance(item, dict):
            continue
        try:
            matches.append(
                PageMatch(
                    table_id=str(item.get("table_id", "")),
                    table_name=str(item.get("table_name", "")),
                    pages=[
                        int(page)
                        for page in item.get("pages", [])
                        if str(page).isdigit()
                    ],
                    score=float(item.get("score", 0) or 0),
                    evidence=str(item.get("evidence", "")),
                    review_required=bool(item.get("review_required", False)),
                    review_reason=str(item.get("review_reason", "")),
                    sources=_decode_value(item.get("sources", {})),
                    strategy_id=str(item.get("strategy_id", "")),
                    table_config=_decode_value(item.get("table_config", {})),
                    confidence=int(item.get("confidence", 0) or 0),
                    confidence_basis=str(item.get("confidence_basis", "")),
                )
            )
        except Exception:
            continue
    return matches


def _serialize_batch_documents(documents: Mapping[str, dict]) -> list[dict[str, Any]]:
    saved: list[dict[str, Any]] = []
    for company, document in documents.items():
        pdf_bytes = document.get("pdf_bytes")
        metadata = dict(document.get("metadata") or {})
        metadata.pop("first_page_text", None)
        saved.append(
            {
                "company": company,
                "pdf_name": document.get("pdf_name", ""),
                "file_size": _file_size(document),
                "pdf_sha256": _sha256(pdf_bytes),
                "metadata": _encode_value(metadata),
                "matches": _serialize_matches(document.get("matches") or []),
                "edited_pages": _encode_value(document.get("edited_pages") or {}),
                "pages_confirmed": bool(document.get("pages_confirmed")),
                "locate_status": document.get("locate_status", ""),
                "locate_note": document.get("locate_note", ""),
                "locate_method": document.get("locate_method", ""),
                "extraction_method": document.get("extraction_method", ""),
            }
        )
    return saved


def _serialize_single_report(state: Mapping[str, Any]) -> dict[str, Any] | None:
    pdf_name = str(state.get("pdf_name", "") or "")
    if not pdf_name and not state.get("page_matches"):
        return None
    metadata = dict(state.get("metadata") or {})
    metadata.pop("first_page_text", None)
    return {
        "pdf_name": pdf_name,
        "file_size": _file_size(state),
        "pdf_sha256": _sha256(state.get("pdf_bytes")),
        "metadata": _encode_value(metadata),
        "page_matches": _serialize_matches(state.get("page_matches") or []),
        "edited_pages": _encode_value(state.get("edited_pages") or {}),
        "pages_confirmed": bool(state.get("pages_confirmed")),
    }


def _scope_matches(key: str, scope: str) -> bool:
    if not is_restorable_setting_key(key):
        return False
    prefixes = SCOPE_PREFIXES.get(scope, ())
    return any(key == prefix or key.startswith(prefix) for prefix in prefixes)


def build_workflow_settings_bundle(
    state: Mapping[str, Any],
    *,
    profile_id: str,
    scope: str,
) -> dict[str, Any]:
    """Build a portable JSON-compatible settings bundle."""
    all_steps = scope == "全部 Step"
    settings: dict[str, Any] = {}
    for key, value in state.items():
        if not all_steps and not _scope_matches(str(key), scope):
            continue
        if all_steps and not any(
            _scope_matches(str(key), step) for step in SCOPE_PREFIXES
        ):
            continue
        encoded = _encode_value(value)
        if encoded is not None:
            settings[str(key)] = encoded

    include_documents = all_steps or scope in {
        "STEP1 页码定位",
        "STEP2 表格提取",
    }
    documents = (
        _serialize_batch_documents(state.get("batch_documents") or {})
        if include_documents
        else []
    )
    single_report = (
        _serialize_single_report(state)
        if include_documents
        else None
    )
    return {
        "schema_version": SETTINGS_SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "profile_id": profile_id,
        "scope": scope,
        "settings": settings,
        "documents": documents,
        "single_report": single_report,
    }


def count_workflow_settings(
    state: Mapping[str, Any],
    *,
    scope: str,
) -> int:
    """Count exportable settings without serializing PDFs or page matches."""
    scopes = SCOPE_PREFIXES if scope == "全部 Step" else (scope,)
    return sum(
        1
        for key in state
        if any(_scope_matches(str(key), item) for item in scopes)
    )


def bundle_to_json_bytes(bundle: Mapping[str, Any]) -> bytes:
    return json.dumps(
        bundle,
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")


def bundle_from_json_bytes(payload: bytes) -> dict[str, Any]:
    bundle = json.loads(payload.decode("utf-8-sig"))
    if not isinstance(bundle, dict):
        raise ValueError("配置文件不是 JSON 对象。")
    if int(bundle.get("schema_version", 0) or 0) != SETTINGS_SCHEMA_VERSION:
        raise ValueError(
            f"不支持的工作流配置版本：{bundle.get('schema_version')}"
        )
    return bundle


def _document_matches(
    current: Mapping[str, Any],
    saved: Mapping[str, Any],
) -> tuple[bool, str]:
    saved_name = str(saved.get("pdf_name", ""))
    current_name = str(current.get("pdf_name", ""))
    if saved_name and current_name and saved_name != current_name:
        return False, "文件名不同"
    saved_size = int(saved.get("file_size", 0) or 0)
    current_size = _file_size(current)
    if saved_size and current_size and saved_size != current_size:
        return False, "文件大小不同"
    saved_hash = str(saved.get("pdf_sha256", "") or "")
    current_hash = _sha256(current.get("pdf_bytes"))
    if saved_hash and current_hash and saved_hash != current_hash:
        return False, "文件内容指纹不同"
    return True, ""


def _apply_saved_document(current: dict[str, Any], saved: Mapping[str, Any]) -> None:
    saved_metadata = _decode_value(saved.get("metadata") or {})
    current_metadata = dict(current.get("metadata") or {})
    if isinstance(saved_metadata, dict):
        current_metadata.update(saved_metadata)
    current.update(
        {
            "matches": _deserialize_matches(saved.get("matches")),
            "edited_pages": _decode_value(saved.get("edited_pages") or {}),
            "pages_confirmed": bool(saved.get("pages_confirmed")),
            "locate_status": str(saved.get("locate_status", "")),
            "locate_note": str(saved.get("locate_note", "")),
            "locate_method": str(saved.get("locate_method", "")),
            "extraction_method": str(saved.get("extraction_method", "")),
            "metadata": current_metadata,
            "settings_restored": True,
        }
    )


def apply_workflow_settings_bundle(
    bundle: Mapping[str, Any],
    state: dict[str, Any],
    *,
    profile_id: str,
) -> dict[str, Any]:
    """Apply settings and bind saved page selections to matching PDFs."""
    bundle_profile = str(bundle.get("profile_id", ""))
    if bundle_profile and bundle_profile != profile_id:
        raise ValueError(
            f"配置属于 {bundle_profile}，当前是 {profile_id}，不能混用。"
        )

    # Drop action-widget values left behind by an older bundle or an older
    # version of this app before applying the portable settings.
    for key in ACTION_WIDGET_KEYS:
        state.pop(key, None)
    for key in list(state):
        if any(
            str(key).startswith(prefix)
            for prefix in ACTION_WIDGET_PREFIXES
        ):
            state.pop(key, None)

    applied_settings = 0
    for key, encoded in (bundle.get("settings") or {}).items():
        key = str(key)
        if not is_restorable_setting_key(key):
            continue
        state[key] = _decode_value(encoded)
        applied_settings += 1
    if any(
        key.startswith("step7_notes_bytes_") and state.get(key)
        for key in state
    ):
        state.pop("step7_modules", None)

    documents = dict(state.get("batch_documents") or {})
    pending_documents: list[dict[str, Any]] = []
    applied_documents = 0
    rejected_documents: list[str] = []
    saved_documents = bundle.get("documents") or []
    for saved in saved_documents:
        if not isinstance(saved, dict):
            continue
        company = str(saved.get("company", "") or Path(
            str(saved.get("pdf_name", ""))
        ).stem)
        current = documents.get(company)
        if current is None:
            pending_documents.append(dict(saved))
            continue
        matched, reason = _document_matches(current, saved)
        if not matched:
            rejected_documents.append(f"{company}（{reason}）")
            continue
        _apply_saved_document(current, saved)
        applied_documents += 1
    state["batch_documents"] = documents

    single_report = bundle.get("single_report")
    if isinstance(single_report, dict):
        saved_name = str(single_report.get("pdf_name", ""))
        if state.get("pdf_name") and saved_name:
            current = {
                "pdf_name": state.get("pdf_name", ""),
                "pdf_bytes": state.get("pdf_bytes"),
                "uploaded_file": None,
            }
            matched, reason = _document_matches(current, single_report)
            if matched:
                state["page_matches"] = _deserialize_matches(
                    single_report.get("page_matches")
                )
                state["auto_page_matches"] = list(state["page_matches"])
                state["edited_pages"] = _decode_value(
                    single_report.get("edited_pages") or {}
                )
                state["pages_confirmed"] = bool(
                    single_report.get("pages_confirmed")
                )
                saved_metadata = _decode_value(
                    single_report.get("metadata") or {}
                )
                current_metadata = dict(state.get("metadata") or {})
                if isinstance(saved_metadata, dict):
                    current_metadata.update(saved_metadata)
                state["metadata"] = current_metadata
            else:
                rejected_documents.append(f"单份PDF（{reason}）")
        else:
            state["pending_single_workflow_settings"] = dict(single_report)

    state["pending_batch_workflow_settings"] = pending_documents
    saved_documents = [
        item for item in saved_documents if isinstance(item, dict)
    ]
    saved_page_targets = sum(
        1
        for saved in saved_documents
        for match in (saved.get("matches") or [])
        if isinstance(match, dict) and match.get("pages")
    )
    return {
        "applied_settings": applied_settings,
        "applied_documents": applied_documents,
        "pending_documents": len(pending_documents),
        "rejected_documents": rejected_documents,
        "total_documents": len(saved_documents),
        "saved_page_targets": saved_page_targets,
    }


def apply_pending_settings_to_batch_documents(
    documents: dict[str, dict],
    pending: Any,
) -> list[str]:
    """Bind pending page configuration after PDFs are uploaded."""
    if not isinstance(pending, list) or not documents:
        return []
    unmatched: list[dict[str, Any]] = []
    applied: list[str] = []
    for saved in pending:
        if not isinstance(saved, dict):
            continue
        company = str(saved.get("company", "") or Path(
            str(saved.get("pdf_name", ""))
        ).stem)
        current = documents.get(company)
        if current is None:
            unmatched.append(saved)
            continue
        matched, _ = _document_matches(current, saved)
        if matched:
            _apply_saved_document(current, saved)
            applied.append(company)
        else:
            unmatched.append(saved)
    return applied


def apply_pending_single_settings(
    state: dict[str, Any],
    *,
    pdf_name: str,
    pdf_bytes: bytes,
) -> bool:
    saved = state.get("pending_single_workflow_settings")
    if not isinstance(saved, dict):
        return False
    matched, _ = _document_matches(
        {"pdf_name": pdf_name, "pdf_bytes": pdf_bytes},
        saved,
    )
    if not matched:
        return False
    state["page_matches"] = _deserialize_matches(saved.get("page_matches"))
    state["auto_page_matches"] = list(state["page_matches"])
    state["edited_pages"] = _decode_value(saved.get("edited_pages") or {})
    state["pages_confirmed"] = bool(saved.get("pages_confirmed"))
    saved_metadata = _decode_value(saved.get("metadata") or {})
    current_metadata = dict(state.get("metadata") or {})
    if isinstance(saved_metadata, dict):
        current_metadata.update(saved_metadata)
    state["metadata"] = current_metadata
    state.pop("pending_single_workflow_settings", None)
    return True
