"""Optional local OCR helpers for image-only solvency report pages."""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


@dataclass(frozen=True)
class OCRToken:
    text: str
    score: float
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def x_center(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def y_center(self) -> float:
        return (self.y0 + self.y1) / 2


_ENGINE = None
_ENGINE_LOCK = threading.RLock()
_CACHE: dict[tuple[str, int, float], tuple[OCRToken, ...]] = {}
_MAX_CACHE_ITEMS = 192
_CACHE_DIR = (
    Path(__file__).resolve().parents[2]
    / "gi_solvency_cache"
    / "ocr_v1"
)


def ocr_available() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


def _engine_instance():
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    from rapidocr_onnxruntime import RapidOCR

    _ENGINE = RapidOCR()
    return _ENGINE


def _digest(pdf_bytes: bytes) -> str:
    return hashlib.sha1(pdf_bytes).hexdigest()


def _disk_cache_path(key: tuple[str, int, float]) -> Path:
    digest, page, scale = key
    return _CACHE_DIR / digest / f"{page}_{scale:.2f}.json"


def _load_disk_cache(
    key: tuple[str, int, float],
) -> tuple[OCRToken, ...] | None:
    path = _disk_cache_path(key)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        tokens = tuple(
            OCRToken(
                text=str(item["text"]),
                score=float(item["score"]),
                x0=float(item["x0"]),
                y0=float(item["y0"]),
                x1=float(item["x1"]),
                y1=float(item["y1"]),
            )
            for item in payload
        )
    except (OSError, ValueError, TypeError, KeyError):
        return None
    return tokens


def _write_disk_cache(
    key: tuple[str, int, float],
    tokens: Sequence[OCRToken],
) -> None:
    path = _disk_cache_path(key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                [
                    {
                        "text": token.text,
                        "score": token.score,
                        "x0": token.x0,
                        "y0": token.y0,
                        "x1": token.x1,
                        "y1": token.y1,
                    }
                    for token in tokens
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        temporary.replace(path)
    except OSError:
        return


def ocr_page_tokens(
    pdf_bytes: bytes,
    page_number: int,
    *,
    scale: float = 2.0,
) -> tuple[OCRToken, ...]:
    """Return OCR tokens in reading order, or an empty tuple when unavailable."""
    page_number = int(page_number)
    if page_number < 1 or not ocr_available():
        return ()
    key = (_digest(pdf_bytes), page_number, float(scale))
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    cached = _load_disk_cache(key)
    if cached is not None:
        _CACHE[key] = cached
        return cached

    import fitz
    import numpy as np

    with fitz.open(stream=pdf_bytes, filetype="pdf") as document:
        if page_number > len(document):
            return ()
        page = document.load_page(page_number - 1)
        pixmap = page.get_pixmap(
            matrix=fitz.Matrix(float(scale), float(scale)),
            alpha=False,
        )
        image = np.frombuffer(
            pixmap.samples,
            dtype=np.uint8,
        ).reshape(pixmap.height, pixmap.width, pixmap.n)

    with _ENGINE_LOCK:
        result, _elapsed = _engine_instance()(image)
    tokens = []
    for box, text, score in result or []:
        if not text or float(score or 0) < 0.35:
            continue
        points = [(float(point[0]), float(point[1])) for point in box]
        tokens.append(
            OCRToken(
                text=str(text).strip(),
                score=float(score),
                x0=min(point[0] for point in points),
                y0=min(point[1] for point in points),
                x1=max(point[0] for point in points),
                y1=max(point[1] for point in points),
            )
        )
    tokens.sort(key=lambda item: (round(item.y_center / 4), item.x_center))
    packed = tuple(tokens)
    _write_disk_cache(key, packed)
    if len(_CACHE) >= _MAX_CACHE_ITEMS:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = packed
    return packed


def group_tokens_into_lines(
    tokens: Sequence[OCRToken],
) -> list[list[OCRToken]]:
    """Group nearby OCR tokens into visual lines."""
    lines: list[list[OCRToken]] = []
    centers: list[float] = []
    for token in sorted(tokens, key=lambda item: (item.y_center, item.x0)):
        height = max(1.0, token.y1 - token.y0)
        tolerance = max(4.0, min(12.0, height * 0.55))
        position = next(
            (
                index
                for index, center in enumerate(centers)
                if abs(token.y_center - center) <= tolerance
            ),
            None,
        )
        if position is None:
            lines.append([token])
            centers.append(token.y_center)
        else:
            lines[position].append(token)
            centers[position] = sum(
                item.y_center for item in lines[position]
            ) / len(lines[position])
    ordered = sorted(zip(centers, lines), key=lambda item: item[0])
    return [sorted(line, key=lambda item: item.x0) for _, line in ordered]


def page_text_lines(
    pdf_bytes: bytes,
    page_number: int,
    *,
    scale: float = 2.0,
) -> list[str]:
    """Return OCR text grouped by visual line."""
    return [
        "".join(token.text for token in line).strip()
        for line in group_tokens_into_lines(
            ocr_page_tokens(pdf_bytes, page_number, scale=scale)
        )
        if "".join(token.text for token in line).strip()
    ]


def page_cell_lines(
    pdf_bytes: bytes,
    page_number: int,
    *,
    scale: float = 2.0,
) -> list[str]:
    """Return one OCR token per line for cell-per-line company parsers."""
    tokens = ocr_page_tokens(pdf_bytes, page_number, scale=scale)
    return [
        token.text
        for line in group_tokens_into_lines(tokens)
        for token in line
        if token.text
    ]


def pages_text_lines(
    pdf_bytes: bytes,
    pages: Iterable[int],
    *,
    scale: float = 2.0,
) -> list[str]:
    lines: list[str] = []
    for page in pages:
        lines.extend(page_text_lines(pdf_bytes, int(page), scale=scale))
    return lines
