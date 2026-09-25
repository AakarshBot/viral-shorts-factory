"""Shared visual cache and bounded fetch runtime.

Regular visual retrieval lives in visual_retrieval_runtime.py. This module only
provides the cache, provider timeout wrapper, and Top 5 title-card renderer.
"""
import hashlib
import io
import json
import os
import threading

from PIL import Image, ImageDraw

VISUAL_FETCH_TIMEOUT_SECONDS = int(os.getenv("VISUAL_FETCH_TIMEOUT_SECONDS", "10"))
VISUAL_CACHE_MAX_AGE_SECONDS = int(
    os.getenv("VISUAL_CACHE_MAX_AGE_SECONDS", str(7 * 86400))
)


def _cache_root(bot):
    root = getattr(bot, "ASSET_CACHE_DIR", None) or os.getenv("ASSET_CACHE_DIR")
    if not root:
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "asset_cache")
    os.makedirs(root, exist_ok=True)
    return root


def _cache_key(entity, visual_type, context=""):
    raw = (
        f"{str(entity).strip().lower()}::"
        f"{str(visual_type).strip().upper()}::"
        f"{str(context).strip().lower()}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def _local_visual_sanity(img_bytes):
    try:
        image = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        return min(image.size) >= 540
    except Exception:
        return False


def get_cached_asset(bot, entity, visual_type, context=""):
    """Return a cached entity/type/context visual that passed verification."""
    if not entity or str(entity).strip().lower() in {"none", "unknown", "n/a"}:
        return None, None
    key = _cache_key(entity, visual_type, context)
    root = _cache_root(bot)
    image_path = os.path.join(root, f"{key}.jpg")
    meta_path = os.path.join(root, f"{key}.json")
    try:
        if not os.path.exists(image_path) or not os.path.exists(meta_path):
            return None, None
        if (
            os.path.getmtime(image_path)
            < __import__("time").time() - VISUAL_CACHE_MAX_AGE_SECONDS
        ):
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
    """Persist an image; runtime bindings require an explicit verified=True flag."""
    if not img_bytes or not entity:
        return None
    key = _cache_key(entity, visual_type, context)
    root = _cache_root(bot)
    image_path = os.path.join(root, f"{key}.jpg")
    meta_path = os.path.join(root, f"{key}.json")
    try:
        image = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        image.save(image_path, "JPEG", quality=95)
        with open(meta_path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "entity": str(entity).strip(),
                    "visual_type": str(visual_type).upper(),
                    "source_type": str(source_type),
                    "context": str(context),
                    "verified": True,
                },
                fh,
                ensure_ascii=False,
                indent=2,
            )
        return image_path
    except Exception as exc:
        print(f"   [Visual Cache] Write failed: {type(exc).__name__}: {exc}", flush=True)
        return None


def _call_fetcher_with_timeout(
    fetcher,
    args,
    source,
    query,
    timeout=VISUAL_FETCH_TIMEOUT_SECONDS,
):
    result = {"value": None, "error": None}

    def worker():
        try:
            result["value"] = fetcher(*args)
        except Exception as exc:
            result["error"] = exc

    thread = threading.Thread(
        target=worker,
        name=f"visual-{source.lower()}-fetch",
        daemon=True,
    )
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        print(
            f"   [Visual Source] {source} | timed out after {timeout}s | "
            f"query='{query}'",
            flush=True,
        )
        return None
    if result["error"] is not None:
        print(
            f"   [Visual Source] {source} | failed: {result['error']} | "
            f"query='{query}'",
            flush=True,
        )
        return None
    return result["value"]


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