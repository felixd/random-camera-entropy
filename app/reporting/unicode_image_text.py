#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unicode/TrueType text rendering for OpenCV-generated diagnostic images.

OpenCV's built-in Hershey fonts contain only a small ASCII subset.  This
module draws text with Pillow and an installed system font, preserving Polish
characters without bundling a font file in the project.
"""
from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image, ImageDraw, ImageFont


_REGULAR_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/opentype/noto/NotoSans-Regular.ttf",
)
_BOLD_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/opentype/noto/NotoSans-Bold.ttf",
)


def _existing(paths: Iterable[str]) -> str | None:
    for value in paths:
        if value and Path(value).is_file():
            return value
    return None


@lru_cache(maxsize=2)
def font_path(bold: bool = False) -> str | None:
    """Return an installed Unicode font path without shipping font binaries."""
    environment = os.environ.get("CAMERA_ENTROPY_FONT_BOLD" if bold else "CAMERA_ENTROPY_FONT", "").strip()
    result = _existing((environment,))
    if result:
        return result

    result = _existing(_BOLD_CANDIDATES if bold else _REGULAR_CANDIDATES)
    if result:
        return result

    # Last-resort discovery for distributions with a different font layout.
    family = "DejaVu Sans:style=Bold" if bold else "DejaVu Sans"
    try:
        completed = subprocess.run(
            ["fc-match", "-f", "%{file}", family],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2.0,
        )
        candidate = completed.stdout.strip()
        if completed.returncode == 0 and candidate and Path(candidate).is_file():
            return candidate
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        pass
    return None


@lru_cache(maxsize=64)
def unicode_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    size = max(8, int(size))
    path = font_path(bool(bold))
    if path:
        return ImageFont.truetype(path, size=size)
    # Pillow's embedded fallback is preferable to corrupting the image.  A
    # deployment without any Unicode TTF should set CAMERA_ENTROPY_FONT.
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10 compatibility
        return ImageFont.load_default()


def font_description() -> str:
    return font_path(False) or "Pillow default font"


class UnicodeTextCanvas:
    """Batch Unicode text drawing over a BGR NumPy image."""

    def __init__(self, bgr: np.ndarray):
        if bgr.ndim != 3 or bgr.shape[2] != 3:
            raise ValueError("expected HxWx3 BGR image")
        rgb = np.ascontiguousarray(bgr[:, :, ::-1])
        self.image = Image.fromarray(rgb, mode="RGB")
        self.draw = ImageDraw.Draw(self.image)

    @staticmethod
    def _rgb(color_bgr: tuple[int, int, int]) -> tuple[int, int, int]:
        return int(color_bgr[2]), int(color_bgr[1]), int(color_bgr[0])

    def text(
        self,
        xy: tuple[float, float],
        value: object,
        *,
        size: int = 16,
        color_bgr: tuple[int, int, int] = (20, 20, 20),
        bold: bool = False,
        anchor: str = "lt",
    ) -> None:
        self.draw.text(
            xy,
            str(value),
            font=unicode_font(size, bold),
            fill=self._rgb(color_bgr),
            anchor=anchor,
        )

    def rotated_text(
        self,
        center: tuple[float, float],
        value: object,
        *,
        size: int = 16,
        color_bgr: tuple[int, int, int] = (20, 20, 20),
        bold: bool = False,
        angle: float = 90.0,
    ) -> None:
        text = str(value)
        font = unicode_font(size, bold)
        probe = ImageDraw.Draw(Image.new("RGBA", (1, 1), (0, 0, 0, 0)))
        left, top, right, bottom = probe.textbbox((0, 0), text, font=font)
        width = max(1, right - left + 10)
        height = max(1, bottom - top + 10)
        layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text(
            (5 - left, 5 - top), text, font=font,
            fill=self._rgb(color_bgr) + (255,),
        )
        rotated = layer.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)
        x = int(round(center[0] - rotated.width / 2.0))
        y = int(round(center[1] - rotated.height / 2.0))
        self.image.paste(rotated, (x, y), rotated)

    def finish(self) -> np.ndarray:
        rgb = np.asarray(self.image, dtype=np.uint8)
        return np.ascontiguousarray(rgb[:, :, ::-1])
