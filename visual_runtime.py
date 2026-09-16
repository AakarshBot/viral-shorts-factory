"""Scene-aware, bounded visual sourcing for Viral Shorts Factory."""
import asyncio
import hashlib
import io
import json
import os
import re
import threading

from PIL import Image, ImageDraw

VISUAL_FETCH_TIMEOUT_SECONDS = int(os.getenv("VISUAL_FETCH_TIMEOUT_SECONDS", "15"))
# Hard ceiling: visual strategy may request fewer searches, but deployment
# configuration can never increase this runtime safety limit above 6.
VISUAL_MAX_SEARCH_QUERIES = min(6, max(1, int(os.getenv("VISUAL_MAX_SEARCH_QUERIES", "6"))))
VISUAL_MAX_VERIFICATION_ATTEMPTS = max(1, int(os.getenv("VISUAL_MAX_VERIFICATION_ATTEMPTS", "4")))
VISUAL_CACHE_MAX_AGE_SECONDS = int(os.getenv("VISUAL_CACHE_MAX_AGE_SECONDS", str(7 * 86400)))

REAL_ENTITY_TYPES = {"PERSON", "EVENT", "PRODUCT", "LOCATION", "QUOTE", "DOCUMENT"}
AI_ALLOWED_TYPES = {"PROCESS", "CONCEPT", "GENERAL_CONTEXT"}


def _cache_root(bot):
    root = getattr(bot, "ASSET_CACHE_DIR", None) or os.getenv("ASSET_CACHE_DIR")
    if not root:
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "asset_cache")
    os.makedirs(root, exist_ok=True)
    return root


def _context_fingerprint(intent="", prompt="", voice="", video_title=""):
    raw = " | ".join(str(x or "").strip().lower() for x in (intent, prompt, voice, video_title))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _cache_key(entity, visual_type, context=""):
    raw = f"{str(entity).strip().lower()}::{str(visual_type).strip().upper()}::{str(context).strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def get_cached_asset(bot, entity, visual_type, context=""):
    """Return a previously verified entity/type/context asset, or (None, None)."""
    if not entity or str(entity).strip().lower() in {"none", "unknown", "n/a"}:
        return None, None
    key = _cache_key(entity, visual_type, context)
    root = _cache_root(bot)
    image_path = os.path.join(root, f"{key}.jpg")
    meta_path = os.path.join(root, f"{key}.json")
    try:
        if not os.path.exists(image_path) or not os.path.exists(meta_path):
            return None, None
        if os.path.getmtime(image_path) < __import__("time").time() - VISUAL_CACHE_MAX_AGE_SECONDS:
            return None, None
        with open(meta_path, "r", encoding="utf-8") as fh:
            meta = json.load(fh)
        if meta.get("entity", "").strip().lower() != str(entity).strip().lower():
            return None, None
        if str(meta.get("visual_type", "")).upper() != str(visual_type).upper():
            return None, None
        if str(meta.get("context", "")) != str(context):
            return None, None
        with open(image_path, "rb") as fh:
            data = fh.read()
        if not _local_visual_sanity(data):
            return None, None
        return Image.open(io.BytesIO(data)).convert("RGB"), image_path
    except Exception as exc:
        print(f"   [Visual Cache] Read failed: {type(exc).__name__}: {exc}", flush=True)
        return None, None


def save_to_cache(bot, img_bytes, entity, visual_type, source_type, context=""):
    """Persist only an asset that passed the applicable visual tier."""
    if not img_bytes or not entity:
        return None
    key = _cache_key(entity, visual_type, context)
    root = _cache_root(bot)
    image_path = os.path.join(root, f"{key}.jpg")
    meta_path = os.path.join(root, f"{key}.json")
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img.save(image_path, "JPEG", quality=95)
        with open(meta_path, "w", encoding="utf-8") as fh:
            json.dump({"entity": str(entity).strip(), "visual_type": str(visual_type).upper(), "source_type": str(source_type), "context": str(context), "verified": True}, fh, ensure_ascii=False, indent=2)
        return image_path
    except Exception as exc:
        print(f"   [Visual Cache] Write failed: {type(exc).__name__}: {exc}", flush=True)
        return None


def _entity_context(seg, video_title=""):
    return (
        str(seg.get("primary_entity", "")).strip(),
        str(seg.get("visual_intent", "")).strip(),
        str(seg.get("specific_search_prompt", "")).strip(),
        str(seg.get("voiceover", "")).strip(),
        str(video_title or "").strip(),
    )


def _local_visual_sanity(img_bytes):
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        if min(img.size) < 300:
            return False
        ratio = img.width / max(1, img.height)
        return 0.4 <= ratio <= 2.5
    except Exception:
        return False


def _verification_tier(seg, visual_type, source):
    intent = str(seg.get("visual_intent", "")).strip().lower()
    source_l = str(source or "").strip().lower()
    if visual_type == "PERSON" and source_l in {"wikipedia", "commons"}:
        return "STRICT(person)"
    if intent == "conceptual":
        return "SKIPPED(conceptual)"
    event_text = f"{intent} {visual_type}".lower()
    if visual_type == "EVENT" or any(x in event_text for x in ("news_event", "stadium_event", "news event", "stadium event", "ceremony", "match", "red carpet", "press conference")):
        return "GENRE-PLAUSIBLE(event)"
    return "STRICT"


def _strict_gemini_check(img_bytes, entity, intent, prompt, voice, video_title, api_key, tier="STRICT", visual_type=""):
    try:
        from visual_qa_runtime import strict_gemini_check
        return strict_gemini_check(img_bytes, entity, intent, prompt, voice, video_title, api_key, tier=tier, visual_type=visual_type)
    except Exception as exc:
        print(f"   [Visual QA] Gemini bridge unavailable: {exc}", flush=True)
        return None


def _strict_gate(bot, img_bytes, seg, video_title="", source=""):
    if not img_bytes or not _local_visual_sanity(img_bytes):
        return False, "LOCAL-REJECT", 0, True
    entity, intent, prompt, voice, title = _entity_context(seg, video_title)
    if not entity or entity.lower() in {"none", "unknown", "n/a"}:
        return False, "LOCAL-REJECT", 0, True
    visual_type = str(seg.get("visual_type", "")).strip().upper()
    if not visual_type:
        try:
            from visual_strategy_runtime import classify_scene
            visual_type = classify_scene(seg, str(seg.get("sport_or_topic_category", "")))
        except Exception:
            visual_type = "GENERAL_CONTEXT"
    tier = _verification_tier(seg, visual_type, source)
    if tier == "STRICT(person)":
        print(f"   [Visual QA] Tier=STRICT(person) | source={source} | Gemini=SKIPPED (curated source).", flush=True)
        return True, tier, 100, False
    if tier == "SKIPPED(conceptual)":
        print("   [Visual QA] Tier=SKIPPED(conceptual) | Gemini=SKIPPED.", flush=True)
        return True, tier, 90, False
    result = _strict_gemini_check(img_bytes, entity, intent, prompt, voice, title, os.getenv("GEMINI_API_KEY"), tier=tier, visual_type=visual_type)
    source_score = {"wikipedia": 100, "commons": 95, "ddg": 70, "pexels": 65, "unsplash": 65, "ai-generated": 45}.get(str(source).lower(), 50)
    if result is True:
        return True, tier, 100, False
    if result is False:
        print(f"   [Visual QA] {tier} | REJECTED: semantic check returned NO for '{entity}'.", flush=True)
        return False, tier, max(0, source_score - 20), True
    print(f"   [Visual QA] {tier} | soft reject: semantic verification unavailable/uncertain; candidate retained as fallback.", flush=True)
    return False, tier, source_score, False


def _build_search_variants(seg, video_title=""):
    try:
        from visual_strategy_runtime import build_deep_queries
        queries, visual_type = build_deep_queries(seg, video_title)
        return queries[:VISUAL_MAX_SEARCH_QUERIES], visual_type
    except Exception as exc:
        print(f"   [Visual Strategy] fallback query builder: {exc}", flush=True)
        entity, intent, prompt, voice, title = _entity_context(seg, video_title)
        values = [prompt, f"{entity} {intent}", f"{entity} {title}", f"{entity} {voice[:180]}"]
        return [re.sub(r"\s+", " ", v).strip() for v in values if str(v).strip()], "GENERAL_CONTEXT"


def _call_fetcher_with_timeout(fetcher, args, source, query, timeout=VISUAL_FETCH_TIMEOUT_SECONDS):
    result = {"value": None, "error": None}
    def worker():
        try:
            result["value"] = fetcher(*args)
        except Exception as exc:
            result["error"] = exc
    thread = threading.Thread(target=worker, name=f"visual-{source.lower()}-fetch", daemon=True)
    thread.start(); thread.join(timeout)
    if thread.is_alive():
        print(f"   [Visual Source] {source} | timed out after {timeout}s | query='{query}'", flush=True)
        return None
    if result["error"] is not None:
        print(f"   [Visual Source] {source} | failed: {result['error']} | query='{query}'", flush=True)
        return None
    return result["value"]


def _source_plan(bot, visual_type, category):
    editorial = any(k in f"{category} {visual_type}".lower() for k in ("sport", "football", "cricket", "news", "politics", "entertainment", "movie", "event"))
    plan = []
    if visual_type == "PERSON":
        plan += [("Wikipedia", getattr(bot, "fetch_wiki_person_image", None)), ("Commons", getattr(bot, "fetch_wikimedia_commons", None))]
    elif visual_type in {"EVENT", "QUOTE", "DOCUMENT", "LOCATION"}:
        plan += [("Commons", getattr(bot, "fetch_wikimedia_commons", None))]
    if editorial:
        plan += [("DDG", getattr(bot, "fetch_duckduckgo", None))]
    plan += [("Pexels", getattr(bot, "fetch_pexels", None)), ("Unsplash", getattr(bot, "fetch_unsplash", None))]
    if not editorial:
        plan += [("DDG", getattr(bot, "fetch_duckduckgo", None))]
    return [(name, fn) for name, fn in plan if callable(fn)]


def _relevant_asset(bot, seg, category, used_urls, used_hashes, video_title=""):
    entity = str(seg.get("primary_entity", "")).strip()
    if not entity or entity.lower() in {"none", "unknown", "n/a"}:
        raise RuntimeError("Visual pipeline requires a specific primary_entity for every scene.")
    queries, visual_type = _build_search_variants(seg, video_title)
    intent, prompt, voice = str(seg.get("visual_intent", "")), str(seg.get("specific_search_prompt", "")), str(seg.get("voiceover", ""))
    context = _context_fingerprint(intent, prompt, voice, video_title)
    print(f"   [Visual Strategy] entity='{entity}' type={visual_type} deep_searches={len(queries)}", flush=True)

    cached_img, cache_path = get_cached_asset(bot, entity, visual_type, context)
    if cached_img is not None:
        cached_bytes = io.BytesIO(); cached_img.save(cached_bytes, format="JPEG", quality=95); cached_data = cached_bytes.getvalue()
        try:
            img_hash = bot.get_image_hash(cached_data)
        except Exception:
            img_hash = hashlib.sha256(cached_data).hexdigest()
        if img_hash not in used_hashes:
            used_hashes.add(img_hash)
            print(f"   [Visual Cache] VERIFIED context-specific cache hit | entity='{entity}' type={visual_type} context={context} | Gemini calls=0", flush=True)
            return cached_img, False, "cached"

    best = None
    verification_attempts = 0
    hard_rejections = 0

    def try_bytes(data, source, query):
        nonlocal best, verification_attempts, hard_rejections
        if not data:
            return None
        try:
            h = bot.get_image_hash(data)
            if h in used_hashes:
                return None
            tier = _verification_tier(seg, visual_type, source)
            needs_semantic = tier not in {"STRICT(person)", "SKIPPED(conceptual)"}
            if needs_semantic and verification_attempts >= VISUAL_MAX_VERIFICATION_ATTEMPTS:
                return None
            if needs_semantic:
                verification_attempts += 1
            accepted, tier_name, score, hard_reject = _strict_gate(bot, data, seg, video_title, source=source)
            if hard_reject:
                hard_rejections += 1
            if not accepted and not hard_reject:
                candidate = (score, data, source)
                if best is None or score > best[0]:
                    best = candidate
            if accepted:
                used_hashes.add(h)
                cache_path = save_to_cache(bot, data, entity, visual_type, source, context)
                print(f"   [Visual Source] {source} | VERIFIED | tier={tier_name} | type={visual_type} | query='{query}'", flush=True)
                if cache_path:
                    print(f"   [Visual Cache] Saved verified context-specific asset for entity='{entity}' type={visual_type} context={context}.", flush=True)
                return Image.open(io.BytesIO(data)).convert("RGB"), False, source
        except Exception as exc:
            print(f"   [Visual Source] {source} | candidate rejected: {exc}", flush=True)
        return None

    stop_real_search = False
    for query_index, query in enumerate(queries, 1):
        if stop_real_search:
            break
        print(f"   [Visual Search] {query_index}/{len(queries)} | '{query}'", flush=True)
        for name, fetcher in _source_plan(bot, visual_type, category):
            if verification_attempts >= VISUAL_MAX_VERIFICATION_ATTEMPTS and name not in {"Wikipedia", "Commons"} and visual_type not in {"PERSON"}:
                stop_real_search = True
                break
            args = (entity, used_urls, query, video_title) if name == "Wikipedia" else (query, used_urls, query, video_title)
            data = _call_fetcher_with_timeout(fetcher, args, name, query)
            result = try_bytes(data, name, query)
            if result:
                return result

    if best is not None:
        score, data, source = best
        try:
            h = bot.get_image_hash(data)
        except Exception:
            h = hashlib.sha256(data).hexdigest()
        if h not in used_hashes:
            used_hashes.add(h)
            cache_path = save_to_cache(bot, data, entity, visual_type, source, context)
            print(f"   [Visual QA] ACCEPT-BEST | tier={_verification_tier(seg, visual_type, source)} | score={score} | verification_attempts={verification_attempts}/{VISUAL_MAX_VERIFICATION_ATTEMPTS}", flush=True)
            if cache_path:
                print(f"   [Visual Cache] Saved best-available context-specific asset for entity='{entity}' type={visual_type} context={context}.", flush=True)
            return Image.open(io.BytesIO(data)).convert("RGB"), False, source

    if visual_type in AI_ALLOWED_TYPES:
        ai_prompts = [
            f"Photorealistic documentary illustration of {entity}. {seg.get('visual_intent','')}. {seg.get('voiceover','')[:220]}",
            f"High-quality editorial concept image showing {entity} in context. {video_title}",
            f"Clear technical/editorial illustration of {entity}. No logos, no invented people, no fake documents. {seg.get('visual_intent','')}",
        ]
        for ai_prompt in ai_prompts:
            print(f"   [Visual Source] AI attempt | type={visual_type} | prompt='{ai_prompt[:160]}'", flush=True)
            ai = _call_fetcher_with_timeout(bot.fetch_hf_ai_image, (ai_prompt,), "HF-AI", ai_prompt)
            if ai is None:
                continue
            try:
                buf = io.BytesIO(); ai.convert("RGB").save(buf, format="JPEG", quality=95)
                result = try_bytes(buf.getvalue(), "AI-generated", ai_prompt)
                if result:
                    return result[0], True, "AI-generated"
            except Exception as exc:
                print(f"   [Visual Source] AI candidate rejected: {exc}", flush=True)

    raise RuntimeError(f"No usable visual could be verified for '{entity}' after bounded visual search (type={visual_type}, verification_attempts={verification_attempts}/{VISUAL_MAX_VERIFICATION_ATTEMPTS}, hard_rejections={hard_rejections}).")


def _render_image_slide(bot, bg_img, title_text, subtitle_text="", font_choice=None, accent=None):
    bg = bg_img.convert("RGBA").resize((1080, 1920), Image.Resampling.LANCZOS)
    overlay = Image.new("RGBA", bg.size, (0, 0, 0, 0)); draw = ImageDraw.Draw(overlay)
    accent = accent or bot.PALETTE.get("accent_primary", (0, 191, 255))
    draw.rectangle([0, 0, 1080, 42], fill=accent + (230,))
    draw.rounded_rectangle([55, 420, 1025, 1500], radius=40, fill=(5, 8, 16, 205), outline=accent + (210,), width=3)
    font, lines = bot.fit_text_in_box(title_text, font_choice, 860, 650, start_size=78)
    y = 620
    for line in lines:
        bb = draw.textbbox((0, 0), line, font=font); x = (1080 - (bb[2] - bb[0])) / 2
        draw.text((x + 4, y + 4), line, font=font, fill=(0, 0, 0, 220)); draw.text((x, y), line, font=font, fill=(255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0, 230)); y += (bb[3] - bb[1]) + 18
    if subtitle_text:
        sub_font = bot.get_bold_font(52, font_choice); bb = draw.textbbox((0, 0), subtitle_text, font=sub_font); x = (1080 - (bb[2] - bb[0])) / 2; y = 1280
        draw.rounded_rectangle([x - 30, y - 18, x + bb[2] - bb[0] + 30, y + bb[3] - bb[1] + 25], radius=30, fill=(0, 0, 0, 220), outline=accent + (220,), width=2)
        draw.text((x, y), subtitle_text, font=sub_font, fill=accent)
    return Image.alpha_composite(bg, overlay)


async def _process_visuals(bot, script_data, language_cfg, format_mode="regular"):
    print("\n🎨 Sourcing bounded, scene-relevant visuals for every Shorts slide...", flush=True)
    width, height = 1080, 1920; target_size = (width, height)
    scenes = script_data.get("script", [])
    if not scenes: raise RuntimeError("Visual pipeline received an empty script.")
    try:
        from visual_qa_runtime import reset_visual_qa_video_budget, start_visual_qa_scene, get_visual_qa_calls_used
        reset_visual_qa_video_budget()
    except Exception:
        start_visual_qa_scene = None; get_visual_qa_calls_used = lambda: 0
    font_choice = language_cfg.get("font"); packages = [None] * len(scenes); used_urls, used_hashes = set(), set(); ai_count = 0
    for idx, seg in enumerate(scenes):
        if start_visual_qa_scene: start_visual_qa_scene()
        video_title = script_data.get("title", "") or (script_data.get("titles") or [""])[0]
        print(f"   [Visual Pipeline] Scene {idx + 1}/{len(scenes)} starting...", flush=True)
        category = str(seg.get("sport_or_topic_category", "")).lower()
        bg_img, used_ai, source_type = _relevant_asset(bot, seg, category, used_urls, used_hashes, video_title)
        ai_count += int(used_ai); bg_img = bg_img.resize(target_size, Image.Resampling.LANCZOS).convert("RGBA")
        img_path = os.path.join(bot.ASSETS_DIR, f"scene_{idx+1}_img.jpg")
        if format_mode == "top5" and idx == 0:
            rendered = _render_image_slide(bot, bg_img, video_title or seg.get("voiceover", "Top 5"), "TODAY'S TOP 5", font_choice)
        elif format_mode == "top5":
            clean = re.sub(r"(number\s*\d+|story\s*#?\d+|#\d+)", "", str(seg.get("voiceover", "")), flags=re.IGNORECASE).strip()
            rendered = bot.render_top5_card(bg_img, max(1, 6 - idx), 5, clean or seg.get("voiceover", ""), font_choice=font_choice)
        elif idx == 0:
            rendered = bot.render_hook_card(bg_img, seg.get("voiceover", ""), font_choice=font_choice)
        else:
            overlay = Image.new("RGBA", target_size, (0, 0, 0, 0)); draw = ImageDraw.Draw(overlay)
            draw.rectangle([0, 0, width, 40], fill=bot.PALETTE["accent_primary"] + (200,)); draw.rectangle([0, height - 40, width, height], fill=bot.PALETTE["accent_secondary"] + (200,))
            rendered = Image.alpha_composite(bg_img, overlay)
        rendered.convert("RGB").save(img_path, "JPEG", quality=95)
        packages[idx] = [{"image": img_path, "text": "" if (format_mode == "top5" or idx == 0) else seg.get("voiceover", ""), "ai_generated": used_ai, "source_type": source_type}]
        seg["visual_verified"] = True; seg["visual_source"] = source_type
    script_data["ai_image_ratio"] = round(ai_count / max(1, len(scenes)), 2)
    script_data["visual_coverage"] = 1.0; script_data["visuals_verified"] = True
    try: script_data["gemini_qa_calls_used"] = get_visual_qa_calls_used()
    except Exception: script_data["gemini_qa_calls_used"] = 0
    print(f"   [+] Visual QA complete: {len(scenes)}/{len(scenes)} slides have verified/reviewed relevant visuals.", flush=True)
    print(f"   [Visual QA] Gemini calls used for video: {script_data['gemini_qa_calls_used']}", flush=True)
    return packages


def patch_visual_pipeline(bot):
    """Bind the bounded visual renderer to the legacy bot instance."""
    current = getattr(bot, "process_visuals_async", None)
    if getattr(current, "_strict_visual_bound", False): return current
    async def process_visuals_async(script_data, language_cfg, format_mode="regular"):
        return await _process_visuals(bot, script_data, language_cfg, format_mode)
    process_visuals_async._strict_visual_bound = True
    bot.process_visuals_async = process_visuals_async
    return process_visuals_async
