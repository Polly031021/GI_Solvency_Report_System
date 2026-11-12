"""Capture what a report module renders so it can be rebuilt as a PDF page.

Streamlit has no supported way to read back the rendered DOM, so the capture
layer wraps the handful of ``st`` primitives the report modules use.  The
wrappers forward to the real functions (the page still renders normally) and
append a lightweight record to the module currently being rendered.

Records keep live Altair/Plotly objects and DataFrames; the expensive SVG and
HTML conversion only happens when the user actually exports a PDF.
"""

from __future__ import annotations

import base64
import html
import re
from contextlib import contextmanager
from dataclasses import dataclass, field

import streamlit as st

from .models import ContentItem, ModuleSnapshot

_FLAG = "_s7_pdf_capture_enabled"
_MODULES = "_s7_pdf_modules"
_CURRENT = "_s7_pdf_current_module"
_REQUESTED = "_s7_pdf_requested_key"
_RESULT = "_s7_pdf_result"
_INSTALLED = "_s7_pdf_capture_installed"

# Column layout captured from ``st.columns``: id(col) -> (group, index, total, weight)
_COLUMN_MAP: dict[int, tuple[int, int, int, float]] = {}
_COLUMN_GROUP = [0]
_INSTALLED_COLUMNS = "_s7_pdf_columns_installed"
_SUSPEND = "_s7_pdf_capture_suspend"
_LAYOUT_COUNTER = "_s7_pdf_layout_counter"
_ACTIVE_LAYOUT = "_s7_pdf_active_layout"


def _column_weights(spec: object, count: int) -> list[float]:
    try:
        if isinstance(spec, (list, tuple)):
            weights = [float(value) for value in spec]
            if len(weights) == count:
                return weights
    except (TypeError, ValueError):
        pass
    return [1.0] * count


def _current_column() -> tuple[int, int, int, float] | None:
    if not _COLUMN_MAP:
        return None
    try:
        from streamlit.delta_generator import get_last_dg_added_to_context_stack

        active = get_last_dg_added_to_context_stack()
    except Exception:
        return None
    if active is None:
        return None
    return _COLUMN_MAP.get(id(active))


@dataclass
class _RawItem:
    kind: str
    payload: object
    extra: dict = field(default_factory=dict)


def is_enabled() -> bool:
    try:
        return bool(st.session_state.get(_FLAG)) and not bool(
            st.session_state.get(_SUSPEND, 0)
        )
    except Exception:
        return False


@contextmanager
def layout_card():
    """Group captured elements rendered inside one company card."""
    if not is_enabled():
        yield
        return
    counter = int(st.session_state.get(_LAYOUT_COUNTER, 0)) + 1
    st.session_state[_LAYOUT_COUNTER] = counter
    previous = st.session_state.get(_ACTIVE_LAYOUT)
    st.session_state[_ACTIVE_LAYOUT] = (counter, 0, 1, 1.0)
    try:
        yield
    finally:
        if previous is None:
            st.session_state.pop(_ACTIVE_LAYOUT, None)
        else:
            st.session_state[_ACTIVE_LAYOUT] = previous


@contextmanager
def suspend():
    """Render UI-only diagnostics without adding them to the PDF capture."""
    try:
        st.session_state[_SUSPEND] = int(st.session_state.get(_SUSPEND, 0)) + 1
        yield
    finally:
        try:
            remaining = max(0, int(st.session_state.get(_SUSPEND, 1)) - 1)
            if remaining:
                st.session_state[_SUSPEND] = remaining
            else:
                st.session_state.pop(_SUSPEND, None)
        except Exception:
            pass


def start_document() -> None:
    """Begin a fresh capture pass for the current Streamlit script run."""
    st.session_state[_FLAG] = True
    st.session_state[_MODULES] = []
    st.session_state[_CURRENT] = None


def begin_module(title: str, section: str = "") -> None:
    if not is_enabled():
        return
    st.session_state[_CURRENT] = {
        "title": title,
        "section": section,
        "items": [],
        "note": "",
    }


def set_note(note: str) -> None:
    if not is_enabled():
        return
    current = st.session_state.get(_CURRENT)
    if current is not None:
        current["note"] = note or ""


def end_module() -> None:
    if not is_enabled():
        return
    current = st.session_state.get(_CURRENT)
    if current is not None:
        st.session_state[_MODULES].append(ModuleSnapshot(**current))
    st.session_state[_CURRENT] = None


def captured_modules() -> list[ModuleSnapshot]:
    return list(st.session_state.get(_MODULES, []))


def has_capture() -> bool:
    return bool(st.session_state.get(_MODULES))


def request_export(key: str) -> None:
    st.session_state[_REQUESTED] = key


def requested_key() -> str | None:
    return st.session_state.get(_REQUESTED)


def clear_request() -> None:
    st.session_state.pop(_REQUESTED, None)


def store_result(key: str, payload: bytes, filename: str) -> None:
    st.session_state[_RESULT] = {
        "key": key,
        "bytes": payload,
        "filename": filename,
    }


def result() -> dict | None:
    return st.session_state.get(_RESULT)


def clear_result() -> None:
    st.session_state.pop(_RESULT, None)


def _append(item: _RawItem) -> None:
    if not is_enabled():
        return
    current = st.session_state.get(_CURRENT)
    if current is None:
        return
    column = _current_column()
    if column is not None:
        item.extra["column"] = column
    active_layout = st.session_state.get(_ACTIVE_LAYOUT)
    if active_layout is not None:
        item.extra["layout_card"] = active_layout
    current["items"].append(item)


def _markdown_to_html(body: str) -> str:
    text = str(body or "").strip()
    if not text:
        return ""
    # Any real markup is passed through: report cards are authored as HTML.
    if re.search(r"<[a-zA-Z][^>]*>", text):
        return text
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", escaped)
    escaped = escaped.replace("\n", "<br/>")
    return f"<p>{escaped}</p>"


def _image_data_uri(payload: object) -> str:
    """Turn any object Streamlit accepts in ``st.image`` into a data URI.

    Streamlit accepts bytes, paths, URLs, PIL images, numpy arrays and the
    ``UploadedFile`` objects returned by ``st.file_uploader``.  All of them have
    to survive into the PDF, otherwise an uploaded chart screenshot is silently
    dropped and only its caption remains.
    """
    # 1) Raw bytes.
    if isinstance(payload, (bytes, bytearray)):
        mime = _sniff_image_mime(bytes(payload[:16]))
        encoded = base64.b64encode(bytes(payload)).decode("ascii")
        return f"data:{mime};base64,{encoded}"
    # 2) Already a data URI or a remote URL.
    if isinstance(payload, str):
        if payload.startswith("data:"):
            return payload
        if payload.startswith(("http://", "https://")):
            return payload
        try:
            with open(payload, "rb") as handle:
                raw = handle.read()
        except OSError:
            return ""
        mime = _sniff_image_mime(raw[:16])
        return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
    # 3) UploadedFile / file-like objects.
    getter = getattr(payload, "getvalue", None)
    if callable(getter):
        try:
            raw = getter()
        except Exception:  # pragma: no cover - defensive
            raw = None
        if isinstance(raw, (bytes, bytearray)):
            mime = _sniff_image_mime(bytes(raw[:16]))
            return f"data:{mime};base64,{base64.b64encode(bytes(raw)).decode('ascii')}"
    # 4) PIL images and numpy arrays.
    try:
        from io import BytesIO

        buffer = BytesIO()
        save = getattr(payload, "save", None)
        if callable(save):
            save(buffer, format="PNG")
            return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        try:
            from PIL import Image

            Image.fromarray(payload).save(buffer, format="PNG")
            return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        except Exception:
            return ""
    except Exception:  # pragma: no cover - defensive
        return ""


def _sniff_image_mime(head: bytes) -> str:
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"GIF8"):
        return "image/gif"
    if head.lstrip().startswith(b"<svg"):
        return "image/svg+xml"
    return "image/png"


class _Render:
    """Result of one chart conversion."""

    __slots__ = ("svg", "panels", "aspect")

    def __init__(self, svg: str | None, panels: int, aspect: float) -> None:
        self.svg = svg
        self.panels = panels
        self.aspect = aspect


def _convert_chart(
    kind: str, payload: object, width: int, height: int
) -> _Render:
    from .svg import (
        altair_panels,
        altair_to_svg,
        plotly_to_svg,
        svg_dimensions,
    )

    panels = 1
    if kind == "altair":
        try:
            panels = altair_panels(payload)
        except Exception:  # pragma: no cover - defensive
            panels = 1
        svg = altair_to_svg(payload, width=width, height=height)
    elif kind == "plotly":
        # Plotly/Kaleido conversion is serialised inside ``svg.plotly_to_svg``;
        # the thread pool below may still race Altair and Plotly conversions,
        # which is fine (vl-convert has no shared subprocess).
        svg = plotly_to_svg(payload, width=width, height=height)
    else:
        svg = None
    svg_width, svg_height = svg_dimensions(svg)
    aspect = (svg_width / svg_height) if svg_width and svg_height else 0.0
    return _Render(svg, panels, aspect)


def _layout_fields(column: object) -> dict[str, object]:
    """Copy Streamlit column metadata onto a PDF item."""
    if not isinstance(column, tuple) or len(column) != 4:
        return {}
    group, index, total, weight = column
    return {
        "layout_group": int(group),
        "layout_index": int(index),
        "layout_total": int(total),
        "layout_weight": float(weight),
    }


def _collapse_columns(items: list[ContentItem]) -> list[ContentItem]:
    """Merge Streamlit columns into one block so the PDF can keep two panels.

    Without this the export flattens ``st.columns`` and puts the chart and the
    metric panel one below the other.
    """
    if not any(item.column for item in items):
        return items
    collapsed: list[ContentItem] = []
    index = 0
    while index < len(items):
        item = items[index]
        if item.column is None:
            collapsed.append(item)
            index += 1
            continue
        group, _, total, _ = item.column
        buckets: list[list[ContentItem]] = [[] for _ in range(total)]
        weights: list[float] = [1.0] * total
        while index < len(items):
            current = items[index]
            if current.column is None or current.column[0] != group:
                break
            _, col_index, _, weight = current.column
            buckets[col_index].append(current)
            weights[col_index] = weight
            index += 1
        if total == 2 and any(buckets):
            # A genuine two-panel layout (chart + metric/insight panel) keeps
            # its side-by-side arrangement in the PDF.
            collapsed.append(
                ContentItem(
                    kind="columns",
                    payload=(buckets, weights),
                    max_height_px=520,
                )
            )
        else:
            # Keep each company card's items adjacent so its title, metadata and
            # chart remain a single logical unit in the PDF.
            for col_index, bucket in enumerate(buckets):
                for child in bucket:
                    child.column = None
                    child.layout_group = group
                    child.layout_index = col_index
                    child.layout_total = total
                    child.layout_weight = weights[col_index]
                    collapsed.append(child)
    return collapsed


def to_content_items(
    modules: list[ModuleSnapshot],
    progress_cb: object | None = None,
) -> list[ModuleSnapshot]:
    """Convert raw capture records into embeddable PDF content.

    Chart conversion dominates export time, so charts are rendered in parallel
    while the original module order is preserved.  ``progress_cb`` (optional)
    is called with ``(done, total, message)`` as each chart finishes so the UI
    can show a live progress indicator.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    layouts = []
    plans: list[tuple[int, int, str, object, int, int, int]] = []
    for module in modules:
        raws = [raw for raw in module.items if isinstance(raw, _RawItem)]  # type: ignore[union-attr]
        layouts.append(raws)
        module_index = len(layouts) - 1
        for item_index, raw in enumerate(raws):
            if raw.kind in ("altair", "plotly"):
                panels = 1
                if raw.kind == "altair":
                    try:
                        from .svg import altair_panels

                        panels = altair_panels(raw.payload)
                    except Exception:  # pragma: no cover - defensive
                        panels = 1
                # The requested box is intentionally smaller than the space the
                # chart gets in the PDF: the SVG is scaled up to fill its cell,
                # which also magnifies axis labels and data labels (they are
                # fixed in chart units, so scaling up makes text readable).
                if panels >= 5:
                    chart_width, chart_height = 1250, 520
                elif panels >= 2:
                    chart_width, chart_height = 900, 360
                else:
                    chart_width, chart_height = 620, 300
                plans.append(
                    (
                        module_index,
                        item_index,
                        raw.kind,
                        raw.payload,
                        chart_width,
                        chart_height,
                        panels,
                    )
                )

    renders: dict[tuple[int, int], _Render] = {}
    if plans:
        with ThreadPoolExecutor(max_workers=6) as pool:
            future_to_key = {
                pool.submit(_convert_chart, kind, payload, width, height): (
                    module_index,
                    item_index,
                )
                for module_index, item_index, kind, payload, width, height, _ in plans
            }
            done = 0
            total = len(plans)
            for future in as_completed(future_to_key):
                key = future_to_key[future]
                try:
                    renders[key] = future.result()
                except Exception:  # pragma: no cover - defensive
                    renders[key] = _Render(None, 1, 0.0)
                done += 1
                if progress_cb is not None:
                    progress_cb(done, total, "chart")

    converted: list[ModuleSnapshot] = []
    for module_index, module in enumerate(modules):
        raws = layouts[module_index]
        items: list[ContentItem] = []
        for item_index, raw in enumerate(raws):
            if raw.kind in ("altair", "plotly"):
                render = renders.get((module_index, item_index))
                if render and render.svg:
                    items.append(
                        ContentItem(
                            kind="svg",
                            payload=render.svg,
                            caption=str(raw.extra.get("caption") or ""),
                            max_height_px=360,
                            column=raw.extra.get("column"),
                            panels=render.panels,
                            aspect=render.aspect,
                            **_layout_fields(raw.extra.get("column") or raw.extra.get("layout_card")),
                        )
                    )
            elif raw.kind == "dataframe":
                frame = raw.payload
                try:
                    table_html = frame.to_html(index=False, border=0, classes="s7-table")
                except Exception:
                    table_html = ""
                if table_html:
                    items.append(
                        ContentItem(
                            kind="table",
                            payload=table_html,
                            column=raw.extra.get("column"),
                            **_layout_fields(raw.extra.get("column") or raw.extra.get("layout_card")),
                        )
                    )
            elif raw.kind == "image":
                uri = _image_data_uri(raw.payload)
                if uri:
                    items.append(
                        ContentItem(
                            kind="image",
                            payload=uri,
                            caption=str(raw.extra.get("caption") or ""),
                            column=raw.extra.get("column"),
                            **_layout_fields(raw.extra.get("column") or raw.extra.get("layout_card")),
                        )
                    )
            elif raw.kind == "markdown":
                markup = _markdown_to_html(str(raw.payload))
                # Skip layout-only marker divs (e.g. the print page-break and
                # aria-hidden grid markers) so they do not add empty gaps to
                # the rebuilt PDF or distort pagination estimates.
                if markup and re.sub(r"<[^>]+>", "", markup).strip():
                    items.append(
                        ContentItem(
                            kind="html",
                            payload=markup,
                            column=raw.extra.get("column"),
                            **_layout_fields(raw.extra.get("column") or raw.extra.get("layout_card")),
                        )
                    )
            elif raw.kind == "metric":
                items.append(
                    ContentItem(
                        kind="metric",
                        payload=raw.payload,
                        column=raw.extra.get("column"),
                        **_layout_fields(raw.extra.get("column") or raw.extra.get("layout_card")),
                    )
                )
        converted.append(
            ModuleSnapshot(
                title=module.title,
                section=module.section,
                items=_collapse_columns(items),
                note=module.note,
            )
        )
    return converted


_LEGEND_PREFIXES = (
    "柱：", "折线：", "左：", "右：", "条形：", "面积：", "点：", "横轴", "纵轴",
)
_LEGEND_HINTS = ("（右轴", "(右轴", "右轴，%)", "右轴，%），")


def _drop_trailing_swatches(items: list[ContentItem]) -> list[ContentItem]:
    """Remove the colour-swatch legend that follows the last chart.

    Streamlit renders ``■ 健康险`` style legend entries below the charts; the
    chart itself already carries the same labels.
    """
    last_chart = -1
    for index, item in enumerate(items):
        if item.kind == "svg":
            last_chart = index
    if last_chart < 0:
        return items
    kept: list[ContentItem] = []
    for index, item in enumerate(items):
        if index > last_chart and item.kind in ("html", "markdown"):
            markup = str(item.payload)
            if len(markup) < 160 and "■" in markup:
                continue
        kept.append(item)
    return kept


def _cluster_charts(items: list[ContentItem]) -> list[ContentItem]:
    """Keep consecutive charts next to each other so two fit on one row.

    Short captions that sit between two charts are moved behind the chart
    cluster; the charts themselves keep their original order.
    """
    out: list[ContentItem] = []
    index = 0
    total = len(items)
    while index < total:
        item = items[index]
        if item.kind != "svg":
            out.append(item)
            index += 1
            continue
        charts = [item]
        captions: list[ContentItem] = []
        cursor = index + 1
        while cursor < total:
            nxt_item = items[cursor]
            if nxt_item.kind == "svg":
                charts.append(nxt_item)
                cursor += 1
            elif nxt_item.kind in ("html", "markdown") and 20 <= len(str(nxt_item.payload)) <= 1200:
                captions.append(nxt_item)
                cursor += 1
            else:
                break
        out.extend(charts)
        out.extend(captions)
        index = cursor
    return out



def _is_legend_text(text: str) -> bool:
    """True when a caption is just a chart-legend explanation.

    Legend captions repeat what the chart already shows (axis notes, series
    descriptions).  Multi-line blocks count as a legend when at least half of
    their lines are legend lines, so a paragraph that merely ends with one
    legend line is kept.
    """
    cleaned = str(text or "").strip()
    if not cleaned:
        return False
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if not lines:
        return False

    def is_legend_line(line: str) -> bool:
        return line.startswith(_LEGEND_PREFIXES) or any(hint in line for hint in _LEGEND_HINTS)

    markers = sum(cleaned.count(prefix) for prefix in _LEGEND_PREFIXES)
    if markers >= 1 and len(cleaned) > 60:
        return True
    if markers >= 2:
        return True
    if len(lines) == 1:
        return is_legend_line(lines[0])
    legend_lines = sum(1 for line in lines if is_legend_line(line))
    return legend_lines >= max(2, len(lines) * 0.5)

def install() -> None:
    """Wrap Streamlit primitives once per process."""
    if getattr(st, _INSTALLED, False):
        return

    original_altair = st.altair_chart
    original_plotly = st.plotly_chart
    original_dataframe = st.dataframe
    original_table = st.table
    original_image = st.image
    original_markdown = st.markdown
    original_caption = st.caption
    original_metric = st.metric
    original_columns = st.columns
    # ``col.metric(...)`` bypasses the module-level ``st.metric`` binding, so the
    # DeltaGenerator method needs its own wrapper.
    try:
        from streamlit.delta_generator import DeltaGenerator

        original_dg_metric = DeltaGenerator.metric
    except Exception:  # pragma: no cover - defensive
        DeltaGenerator = None  # type: ignore[assignment]
        original_dg_metric = None

    def columns(spec=1, *args, **kwargs):
        cols = original_columns(spec, *args, **kwargs)
        try:
            count = len(cols)
        except TypeError:  # pragma: no cover - defensive
            count = 0
        if count >= 2:
            group = _COLUMN_GROUP[0]
            _COLUMN_GROUP[0] += 1
            weights = _column_weights(spec, count)
            for index, col in enumerate(cols):
                _COLUMN_MAP[id(col)] = (group, index, count, weights[index])
        return cols

    def altair_chart(chart, *args, **kwargs):
        _append(_RawItem("altair", chart))
        return original_altair(chart, *args, **kwargs)

    def plotly_chart(figure_or_data, *args, **kwargs):
        _append(_RawItem("plotly", figure_or_data))
        return original_plotly(figure_or_data, *args, **kwargs)

    def dataframe(data=None, *args, **kwargs):
        # Interactive data grids are deliberately excluded from the formal PDF.
        # They are navigational/diagnostic UI, not report content.
        return original_dataframe(data, *args, **kwargs)

    def table(data=None, *args, **kwargs):
        return original_table(data, *args, **kwargs)

    def image(image=None, *args, **kwargs):
        try:
            _append(
                _RawItem(
                    "image",
                    image,
                    {"caption": kwargs.get("caption") or ""},
                )
            )
        except Exception:
            pass
        return original_image(image, *args, **kwargs)

    def markdown(body="", *args, **kwargs):
        if not _is_legend_text(body):
            _append(_RawItem("markdown", body))
        return original_markdown(body, *args, **kwargs)

    def caption(body="", *args, **kwargs):
        if not _is_legend_text(body):
            _append(_RawItem("markdown", body))
        return original_caption(body, *args, **kwargs)

    def metric(*args, **kwargs):
        label = kwargs.get("label", args[0] if args else "")
        value = kwargs.get("value", args[1] if len(args) > 1 else "")
        delta = kwargs.get("delta", args[2] if len(args) > 2 else None)
        try:
            _append(
                _RawItem(
                    "metric",
                    {
                        "label": "" if label is None else str(label),
                        "value": "" if value is None else str(value),
                        "delta": "" if delta is None else str(delta),
                        "tone": str(kwargs.get("delta_color") or "normal"),
                    },
                )
            )
        except Exception:
            pass
        return original_metric(*args, **kwargs)

    if DeltaGenerator is not None and original_dg_metric is not None:

        def dg_metric(self, *args, **kwargs):
            label = kwargs.get("label", args[0] if args else "")
            value = kwargs.get("value", args[1] if len(args) > 1 else "")
            delta = kwargs.get("delta", args[2] if len(args) > 2 else None)
            try:
                _append(
                    _RawItem(
                        "metric",
                        {
                            "label": "" if label is None else str(label),
                            "value": "" if value is None else str(value),
                            "delta": "" if delta is None else str(delta),
                            "tone": str(kwargs.get("delta_color") or "normal"),
                        },
                    )
                )
            except Exception:
                pass
            return original_dg_metric(self, *args, **kwargs)

        DeltaGenerator.metric = dg_metric

    st.columns = columns
    st.altair_chart = altair_chart
    st.plotly_chart = plotly_chart
    st.dataframe = dataframe
    st.table = table
    st.image = image
    st.markdown = markdown
    st.caption = caption
    st.metric = metric
    setattr(st, _INSTALLED, True)
