"""Polished Shorts subtitle and channel-branding renderer.

Keeps captions readable, compact, and inside a predictable 1-2 line safe area.
Also supplies the channel-logo badge used by the legacy video compositor.
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


def _measure_line(words, font):
    if not words:
        return 0.0
    space = font.getlength(" ")
    return sum(font.getlength(word) for word in words) + space * max(0, len(words) - 1)


def _split_lines(words, font, max_width):
    """Wrap into at most two balanced lines whenever the caption needs wrapping."""
    lines = []
    current = []
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

    best = None
    for split in range(1, len(words)):
        left = words[:split]
        right = words[split:]
        left_w = _measure_line(left, font)
        right_w = _measure_line(right, font)
        overflow = max(0.0, left_w - max_width) + max(0.0, right_w - max_width)
        balance = abs(left_w - right_w)
        score = (overflow, balance)
        if best is None or score < best[0]:
            best = (score, left, right)
    return [best[1], best[2]] if best else [words]


def _fit_layout(words, base_font_size, font_path, max_width, max_lines=2):
    """Use the largest font that keeps captions inside the safe width."""
    size = int(base_font_size)
    while size >= 40:
        font = _load_font(font_path, size)
        lines = _split_lines(words, font, max_width)
        if len(lines) <= max_lines and all(_measure_line(line, font) <= max_width + 1 for line in lines):
            return font, lines
        size -= 2
    font = _load_font(font_path, 40)
    return font, _split_lines(words, font, max_width)[:max_lines]


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
    """Render a compact premium caption card with a single active-word highlight."""
    width = max(1, int(video_width))
    height = 260
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    words = [_clean_word(item.get("word") if isinstance(item, dict) else item) for item in chunk]
    words = [word for word in words if word]
    if not words:
        canvas.save(output_path)
        return output_path

    max_text_width = int(width * 0.82)
    base_font_size = max(48, min(64, int(width * 0.056)))
    font, lines = _fit_layout(words, base_font_size, font_path, max_text_width, max_lines=2)
    font_size = getattr(font, "size", base_font_size)
    line_height = int(font_size * 1.12)
    line_gap = max(5, int(font_size * 0.10))
    pad_x = max(24, int(width * 0.028))
    pad_y = max(17, int(width * 0.016))

    text_block_h = len(lines) * line_height + max(0, len(lines) - 1) * line_gap
    card_w = min(width - 48, max_text_width + pad_x * 2)
    card_h = text_block_h + pad_y * 2
    card_x = (width - card_w) // 2
    card_y = max(8, height - card_h - int(width * 0.018))
    radius = min(30, max(18, card_h // 2))

    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle(
        (card_x + 5, card_y + 8, card_x + card_w + 5, card_y + card_h + 8),
        radius=radius,
        fill=(0, 0, 0, 145),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(11))
    canvas = Image.alpha_composite(canvas, shadow)

    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        (card_x, card_y, card_x + card_w, card_y + card_h),
        radius=radius,
        fill=(8, 14, 24, 214),
        outline=(255, 255, 255, 62),
        width=2,
    )
    accent_x1 = card_x + int(card_w * 0.18)
    accent_x2 = card_x + int(card_w * 0.82)
    draw.rounded_rectangle(
        (accent_x1, card_y + 2, accent_x2, card_y + 6),
        radius=2,
        fill=(255, 190, 70, 235),
    )

    normal = (250, 252, 255, 255)
    active = (255, 194, 78, 255)
    active_glow = (255, 194, 78, 42)
    active_index = _validate_active_index(active_index, len(words))

    flat_index = 0
    y = card_y + pad_y
    space = font.getlength(" ")
    for line in lines:
        widths = [font.getlength(word) for word in line]
        total = sum(widths) + space * max(0, len(line) - 1)
        x = (width - total) / 2
        for word, word_width in zip(line, widths):
            is_active = flat_index == active_index
            if is_active:
                draw.rounded_rectangle(
                    (x - 8, y + max(3, int(font_size * 0.12)), x + word_width + 8, y + font_size + 8),
                    radius=10,
                    fill=active_glow,
                )
            draw.text(
                (x, y),
                word,
                font=font,
                fill=active if is_active else normal,
                stroke_width=2,
                stroke_fill=(0, 0, 0, 220),
            )
            if is_active:
                underline_y = y + font_size + 7
                draw.rounded_rectangle(
                    (x, underline_y, x + word_width, underline_y + 3),
                    radius=2,
                    fill=active,
                )
            x += word_width + space
            flat_index += 1
        y += line_height + line_gap

    canvas.save(output_path)
    return output_path


def create_glossy_logo_watermark(logo_path, size=112):
    """Create the compact channel badge used in the final video compositor."""
    if not logo_path or not os.path.exists(logo_path):
        return None
    try:
        size = max(72, min(120, int(size or 112)))
        radius = max(18, int(size * 0.20))
        border = max(2, int(size * 0.018))
        logo = Image.open(logo_path).convert("RGBA")
        logo.thumbnail((size - 14, size - 14), Image.Resampling.LANCZOS)

        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow)
        shadow_draw.rounded_rectangle((5, 6, size - 1, size), radius=radius, fill=(0, 0, 0, 135))
        shadow = shadow.filter(ImageFilter.GaussianBlur(max(4, size // 12)))
        canvas = Image.alpha_composite(canvas, shadow)

        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle(
            (2, 2, size - 3, size - 3),
            radius=radius,
            fill=(5, 10, 18, 188),
            outline=(255, 255, 255, 105),
            width=border,
        )
        draw.rounded_rectangle(
            (6, 6, size - 7, int(size * 0.38)),
            radius=max(10, radius - 5),
            fill=(255, 255, 255, 24),
        )

        x = (size - logo.width) // 2
        y = (size - logo.height) // 2
        mask = Image.new("L", logo.size, 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.rounded_rectangle(
            (0, 0, logo.width - 1, logo.height - 1),
            radius=max(8, int(radius * 0.65)),
            fill=255,
        )
        canvas.paste(logo, (x, y), mask)
        return canvas
    except Exception:
        return None


def patch_subtitle_pipeline(bot):
    if getattr(bot, "_subtitle_pipeline_patch_installed", False):
        return bot
    run_robot = getattr(bot, "run_robot", None)
    if run_robot is not None and hasattr(run_robot, "__globals__"):
        namespace = run_robot.__globals__
        namespace["generate_karaoke_clip"] = generate_readable_karaoke_clip
        namespace["create_glossy_logo_watermark"] = create_glossy_logo_watermark
    bot.generate_karaoke_clip = generate_readable_karaoke_clip
    bot.create_glossy_logo_watermark = create_glossy_logo_watermark
    bot._subtitle_pipeline_patch_installed = True
    print("   [Subtitle Patch] Premium 1–2 line karaoke + compact channel badge installed.", flush=True)
    return bot
