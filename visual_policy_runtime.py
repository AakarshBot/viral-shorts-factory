"""Runtime visual policy patches.

This module keeps non-query visual policies local and deliberately does not
replace the authoritative scene-aware search planner in visual_strategy_runtime.
"""
from __future__ import annotations

import os
import re

from PIL import Image, ImageDraw, ImageFont

_INSTALLED = False

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
        candidates.extend([str(font_choice), os.path.join("C:\\Windows\\Fonts", str(font_choice))])
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
    """Reduce a model-produced visual subject to a compact searchable entity."""
    text = re.sub(r"\s+", " ", str(value or "")).strip(" ,.-:;|\"'")
    if not text:
        return ""
    text = re.sub(
        r"^(?:primary\s+entity|visual\s+subject|subject|search\s+term|keyword)\s*[:=-]\s*",
        "", text, flags=re.I,
    ).strip()
    text = re.sub(
        r"\s+(?:official(?:\s+photo)?|press\s+photo|editorial\s+photo|news\s+photo|real\s+photo|best\s+innings|latest\s+update|latest\s+news|breaking\s+news|photo|image)\s*$",
        "", text, flags=re.I,
    ).strip(" ,.-:;|\"'")
    words = text.split()
    if not words:
        return ""

    # Only collapse a known acronym when the value becomes sentence-like.
    sentence_cues = {
        "every", "each", "when", "while", "because", "after", "before", "try", "tried", "tries",
        "get", "gets", "got", "getting", "happen", "happens", "will", "would", "could", "should",
        "announce", "announced", "announces", "said", "says", "told", "revealed", "confirmed",
        "expects", "expected", "plans", "planned", "wants", "wanted", "reports", "reported",
    }
    cue_index = next(
        (i for i, word in enumerate(words[1:], start=1) if word.lower().strip(".,!?;:") in sentence_cues),
        None,
    )
    if cue_index is not None:
        prefix = words[:cue_index]
        for word in prefix[:6]:
            token = re.sub(r"[^A-Za-z0-9&.-]", "", word).upper()
            if token in _ORGANISATION_ACRONYMS:
                return token
        words = prefix

    return " ".join(words[:8]).strip(" ,.-:;|\"'")


def _headline_fallback(video_title):
    """Choose a compact subject from a headline when the scene has no entity."""
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
    return _clean_search_subject(" ".join(proper[:3])) if proper else _clean_search_subject(words[0])


def install_visual_card_policy(bot=None):
    """Patch legacy card renderers without replacing visual search strategy."""
    global _INSTALLED
    if _INSTALLED:
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
            def render_hook_card_no_card(_bot, bg_img, hook_text, width=1080, height=1920, font_choice=None, script_data=None):
                return bg_img.convert("RGBA")
            render_hook_card_no_card._qc_hook_passthrough = True
            namespace["render_hook_card"] = render_hook_card_no_card

        original_slide = namespace.get("create_branded_slide")
        if callable(original_slide) and not getattr(original_slide, "_qc_non_top5_slide", False):
            def create_branded_slide_policy(_bot, title_text, subtitle_text, is_outro=False, width=1080, height=1920, font_choice=None, script_data=None):
                active_bot = _bot or bot
                if str(subtitle_text or "").strip().upper() in {"TODAY'S SPECIAL", "SUBSCRIBE!"}:
                    return original_slide(active_bot, title_text, subtitle_text, is_outro=is_outro, width=width, height=height, font_choice=font_choice, script_data=script_data)
                bg = Image.new("RGBA", (width, height), _bg_color(active_bot) + (255,))
                draw = ImageDraw.Draw(bg)
                palette = getattr(active_bot, "PALETTE", {})
                primary = tuple(palette.get("accent_primary", (0, 191, 255)))
                secondary = tuple(palette.get("accent_secondary", (255, 140, 0)))
                draw.rectangle((0, 0, width, 36), fill=primary + (180,))
                draw.rectangle((0, height - 36, width, height), fill=secondary + (150,))
                title_font = _font(active_bot, 86, font_choice)
                cta_font = _font(active_bot, 60, font_choice)
                title = str(title_text or "").strip()
                cta = str(subtitle_text or "").strip()
                def centered(text, font, y, fill):
                    bbox = draw.textbbox((0, 0), text, font=font)
                    draw.text(((width - (bbox[2] - bbox[0])) / 2, y), text, font=font, fill=fill)
                if title:
                    centered(title, title_font, int(height * 0.42), (242, 244, 246, 255))
                if cta:
                    centered(cta, cta_font, int(height * 0.52), secondary + (255,))
                return bg
            create_branded_slide_policy._qc_non_top5_slide = True
            namespace["create_branded_slide"] = create_branded_slide_policy

        _INSTALLED = True
        print("   [Visual Policy] Non-Top-5 hook/outro cards disabled; Top-5 cards preserved; authoritative search planner retained.", flush=True)
        return True
    except Exception as exc:
        print(f"   [Visual Policy] Card policy unavailable: {type(exc).__name__}: {exc}", flush=True)
    return False


try:
    from script_guard_runtime import install as _install_script_output_guard
    _install_script_output_guard()
except Exception as exc:
    print(f"   [Script Guard] Auto-install unavailable: {type(exc).__name__}: {exc}", flush=True)

try:
    from visual_safety_runtime import install as _install_visual_safety
    _install_visual_safety()
except Exception as exc:
    print(f"   [Visual Safety] Auto-install unavailable: {type(exc).__name__}: {exc}", flush=True)
