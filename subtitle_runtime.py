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


def _rgba_logo_without_edge_white(logo: Image.Image) -> Image.Image:
    """Remove white JPEG background only where it touches the image edge."""
    rgba = logo.convert("RGBA")
    width, height = rgba.size
    pixels = rgba.load()
    near_white = set()
    for y in range(height):
        for x in range(width):
            r, g, b, _ = pixels[x, y]
            if r >= 244 and g >= 244 and b >= 244:
                near_white.add((x, y))

    stack = []
    for x in range(width):
        if (x, 0) in near_white:
            stack.append((x, 0))
        if (x, height - 1) in near_white:
            stack.append((x, height - 1))
    for y in range(height):
        if (0, y) in near_white:
            stack.append((0, y))
        if (width - 1, y) in near_white:
            stack.append((width - 1, y))

    visited = set()
    while stack:
        point = stack.pop()
        if point in visited or point not in near_white:
            continue
        visited.add(point)
        x, y = point
        if x > 0:
            stack.append((x - 1, y))
        if x + 1 < width:
            stack.append((x + 1, y))
        if y > 0:
            stack.append((x, y - 1))
        if y + 1 < height:
            stack.append((x, y + 1))

    for x, y in visited:
        r, g, b, _ = pixels[x, y]
        pixels[x, y] = (r, g, b, 0)
    return rgba


def _glass_surface(base: Image.Image, box, radius: int, tint=(7, 13, 23, 120), blur_radius: int = 20):
    """Build a frosted panel from the underlying image, not a flat opaque card."""
    x0, y0, x1, y1 = [int(v) for v in box]
    surface = Image.new("RGBA", base.size, (0, 0, 0, 0))
    panel_w = max(1, x1 - x0)
    panel_h = max(1, y1 - y0)
    crop = base.crop((x0, y0, x1, y1)).convert("RGBA")
    crop = crop.filter(ImageFilter.GaussianBlur(max(2, int(blur_radius))))
    crop = Image.blend(crop, Image.new("RGBA", crop.size, tint), 0.62)

    mask = Image.new("L", (panel_w, panel_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, panel_w - 1, panel_h - 1), radius=max(4, int(radius)), fill=255)
    surface.paste(crop, (x0, y0), mask)

    draw = ImageDraw.Draw(surface)
    draw.rounded_rectangle((x0, y0, x1 - 1, y1 - 1), radius=max(4, int(radius)), outline=(255, 255, 255, 90), width=2)
    highlight_h = max(10, int(panel_h * 0.20))
    highlight = Image.new("RGBA", base.size, (0, 0, 0, 0))
    hmask = Image.new("L", (panel_w, highlight_h), 0)
    ImageDraw.Draw(hmask).rounded_rectangle((0, 0, panel_w - 1, min(highlight_h * 2, highlight_h - 1)), radius=max(4, int(radius)), fill=255)
    highlight.paste((255, 255, 255, 22), (x0, y0), hmask)
    return Image.alpha_composite(surface, highlight)


def generate_readable_karaoke_clip(
    chunk,
    active_index,
    font_path,
    video_width,
    output_path,
    bg_img_path=None,
    source_type="bg",
):
    """Render quiet, readable captions with a single frosted-glass surface.

    Word timings are still respected by the compositor, but the appearance no
    longer jumps between giant highlighted words. The full chunk stays visually
    stable while the audio remains word-synchronised.
    """
    width = max(1, int(video_width))
    height = 300
    transparent = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    words = [_clean_word(item.get("word") if isinstance(item, dict) else item) for item in chunk]
    words = [word for word in words if word]
    if not words:
        transparent.save(output_path, "PNG")
        return output_path

    max_text_width = int(width * 0.80)
    base_font_size = max(48, min(72, int(width * 0.062)))
    font, lines = _fit_layout(words, base_font_size, font_path, max_text_width, max_lines=2)
    font_size = int(getattr(font, "size", base_font_size) or base_font_size)
    line_height = max(42, int(font_size * 1.08))
    line_gap = max(7, int(font_size * 0.10))
    text_h = len(lines) * line_height + max(0, len(lines) - 1) * line_gap
    panel_h = min(190, max(120, text_h + 54))
    panel_w = min(width - 72, max(480, int(width * 0.88)))
    panel_x = (width - panel_w) // 2
    panel_y = (height - panel_h) // 2

    base_for_glass = transparent
    if bg_img_path and os.path.isfile(str(bg_img_path)):
        try:
            bg = Image.open(bg_img_path).convert("RGBA")
            if bg.size != (width, 1920):
                bg = bg.resize((width, 1920), Image.Resampling.LANCZOS)
            center_y = 1160 if str(source_type).lower() == "person" else 1010
            y0 = max(0, center_y - height // 2)
            crop = bg.crop((0, y0, width, min(bg.height, y0 + height)))
            if crop.height < height:
                padded = Image.new("RGBA", (width, height), (7, 13, 23, 255))
                padded.paste(crop, (0, 0))
                crop = padded
            base_for_glass = crop
        except Exception:
            base_for_glass = transparent

    glass_base = _glass_surface(base_for_glass, (panel_x, panel_y, panel_x + panel_w, panel_y + panel_h), 30, tint=(5, 10, 18, 145), blur_radius=18)
    if base_for_glass is transparent:
        glass_base = _glass_surface(
            Image.new("RGBA", (width, height), (9, 15, 25, 255)),
            (panel_x, panel_y, panel_x + panel_w, panel_y + panel_h),
            30,
            tint=(5, 10, 18, 170),
            blur_radius=6,
        )

    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    accent = (64, 196, 255, 185)
    draw.rounded_rectangle(
        (panel_x, panel_y, panel_x + panel_w - 1, panel_y + panel_h - 1),
        radius=30,
        outline=accent,
        width=2,
    )

    normal = (248, 249, 250, 255)
    shadow = (0, 0, 0, 155)
    y = panel_y + max(18, (panel_h - text_h) // 2) - 2
    for line in lines:
        text_w = _measure_line(line, font)
        x = (width - text_w) / 2
        text = " ".join(line)
        draw.text((x + 2, y + 3), text, font=font, fill=shadow, stroke_width=1, stroke_fill=(0, 0, 0, 125))
        draw.text((x, y), text, font=font, fill=normal)
        y += line_height + line_gap

    result = Image.alpha_composite(glass_base.convert("RGBA"), overlay)
    result.save(output_path, "PNG")
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


def create_glossy_logo_watermark(logo_path, size=128):
    """Create a dark frosted glass channel badge and remove JPEG white borders."""
    if not logo_path or not os.path.exists(logo_path):
        return None
    try:
        size = max(88, min(150, int(size or 128)))
        radius = max(20, int(size * 0.22))
        logo = _rgba_logo_without_edge_white(Image.open(logo_path))
        logo.thumbnail((size - 28, size - 28), Image.Resampling.LANCZOS)

        badge = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        sd = ImageDraw.Draw(shadow)
        sd.rounded_rectangle((7, 9, size - 2, size - 1), radius=radius, fill=(0, 0, 0, 145))
        shadow = shadow.filter(ImageFilter.GaussianBlur(max(5, size // 10)))
        badge = Image.alpha_composite(badge, shadow)

        panel = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        pd = ImageDraw.Draw(panel)
        pd.rounded_rectangle((2, 2, size - 4, size - 4), radius=radius, fill=(7, 13, 23, 178), outline=(255, 255, 255, 105), width=2)
        pd.rounded_rectangle((7, 7, size - 9, max(16, int(size * 0.38))), radius=max(10, radius - 6), fill=(255, 255, 255, 24))
        pd.line((size * 0.22, 4, size * 0.78, 4), fill=(255, 255, 255, 110), width=2)
        badge = Image.alpha_composite(badge, panel)

        x = (size - logo.width) // 2
        y = (size - logo.height) // 2
        mask = Image.new("L", logo.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, logo.width - 1, logo.height - 1), radius=max(8, int(radius * 0.65)), fill=255)
        badge.paste(logo, (x, y), mask)
        return badge
    except Exception:
        return None


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
    namespace["create_glossy_logo_watermark"] = create_glossy_logo_watermark
    bot.generate_karaoke_clip = generate_readable_karaoke_clip
    bot.create_glossy_logo_watermark = create_glossy_logo_watermark
    bot._subtitle_pipeline_patch_installed = True
    print("   [Subtitle Patch] Clean glass captions + matching Top-5 cards installed.", flush=True)
    return bot
