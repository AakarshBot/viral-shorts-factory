"""Small runtime-only visual policy patches.

Keeps the existing visual sourcing/render pipeline intact while removing the
opaque hook/outro cards from non-Top-5 formats. Top-5 card behavior is kept.
Also limits repeated visual subjects to two uses per production run.
"""
from __future__ import annotations

import os
import re
import threading
from collections import defaultdict

from PIL import Image, ImageDraw, ImageFont


_CONTEXT = threading.local()
_INSTALLED = False
_SUBJECT_LOCK = threading.Lock()
_SUBJECT_RUNS = {}


def _bg_color(bot):
    palette = getattr(bot, "PALETTE", {})
    return tuple(palette.get("bg", (15, 20, 35)))


def _font(bot, size, font_choice=None):
    candidates = []
    if font_choice:
        candidates.extend([
            str(font_choice),
            os.path.join("C:\\Windows\\Fonts", str(font_choice)),
        ])
    candidates.extend([
        r"C:\Windows\Fonts\segoeuib.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ])
    for path in candidates:
        try:
            if os.path.isfile(path):
                return ImageFont.truetype(path, size=int(size))
        except Exception:
            pass
    return ImageFont.load_default()


def _scene_fallback_subject(seg):
    """Create a contextual search subject when the same entity has already been used twice."""
    try:
        from visual_strategy_runtime import _scene_phrase
        phrase = str(_scene_phrase(seg) or "").strip()
    except Exception:
        phrase = ""
    if not phrase:
        phrase = str(seg.get("specific_search_prompt", "") or "").strip()
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'/-]*", phrase)
    return " ".join(words[:8]).strip() or "documentary context"


def _subject_limit_wrapper(original):
    if getattr(original, "_subject_limit_bound", False):
        return original

    def limited_relevant_asset(bot, seg, category, used_urls, used_hashes, video_title=""):
        entity = str(seg.get("primary_entity", "") or "").strip()
        key = re.sub(r"\s+", " ", entity.lower()).strip()
        run_key = id(used_hashes)
        if key:
            with _SUBJECT_LOCK:
                run_counts = _SUBJECT_RUNS.setdefault(run_key, defaultdict(int))
                count = run_counts[key]
                if count < 2:
                    run_counts[key] += 1
                    chosen_seg = seg
                else:
                    chosen_seg = dict(seg)
                    chosen_seg["primary_entity"] = _scene_fallback_subject(seg)
                    chosen_seg["visual_type"] = "GENERAL_CONTEXT"
                    chosen_seg["visual_intent"] = "documentary context"
                    chosen_seg["specific_search_prompt"] = chosen_seg["primary_entity"]
                    # Mark the fallback as a separate contextual subject so the
                    # same entity can be mentioned in the narration without
                    # forcing the image search back onto it.
                    chosen_seg["subject_repeat_limited"] = True
        else:
            chosen_seg = seg

        try:
            return original(bot, chosen_seg, category, used_urls, used_hashes, video_title)
        finally:
            # Keep memory bounded; each run's hash-set is unique.
            with _SUBJECT_LOCK:
                if len(_SUBJECT_RUNS) > 32:
                    oldest_key = next(iter(_SUBJECT_RUNS))
                    if oldest_key != run_key:
                        _SUBJECT_RUNS.pop(oldest_key, None)

    limited_relevant_asset._subject_limit_bound = True
    return limited_relevant_asset


def _install_subject_limit():
    try:
        import visual_runtime
        current = getattr(visual_runtime, "_relevant_asset", None)
        if current and not getattr(current, "_subject_limit_bound", False):
            visual_runtime._relevant_asset = _subject_limit_wrapper(current)
            print("   [Visual Policy] Subject repeat limit installed: max two uses per entity/run.", flush=True)
            return True
    except Exception as exc:
        print(f"   [Visual Policy] Subject limit unavailable: {type(exc).__name__}: {exc}", flush=True)
    return False


def install_visual_card_policy(bot=None):
    """Patch the legacy card renderers once, without replacing the visual pipeline."""
    global _INSTALLED
    if _INSTALLED:
        _install_subject_limit()
        return True

    try:
        import ultimate_bot as default_bot
        bot = bot or default_bot
        run_robot = getattr(bot, "run_robot", None)
        namespace = getattr(run_robot, "__globals__", None)
        if namespace is None:
            return False

        original_hook = namespace.get("render_hook_card")
        if callable(original_hook) and not getattr(original_hook, "_qc_hook_passthrough", False):
            def render_hook_card_no_card(bg_img, hook_text, width=1080, height=1920, font_choice=None):
                # Non-Top-5 first frames simply reveal the fetched image.
                return bg_img.convert("RGBA")
            render_hook_card_no_card._qc_hook_passthrough = True
            namespace["render_hook_card"] = render_hook_card_no_card

        original_slide = namespace.get("create_branded_slide")
        if callable(original_slide) and not getattr(original_slide, "_qc_non_top5_slide", False):
            def create_branded_slide_policy(
                title_text,
                subtitle_text,
                is_outro=False,
                width=1080,
                height=1920,
                font_choice=None,
            ):
                # Top-5 keeps the existing title/outro cards exactly as designed.
                if str(subtitle_text or "").strip().upper() in {"TODAY'S SPECIAL", "SUBSCRIBE!"}:
                    return original_slide(
                        title_text,
                        subtitle_text,
                        is_outro=is_outro,
                        width=width,
                        height=height,
                        font_choice=font_choice,
                    )

                # Regular/trending/tech-review outros keep only a clean CTA on a
                # simple branded background; the opaque rounded card is removed.
                bg = Image.new("RGBA", (width, height), _bg_color(bot) + (255,))
                draw = ImageDraw.Draw(bg)
                palette = getattr(bot, "PALETTE", {})
                primary = tuple(palette.get("accent_primary", (0, 191, 255)))
                secondary = tuple(palette.get("accent_secondary", (255, 140, 0)))
                draw.rectangle((0, 0, width, 36), fill=primary + (180,))
                draw.rectangle((0, height - 36, width, height), fill=secondary + (150,))

                title_font = _font(bot, 86, font_choice)
                cta_font = _font(bot, 60, font_choice)
                title = str(title_text or "").strip()
                cta = str(subtitle_text or "").strip()

                def centered(text, font, y, fill):
                    bbox = draw.textbbox((0, 0), text, font=font)
                    w = bbox[2] - bbox[0]
                    draw.text(((width - w) / 2, y), text, font=font, fill=fill)

                if title:
                    centered(title, title_font, int(height * 0.42), (242, 244, 246, 255))
                if cta:
                    centered(cta, cta_font, int(height * 0.52), secondary + (255,))
                return bg

            create_branded_slide_policy._qc_non_top5_slide = True
            namespace["create_branded_slide"] = create_branded_slide_policy

        _INSTALLED = True
        _install_subject_limit()
        print("   [Visual Policy] Non-Top-5 hook/outro cards disabled; Top-5 cards preserved.", flush=True)
        return True
    except Exception as exc:
        print(f"   [Visual Policy] Card policy unavailable: {type(exc).__name__}: {exc}", flush=True)
        return False
