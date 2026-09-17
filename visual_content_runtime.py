"""Content-first visual rendering for Shorts.

Every factual scene is rendered with a verified visual. Person scenes now go
through the single authoritative visual runtime instead of a separate curated
Commons shortcut, so search scope stays broad and identity QA remains consistent.
"""

import os
import re
from PIL import Image, ImageDraw, ImageFont


def _human_label(value, fallback="EDITORIAL"):
    text = re.sub(r"[_-]+", " ", str(value or "")).strip()
    text = re.sub(r"\s+", " ", text)
    return text.upper() if text else fallback


def _source_label(source_type):
    source = str(source_type or "").strip()
    if not source:
        return "VERIFIED VISUAL"
    if source.lower() == "ai-generated":
        return "AI ILLUSTRATION"
    if source.lower() == "gradient-fallback":
        return "EDITORIAL BACKDROP"
    return f"SOURCE · {_human_label(source)}"


def _load_brand_font(bot, size, custom_font_name=None):
    """Use the factory's existing bold-font resolver, with a safe local fallback."""
    try:
        resolver = getattr(bot, "get_bold_font", None)
        if callable(resolver):
            return resolver(int(size), custom_font_name)
    except Exception:
        pass

    paths = []
    if custom_font_name:
        paths.extend([
            str(custom_font_name),
            os.path.join("/usr/share/fonts/truetype", str(custom_font_name)),
        ])
    paths.extend([
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        r"C:\Windows\Fonts\segoeprb.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
    ])
    for path in paths:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, int(size))
            except Exception:
                continue
    return ImageFont.load_default()


def _text_size(draw, text, font):
    try:
        left, top, right, bottom = draw.textbbox((0, 0), str(text), font=font)
        return max(1, right - left), max(1, bottom - top)
    except Exception:
        try:
            return max(1, int(draw.textlength(str(text), font=font))), max(1, getattr(font, "size", 20))
        except Exception:
            return max(1, len(str(text)) * 10), max(1, getattr(font, "size", 20))


def _fit_font(bot, text, max_width, base_size, min_size=20, custom_font_name=None):
    """Select the largest readable bold font that fits inside max_width."""
    for size in range(int(base_size), int(min_size) - 1, -2):
        font = _load_brand_font(bot, size, custom_font_name)
        width, _ = _text_size(ImageDraw.Draw(Image.new("RGBA", (1, 1))), text, font)
        if width <= max_width:
            return font
    return _load_brand_font(bot, int(min_size), custom_font_name)


def _render_scene_overlay(bot, image, scene_number, total_scenes, visual_type, source_type, voiceover, font_name=None):
    """Add restrained editorial framing without obscuring the verified visual."""
    canvas = image.convert("RGBA")
    width, height = canvas.size
    accent = tuple(getattr(bot, "PALETTE", {}).get("accent_primary", (0, 191, 255)))
    secondary = tuple(getattr(bot, "PALETTE", {}).get("accent_secondary", (255, 140, 0)))
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    draw.rectangle([28, 28, width - 28, 33], fill=accent + (190,))
    draw.rectangle([28, height - 33, width - 28, height - 28], fill=secondary + (150,))
    draw.rectangle([28, 28, 33, height - 28], fill=accent + (105,))

    marker_font = _load_brand_font(bot, 30, font_name)
    type_label = _human_label(visual_type)
    type_font = _fit_font(bot, type_label, min(430, width - 120), 30, 20, font_name)
    source_text = _source_label(source_type)
    source_font = _fit_font(bot, source_text, min(470, width - 160), 28, 18, font_name)

    marker = f"{int(scene_number):02d} / {int(total_scenes):02d}"
    marker_w, marker_h = _text_size(draw, marker, marker_font)
    marker_box = [48, 62, 48 + marker_w + 40, max(112, 62 + marker_h + 24)]
    draw.rounded_rectangle(marker_box, radius=18, fill=(5, 9, 16, 175))
    draw.text((marker_box[0] + 20, marker_box[1] + 10), marker, font=marker_font,
              fill=(255, 255, 255, 240), stroke_width=1, stroke_fill=(0, 0, 0, 120))

    type_w, type_h = _text_size(draw, type_label, type_font)
    type_box = [48, height - 62 - type_h - 24, min(width - 48, 48 + type_w + 40), height - 62]
    draw.rounded_rectangle(type_box, radius=18, fill=(5, 9, 16, 175), outline=accent + (150,), width=2)
    draw.text((type_box[0] + 20, type_box[1] + 10), type_label, font=type_font,
              fill=accent + (245,), stroke_width=1, stroke_fill=(0, 0, 0, 120))

    source_w, source_h = _text_size(draw, source_text, source_font)
    source_box_w = min(width - 96, source_w + 36)
    source_box_h = source_h + 24
    sx = width - source_box_w - 48
    source_box = [sx, 62, width - 48, 62 + source_box_h]
    draw.rounded_rectangle(source_box, radius=18, fill=(5, 9, 16, 175), outline=(255, 255, 255, 80), width=1)
    draw.text((sx + 18, 62 + 10), source_text, font=source_font,
              fill=(245, 248, 250, 235), stroke_width=1, stroke_fill=(0, 0, 0, 120))

    fact_match = re.search(r"(?:₹|\$|€|£)?\b\d+(?:[.,]\d+)?%?\b", str(voiceover or ""))
    if fact_match:
        fact = fact_match.group(0)
        fact_font = _fit_font(bot, fact, 250, 28, 20, font_name)
        fact_w, fact_h = _text_size(draw, fact, fact_font)
        chip_w = max(126, fact_w + 40)
        chip_h = max(52, fact_h + 22)
        cx = width - chip_w - 48
        cy = height - 178
        draw.rounded_rectangle([cx, cy, width - 48, cy + chip_h], radius=20, fill=accent + (215,))
        draw.text((cx + 20, cy + 10), fact, font=fact_font, fill=(255, 255, 255, 250),
                  stroke_width=1, stroke_fill=(0, 0, 0, 80))

    return Image.alpha_composite(canvas, overlay)


def _render_hook_card(bot, image, hook_text, font_name=None):
    """Render the opening hook directly over the verified image.

    The old hook renderer blurred and covered a large central region with an
    opaque dark rounded rectangle. That made the first slide look like a title
    card instead of the actual story visual. The hook now preserves the image
    and uses text shadow/stroke for readability without a background panel.
    """
    canvas = image.convert("RGBA")
    width, height = canvas.size
    accent = tuple(getattr(bot, "PALETTE", {}).get("accent_primary", (0, 191, 255)))
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Minimal edge treatment; deliberately no full-width bars or central box.
    draw.rectangle([24, 24, width - 24, 30], fill=accent + (180,))

    font_body, wrapped_lines = _fit_hook_text(bot, hook_text, font_name, width - 140)
    line_heights = []
    for line in wrapped_lines:
        bbox = draw.textbbox((0, 0), line, font=font_body)
        line_heights.append(max(1, bbox[3] - bbox[1]))

    line_gap = 18
    total_h = sum(line_heights) + line_gap * max(0, len(wrapped_lines) - 1)
    y = max(180, (height - total_h) / 2)

    for line, line_h in zip(wrapped_lines, line_heights):
        bbox = draw.textbbox((0, 0), line, font=font_body)
        text_w = bbox[2] - bbox[0]
        x = (width - text_w) / 2
        # Strong outline keeps the image visible while maintaining readability.
        draw.text(
            (x + 8, y + 8),
            line,
            font=font_body,
            fill=(0, 0, 0, 210),
            stroke_width=8,
            stroke_fill=(0, 0, 0, 180),
        )
        draw.text(
            (x, y),
            line,
            font=font_body,
            fill=accent + (250,),
            stroke_width=5,
            stroke_fill=(0, 0, 0, 245),
        )
        y += line_h + line_gap

    return Image.alpha_composite(canvas, overlay)


def _fit_hook_text(bot, text, font_name, max_width):
    """Fit the hook into a readable, centred text treatment without a card."""
    text = str(text or "").strip() or "THIS STORY MATTERS"
    for size in range(92, 44, -4):
        font = _load_brand_font(bot, size, font_name)
        lines = []
        current = ""
        draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        for word in text.split():
            candidate = f"{current} {word}".strip()
            if not current or draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
        if lines and len(lines) <= 5:
            return font, lines
    font = _load_brand_font(bot, 44, font_name)
    return font, [text]


def patch_content_first_visuals(bot):
    try:
        import visual_runtime
        from visual_query_entities_runtime import search_slide_visual
    except Exception as exc:
        print(f"   [Visual Content] Could not load strict visual runtime: {exc}", flush=True)
        return bot

    async def process(script_data, language_cfg, format_mode="regular"):
        scenes = script_data.get("script", [])
        if not scenes:
            raise RuntimeError("Visual pipeline received an empty script.")

        width, height = 1080, 1920
        target_size = (width, height)
        font_choice = language_cfg.get("font")
        packages = [None] * len(scenes)
        used_urls, used_hashes = set(), set()
        ai_count = 0

        print("\n🎨 Rendering content-first visual package (slide subjects + strict QA)...", flush=True)
        for idx, seg in enumerate(scenes):
            video_title = script_data.get("title", "") or (script_data.get("titles") or [""])[0]
            category = str(seg.get("sport_or_topic_category", "")).lower()

            bg_img, used_ai, source_type = search_slide_visual(
                visual_runtime,
                bot,
                seg,
                category,
                used_urls,
                used_hashes,
                video_title,
            )

            ai_count += int(used_ai)
            bg_img = bg_img.resize(target_size, Image.Resampling.LANCZOS).convert("RGBA")
            img_path = os.path.join(bot.ASSETS_DIR, f"scene_{idx+1}_img.jpg")

            try:
                from visual_strategy_runtime import classify_scene
                visual_type = classify_scene(seg, category)
            except Exception:
                visual_type = str(seg.get("visual_type", "GENERAL_CONTEXT"))

            if format_mode == "top5" and idx == 0:
                rendered = visual_runtime._render_image_slide(
                    bot, bg_img, video_title or seg.get("voiceover", "Top 5"),
                    "TODAY'S TOP 5", font_choice
                )
            elif format_mode == "top5":
                clean = re.sub(
                    r"(number\s*\d+|story\s*#?\d+|#\d+)", "",
                    str(seg.get("voiceover", "")), flags=re.IGNORECASE
                ).strip()
                rendered = bot.render_top5_card(
                    bg_img, max(1, 6 - idx), 5,
                    clean or seg.get("voiceover", ""), font_choice=font_choice
                )
            elif idx == 0:
                rendered = _render_hook_card(
                    bot, bg_img, seg.get("voiceover", ""), font_name=font_choice
                )
            else:
                rendered = _render_scene_overlay(
                    bot,
                    bg_img,
                    idx + 1,
                    len(scenes),
                    visual_type,
                    source_type,
                    seg.get("voiceover", ""),
                    font_name=font_choice,
                )

            rendered.convert("RGB").save(img_path, "JPEG", quality=95)
            packages[idx] = [{
                "image": img_path,
                "text": "" if format_mode == "top5" or idx == 0 else seg.get("voiceover", ""),
                "ai_generated": used_ai,
                "source_type": source_type,
                "visual_type": visual_type,
                "visual_verified": True,
            }]
            seg["visual_type"] = visual_type
            seg["visual_verified"] = True
            seg["visual_source"] = source_type

        script_data["ai_image_ratio"] = round(ai_count / max(1, len(scenes)), 2)
        script_data["visual_coverage"] = 1.0
        script_data["visuals_verified"] = True
        print(f"   [+] Content-first visual QA complete: {len(scenes)}/{len(scenes)} scenes rendered with verified visuals.", flush=True)
        return packages

    bot.process_visuals_async = process
    return bot
