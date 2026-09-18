"""Canonical final-video branding compositor.

This module owns the entire persistent channel signature:
- fixed top-right logo glass badge
- fixed bottom-right per-scene source badge
- one restrained frame signature

It deliberately does NOT re-encode the finished MP4.  The compositor returns
MoviePy-ready RGBA overlays so branding is rendered in the existing final
encode, avoiding a second full-video FFmpeg pass.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

BRANDING_VERSION = "2026-09-19-v1"

# 1080x1920 Shorts geometry. These are fixed design tokens, not percentages.
LOGO_BOX_SIZE = 156
LOGO_INNER_SIZE = 120
TOP_RIGHT_MARGIN = 36
BOTTOM_RIGHT_MARGIN = 36
SOURCE_BADGE_MAX_WIDTH = 360
SOURCE_BADGE_HEIGHT = 56
FRAME_INSET = 20
FRAME_WIDTH = 2
ACCENT_WIDTH = 72

_ACCENT = (64, 196, 255, 185)
_WHITE = (255, 255, 255, 92)
_GLASS = (7, 13, 23, 178)
_GLASS_HIGHLIGHT = (255, 255, 255, 24)
_SHADOW = (0, 0, 0, 120)
_TEXT = (248, 249, 250, 238)

_SOURCE_NAMES = {
    "wikipedia": "Wikipedia",
    "commons": "Wikimedia Commons",
    "pexels": "Pexels",
    "unsplash": "Unsplash",
    "pixabay": "Pixabay",
    "openverse": "Openverse",
    "duckduckgo": "DuckDuckGo Images",
    "news_source": "Article source",
    "ai-generated": "AI-generated",
    "cached": "Cached visual",
    "visual-rescue": "Factory visual",
    "hf_generated": "AI-generated",
}


def _assets(bot) -> Path | None:
    root = Path(getattr(bot, "BASE_DIR", Path(__file__).resolve().parent))
    brand = root / "brand_assets"
    for name in ("channels4_profile.jpg", "logo.png", "logo.png.jpg"):
        candidate = brand / name
        if candidate.is_file():
            return candidate
    return None


def source_credit_for_type(source_type: str, explicit: str = "") -> str:
    """Normalize trusted visual provenance into the short label shown on-screen."""
    value = re.sub(r"\s+", " ", str(explicit or "")).strip()
    if value:
        value = re.sub(r"^source\s*[:·-]\s*", "", value, flags=re.I).strip()
    if not value:
        key = str(source_type or "").strip().lower()
        value = _SOURCE_NAMES.get(key, re.sub(r"[_-]+", " ", key).strip().title())
    if not value:
        value = "Visual source"
    return f"SOURCE · {value[:44]}"


def _font(size: int, bold: bool = False):
    names = (
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", r"C:\\Windows\\Fonts\\arialbd.ttf")
        if bold
        else ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", r"C:\\Windows\\Fonts\\arial.ttf")
    )
    for name in names:
        try:
            if os.path.isfile(name):
                return ImageFont.truetype(name, int(size))
        except OSError:
            continue
    return ImageFont.load_default()


def _contain_logo(logo_path: Path, size: int) -> Image.Image:
    """Fit the channel mark inside the badge and remove only edge-connected white JPEG matte."""
    logo = Image.open(logo_path).convert("RGBA")
    pixels = logo.load()
    width, height = logo.size
    near_white = set()
    for y in range(height):
        for x in range(width):
            r, g, bl, _ = pixels[x, y]
            if r >= 244 and g >= 244 and bl >= 244:
                near_white.add((x, y))
    stack = []
    for x in range(width):
        if (x, 0) in near_white: stack.append((x, 0))
        if (x, height - 1) in near_white: stack.append((x, height - 1))
    for y in range(height):
        if (0, y) in near_white: stack.append((0, y))
        if (width - 1, y) in near_white: stack.append((width - 1, y))
    visited = set()
    while stack:
        point = stack.pop()
        if point in visited or point not in near_white:
            continue
        visited.add(point)
        x, y = point
        if x > 0: stack.append((x - 1, y))
        if x + 1 < width: stack.append((x + 1, y))
        if y > 0: stack.append((x, y - 1))
        if y + 1 < height: stack.append((x, y + 1))
    for x, y in visited:
        r, g, bl, _ = pixels[x, y]
        pixels[x, y] = (r, g, bl, 0)
    logo.thumbnail((size, size), Image.Resampling.LANCZOS)
    return logo


@lru_cache(maxsize=8)
def _static_brand_overlay(logo_path: str, width: int, height: int) -> np.ndarray:
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    # A very thin frame is the persistent visual signature.
    inset = FRAME_INSET
    draw.rounded_rectangle(
        (inset, inset, width - inset - 1, height - inset - 1),
        radius=18,
        outline=_WHITE,
        width=FRAME_WIDTH,
    )
    draw.line(
        (inset, inset, inset + ACCENT_WIDTH, inset),
        fill=_ACCENT,
        width=FRAME_WIDTH,
    )
    draw.line(
        (width - inset - ACCENT_WIDTH, height - inset - 1, width - inset - 1, height - inset - 1),
        fill=_ACCENT,
        width=FRAME_WIDTH,
    )

    # Fixed top-right glass logo badge.
    x = width - TOP_RIGHT_MARGIN - LOGO_BOX_SIZE
    y = TOP_RIGHT_MARGIN

    shadow = Image.new("RGBA", (LOGO_BOX_SIZE + 18, LOGO_BOX_SIZE + 18), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle(
        (8, 10, LOGO_BOX_SIZE + 8, LOGO_BOX_SIZE + 8),
        radius=30,
        fill=_SHADOW,
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(8))
    canvas.alpha_composite(shadow, (x - 8, y - 8))

    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        (x, y, x + LOGO_BOX_SIZE - 1, y + LOGO_BOX_SIZE - 1),
        radius=28,
        fill=_GLASS,
        outline=_WHITE,
        width=2,
    )
    draw.rounded_rectangle(
        (x + 6, y + 6, x + LOGO_BOX_SIZE - 7, y + 48),
        radius=20,
        fill=_GLASS_HIGHLIGHT,
    )
    draw.line(
        (x + 30, y + 4, x + LOGO_BOX_SIZE - 30, y + 4),
        fill=(255, 255, 255, 110),
        width=2,
    )

    if logo_path:
        logo = _contain_logo(Path(logo_path), LOGO_INNER_SIZE)
        lx = x + (LOGO_BOX_SIZE - logo.width) // 2
        ly = y + (LOGO_BOX_SIZE - logo.height) // 2
        canvas.alpha_composite(logo, (lx, ly))

    return np.asarray(canvas)


@lru_cache(maxsize=32)
def _source_overlay(label: str, width: int, height: int) -> np.ndarray:
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    text = str(label or "SOURCE · Visual source")
    pad_x = 18
    font = _font(24)
    for size in range(24, 15, -1):
        candidate = _font(size)
        bbox = draw.textbbox((0, 0), text, font=candidate)
        if bbox[2] - bbox[0] + pad_x * 2 <= SOURCE_BADGE_MAX_WIDTH:
            font = candidate
            break
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = max(1, bbox[2] - bbox[0])
    if text_w + pad_x * 2 > SOURCE_BADGE_MAX_WIDTH:
        ellipsis = "…"
        while text and draw.textbbox((0, 0), text + ellipsis, font=font)[2] - draw.textbbox((0, 0), text + ellipsis, font=font)[0] + pad_x * 2 > SOURCE_BADGE_MAX_WIDTH:
            text = text[:-1]
        text = text.rstrip() + ellipsis
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = max(1, bbox[2] - bbox[0])
    box_w = min(SOURCE_BADGE_MAX_WIDTH, text_w + pad_x * 2)
    box_h = SOURCE_BADGE_HEIGHT
    x = width - BOTTOM_RIGHT_MARGIN - box_w
    y = height - BOTTOM_RIGHT_MARGIN - box_h

    draw.rounded_rectangle(
        (x + 3, y + 4, x + box_w + 3, y + box_h + 4),
        radius=18,
        fill=_SHADOW,
    )
    draw.rounded_rectangle(
        (x, y, x + box_w - 1, y + box_h - 1),
        radius=18,
        fill=_GLASS,
        outline=_WHITE,
        width=1,
    )
    draw.rounded_rectangle(
        (x + 5, y + 5, x + box_w - 6, y + 22),
        radius=10,
        fill=_GLASS_HIGHLIGHT,
    )
    draw.line(
        (x + 16, y + 1, x + 68, y + 1),
        fill=_ACCENT,
        width=2,
    )
    draw.text(
        (x + pad_x, y + (box_h - (bbox[3] - bbox[1])) / 2 - bbox[1]),
        text,
        font=font,
        fill=_TEXT,
    )
    return np.asarray(canvas)


def build_scene_branding_overlays(bot, width: int, height: int, source_credit: str = "") -> list[np.ndarray]:
    """Return the final overlay layers for one scene.

    Logo/frame is cached for the entire run. Source badges are cached by label.
    No image files are written and no second video encode is performed.
    """
    logo_path = _assets(bot)
    if logo_path is None:
        print("   [Branding] Logo asset missing; frame signature remains active.", flush=True)

    # The final compositor owns the on-screen provenance label.
    label = source_credit_for_type("", source_credit)
    return [
        _static_brand_overlay(str(logo_path or ""), int(width), int(height)),
        _source_overlay(label, int(width), int(height)),
    ]



__all__ = [
    "BRANDING_VERSION",
    "LOGO_BOX_SIZE",
    "LOGO_INNER_SIZE",
    "source_credit_for_type",
    "build_scene_branding_overlays",
]
