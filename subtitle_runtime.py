"""Polished Shorts subtitle and channel-branding renderer.

Keeps captions large, readable, and inside a predictable 1-2 line safe area.
Also supplies the channel-logo badge used by the legacy video compositor and
softens the legacy full-width frame bars into a quieter branded edge treatment.
"""
from __future__ import annotations

import os
import re
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont


_WINDOWS_FONT_CANDIDATES = (
    r"C:\\Windows\\Fonts\\segoeuib.ttf",
    r"C:\\Windows\\Fonts\\segoeui.ttf",
    r"C:\\Windows\\Fonts\\arialbd.ttf",
    r"C:\\Windows\\Fonts\\arial.ttf",
)
_LINUX_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
)


def _resolve_font_path(font_path: str | None) -> str | None:
    """Resolve a real scalable font instead of Pillow's tiny bitmap fallback."""
    candidates = []
    if font_path:
        candidates.append(str(font_path))
    candidates.extend(_WINDOWS_FONT_CANDIDATES)
    candidates.extend(_LINUX_FONT_CANDIDATES)
    for candidate in candidates:
        try:
            if candidate and os.path.isfile(candidate):
                return candidate
        except Exception:
            continue
    return None


def _load_font(font_path: str, size: int):
    """Load a scalable font; never silently downgrade to the tiny default font."""
    resolved = _resolve_font_path(font_path)
    if resolved:
        try:
            return ImageFont.truetype(resolved, size=max(1, int(size)))
        except Exception:
            pass
    for module_font in ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(module_font, size=max(1, int(size)))
        except Exception:
            continue
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
    while size >= 50:
        font = _load_font(font_path, size)
        lines = _split_lines(words, font, max_width)
        if len(lines) <= max_lines and all(_measure_line(line, font) <= max_width + 1 for line in lines):
            return font, lines
        size -= 2
    font = _load_font(font_path, 50)
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
    """Render very large premium captions without a visible subtitle-box border."""
    width = max(1, int(video_width))
    height = 360
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    words = [_clean_word(item.get("word") if isinstance(item, dict) else item) for item in chunk]
    words = [word for word in words if word]
    if not words:
        canvas.save(output_path)
        return output_path

    max_text_width = int(width * 0.90)
    # Deliberately much larger than the previous 72-92px range.
    base_font_size = max(96, min(124, int(width * 0.108)))
    font, lines = _fit_layout(words, base_font_size, font_path, max_text_width, max_lines=2)
    font_size = getattr(font, "size", base_font_size)
    line_height = int(font_size * 1.05)
    line_gap = max(10, int(font_size * 0.12))
    active_index = _validate_active_index(active_index, len(words))

    text_block_h = len(lines) * line_height + max(0, len(lines) - 1) * line_gap
    y = max(12, (height - text_block_h) // 2)

    normal = (242, 244, 246, 255)
    active = (214, 171, 92, 255)
    shadow = (0, 0, 0, 205)

    flat_index = 0
    space = font.getlength(" ")
    draw = ImageDraw.Draw(canvas)
    for line in lines:
        widths = [font.getlength(word) for word in line]
        total = sum(widths) + space * max(0, len(line) - 1)
        x = (width - total) / 2
        for word, word_width in zip(line, widths):
            is_active = flat_index == active_index
            fill = active if is_active else normal

            draw.text(
                (x + 5, y + 7),
                word,
                font=font,
                fill=shadow,
                stroke_width=4,
                stroke_fill=(0, 0, 0, 145),
            )
            draw.text(
                (x, y),
                word,
                font=font,
                fill=fill,
                stroke_width=2,
                stroke_fill=(0, 0, 0, 220),
            )
            if is_active:
                underline_y = y + font_size + 8
                draw.rounded_rectangle(
                    (x, underline_y, x + word_width, underline_y + 5),
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


def _soften_frame_bars(image_path: str) -> bool:
    """Replace the legacy bars with thicker restrained edge treatment."""
    if not image_path or not os.path.isfile(image_path):
        return False
    try:
        image = Image.open(image_path).convert("RGB")
        width, height = image.size
        band = min(64, max(14, height // 30))
        if height < band * 3:
            return False

        top_source = image.crop((0, band, width, band * 2)).resize((width, band), Image.Resampling.BICUBIC)
        bottom_source = image.crop((0, height - band * 2, width, height - band)).resize((width, band), Image.Resampling.BICUBIC)
        edge = Image.new("RGB", (width, height), (0, 0, 0))
        edge.paste(top_source, (0, 0))
        edge.paste(bottom_source, (0, height - band))
        middle = image.crop((0, band, width, height - band))
        edge.paste(middle, (0, band))

        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        draw.rectangle((0, 0, width, band - 1), fill=(8, 14, 24, 42))
        draw.rectangle((0, height - band, width, height - 1), fill=(8, 14, 24, 50))
        draw.rectangle((0, band - 3, width, band), fill=(205, 166, 89, 135))
        draw.rectangle((0, height - band - 2, width, height - band + 1), fill=(87, 127, 149, 120))
        image = Image.alpha_composite(edge.convert("RGBA"), overlay).convert("RGB")
        image.save(image_path, "JPEG", quality=95)
        return True
    except Exception:
        return False


def _patch_scene_overlay(bot):
    """Post-process scene images after legacy composition and before MoviePy rendering."""
    run_robot = getattr(bot, "run_robot", None)
    if run_robot is None or not hasattr(run_robot, "__globals__"):
        return False
    namespace = run_robot.__globals__
    current = namespace.get("process_visuals_async")
    if current is None or getattr(current, "_soft_frame_overlay_bound", False):
        return bool(current)

    async def polished_process_visuals(*args, **kwargs):
        packages = await current(*args, **kwargs)
        changed = 0
        try:
            for package in packages or []:
                for layer in package or []:
                    path = layer.get("image") if isinstance(layer, dict) else None
                    if path and _soften_frame_bars(path):
                        changed += 1
        except Exception:
            pass
        if changed:
            print(f"   [Overlay Patch] Refined {changed} scene frame edge(s).", flush=True)
        return packages

    polished_process_visuals._soft_frame_overlay_bound = True
    namespace["process_visuals_async"] = polished_process_visuals
    bot.process_visuals_async = polished_process_visuals
    return True


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
    _patch_scene_overlay(bot)
    bot._subtitle_pipeline_patch_installed = True
    print("   [Subtitle Patch] Oversized scalable subtitles + compact channel badge + refined frame overlay installed.", flush=True)
    return bot
