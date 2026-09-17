"""Premium subtitle, Top-5 card and branding finish runtime.

The visual language is intentionally restrained: clean typography, white text,
frosted-glass surfaces, subtle highlights, and thin accent borders. The same
language is shared by Deep Dive subtitles, Top-5 cards, the channel watermark,
and the final branded finish.
"""
from __future__ import annotations

import inspect
import os
import re
import subprocess
import tempfile
import textwrap
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont


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


def _validate_active_index(active_index: int, word_count: int) -> int:
    try:
        value = int(active_index)
    except (TypeError, ValueError):
        return -1
    return value if 0 <= value < word_count else -1


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


def _soften_frame_bars(image_path: str) -> bool:
    """Keep scene edges restrained so the final branding can add the premium finish."""
    if not image_path or not os.path.isfile(image_path):
        return False
    try:
        image = Image.open(image_path).convert("RGB")
        width, height = image.size
        band = min(52, max(12, height // 36))
        if height < band * 3:
            return False
        top = image.crop((0, band, width, band * 2)).resize((width, band), Image.Resampling.BICUBIC)
        bottom = image.crop((0, height - band * 2, width, height - band)).resize((width, band), Image.Resampling.BICUBIC)
        edge = Image.new("RGB", (width, height), (0, 0, 0))
        edge.paste(top, (0, 0))
        edge.paste(image.crop((0, band, width, height - band)), (0, band))
        edge.paste(bottom, (0, height - band))
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        draw.rectangle((0, 0, width, max(0, band - 1)), fill=(7, 13, 23, 32))
        draw.rectangle((0, height - band, width, height - 1), fill=(7, 13, 23, 38))
        draw.rectangle((0, band - 2, width, band), fill=(64, 196, 255, 105))
        draw.rectangle((0, height - band - 2, width, height - band + 1), fill=(255, 255, 255, 72))
        Image.alpha_composite(edge.convert("RGBA"), overlay).convert("RGB").save(image_path, "JPEG", quality=95)
        return True
    except Exception:
        return False


def _patch_scene_overlay(bot):
    run_robot = getattr(bot, "run_robot", None)
    namespace = getattr(run_robot, "__globals__", None)
    if not isinstance(namespace, dict):
        return False
    current = namespace.get("process_visuals_async")
    if current is None or getattr(current, "_soft_frame_overlay_bound", False):
        return bool(current)

    async def polished_process_visuals(*args, **kwargs):
        packages = await current(*args, **kwargs)
        changed = 0
        for package in packages or []:
            for layer in package or []:
                path = layer.get("image") if isinstance(layer, dict) else None
                if path and _soften_frame_bars(path):
                    changed += 1
        if changed:
            print(f"   [Overlay Patch] Refined {changed} scene frame edge(s).", flush=True)
        return packages

    polished_process_visuals._soft_frame_overlay_bound = True
    namespace["process_visuals_async"] = polished_process_visuals
    bot.process_visuals_async = polished_process_visuals
    return True


def _patch_top5_card(bot):
    current = getattr(bot, "render_top5_card", None)
    run_robot = getattr(bot, "run_robot", None)
    namespace = getattr(run_robot, "__globals__", None)
    if not isinstance(namespace, dict):
        return
    if getattr(current, "_premium_top5_bound", False):
        return

    def premium_top5(bg_img, item_number, total_items, summary_text, width=1080, height=1920, font_choice=None):
        return render_premium_top5_card(bg_img, item_number, total_items, summary_text, width, height, font_choice)

    premium_top5._premium_top5_bound = True
    bot.render_top5_card = premium_top5
    namespace["render_top5_card"] = premium_top5


def _patch_deep_dive_subtitle_condition(bot):
    """Let Deep Dive scene 1 receive normal subtitles now that its hook card is gone."""
    run_robot = getattr(bot, "run_robot", None)
    namespace = getattr(run_robot, "__globals__", None)
    if not isinstance(namespace, dict):
        return False
    current = namespace.get("compile_video")
    if not callable(current) or getattr(current, "_deep_dive_subtitles_bound", False):
        return False
    try:
        source = inspect.getsource(current)
        changes = []
        marker = "if not is_outro_scene and not is_hook_scene and idx < len(word_timings):"
        replacement = "if not is_outro_scene and idx < len(word_timings):"
        if marker in source:
            source = source.replace(marker, replacement, 1)
            changes.append("Deep Dive scene 1 subtitles enabled")

        logo_start = '        logo_file_path = os.path.join(BRAND_ASSETS_DIR, "logo.png")'
        logo_end = '        print("   [+] Writing video file to disk for Quality Control...")'
        if logo_start in source and logo_end in source:
            start = source.index(logo_start)
            end = source.index(logo_end)
            source = source[:start] + "        # Final branding_runtime owns the channel logo; do not duplicate it in the compositor.\n" + source[end:]
            changes.append("legacy compile-time logo removed")

        if not changes:
            return False
        patched_source = textwrap.dedent(source)
        exec(patched_source, namespace)
        patched = namespace.get("compile_video")
        if not callable(patched):
            return False
        patched._deep_dive_subtitles_bound = True
        patched._premium_compile_logo_bound = True
        bot.compile_video = patched
        namespace["compile_video"] = patched
        print("   [Subtitle Patch] " + "; ".join(changes) + ".", flush=True)
        return True
    except Exception as exc:
        print(f"   [Subtitle Patch] Could not rebind compile_video: {type(exc).__name__}: {exc}", flush=True)
        return False


def _patch_endpoint_subtitles():
    """Use the same quiet typography for integrity-layer hook/outro captions."""
    try:
        import pipeline_integrity_runtime
    except Exception:
        return
    current = getattr(pipeline_integrity_runtime, "_add_endpoint_subtitles", None)
    if not callable(current) or getattr(current, "_premium_endpoint_bound", False):
        return

    def premium_endpoint_subtitles(video_path, audio_paths, word_timings):
        if not video_path or not os.path.isfile(video_path) or not word_timings:
            return video_path
        try:
            durations = []
            for path in audio_paths[: len(word_timings)]:
                probe = subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path],
                    capture_output=True, text=True, timeout=10, check=False,
                )
                durations.append(max(0.1, float((probe.stdout or "0").strip()) + 0.25))

            temp_dir = tempfile.mkdtemp(prefix="shorts_subtitles_", dir=os.path.dirname(video_path) or None)
            srt_path = os.path.join(temp_dir, "endpoint.srt")

            def write_srt(path, timings, offset=0.0):
                events, chunk, start, last_end = [], [], None, None
                for item in timings:
                    word = str(item.get("word", "")).replace("\u00a0", " ").strip()
                    if not word:
                        continue
                    item_start = max(0.0, float(item.get("start", 0.0)) + offset)
                    item_end = max(item_start + 0.08, float(item.get("end", item_start + 0.1)) + offset)
                    if start is None:
                        start = item_start
                    chunk.append(word)
                    last_end = item_end
                    if len(chunk) >= 6 or (last_end - start) >= 2.2:
                        events.append((start, last_end, " ".join(chunk)))
                        chunk, start = [], None
                if chunk and start is not None and last_end is not None:
                    events.append((start, last_end, " ".join(chunk)))
                if not events:
                    return False

                def stamp(seconds):
                    millis = max(0, int(round(seconds * 1000)))
                    hours, millis = divmod(millis, 3_600_000)
                    minutes, millis = divmod(millis, 60_000)
                    secs, millis = divmod(millis, 1000)
                    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

                with open(path, "w", encoding="utf-8") as handle:
                    for index, (start, end, text) in enumerate(events, 1):
                        handle.write(f"{index}\n{stamp(start)} --> {stamp(end)}\n{text}\n\n")
                return True

            ok = write_srt(srt_path, word_timings[0], 0.0)
            last_index = len(word_timings) - 1
            if last_index != 0:
                outro_path = os.path.join(temp_dir, "outro.srt")
                if write_srt(outro_path, word_timings[last_index], sum(durations[:last_index]) if durations else 0.0):
                    with open(srt_path, "a", encoding="utf-8") as target, open(outro_path, "r", encoding="utf-8") as source:
                        target.write(source.read())
                    ok = True
            if not ok:
                return video_path

            output = str(Path(video_path).with_name(Path(video_path).stem + "_subtitle_integrity.mp4"))
            escaped = srt_path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
            force_style = (
                "FontName=Arial,FontSize=18,PrimaryColour=&H00F8F9FA,"
                "OutlineColour=&H90050A12,BackColour=&H90101925,BorderStyle=3,"
                "Outline=2,Shadow=0,Alignment=2,MarginV=250,Spacing=0"
            )
            command = [
                "ffmpeg", "-y", "-i", video_path,
                "-vf", f"subtitles='{escaped}':force_style='{force_style}'",
                "-map", "0:v:0", "-map", "0:a?",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                "-c:a", "copy", "-map_metadata", "0", "-movflags", "+faststart", output,
            ]
            completed = subprocess.run(command, capture_output=True, text=True, timeout=240, check=False)
            if completed.returncode != 0 or not os.path.isfile(output):
                return video_path
            os.replace(output, video_path)
            return video_path
        except Exception as exc:
            print(f"   [Subtitle Integrity] Premium endpoint caption pass skipped: {type(exc).__name__}: {exc}", flush=True)
            return video_path
        finally:
            try:
                for candidate in Path(temp_dir).glob("*"):
                    candidate.unlink(missing_ok=True)
                Path(temp_dir).rmdir()
            except Exception:
                pass

    premium_endpoint_subtitles._premium_endpoint_bound = True
    pipeline_integrity_runtime._add_endpoint_subtitles = premium_endpoint_subtitles


def _premium_branded_finish(bot, video_path: str) -> str:
    """Apply the final premium glass branding layer without changing media geometry."""
    try:
        import branding_runtime
        probe = branding_runtime._probe
        artifact_qc = branding_runtime._artifact_qc
        assets = branding_runtime._assets
    except Exception as exc:
        print(f"   [Branding Patch] Premium finish unavailable: {type(exc).__name__}: {exc}", flush=True)
        return video_path

    if not video_path or not os.path.isfile(video_path):
        return video_path

    source_w, source_h, source_duration, source_audio_count = probe(video_path)
    valid, reason = artifact_qc(
        video_path,
        expected_width=source_w or None,
        expected_height=source_h or None,
        expected_duration=source_duration or None,
        expected_audio_count=source_audio_count or None,
    )
    if not valid:
        raise RuntimeError(f"Final render QC failed before premium branding: {reason}")

    logo, overlay = assets(bot)
    glass_logo = create_glossy_logo_watermark(logo, size=132) if logo and logo.exists() else None
    if glass_logo is None and (not overlay or not overlay.exists()):
        return video_path

    temp_paths: list[str] = []
    work_dir = os.path.dirname(video_path) or None
    overlay_asset = None
    temp_dir = None
    if overlay and overlay.exists():
        try:
            processed = Image.open(overlay).convert("RGBA")
            if processed.size != (source_w, source_h):
                processed = processed.resize((source_w, source_h), Image.Resampling.LANCZOS)
            safe_y0 = max(0, source_h - 360)
            safe_x0 = int(source_w * 0.08)
            safe_x1 = int(source_w * 0.92)
            alpha = processed.getchannel("A")
            mask = Image.new("L", processed.size, 0)
            md = ImageDraw.Draw(mask)
            md.rectangle((safe_x0, safe_y0, safe_x1, source_h), fill=255)
            alpha = ImageChops.subtract(alpha, mask)
            processed.putalpha(alpha)
            overlay_asset = os.path.join(work_dir, "premium_brand_overlay.png")
            processed.save(overlay_asset, "PNG")
            temp_paths.append(overlay_asset)
        except Exception:
            overlay_asset = None

    if overlay_asset is None and overlay and overlay.exists():
        safe, _ = branding_runtime._overlay_is_caption_safe(overlay, source_w, source_h)
        if safe:
            overlay_asset = str(overlay)

    logo_asset = None
    if glass_logo is not None:
        logo_asset = os.path.join(work_dir, "premium_glass_logo.png")
        glass_logo.save(logo_asset, "PNG")
        temp_paths.append(logo_asset)

    output = str(Path(video_path).with_name(Path(video_path).stem + "_premium.mp4"))
    filters = [
        "[0:v]drawbox=x=10:y=10:w=iw-20:h=ih-20:color=0x40C4FF@0.58:t=3[frame1]",
        "[frame1]drawbox=x=16:y=16:w=iw-32:h=ih-32:color=white@0.16:t=1[frame2]",
        "[frame2]drawbox=x=27:y=27:w=iw-54:h=ih-54:color=0x40C4FF@0.10:t=1[frame3]",
        "[frame3]drawbox=x=32:y=32:w=iw-64:h=2:color=white@0.12:t=fill[frame4]",
    ]
    last = "[frame4]"
    inputs = ["-i", video_path]

    if overlay_asset:
        inputs += ["-loop", "1", "-i", overlay_asset]
        filters += [f"[1:v]format=rgba[brand];{last}[brand]overlay=0:0:eof_action=repeat:shortest=0:format=auto[withbrand]"]
        last = "[withbrand]"

    if logo_asset:
        inputs += ["-loop", "1", "-i", logo_asset]
        badge_index = 2 if overlay_asset else 1
        filters += [f"[{badge_index}:v]format=rgba[badge];{last}[badge]overlay=W-w-28:24:eof_action=repeat:shortest=0:format=auto[finalv]"]
        last = "[finalv]"

    cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(filters), "-map", last, "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "copy", "-map_metadata", "0", "-movflags", "+faststart", "-t", f"{source_duration:.3f}", output]

    try:
        completed = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=240)
        if completed.returncode != 0 or not os.path.isfile(output):
            raise RuntimeError(f"FFmpeg premium branding finish failed: {completed.stderr[-800:]}")
        out_w, out_h, out_duration, out_audio_count = probe(output)
        valid, reason = artifact_qc(
            output,
            expected_width=source_w,
            expected_height=source_h,
            expected_duration=source_duration,
            expected_audio_count=source_audio_count,
        )
        if not valid or (out_w, out_h) != (source_w, source_h) or abs(out_duration - source_duration) > 0.15 or out_audio_count != source_audio_count:
            try:
                os.remove(output)
            except OSError:
                pass
            raise RuntimeError(f"Final premium branding QC failed: {reason}")
        os.replace(output, video_path)
        print(
            f"   [Branding] Premium glass finish applied: frame + {'channel overlay + ' if overlay_asset else ''}glass logo; "
            f"{source_w}x{source_h}, {source_duration:.2f}s, audio_streams={source_audio_count}.",
            flush=True,
        )
        return video_path
    finally:
        for candidate in temp_paths:
            try:
                os.remove(candidate)
            except OSError:
                pass


def _patch_premium_branding(bot):
    try:
        import branding_runtime
        branding_runtime.apply_branded_finish = lambda active_bot, video_path: _premium_branded_finish(active_bot, video_path)
        branding_runtime.BRANDING_VERSION = "2026-09-18-premium-glass-v1"
    except Exception as exc:
        print(f"   [Branding Patch] Could not install premium final finish: {type(exc).__name__}: {exc}", flush=True)


def patch_subtitle_pipeline(bot):
    if getattr(bot, "_subtitle_pipeline_patch_installed", False):
        return bot
    run_robot = getattr(bot, "run_robot", None)
    namespace = getattr(run_robot, "__globals__", None)
    if not isinstance(namespace, dict):
        return bot

    _patch_top5_card(bot)
    _patch_scene_overlay(bot)
    _patch_deep_dive_subtitle_condition(bot)
    _patch_endpoint_subtitles()
    _patch_premium_branding(bot)

    namespace["generate_karaoke_clip"] = generate_readable_karaoke_clip
    namespace["create_glossy_logo_watermark"] = create_glossy_logo_watermark
    bot.generate_karaoke_clip = generate_readable_karaoke_clip
    bot.create_glossy_logo_watermark = create_glossy_logo_watermark
    bot._subtitle_pipeline_patch_installed = True
    print("   [Subtitle Patch] Clean glass captions + matching Top-5 cards + premium glass branding installed.", flush=True)
    return bot
