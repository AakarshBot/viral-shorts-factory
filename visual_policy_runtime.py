"""Small runtime-only visual policy patches.

Keeps the existing visual sourcing/render pipeline intact while:
- removing opaque hook/outro cards from non-Top-5 formats;
- limiting the same visual subject/search term to two scene uses per run;
- simplifying image search to the clean primary visual subject, with a
  headline-derived fallback only when the primary subject yields nothing.
"""
from __future__ import annotations

import os
import re
import threading
from collections import defaultdict

from PIL import Image, ImageDraw, ImageFont


_CONTEXT = threading.local()
_INSTALLED = False
_SIMPLE_SEARCH_INSTALLED = False
_SUBJECT_LOCK = threading.Lock()
_SUBJECT_RUNS = {}

# Common words that strongly suggest the field contains a sentence/description
# rather than a compact visual subject. This stays deterministic and costs no API
# calls. Known organisation acronyms are handled before these cues.
_SENTENCE_CUES = {
    "every", "each", "when", "while", "because", "given", "since", "after", "before", "but", "and",
    "or", "so", "if", "although", "though", "we", "you", "they", "he", "she", "it", "this", "that",
    "try", "tried", "tries", "get", "gets", "got", "getting", "happen", "happens", "happened", "will",
    "would", "could", "should", "season", "year", "years", "today", "tomorrow", "yesterday",
    "don't", "doesn't", "didn't", "isn't", "aren't", "wasn't", "weren't", "can't", "cannot",
}

_ORGANISATION_ACRONYMS = {
    "BCCI", "ICC", "PCB", "SLC", "BCB", "ACB", "FIFA", "UEFA", "NBA", "NFL", "ATP", "WTA",
    "ISRO", "NASA", "ESA", "WHO", "UN", "UNESCO", "IMF", "WTO", "SEBI", "RBI",
    "DRDO", "NITI", "BSE", "NSE", "TCS", "IBM", "AMD", "HP", "LG", "BMW",
}


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


def _clean_search_subject(value):
    """Reduce a model-produced visual subject to a compact, searchable entity."""
    text = re.sub(r"\s+", " ", str(value or "")).strip(" ,.-:;|\"'")
    if not text:
        return ""
    text = re.sub(
        r"^(?:primary\s+entity|visual\s+subject|subject|search\s+term|keyword)\s*[:=-]\s*",
        "",
        text,
        flags=re.I,
    ).strip()
    text = re.sub(
        r"\s+(?:official(?:\s+photo)?|press\s+photo|editorial\s+photo|news\s+photo|real\s+photo|best\s+innings|latest\s+update|latest\s+news|breaking\s+news|photo|image)\s*$",
        "",
        text,
        flags=re.I,
    ).strip(" ,.-:;|\"'")

    words = text.split()
    if not words:
        return ""

    # A known acronym is usually the actual entity even when the rest of the
    # field accidentally contains a sentence. Example:
    # "BCCI Impact Player Every year we try..." -> "BCCI".
    for word in words[:6]:
        token = re.sub(r"[^A-Za-z0-9&.-]", "", word).upper()
        if token in _ORGANISATION_ACRONYMS:
            return token

    # Once a sentence cue appears, keep only the compact subject before it.
    cue_index = next(
        (i for i, word in enumerate(words[1:], start=1) if word.lower().strip(".,!?;:") in _SENTENCE_CUES),
        None,
    )
    if cue_index is not None:
        words = words[:cue_index]

    # Never allow a sentence-like value to become a huge search query.
    if len(words) > 4:
        words = words[:4]

    return " ".join(words).strip(" ,.-:;|\"'")


def _headline_fallback(video_title):
    """Pick one compact subject from a headline when a scene has no usable entity."""
    text = _clean_search_subject(video_title)
    if not text:
        return ""
    stop = {
        "the", "a", "an", "and", "or", "but", "for", "with", "from", "into", "after", "before",
        "over", "under", "this", "that", "these", "those", "here", "there", "why", "how", "what",
        "when", "where", "who", "will", "would", "could", "should", "just", "now", "today", "latest",
        "breaking", "news", "update", "updates", "story", "stories", "report", "reports",
    }
    words = [w for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9&./'-]*", text) if w.lower() not in stop]
    if not words:
        return ""
    proper = [w.strip("'\"") for w in words if any(c.isupper() for c in w if c.isalpha())]
    if proper:
        return _clean_search_subject(" ".join(proper[:3]))
    return _clean_search_subject(words[0])


def _simple_build_deep_queries(seg, video_title="", visual_type=None):
    """Return at most one clean subject query for a scene."""
    entity = _clean_search_subject(seg.get("primary_entity", ""))
    try:
        from visual_strategy_runtime import classify_scene, VISUAL_TYPES
        category = str(seg.get("sport_or_topic_category", "") or "")
        # Classify a sanitised copy so a narration blob cannot force PERSON/other
        # misclassification just because it contains role words such as "player".
        classify_seg = dict(seg)
        classify_seg["primary_entity"] = entity
        resolved_type = visual_type or classify_scene(classify_seg, category)
        if resolved_type not in VISUAL_TYPES:
            resolved_type = "GENERAL_CONTEXT"
    except Exception:
        resolved_type = visual_type or "GENERAL_CONTEXT"
    query = entity if entity and entity.lower() not in {"none", "unknown", "n/a"} else _headline_fallback(video_title)
    return ([query] if query else []), resolved_type


def _install_simple_search_policy():
    global _SIMPLE_SEARCH_INSTALLED
    if _SIMPLE_SEARCH_INSTALLED:
        return True
    try:
        import visual_strategy_runtime
        current = getattr(visual_strategy_runtime, "build_deep_queries", None)
        if current and not getattr(current, "_simple_search_bound", False):
            _simple_build_deep_queries._simple_search_bound = True
            visual_strategy_runtime.build_deep_queries = _simple_build_deep_queries
            _SIMPLE_SEARCH_INSTALLED = True
            print("   [Visual Policy] Simple visual search installed: primary subject only; headline fallback when missing.", flush=True)
            return True
    except Exception as exc:
        print(f"   [Visual Policy] Simple search unavailable: {type(exc).__name__}: {exc}", flush=True)
    return False


def _subject_limit_wrapper(original):
    if getattr(original, "_subject_limit_bound", False):
        return original

    def limited_relevant_asset(bot, seg, category, used_urls, used_hashes, video_title=""):
        entity = _clean_search_subject(seg.get("primary_entity", "") or "")
        key = re.sub(r"\s+", " ", entity.lower()).strip()
        run_key = id(used_hashes)
        chosen_seg = seg
        if key:
            with _SUBJECT_LOCK:
                run_counts = _SUBJECT_RUNS.setdefault(run_key, defaultdict(int))
                count = run_counts[key]
                if count < 2:
                    run_counts[key] += 1
                else:
                    fallback = _headline_fallback(video_title)
                    if fallback and fallback.lower() != key:
                        chosen_seg = dict(seg)
                        chosen_seg["primary_entity"] = fallback
                        chosen_seg["visual_type"] = "GENERAL_CONTEXT"
                        chosen_seg["visual_intent"] = "documentary context"
                        chosen_seg["specific_search_prompt"] = fallback
                    else:
                        chosen_seg = dict(seg)
                        chosen_seg["primary_entity"] = ""
                        chosen_seg["visual_type"] = "GENERAL_CONTEXT"
                        chosen_seg["specific_search_prompt"] = ""

        try:
            return original(bot, chosen_seg, category, used_urls, used_hashes, video_title)
        finally:
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
            print("   [Visual Policy] Subject repeat limit installed: max two primary-subject uses per run.", flush=True)
            return True
    except Exception as exc:
        print(f"   [Visual Policy] Subject limit unavailable: {type(exc).__name__}: {exc}", flush=True)
    return False


def install_visual_card_policy(bot=None):
    """Patch the legacy card renderers and visual search policy once."""
    global _INSTALLED
    if _INSTALLED:
        _install_simple_search_policy()
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
            # This legacy global is ultimately called through factory_runtime's
            # runtime wrapper, which supplies bot + script_data as well.
            def render_hook_card_no_card(_bot, bg_img, hook_text, width=1080, height=1920, font_choice=None, script_data=None):
                return bg_img.convert("RGBA")
            render_hook_card_no_card._qc_hook_passthrough = True
            namespace["render_hook_card"] = render_hook_card_no_card

        original_slide = namespace.get("create_branded_slide")
        if callable(original_slide) and not getattr(original_slide, "_qc_non_top5_slide", False):
            def create_branded_slide_policy(title_text, subtitle_text, is_outro=False, width=1080, height=1920, font_choice=None):
                if str(subtitle_text or "").strip().upper() in {"TODAY'S SPECIAL", "SUBSCRIBE!"}:
                    return original_slide(title_text, subtitle_text, is_outro=is_outro, width=width, height=height, font_choice=font_choice)
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
        _install_simple_search_policy()
        _install_subject_limit()
        print("   [Visual Policy] Non-Top-5 hook/outro cards disabled; Top-5 cards preserved.", flush=True)
        return True
    except Exception as exc:
        print(f"   [Visual Policy] Card/search policy unavailable: {type(exc).__name__}: {exc}", flush=True)
    return False


# The visual strategy module imports this policy before the factory binds
# script_runtime. Install the script guard at import time so the guard is active
# automatically for every normal factory startup.
try:
    from script_guard_runtime import install as _install_script_output_guard
    _install_script_output_guard()
except Exception as exc:
    print(f"   [Script Guard] Auto-install unavailable: {type(exc).__name__}: {exc}", flush=True)
