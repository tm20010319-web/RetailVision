"""Unicode text rendering helpers for BGR video overlays."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image, ImageDraw, ImageFont


@dataclass(frozen=True)
class OverlayText:
    text: str
    position: tuple[int, int]
    font_size: int
    color_bgr: tuple[int, int, int]
    bold: bool = False
    stroke_width: int = 0
    stroke_color_bgr: tuple[int, int, int] = (0, 0, 0)


@lru_cache(maxsize=2)
def _font_path(bold: bool) -> Path:
    configured = os.getenv("BEHAVIOR_OVERLAY_FONT_PATH", "").strip()
    candidates = [
        Path(configured) if configured else None,
        Path(r"C:\Windows\Fonts\msyhbd.ttc") if bold else Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc")
        if bold
        else Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK.ttc"),
        Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate
    raise RuntimeError(
        "Chinese overlay font is unavailable; configure "
        "BEHAVIOR_OVERLAY_FONT_PATH"
    )


@lru_cache(maxsize=64)
def overlay_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    if size < 1:
        raise ValueError("font size must be positive")
    return ImageFont.truetype(str(_font_path(bold)), size=size)


def measure_overlay_text(
    text: str,
    *,
    font_size: int,
    bold: bool = False,
    stroke_width: int = 0,
) -> tuple[int, int]:
    left, top, right, bottom = overlay_font(font_size, bold).getbbox(
        text,
        stroke_width=stroke_width,
    )
    return right - left, bottom - top


def draw_overlay_texts_bgr(
    frame: np.ndarray,
    items: Iterable[OverlayText],
) -> np.ndarray:
    """Draw all Unicode labels with one BGR/RGB conversion per frame."""

    pending = tuple(items)
    if not pending:
        return frame
    if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3:
        raise ValueError("frame must be a BGR image")
    image = Image.fromarray(np.ascontiguousarray(frame[:, :, ::-1]))
    draw = ImageDraw.Draw(image)
    for item in pending:
        color = tuple(reversed(item.color_bgr))
        stroke_color = tuple(reversed(item.stroke_color_bgr))
        draw.text(
            item.position,
            item.text,
            font=overlay_font(item.font_size, item.bold),
            fill=color,
            stroke_width=item.stroke_width,
            stroke_fill=stroke_color,
        )
    return np.ascontiguousarray(np.asarray(image)[:, :, ::-1])
