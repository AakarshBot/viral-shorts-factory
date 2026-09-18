"""Content-first visual rendering for Shorts.

Every scene is sent through the multi-source retrieval engine. A provider/query
failure only means the next source or phrase is tried; the factory continues
through the complete visual package. AI is deliberately limited, and a tiny
renderer rescue exists only to prevent an empty frame after every real source
has been exhausted.
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
        return "VISUAL"
    if source.lower() == "ai-generated":
        return "AI ILLUSTRATION"
    if source.lower() == "cached":
        return "VERIFIED CACHE"
    if source.lower() == "visual-rescue":
        return "VISUAL RESCUE"
    return f"SOURCE · {_human_label(source)}"


def _load_brand_font(bot, size, custom_font_name=None):
    try:
        resolver = getattr(bot, "get_bold_font", None)
        if callable(resolver):
            return resolver(int(size), custom_font_name)
    except Exception:
        pass

    paths = []
    if custom_font_name:
        paths.extend([str(custom_font_name), os.path.join("/usr/share/fonts/truetype", str(custom_font_name))])
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
    for size in range(int(base_size), int(min_size) - 1, -2):
        font = _load_brand_font(bot, size, custom_font_name)
        width, _ = _text_size(ImageDraw.Draw(Image.new("RGBA", (1, 1))), text, font)
        if width <= max_width:
            return font
    return _load_brand_font(bot, int(min_size), custom_font_name)


def _render_scene_overlay(bot, image, scene_number, total_scenes, visual_type, source_type, voiceover, font_name=None):
    """Return the raw scene image.

    Scene numbers, source/type labels, fact chips and other editorial text cards
    are intentionally not baked into the visual anymore. The final branding
    stage is the single owner of the channel logo and border treatment.
    """
    return image.convert("RGBA")


def _render_hook_card(bot, image, hook_text, font_name=None):
    canvas = image.convert("RGBA")
    width, height = canvas.size
    accent = tuple(getattr(bot, "PALETTE", {}).get("accent_primary", (0, 191, 255)))
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
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
        draw.text((x + 8, y + 8), line, font=font_body, fill=(0, 0, 0, 210), stroke_width=8, stroke_fill=(0, 0, 0, 180))
        draw.text((x, y), line, font=font_body, fill=accent + (250,), stroke_width=5, stroke_fill=(0, 0, 0, 245))
        y += line_h + line_gap

    return Image.alpha_composite(canvas, overlay)


def _fit_hook_text(bot, text, font_name, max_width):
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
        from visual_quality_runtime import cover_crop, install as install_visual_quality
        from visual_retrieval_runtime import make_visual_rescue
        from visual_entity_grounding_runtime import apply_grounding
    except Exception as exc:
        print(f"   [Visual Content] Could not load visual runtime: {exc}", flush=True)
        return bot

    try:
        from manual_visual_query_runtime import assign_manual_queries, parse_manual_visual_queries
    except Exception as exc:
        assign_manual_queries = None
        parse_manual_visual_queries = lambda _value: []
        print(f"   [Manual Visual Queries] Optional router unavailable: {type(exc).__name__}: {exc}", flush=True)

    install_visual_quality(visual_runtime)

    async def process(script_data, language_cfg, format_mode="regular"):
        scenes = script_data.get("script", [])
        if not scenes:
            raise RuntimeError("Visual pipeline received an empty script.")

        target_size = (1080, 1920)
        font_choice = language_cfg.get("font")
        packages = [None] * len(scenes)
        used_urls, used_hashes = set(), set()

        # Dashboard manual queries are optional. Blank input preserves the
        # existing AI/automatic visual-search flow exactly.
        manual_raw = ""
        try:
            active_config = getattr(bot, "_active_web_config", {}) or {}
            manual_raw = str(active_config.get("visual_search_queries", "") or "").strip()
        except Exception:
            manual_raw = ""

        manual_assignments = [{} for _ in scenes]
        manual_queries = parse_manual_visual_queries(manual_raw)
        if manual_queries and callable(assign_manual_queries):
            manual_assignments = assign_manual_queries(scenes, manual_queries)
            print(
                f"   [Manual Visual Queries] {len(manual_queries)} supplied query/queries; "
                f"assigned across {len(scenes)} scene(s).",
                flush=True,
            )
            for scene_index, assignment in enumerate(manual_assignments, 1):
                if assignment.get("query"):
                    scenes[scene_index - 1]["manual_visual_query"] = assignment["query"]
                    scenes[scene_index - 1]["manual_visual_query_score"] = assignment.get("score", 0)
                    scenes[scene_index - 1]["manual_visual_query_index"] = assignment.get("query_index", 0)
        elif not manual_queries:
            print("   [Manual Visual Queries] No manual queries supplied; using existing Full AI visual flow.", flush=True)
        # Ground automatic visual identities against the selected story evidence.
        # Manual queries remain untouched and authoritative.
        for scene_index, scene in enumerate(scenes, 1):
            grounded = apply_grounding(scene, script_data)
            scenes[scene_index - 1] = grounded
            original_entity = str(grounded.get('visual_entity_original') or '').strip()
            current_entity = str(grounded.get('primary_entity') or '').strip()
            reason = str(grounded.get('visual_entity_grounding_reason') or '').strip()
            if grounded.get('visual_entity_grounding') == 'MANUAL_LOCK':
                print(
                    f"   [Visual Grounding] Scene {scene_index} | MANUAL_LOCK | query='{grounded.get('manual_visual_query', '')}'",
                    flush=True,
                )
            elif original_entity and original_entity != current_entity:
                print(
                    f"   [Visual Grounding] Scene {scene_index} | REPAIRED | '{original_entity}' -> '{current_entity}' | {reason}",
                    flush=True,
                )
            elif not grounded.get('visual_entity_grounded', False):
                print(
                    f"   [Visual Grounding] Scene {scene_index} | UNGROUNDED | entity='{current_entity}' | {reason}",
                    flush=True,
                )

        ai_count = 0
        verified_count = 0
        rescue_count = 0

        print("\n🎨 Rendering content-first visual package (multi-source retrieval + strict QA)...", flush=True)
        for idx, seg in enumerate(scenes):
            video_title = script_data.get("title", "") or (script_data.get("titles") or [""])[0]
            category = str(seg.get("sport_or_topic_category", "")).lower()

            try:
                bg_img, used_ai, source_type = search_slide_visual(
                    visual_runtime,
                    bot,
                    seg,
                    category,
                    used_urls,
                    used_hashes,
                    video_title,
                    manual_query=str(seg.get("manual_visual_query", "") or "").strip(),
                )
            except Exception as exc:
                subject = str(seg.get("primary_entity") or "Visual rescue").strip()
                seg["visual_verified"] = False
                seg["visual_rescue_reason"] = f"visual-search-exception:{type(exc).__name__}:{exc}"
                seg["visual_fallback_reason"] = ""
                print(
                    f"   [Visual Rescue] Scene {idx + 1} retrieval exception; continuing with renderer rescue: "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )
                bg_img, used_ai, source_type = make_visual_rescue(subject, str(seg.get("visual_type", "GENERAL_CONTEXT"))), False, "visual-rescue"
                # The source-type branch below records this rescue exactly once.

            scene_verified = bool(seg.get("visual_verified", False))
            if scene_verified:
                verified_count += 1
            ai_count += int(used_ai)
            if str(source_type).lower() == "visual-rescue":
                rescue_count += 1

            bg_img = cover_crop(bg_img, target_size).convert("RGBA")
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
                clean = re.sub(r"(number\s*\d+|story\s*#?\d+|#\d+)", "", str(seg.get("voiceover", "")), flags=re.IGNORECASE).strip()
                rendered = bot.render_top5_card(bg_img, max(1, 6 - idx), 5, clean or seg.get("voiceover", ""), font_choice=font_choice)
            else:
                # Deep Dive never receives the legacy opaque hook-card treatment.
                # First scenes use the same content-first visual treatment as all
                # subsequent Deep Dive scenes; Top-5 retains its dedicated cards.
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
                "text": "" if format_mode == "top5" else seg.get("voiceover", ""),
                "ai_generated": used_ai,
                "source_type": source_type,
                "visual_type": visual_type,
                "visual_genre": seg.get("visual_genre", "GENERAL_CONTEXT"),
                "visual_verified": scene_verified,
                "visual_rescue_reason": seg.get("visual_rescue_reason", ""),
                "visual_fallback_reason": "",
                "visual_query_used": seg.get("visual_query_used", ""),
                "manual_visual_query": seg.get("manual_visual_query", ""),
                "manual_visual_query_score": seg.get("manual_visual_query_score", 0),
            }]
            seg["visual_type"] = visual_type
            seg["visual_verified"] = scene_verified
            seg["visual_source"] = source_type

        total = len(scenes)
        script_data["ai_image_ratio"] = round(ai_count / max(1, total), 2)
        script_data["visual_coverage"] = round(verified_count / max(1, total), 2)
        script_data["visuals_verified"] = verified_count == total
        script_data["visual_fallback_count"] = rescue_count
        script_data["visual_rescue_count"] = rescue_count
        print(
            f"   [+] Content-first visual pass complete: {verified_count}/{total} scenes have verified visuals; "
            f"AI images={ai_count}; renderer rescues={rescue_count}.",
            flush=True,
        )
        return packages

    bot.process_visuals_async = process
    return bot
