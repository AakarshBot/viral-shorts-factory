"""Robust, genre-agnostic visual retrieval for the Shorts factory.

Retrieval and semantic verification are deliberately separate concerns. A bad
first result never ends a scene: the retriever walks a bounded query ladder,
tries every applicable provider, verifies only decodable candidates against the
factual identity plus scene context, and uses a clearly marked contextual
fallback when no verified visual exists.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageFilter


ABSTRACT_TYPES = {"PROCESS", "CONCEPT", "GENERAL_CONTEXT"}


def _as_image_bytes(data: Any) -> bytes | None:
    """Normalize provider output into image bytes without spending QA budget."""
    if data is None:
        return None
    if isinstance(data, (bytes, bytearray, memoryview)):
        return bytes(data)
    if isinstance(data, Image.Image):
        try:
            buffer = io.BytesIO()
            data.convert("RGB").save(buffer, format="JPEG", quality=95)
            return buffer.getvalue()
        except Exception:
            return None
    return None


def _source_plan(bot, visual_type: str):
    """Prioritize stronger sources by visual modality, never by content genre."""
    plan = []
    if str(visual_type).upper() == "PERSON":
        plan.extend([
            ("Wikipedia", getattr(bot, "fetch_wiki_person_image", None)),
            ("Commons", getattr(bot, "fetch_wikimedia_commons", None)),
        ])
    elif str(visual_type).upper() in {"ORGANIZATION", "EVENT", "QUOTE", "DOCUMENT", "LOCATION"}:
        plan.append(("Commons", getattr(bot, "fetch_wikimedia_commons", None)))
    plan.extend([
        ("DDG", getattr(bot, "fetch_duckduckgo", None)),
        ("Pexels", getattr(bot, "fetch_pexels", None)),
        ("Unsplash", getattr(bot, "fetch_unsplash", None)),
    ])
    return [(name, fn) for name, fn in plan if callable(fn)]


def _hash_image(bot, data: bytes) -> str:
    try:
        return str(bot.get_image_hash(data))
    except Exception:
        return hashlib.sha256(data).hexdigest()


def _preflight_image(data: Any) -> tuple[bool, str, bytes | None]:
    """Cheap image validation; invalid bytes consume zero semantic-QA budget."""
    normalized = _as_image_bytes(data)
    if not normalized:
        return False, "empty-or-nonimage", None
    try:
        image = Image.open(io.BytesIO(normalized))
        image.load()
        image = image.convert("RGB")
        width, height = image.size
        if min(width, height) < 300:
            return False, f"resolution-too-low:{width}x{height}", None
        ratio = width / max(1, height)
        if not 0.25 <= ratio <= 4.0:
            return False, f"extreme-aspect:{ratio:.2f}", None
        return True, "image-decodable", normalized
    except Exception:
        return False, "invalid-image", None


def _safe_font(size: int):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\segoeuib.ttf",
    ]
    for path in candidates:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, int(size))
            except Exception:
                pass
    return ImageFont.load_default()


def _safe_rounded_rectangle(draw, coords, width: int, height: int, radius: int, fill=None, outline=None):
    """Draw a rounded rectangle only after normalizing/clamping its geometry."""
    try:
        x0, y0, x1, y1 = [float(value) for value in coords]
        left, right = sorted((x0, x1))
        top, bottom = sorted((y0, y1))
        left = max(0.0, min(left, max(0, width - 1)))
        right = max(0.0, min(right, max(0, width - 1)))
        top = max(0.0, min(top, max(0, height - 1)))
        bottom = max(0.0, min(bottom, max(0, height - 1)))
        if right <= left or bottom <= top:
            return
        safe_radius = max(0, min(int(radius), int((right - left) / 2), int((bottom - top) / 2)))
        draw.rounded_rectangle([left, top, right, bottom], radius=safe_radius, fill=fill, outline=outline, width=max(1, int(width and 1)))
    except Exception:
        return


def make_contextual_fallback(subject: str, visual_type: str, size=(1080, 1920)) -> Image.Image:
    """Create a neutral, explicitly non-factual visual when retrieval is exhausted."""
    width, height = [max(2, int(value)) for value in size]
    image = Image.new("RGB", (width, height), (12, 18, 28))
    draw = ImageDraw.Draw(image)

    # Keep the decorative geometry inside the canvas. Previously the fixed
    # twelve-ring loop eventually produced x0 > x1 on normal portrait frames.
    inner_limit = max(1, (min(width, height) - 20) // 2)
    for index in range(12):
        inset = 70 + index * 55
        if inset >= inner_limit:
            break
        _safe_rounded_rectangle(
            draw,
            [inset, inset, width - inset, height - inset],
            width=12 - index // 2,
            height=height,
            radius=42,
            outline=(20 + index * 7, 28 + index * 7, 38 + index * 7),
        )
    for angle_index in range(10):
        x0 = int(width * 0.08 + angle_index * width * 0.095)
        draw.line([(x0, min(120, height // 4)), (max(0, width - x0), max(0, height - min(120, height // 4)))], fill=(24, 44, 64), width=max(1, min(5, width // 100)))

    subject = re.sub(r"\s+", " ", str(subject or "")).strip() or "Visual unavailable"
    if len(subject) > 90:
        subject = subject[:87].rstrip() + "…"
    type_text = re.sub(r"[_-]+", " ", str(visual_type or "GENERAL_CONTEXT")).upper()
    font = _safe_font(68)
    small = _safe_font(30)
    badge = _safe_font(26)

    try:
        box = draw.textbbox((0, 0), subject, font=font)
        max_width = max(1, width - 180)
        if box[2] - box[0] > max_width:
            font = _safe_font(48)
            box = draw.textbbox((0, 0), subject, font=font)
        text_w = box[2] - box[0]
        text_h = box[3] - box[1]
        x = max(0, (width - text_w) / 2)
        y = max(0, (height - text_h) / 2)
        _safe_rounded_rectangle(
            draw,
            [90, y - 70, width - 90, y + text_h + 70],
            width=3,
            height=height,
            radius=38,
            fill=(7, 12, 20),
            outline=(78, 108, 136),
        )
        draw.text((min(width - 1, x + 4), min(height - 1, y + 5)), subject, font=font, fill=(0, 0, 0))
        draw.text((min(width - 1, x), min(height - 1, y)), subject, font=font, fill=(236, 241, 246))
    except Exception:
        try:
            draw.text((max(0, width // 20), max(0, height // 2)), subject, font=font, fill=(236, 241, 246))
        except Exception:
            pass

    footer = "CONTEXTUAL VISUAL · SOURCE IMAGE UNAVAILABLE"
    footer_box = draw.textbbox((0, 0), footer, font=small)
    footer_x = max(0, min(width - 1, (width - (footer_box[2] - footer_box[0])) / 2))
    footer_y = max(0, height - 180)
    draw.text((footer_x, footer_y), footer, font=small, fill=(145, 168, 190))
    badge_text = f"{type_text} · FALLBACK"
    badge_box = draw.textbbox((0, 0), badge_text, font=badge)
    badge_left = min(max(5, 55), max(5, width - 5))
    badge_top = min(max(5, 55), max(5, height - 5))
    badge_right = min(width - 1, max(badge_left + 1, badge_left + badge_box[2] - badge_box[0] + 34))
    badge_bottom = min(height - 1, max(badge_top + 1, badge_top + badge_box[3] - badge_box[1] + 24))
    _safe_rounded_rectangle(
        draw,
        [badge_left, badge_top, badge_right, badge_bottom],
        width=2,
        height=height,
        radius=16,
        fill=(22, 34, 48),
        outline=(72, 104, 132),
    )
    draw.text((min(width - 1, badge_left + 17), min(height - 1, badge_top + 12)), badge_text, font=badge, fill=(188, 208, 224))
    return image.filter(ImageFilter.GaussianBlur(radius=0.15))


def _ai_prompt(subject: str, visual_type: str) -> str:
    if visual_type == "PROCESS":
        return (
            f"Create a clean editorial illustration of the process '{subject}'. "
            "Use realistic objects and environments, no identifiable real people, "
            "no fabricated documents, no logos, no text-heavy UI."
        )
    if visual_type == "CONCEPT":
        return (
            f"Create a polished documentary-style explanatory illustration of '{subject}'. "
            "Use accurate generic visual metaphors, realistic lighting, and no invented real-world identities."
        )
    return (
        f"Create a polished editorial contextual illustration for '{subject}'. "
        "Do not depict or identify any real person or organization as a factual image; "
        "use a neutral documentary visual metaphor."
    )


def run_visual_retrieval(runtime, bot, seg: dict, category: str, used_urls: set[str], used_hashes: set[str], video_title: str = ""):
    """Retrieve one scene visual without making the first query a hard stop."""
    entity = str(seg.get("primary_entity", "")).strip()
    factual_entity = str(seg.get("factual_primary_entity") or entity).strip()
    queries, visual_type = runtime._build_search_variants(seg, video_title)
    visual_type = str(visual_type or "GENERAL_CONTEXT").upper()
    if not entity or not queries:
        seg["visual_verified"] = False
        seg["visual_fallback_reason"] = "no-grounded-visual-query"
        return make_contextual_fallback(entity or factual_entity, visual_type), False, "contextual-fallback"

    intent = str(seg.get("factual_visual_intent") or seg.get("visual_intent") or "").strip()
    prompt = str(seg.get("specific_search_prompt") or entity).strip()
    voice = str(seg.get("factual_voiceover") or seg.get("voiceover") or "").strip()
    context = runtime._context_fingerprint(intent, prompt, voice, video_title)
    cache_entity = factual_entity or entity

    cached_img, _cache_path = runtime.get_cached_asset(bot, cache_entity, visual_type, context)
    if cached_img is not None:
        buffer = io.BytesIO()
        cached_img.save(buffer, format="JPEG", quality=95)
        cached_hash = _hash_image(bot, buffer.getvalue())
        if cached_hash not in used_hashes:
            used_hashes.add(cached_hash)
            seg["visual_verified"] = True
            seg["visual_fallback_reason"] = ""
            seg["visual_query_used"] = "cache"
            return cached_img.convert("RGB"), False, "cached"

    verification_attempts = 0
    hard_rejections = 0
    max_verification = max(1, int(getattr(runtime, "VISUAL_MAX_VERIFICATION_ATTEMPTS", 4)))

    # Semantic QA must verify the factual identity, while the search query may
    # contain scene-specific context. Keep these two concepts separate.
    qa_scene = dict(seg)
    qa_scene["primary_entity"] = cache_entity
    qa_scene["factual_primary_entity"] = cache_entity
    qa_scene["visual_intent"] = intent
    qa_scene["specific_search_prompt"] = prompt
    qa_scene["voiceover"] = voice

    for query_index, query in enumerate(queries, 1):
        print(f"   [Visual Search] {query_index}/{len(queries)} | '{query}'", flush=True)
        for source, fetcher in _source_plan(bot, visual_type):
            tier = runtime._verification_tier(qa_scene, visual_type, source)
            semantic_required = tier not in {"STRICT(person)", "SKIPPED(conceptual)"}
            if semantic_required and verification_attempts >= max_verification:
                print(f"   [Visual QA] semantic budget exhausted; remaining provider checks skipped for query='{query}'", flush=True)
                break

            fetch_entity = cache_entity if source == "Wikipedia" else query
            args = (fetch_entity, used_urls, query, video_title) if source == "Wikipedia" else (query, used_urls, query, video_title)
            data = runtime._call_fetcher_with_timeout(fetcher, args, source, query)
            valid, reason, normalized = _preflight_image(data)
            if not valid:
                print(f"   [Visual Quality] REJECTED | {reason} | source={source} | query='{query}'", flush=True)
                continue

            image_hash = _hash_image(bot, normalized)
            if image_hash in used_hashes:
                print(f"   [Visual Search] duplicate image skipped | source={source} | query='{query}'", flush=True)
                continue

            if semantic_required:
                verification_attempts += 1

            try:
                accepted, tier_name, score, hard_reject = runtime._strict_gate(
                    bot, normalized, qa_scene, video_title, source=source
                )
            except Exception as exc:
                hard_reject = True
                accepted = False
                score = 0
                print(f"   [Visual QA] candidate check failed safely | source={source}: {type(exc).__name__}: {exc}", flush=True)

            if hard_reject:
                hard_rejections += 1
            if not accepted:
                print(f"   [Visual Quality] REJECTED | semantic mismatch/uncertain | source={source} | query='{query}'", flush=True)
                continue

            try:
                runtime.save_to_cache(bot, normalized, cache_entity, visual_type, source, context)
            except Exception:
                pass
            used_hashes.add(image_hash)
            seg["visual_verified"] = True
            seg["visual_fallback_reason"] = ""
            seg["visual_query_used"] = query
            seg["visual_verification_attempts"] = verification_attempts
            print(
                f"   [Visual Source] {source} | VERIFIED | tier={tier_name} | score={score} | query='{query}'",
                flush=True,
            )
            return Image.open(io.BytesIO(normalized)).convert("RGB"), False, source

    if visual_type in ABSTRACT_TYPES and callable(getattr(bot, "fetch_hf_ai_image", None)):
        prompt_text = _ai_prompt(entity, visual_type)
        print(f"   [Visual Source] AI attempt | type={visual_type} | prompt='{prompt_text[:160]}'", flush=True)
        ai = runtime._call_fetcher_with_timeout(bot.fetch_hf_ai_image, (prompt_text,), "HF-AI", prompt_text)
        valid, reason, normalized = _preflight_image(ai)
        if valid and normalized is not None:
            image_hash = _hash_image(bot, normalized)
            tier = runtime._verification_tier(qa_scene, visual_type, "AI-generated")
            semantic_required = tier not in {"SKIPPED(conceptual)"}
            if image_hash not in used_hashes and (not semantic_required or verification_attempts < max_verification):
                if semantic_required:
                    verification_attempts += 1
                try:
                    accepted, tier_name, _score, _hard_reject = runtime._strict_gate(
                        bot, normalized, qa_scene, video_title, source="AI-generated"
                    )
                except Exception as exc:
                    accepted, tier_name = False, "STRICT"
                    print(f"   [Visual QA] AI candidate check failed safely: {type(exc).__name__}: {exc}", flush=True)
                if accepted:
                    used_hashes.add(image_hash)
                    seg["visual_verified"] = True
                    seg["visual_fallback_reason"] = ""
                    seg["visual_query_used"] = "AI-generated"
                    seg["visual_verification_attempts"] = verification_attempts
                    return Image.open(io.BytesIO(normalized)).convert("RGB"), True, "AI-generated"
            else:
                print("   [Visual Quality] REJECTED | AI candidate duplicate or semantic-QA budget exhausted", flush=True)
        else:
            print(f"   [Visual Quality] REJECTED | {reason} | source=HF-AI", flush=True)

    seg["visual_verified"] = False
    seg["visual_fallback_reason"] = (
        f"retrieval-exhausted; queries={len(queries)}; semantic_verifications={verification_attempts}; "
        f"hard_rejections={hard_rejections}"
    )
    seg["visual_verification_attempts"] = verification_attempts
    print(
        f"   [Visual Fallback] Contextual fallback used | subject='{entity}' type={visual_type} "
        f"queries={len(queries)} semantic_verifications={verification_attempts}/{max_verification} "
        f"hard_rejections={hard_rejections}",
        flush=True,
    )
    return make_contextual_fallback(entity, visual_type), False, "contextual-fallback"