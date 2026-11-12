"""Vector conversion helpers: Altair and Plotly figures to inline SVG."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time

logger = logging.getLogger(__name__)

# Repeated exports (and fragment reruns) usually hand us the very same chart
# objects, so cache the rendered SVG by spec hash.  Vega-Lite specs embed the
# data, so the hash is a faithful cache key.
_SVG_CACHE: dict[str, str | None] = {}
_SVG_CACHE_LIMIT = 128


def _cache_key(prefix: str, spec: dict, width: int | None, height: int | None) -> str:
    digest = hashlib.sha1(
        json.dumps(spec, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return f"{prefix}:{digest}:{width}x{height}"


def _ensure_viewbox(svg: str) -> str:
    """Give the SVG a viewBox so CSS keeps the aspect ratio when scaling."""
    if not svg:
        return svg
    head_end = svg.find(">")
    if head_end < 0:
        return svg
    head = svg[: head_end + 1]
    if "viewBox" in head:
        return svg
    match = re.search(r'width="([0-9.]+)"[^>]*height="([0-9.]+)"', head)
    if match:
        width, height = match.group(1), match.group(2)
    else:
        match = re.search(r'height="([0-9.]+)"[^>]*width="([0-9.]+)"', head)
        if not match:
            return svg
        height, width = match.group(1), match.group(2)
    return head[:-1] + f' viewBox="0 0 {width} {height}"' + svg[head_end:]


# A panel narrower than this makes axis labels overlap when the SVG is
# scaled down to the page width, so panel sizes are clamped to a floor.
_MIN_PANEL_WIDTH = 250
_MIN_PANEL_HEIGHT = 190

def _facet_values(spec: dict) -> set[str] | None:
    """Distinct facet values found in the embedded dataset, if any."""
    facet = spec.get("facet")
    datasets = spec.get("datasets") or {}
    if not isinstance(facet, dict):
        return None
    field = facet.get("field")
    if not field:
        return None
    for records in datasets.values():
        if not isinstance(records, list) or not records:
            continue
        try:
            return {str(record.get(field)) for record in records}
        except AttributeError:
            continue
    return None


def _facet_row_count(spec: dict, cols: int) -> int:
    """Best-effort row count of a faceted chart from its embedded data."""
    values = _facet_values(spec)
    if not values:
        return 1
    return max(1, -(-len(values) // max(cols, 1)))


def _panel_count(spec: dict) -> int:
    """Count leaf charts inside a (possibly nested) Vega-Lite spec."""
    if not isinstance(spec, dict):
        return 1
    for key in ("hconcat", "vconcat", "concat"):
        children = spec.get(key)
        if isinstance(children, list) and children:
            return sum(_panel_count(child) for child in children)
    inner = spec.get("spec")
    if isinstance(inner, dict) and isinstance(spec.get("facet"), dict):
        cols = int(spec.get("columns") or 1)
        rows = _facet_row_count(spec, max(cols, 1))
        return _panel_count(inner) * max(cols, 1) * rows
    return 1


def _size_spec(spec: dict, width: float, height: float, depth: int = 0) -> None:
    """Distribute the requested box size across every sub-chart in ``spec``.

    Vega-Lite ignores a top-level ``width``/``height`` as soon as the spec is
    faceted or concatenated; the size has to live on the leaf specs.  Without
    this, faceted company grids render at their own tiny panel size and the
    PDF shows unreadable labels.
    """
    if not isinstance(spec, dict) or depth > 8:
        return
    children = spec.get("hconcat")
    if isinstance(children, list) and children:
        share = width / len(children)
        for child in children:
            _size_spec(child, share, height, depth + 1)
        return
    children = spec.get("vconcat")
    if isinstance(children, list) and children:
        share = height / len(children)
        for child in children:
            _size_spec(child, width, share, depth + 1)
        return
    children = spec.get("concat")
    if isinstance(children, list) and children:
        cols = int(spec.get("columns") or 1) or len(children)
        cols = max(1, min(cols, len(children)))
        rows = -(-len(children) // cols)
        for child in children:
            _size_spec(child, width / cols, height / rows, depth + 1)
        return
    inner = spec.get("spec")
    if isinstance(inner, dict) and isinstance(spec.get("facet"), dict):
        values = _facet_values(spec)
        panel_total = _panel_count(inner)
        total = len(values) * panel_total if values else panel_total
        # Re-wrap the facets for print: a single row of ten company panels is
        # never legible on a 1600px page, so use as many columns as fit while
        # keeping every panel at a readable width.
        cols = max(1, int(width // _MIN_PANEL_WIDTH))
        if values:
            cols = max(1, min(cols, len(values)))
        rows = max(1, -(-total // cols))
        spec["columns"] = cols
        _size_spec(inner, width / cols, height / rows, depth + 1)
        return
    # Leaf spec: clamp only when the requested panel would be unreadable.
    panel_width = max(_MIN_PANEL_WIDTH, min(width, 1400))
    panel_height = max(_MIN_PANEL_HEIGHT, min(height, 900))
    spec["width"] = int(round(panel_width))
    spec["height"] = int(round(panel_height))


def svg_dimensions(svg: str | None) -> tuple[int, int]:
    """Return the intrinsic ``(width, height)`` of an SVG string."""
    if not svg:
        return (0, 0)
    head_end = svg.find(">")
    head = svg[: head_end + 1] if head_end > 0 else svg[:400]
    match = re.search(r'width="([0-9.]+)(?:px)?"[^>]*height="([0-9.]+)(?:px)?"', head)
    if not match:
        match = re.search(r'height="([0-9.]+)(?:px)?"[^>]*width="([0-9.]+)(?:px)?"', head)
        if match:
            return (int(float(match.group(2))), int(float(match.group(1))))
        viewbox = re.search(r'viewBox="0 0 ([0-9.]+) ([0-9.]+)"', head)
        if viewbox:
            return (int(float(viewbox.group(1))), int(float(viewbox.group(2))))
        return (0, 0)
    return (int(float(match.group(1))), int(float(match.group(2))))


def altair_panels(chart: object) -> int:
    """Number of sub-charts in an Altair chart (1 for a plain chart)."""
    try:
        return _panel_count(chart.to_dict())
    except Exception:  # pragma: no cover - defensive
        return 1


def altair_to_svg(
    chart: object, *, width: int | None = None, height: int | None = None
) -> str | None:
    """Convert an Altair chart to an SVG string at the requested size.

    Altair 6 no longer ships ``chart.to_svg``; ``vl-convert-python`` is the
    supported vector backend and is already a project dependency.
    """
    try:
        import vl_convert as vlc

        spec = chart.to_dict()
        # ``autosize: container`` only works inside a sized HTML box; in the
        # standalone PDF document it would drop the explicit width/height.
        if spec.get("autosize") in ("container", "fit"):
            spec["autosize"] = "pad"
        _size_spec(
            spec,
            float(width or 700),
            float(height or 340),
        )
        key = _cache_key("altair", spec, width, height)
        if key in _SVG_CACHE:
            return _SVG_CACHE[key]
        if len(_SVG_CACHE) > _SVG_CACHE_LIMIT:
            _SVG_CACHE.clear()
        svg = _ensure_viewbox(vlc.vegalite_to_svg(spec))
        _SVG_CACHE[key] = svg
        return svg
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Altair chart could not be converted to SVG: %s", exc)
        return None


# Kaleido spawns a Chromium subprocess per conversion.  Running several of
# those in parallel (the export uses a thread pool) races the subprocess
# lifecycle and surfaces the "Couldn't close or kill browser subprocess"
# error, so all Plotly conversions are serialised and retried once.
_KALEIDO_LOCK = threading.Lock()


def plotly_to_svg(
    fig: object, *, width: int | None = None, height: int | None = None
) -> str | None:
    """Convert a Plotly figure to an SVG string through Kaleido."""
    kwargs: dict = {"format": "svg"}
    if width:
        kwargs["width"] = int(width)
    if height:
        kwargs["height"] = int(height)

    payload: bytes | str | None = None
    last_error: Exception | None = None
    with _KALEIDO_LOCK:
        for attempt in range(2):
            try:
                payload = fig.to_image(**kwargs)  # type: ignore[attr-defined]
                break
            except Exception as exc:  # pragma: no cover - defensive
                last_error = exc
                logger.warning(
                    "Plotly figure could not be converted to SVG (attempt %d): %s",
                    attempt + 1,
                    exc,
                )
                time.sleep(0.5 * (attempt + 1))
    if payload is None:
        logger.warning("Plotly figure could not be converted to SVG: %s", last_error)
        return None
    if isinstance(payload, bytes):
        return _ensure_viewbox(payload.decode("utf-8", errors="replace"))
    return _ensure_viewbox(str(payload))
