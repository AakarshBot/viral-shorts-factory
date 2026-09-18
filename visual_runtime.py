"""Scene-aware, bounded visual sourcing for Viral Shorts Factory.

This module owns visual retrieval, verification and the Top 5 title-card renderer.
Regular Shorts rendering lives in visual_content_runtime.py so there is only one
active regular-scene renderer.
"""
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
    """Compatibility boundary to the single authoritative verification tier."""
    from visual_query_entities_runtime import _install_runtime_query_guard
    # Resolve the canonical guard on this module and use the installed tier.
    _install_runtime_query_guard(__import__(__name__))
    return __import__(__name__)._verification_tier(seg, visual_type, source)


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
    visual_genre = str(seg.get("visual_genre", "")).strip().upper()
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
    result = _strict_gemini_check(img_bytes, entity, intent, prompt, voice, title, os.getenv("GEMINI_API_KEY"), tier=tier, visual_type=visual_type, visual_genre=visual_genre)
    source_score = {"wikipedia": 100, "commons": 95, "ddg": 70, "pexels": 65, "unsplash": 65, "ai-generated": 45}.get(str(source).lower(), 50)
    if result is True:
        return True, tier, 100, False
    if result is False:
        print(f"   [Visual QA] {tier} | REJECTED: semantic check returned NO for '{entity}'.", flush=True)
        return False, tier, max(0, source_score - 20), True
    print(f"   [Visual QA] {tier} | soft reject: semantic verification unavailable/uncertain; candidate retained as fallback.", flush=True)
    return False, tier, source_score, False


def _build_search_variants(seg, video_title=""):
    """Compatibility boundary to the canonical exact-first search intent."""
    from visual_search_intent_runtime import resolve_visual_search_intent
    intent = resolve_visual_search_intent(seg, video_title)
    if not intent.subject or not intent.query:
        raise RuntimeError("No grounded visual search intent could be resolved from the scene.")
    return [intent.query], intent.visual_type


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


def _source_plan(bot, visual_type, category=""):
    """Compatibility boundary to the authoritative provider plan."""
    from visual_provider_boundary_runtime import build_raw_source_plan
    return build_raw_source_plan(visual_type, category)


def _relevant_asset(bot, seg, category, used_urls, used_hashes, video_title=""):
    """Compatibility boundary to the single active retrieval implementation."""
    from visual_retrieval_runtime import run_visual_retrieval
    return run_visual_retrieval(__import__(__name__), bot, seg, category, used_urls, used_hashes, video_title)


def _render_image_slide(bot, bg_img, title_text, subtitle_text="", font_choice=None, accent=None):
    """Render the intentional Top 5 title card.

    This opaque panel is deliberately retained for the Top 5 format only.
    Regular Shorts never use this renderer.
    """
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
