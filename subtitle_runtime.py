"""Readable Shorts subtitle renderer.

Uses the word timings produced by the strict audio pipeline. The renderer keeps
captions inside a predictable 1–2 line safe area and prevents oversized words
from making the whole subtitle card jump in height.
"""
from __future__ import annotations

import os
import re
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont


def _load_font(font_path: str, size: int):
    try:
        if font_path and os.path.isfile(font_path):
            return ImageFont.truetype(font_path, size=size)
    except Exception:
        pass
    return ImageFont.load_default()


def _clean_word(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _split_lines(words, font, max_width):
    lines = []
    current = []
    current_width = 0
    space = font.getlength(" ")
    for word in words:
        width = font.getlength(word)
        if width > max_width:
            # Keep the word intact rather than shrinking the whole caption.
            # It will be clipped by the card width only in pathological cases.
            width = max_width
        proposed = width if not current else current_width + space + width
        if current and proposed > max_width:
            lines.append(current)
            current = [word]
            current_width = width
        else:
            current.append(word)
            current_width = proposed
    if current:
        lines.append(current)
    if len(lines) <= 2:
        return lines
    # Balance long chunks into two readable lines instead of producing a tiny font.
    midpoint = max(1, len(words) // 2)
    return [words[:midpoint], words[midpoint:]]


def _validate_active_index(active_index: int, word_count: int) -> int:
    try:
        value = int(active_index)
    except (TypeError, ValueError):
        return -1
    return value if 0 <= value < word_count else -1


def generate_readable_karaoke_clip(
    chunk,
    active_index,
    font_path,
    video_width,
    output_path,
    bg_img_path=None,
    source_type="bg",
):
    width = int(video_width)
    height = 300
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    font_size = max(44, min(66, int(width * 0.058)))
    font = _load_font(font_path, font_size)
    words = [_clean_word(item.get("word") if isinstance(item, dict) else item) for item in chunk]
    words = [word for word in words if word]
    if not words:
        canvas.save(output_path)
        return output_path

    max_text_width = int(width * 0.84)
    lines = _split_lines(words, font, max_text_width)
    line_height = int(font_size * 1.18)
    gap = max(8, int(font_size * 0.16))
    text_block_h = len(lines) * line_height + (len(lines) - 1) * gap
    pad_x = int(width * 0.035)
    pad_y = int(width * 0.018)
    card_w = min(width - 60, max_text_width + pad_x * 2)
    card_h = text_block_h + pad_y * 2
    card_x = (width - card_w) // 2
    card_y = height - card_h - int(width * 0.025)

    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle(
        (card_x + 4, card_y + 8, card_x + card_w + 4, card_y + card_h + 8),
        radius=28,
        fill=(0, 0, 0, 150),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(10))
    canvas = Image.alpha_composite(canvas, shadow)

    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        (card_x, card_y, card_x + card_w, card_y + card_h),
        radius=28,
        fill=(7, 13, 22, 218),
        outline=(255, 255, 255, 70),
        width=2,
    )

    accent = (64, 196, 255, 255)
    normal = (255, 255, 255, 255)
    active = (255, 194, 78, 255)
    active_index = _validate_active_index(active_index, len(words))

    flat_index = 0
    y = card_y + pad_y
    for line in lines:
        raw_widths = [font.getlength(word) for word in line]
        total = sum(raw_widths) + font.getlength(" ") * max(0, len(line) - 1)
        x = (width - total) / 2
        for word, word_width in zip(line, raw_widths):
            is_active = flat_index == active_index
            fill = active if is_active else normal
            draw.text((x, y), word, font=font, fill=fill, stroke_width=2, stroke_fill=(0, 0, 0, 205))
            if is_active:
                underline_y = y + font_size + 5
                draw.rounded_rectangle(
                    (x, underline_y, x + word_width, underline_y + 4),
                    radius=2,
                    fill=accent,
                )
            x += word_width + font.getlength(" ")
            flat_index += 1
        y += line_height + gap

    canvas.save(output_path)
    return output_path


def patch_subtitle_pipeline(bot):
    if getattr(bot, "_subtitle_pipeline_patch_installed", False):
        return bot
    run_robot = getattr(bot, "run_robot", None)
    if run_robot is not None and hasattr(run_robot, "__globals__"):
        run_robot.__globals__["generate_karaoke_clip"] = generate_readable_karaoke_clip
    bot.generate_karaoke_clip = generate_readable_karaoke_clip
    bot._subtitle_pipeline_patch_installed = True
    print("   [Subtitle Patch] Readable 1–2 line karaoke renderer installed.", flush=True)
    return bot
