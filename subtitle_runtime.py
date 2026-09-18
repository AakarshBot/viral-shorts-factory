"""Premium subtitle, Top-5 card and branding finish runtime.

The visual language is intentionally restrained: clean typography, white text,
frosted-glass surfaces, subtle highlights, and thin accent borders. The same
language is shared by Deep Dive subtitles, Top-5 cards, the channel watermark,
and the final branded finish.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont


_WINDOWS_REGULAR_FONTS = (
    r"C:\\Windows\\Fonts\\arial.ttf",
    r"C:\\Windows\\Fonts\\segoeui.ttf",
    r"C:\\Windows\\Fonts\\calibri.ttf",
)
_WINDOWS_BOLD_FONTS = (
    r"C:\\Windows\\Fonts\\arialbd.ttf",
    r"C:\\Windows\\Fonts\\segoeuib.ttf",
)
_LINUX_REGULAR_FONTS = (
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)
_LINUX_BOLD_FONTS = (
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)


def _resolve_font_path(font_path: str | None) -> str | None:
    candidates: list[str] = []
    if font_path:
        requested = str(font_path)
        candidates.append(requested)
        requested_name = os.path.basename(requested).lower()
        if "bold" not in requested_name and "bd" not in requested_name:
            candidates.extend(_WINDOWS_REGULAR_FONTS)
            candidates.extend(_LINUX_REGULAR_FONTS)
        else:
            candidates.extend(_WINDOWS_BOLD_FONTS)
            candidates.extend(_LINUX_BOLD_FONTS)
    candidates.extend(_WINDOWS_REGULAR_FONTS)
    candidates.extend(_WINDOWS_BOLD_FONTS)
    candidates.extend(_LINUX_REGULAR_FONTS)
    candidates.extend(_LINUX_BOLD_FONTS)
    for candidate in candidates:
        try:
            if candidate and os.path.isfile(candidate):
                return candidate
        except OSError:
            continue
    return None


def _load_font(font_path: str | None, size: int, bold: bool = False):
    resolved = _resolve_font_path(font_path)
    if resolved:
        try:
            return ImageFont.truetype(resolved, size=max(1, int(size)))
        except Exception:
            pass
    fallback_names = ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf") if bold else ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf")
    for module_font in fallback_names:
        try:
            return ImageFont.truetype(module_font, size=max(1, int(size)))
        except Exception:
            continue
    return ImageFont.load_default()


def _load_regular_font(font_path: str | None, size: int):
    requested = str(font_path or "").lower()
    if requested.endswith("bold.ttf") or requested.endswith("bd.ttf"):
        return _load_font(font_path, size, bold=True)
    if requested:
        for path in (font_path, *_WINDOWS_REGULAR_FONTS, *_LINUX_REGULAR_FONTS):
            if path and os.path.isfile(str(path)):
                try:
                    return ImageFont.truetype(str(path), size=max(1, int(size)))
                except Exception:
                    continue
    return _load_font(None, size, bold=False)


def _clean_word(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    return text.strip()


def _measure_line(words: list[str], font) -> float:
    if not words:
        return 0.0
    space = font.getlength(" ")
    return sum(font.getlength(word) for word in words) + space * max(0, len(words) - 1)


def _split_lines(words: list[str], font, max_width: int) -> list[list[str]]:
    if not words:
        return []
    lines: list[list[str]] = []
    current: list[str] = []
    for word in words:
        proposed = _measure_line(current + [word], font)
        if current and proposed > max_width:
            lines.append(current)
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(current)
    if len(lines) <= 2:
        return lines

    best: tuple[tuple[float, float], list[str], list[str]] | None = None
    for split in range(1, len(words)):
        left, right = words[:split], words[split:]
        left_w, right_w = _measure_line(left, font), _measure_line(right, font)
        overflow = max(0.0, left_w - max_width) + max(0.0, right_w - max_width)
        balance = abs(left_w - right_w)
        score = (overflow, balance)
        if best is None or score < best[0]:
            best = (score, left, right)
    return [best[1], best[2]] if best else [words]


def _fit_layout(words: list[str], base_font_size: int, font_path: str | None, max_width: int, max_lines: int = 2):
    for size in range(int(base_font_size), 22, -2):
        font = _load_regular_font(font_path, size)
        lines = _split_lines(words, font, max_width)
        if len(lines) <= max_lines and all(_measure_line(line, font) <= max_width for line in lines):
            return font, lines
    for size in range(20, 9, -2):
        font = _load_regular_font(font_path, size)
        lines = _split_lines(words, font, max_width)
        if len(lines) <= max_lines and all(_measure_line(line, font) <= max_width for line in lines):
            return font, lines
    font = _load_regular_font(font_path, 12)
    return font, _split_lines(words, font, max_width)[:max_lines]


def generate_readable_karaoke_clip(
    chunk,
    active_index,
    font_path,
    video_width,
    output_path,
    bg_img_path=None,
    source_type="bg",
):
    """Render one stable caption card per timing chunk.

    The active word argument remains for API compatibility, but captions no
    longer regenerate an image for every word. One lightweight PNG is created
    per chunk, eliminating the repeated background crop/blur and per-word PNG
    generation that dominated subtitle overhead.
    """
    width = max(1, int(video_width))
    height = 220
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    words = [
        _clean_word(item.get("word") if isinstance(item, dict) else item)
        for item in chunk
    ]
    words = [word for word in words if word]
    if not words:
        image.save(output_path, "PNG")
        return output_path

    max_text_width = int(width * 0.82)
    base_font_size = max(48, min(72, int(width * 0.062)))
    font, lines = _fit_layout(
        words, base_font_size, font_path, max_text_width, max_lines=2
    )
    font_size = int(getattr(font, "size", base_font_size) or base_font_size)
    line_height = max(42, int(font_size * 1.08))
    line_gap = max(6, int(font_size * 0.08))
    text_h = len(lines) * line_height + max(0, len(lines) - 1) * line_gap

    # A quiet, translucent panel: no gloss, no white outline, no accent border.
    panel_h = min(170, max(104, text_h + 42))
    panel_w = min(width - 96, max(440, int(width * 0.84)))
    panel_x = (width - panel_w) // 2
    panel_y = (height - panel_h) // 2
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (panel_x, panel_y, panel_x + panel_w - 1, panel_y + panel_h - 1),
        radius=22,
        fill=(8, 12, 20, 168),
    )

    # Soft shadow only; the caption remains the visual focus.
    y = panel_y + max(14, (panel_h - text_h) // 2) - 1
    for line in lines:
        text = " ".join(line)
        text_w = _measure_line(line, font)
        x = (width - text_w) / 2
        draw.text(
            (x + 2, y + 3),
            text,
            font=font,
            fill=(0, 0, 0, 150),
            stroke_width=1,
            stroke_fill=(0, 0, 0, 130),
        )
        draw.text((x, y), text, font=font, fill=(248, 249, 250, 255))
        y += line_height + line_gap

    image.save(output_path, "PNG")
    return output_path

def render_premium_top5_card(
    bg_img,
    item_number,
    total_items,
    summary_text,
    width=1080,
    height=1920,
    font_choice=None,
):
    """Render a Top-5 card using the same restrained frosted-glass language."""
    canvas = bg_img.convert("RGBA").resize((width, height), Image.Resampling.LANCZOS)
    box = (64, 330, width - 64, height - 330)

    crop = canvas.crop(box).filter(ImageFilter.GaussianBlur(28))
    canvas.paste(crop, box)
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    x0, y0, x1, y1 = box
    draw.rounded_rectangle(box, radius=44, fill=(7, 13, 23, 142), outline=(255, 255, 255, 88), width=2)
    draw.rounded_rectangle((x0 + 6, y0 + 6, x1 - 6, y0 + 84), radius=36, fill=(255, 255, 255, 18))
    draw.rectangle((x0 + 42, y0 + 104, x0 + 185, y0 + 108), fill=(64, 196, 255, 160))

    accent = (64, 196, 255, 245)
    num_font = _load_regular_font(font_choice, 88)
    clean_summary = _clean_word(summary_text)
    body_font, lines = _fit_layout(clean_summary.split(), 58, font_choice, width - 220, 3)
    draw.text((x0 + 54, y0 + 44), f"#{int(item_number)}", font=num_font, fill=accent)

    y = y0 + 230
    line_height = int(getattr(body_font, "size", 58) * 1.18)
    for line in lines[:3]:
        text = " ".join(line)
        tw = draw.textlength(text, font=body_font)
        draw.text(((width - tw) / 2 + 2, y + 3), text, font=body_font, fill=(0, 0, 0, 155))
        draw.text(((width - tw) / 2, y), text, font=body_font, fill=(248, 249, 250, 255))
        y += line_height + 10

    result = Image.alpha_composite(canvas, overlay)
    return result.convert("RGBA")


def patch_subtitle_pipeline(bot):
    if getattr(bot, "_subtitle_pipeline_patch_installed", False):
        return bot
    run_robot = getattr(bot, "run_robot", None)
    namespace = getattr(run_robot, "__globals__", None)
    if not isinstance(namespace, dict):
        return bot

    namespace["render_top5_card"] = render_premium_top5_card
    bot.render_top5_card = render_premium_top5_card
    namespace["generate_karaoke_clip"] = generate_readable_karaoke_clip
    bot.generate_karaoke_clip = generate_readable_karaoke_clip
    bot._subtitle_pipeline_patch_installed = True
    print("   [Subtitle Patch] Clean glass captions + matching Top-5 cards installed.", flush=True)
    return bot
