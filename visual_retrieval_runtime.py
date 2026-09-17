"""Genre-agnostic multi-source visual retrieval for the Shorts factory.

The active visual path is deliberately resilient: each scene gets multiple
search phrases, multiple real image sources, several candidates per source,
cheap image validation, then semantic verification. A real source candidate
that is merely unverified is preferred over an empty frame when verification
infrastructure is unavailable. AI generation is reserved for abstract/contextual
scenes after real-source retrieval is exhausted. A tiny built-in visual rescue
exists only as the final renderer guarantee; it is never presented as a factual
photograph.

The provider layer is intentionally raw. Provider adapters only search and
return downloadable image bytes; this module is the sole active acceptance
boundary for image decode, dimensions, deduplication and semantic QA.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageFilter


ABSTRACT_TYPES = {"PROCESS", "CONCEPT", "GENERAL_CONTEXT"}
REAL_SOURCE_SCORES = {
    "wikipedia": 100,
    "commons": 96,
    "openverse": 90,
    "pixabay": 82,
    "ddg": 76,
    "pexels": 72,
    "unsplash": 70,
}
MAX_CANDIDATES_PER_SOURCE = max(1, min(6, int(os.getenv("VISUAL_CANDIDATES_PER_SOURCE", "4"))))


def _source_plan(_bot, visual_type: str):
    """Compatibility boundary backed only by the authoritative raw provider plan."""
    from visual_provider_boundary_runtime import build_raw_source_plan
    return build_raw_source_plan(visual_type)


def _context_fingerprint(intent="", prompt="", voice="", video_title=""):
    raw = " | ".join(str(value or "").strip().lower() for value in (intent, prompt, voice, video_title))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _as_image_bytes(data: Any) -> bytes | None:
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


def _preflight_image(data: Any) -> tuple[bool, str, bytes | None]:
    """Cheap decode/size/aspect validation; invalid bytes consume no QA budget."""
    if data is None:
        return False, "provider-returned-no-candidate", None
    normalized = _as_image_bytes(data)
    if not normalized:
        return False, "provider-returned-unsupported-data", None
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
        try:
            if os.path.isfile(path):
                return ImageFont.truetype(path, int(size))
        except Exception:
            pass
    return ImageFont.load_default()


def make_visual_rescue(subject: str, visual_type: str, size=(1080, 1920)) -> Image.Image:
    """Guarantee a non-blank frame only after every real/AI source is exhausted."""
    width, height = [max(2, int(v)) for v in size]
    image = Image.new("RGB", (width, height), (10, 16, 25))
    draw = ImageDraw.Draw(image)
    subject = re.sub(r"\s+", " ", str(subject or "Visual unavailable")).strip()[:110].rstrip()
    kind = re.sub(r"[_-]+", " ", str(visual_type or "GENERAL_CONTEXT")).upper()
    title_font = _safe_font(64)
    meta_font = _safe_font(28)
    sub_font = _safe_font(32)

    for index in range(9):
        margin = 50 + index * 35
        draw.rounded_rectangle(
            [margin, margin, max(margin + 2, width - margin), max(margin + 2, height - margin)],
            radius=max(10, min(36, margin // 2)),
            outline=(30 + index * 7, 45 + index * 8, 62 + index * 10),
            width=max(2, 10 - index // 2),
        )

    box = draw.textbbox((0, 0), subject, font=title_font)
    if box[2] - box[0] > width - 160:
        title_font = _safe_font(46)
        box = draw.textbbox((0, 0), subject, font=title_font)
    text_w = max(1, box[2] - box[0])
    text_h = max(1, box[3] - box[1])
    x = max(0, (width - text_w) / 2)
    y = max(0, (height - text_h) / 2)
    panel_left, panel_right = 80, max(82, width - 80)
    panel_top = max(100, int(y - 80))
    panel_bottom = min(height - 100, int(y + text_h + 80))
    if panel_bottom <= panel_top:
        panel_top, panel_bottom = 10, max(12, height - 10)
    draw.rounded_rectangle([panel_left, panel_top, panel_right, panel_bottom], radius=34, fill=(5, 9, 15), outline=(76, 108, 138), width=3)
    draw.text((x + 4, y + 5), subject, font=title_font, fill=(0, 0, 0))
    draw.text((x, y), subject, font=title_font, fill=(238, 243, 248))

    label = "REAL SOURCES EXHAUSTED · VISUAL RESCUE"
    lb = draw.textbbox((0, 0), label, font=meta_font)
    draw.text((max(0, (width - (lb[2] - lb[0])) / 2), height - 180), label, font=meta_font, fill=(145, 169, 193))
    type_text = f"{kind} · RESCUE"
    tb = draw.textbbox((0, 0), type_text, font=sub_font)
    badge_w = min(width - 40, tb[2] - tb[0] + 36)
    draw.rounded_rectangle([20, 28, max(22, 20 + badge_w), 80], radius=18, fill=(22, 34, 48), outline=(72, 104, 132), width=2)
    draw.text((38, 40), type_text, font=sub_font, fill=(190, 211, 228))
    return image.filter(ImageFilter.GaussianBlur(radius=0.12))


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
        f"Create a polished editorial context illustration for '{subject}'. "
        "Do not depict or identify any real person or organization as a factual image; "
        "use a neutral documentary visual metaphor."
    )


def _candidate_items(data: Any) -> list[Any]:
    if data is None:
        return []
    if isinstance(data, (list, tuple)):
        return list(data)[:MAX_CANDIDATES_PER_SOURCE]
    return [data]


def run_visual_retrieval(runtime, bot, seg: dict, category: str, used_urls: set[str], used_hashes: set[str], video_title: str = ""):
    """Search grounded phrases through raw providers and apply one QA boundary."""
    entity = str(seg.get("primary_entity", "")).strip()
    factual_entity = str(seg.get("factual_primary_entity") or entity).strip()
    queries, visual_type = runtime._build_search_variants(seg, video_title)
    visual_type = str(visual_type or "GENERAL_CONTEXT").upper()

    if not entity or not queries:
        rescue = make_visual_rescue(entity or factual_entity, visual_type)
        seg["visual_verified"] = False
        seg["visual_rescue_reason"] = "no-grounded-query"
        seg["visual_fallback_reason"] = ""
        seg["visual_query_used"] = ""
        return rescue, False, "visual-rescue"

    intent = str(seg.get("factual_visual_intent") or seg.get("visual_intent") or "").strip()
    prompt = str(seg.get("specific_search_prompt") or entity).strip()
    voice = str(seg.get("factual_voiceover") or seg.get("voiceover") or "").strip()
    context = _context_fingerprint(intent, prompt, voice, video_title)
    cache_entity = factual_entity or entity

    cached_img, _cache_path = runtime.get_cached_asset(bot, cache_entity, visual_type, context)
    if cached_img is not None:
        buffer = io.BytesIO()
        cached_img.save(buffer, format="JPEG", quality=95)
        cached_hash = _hash_image(bot, buffer.getvalue())
        if cached_hash not in used_hashes:
            used_hashes.add(cached_hash)
            seg["visual_verified"] = True
            seg["visual_rescue_reason"] = ""
            seg["visual_fallback_reason"] = ""
            seg["visual_query_used"] = "cache"
            return cached_img.convert("RGB"), False, "cached"

    verification_attempts = 0
    hard_rejections = 0
    max_verification = max(1, int(getattr(runtime, "VISUAL_MAX_VERIFICATION_ATTEMPTS", 8)))
    source_plan = _source_plan(bot, visual_type)
    max_provider_checks = max(1, min(40, len(queries) * max(1, len(source_plan))))
    provider_checks = 0
    best_uncertain = None

    qa_scene = dict(seg)
    qa_scene["primary_entity"] = cache_entity
    qa_scene["factual_primary_entity"] = cache_entity
    qa_scene["visual_intent"] = intent
    qa_scene["specific_search_prompt"] = prompt
    qa_scene["voiceover"] = voice

    print(
        f"   [Visual Strategy] entity='{cache_entity}' type={visual_type} "
        f"search_phrases={len(queries)} sources={len(source_plan)} max_checks={max_provider_checks} "
        f"candidates_per_source={MAX_CANDIDATES_PER_SOURCE}",
        flush=True,
    )

    for query_index, query in enumerate(queries, 1):
        print(f"   [Visual Search] {query_index}/{len(queries)} | '{query}'", flush=True)
        for source, fetcher in source_plan:
            if provider_checks >= max_provider_checks:
                break
            provider_checks += 1
            tier = runtime._verification_tier(qa_scene, visual_type, source)
            semantic_required = tier not in {"STRICT(person)", "SKIPPED(conceptual)"}

            fetch_entity = cache_entity if source == "Wikipedia" else query
            args = (fetch_entity, used_urls, query, video_title) if source == "Wikipedia" else (query, used_urls, query, video_title)
            raw_data = runtime._call_fetcher_with_timeout(fetcher, args, source, query)
            candidates = _candidate_items(raw_data)
            if not candidates:
                print(f"   [Visual Source] {source} | no candidate returned | query='{query}'", flush=True)
                continue

            for candidate_index, data in enumerate(candidates, 1):
                valid, reason, normalized = _preflight_image(data)
                if not valid:
                    if reason.startswith("provider-returned-"):
                        print(f"   [Visual Source] {source} | candidate {candidate_index}/{len(candidates)} unavailable | query='{query}'", flush=True)
                    else:
                        print(
                            f"   [Visual Quality] REJECTED | {reason} | source={source} "
                            f"candidate={candidate_index}/{len(candidates)} | query='{query}'",
                            flush=True,
                        )
                    continue

                image_hash = _hash_image(bot, normalized)
                if image_hash in used_hashes:
                    print(f"   [Visual Search] duplicate image skipped | source={source} | query='{query}'", flush=True)
                    continue

                verification_available = semantic_required and verification_attempts < max_verification
                if verification_available:
                    verification_attempts += 1
                    try:
                        accepted, tier_name, score, hard_reject = runtime._strict_gate(
                            bot, normalized, qa_scene, video_title, source=source
                        )
                    except Exception as exc:
                        accepted, tier_name, score, hard_reject = False, tier, 0, False
                        print(f"   [Visual QA] candidate check unavailable; keeping as uncertain: {type(exc).__name__}: {exc}", flush=True)
                elif semantic_required:
                    accepted, tier_name, score, hard_reject = False, "QA-BUDGET", REAL_SOURCE_SCORES.get(source.lower(), 50), False
                else:
                    accepted, tier_name, score, hard_reject = True, tier, REAL_SOURCE_SCORES.get(source.lower(), 50), False

                if accepted:
                    try:
                        runtime.save_to_cache(bot, normalized, cache_entity, visual_type, source, context)
                    except Exception:
                        pass
                    used_hashes.add(image_hash)
                    seg["visual_verified"] = True
                    seg["visual_rescue_reason"] = ""
                    seg["visual_fallback_reason"] = ""
                    seg["visual_query_used"] = query
                    seg["visual_verification_attempts"] = verification_attempts
                    print(
                        f"   [Visual Source] {source} | VERIFIED | tier={tier_name} | score={score} | "
                        f"candidate={candidate_index}/{len(candidates)} | query='{query}'",
                        flush=True,
                    )
                    return Image.open(io.BytesIO(normalized)).convert("RGB"), False, source

                if hard_reject:
                    hard_rejections += 1
                    print(
                        f"   [Visual Quality] REJECTED | semantic mismatch | source={source} "
                        f"candidate={candidate_index}/{len(candidates)} | query='{query}'",
                        flush=True,
                    )
                    continue

                candidate_score = float(score or REAL_SOURCE_SCORES.get(source.lower(), 50))
                if best_uncertain is None or candidate_score > best_uncertain[0]:
                    best_uncertain = (candidate_score, normalized, source, query)
                print(
                    f"   [Visual Candidate] retained as uncertain | source={source} | score={candidate_score:.0f} | "
                    f"candidate={candidate_index}/{len(candidates)} | query='{query}'",
                    flush=True,
                )
        if provider_checks >= max_provider_checks:
            break

    if best_uncertain is not None:
        score, normalized, source, query = best_uncertain
        image_hash = _hash_image(bot, normalized)
        if image_hash not in used_hashes:
            used_hashes.add(image_hash)
            seg["visual_verified"] = False
            seg["visual_rescue_reason"] = "real-source-unverified"
            seg["visual_fallback_reason"] = ""
            seg["visual_query_used"] = query
            seg["visual_verification_attempts"] = verification_attempts
            print(
                f"   [Visual Source] {source} | USED-UNVERIFIED-REAL | score={score:.0f} | query='{query}' | "
                f"QA={verification_attempts}/{max_verification} hard_rejections={hard_rejections}",
                flush=True,
            )
            return Image.open(io.BytesIO(normalized)).convert("RGB"), False, source

    if visual_type in ABSTRACT_TYPES and callable(getattr(bot, "fetch_hf_ai_image", None)):
        prompt_text = _ai_prompt(entity, visual_type)
        print(f"   [Visual Source] AI attempt | type={visual_type} | prompt='{prompt_text[:180]}'", flush=True)
        ai = runtime._call_fetcher_with_timeout(bot.fetch_hf_ai_image, (prompt_text,), "HF-AI", prompt_text)
        valid, reason, normalized = _preflight_image(ai)
        if valid and normalized is not None and _hash_image(bot, normalized) not in used_hashes:
            if verification_attempts < max_verification:
                verification_attempts += 1
                try:
                    accepted, tier_name, score, hard_reject = runtime._strict_gate(
                        bot, normalized, qa_scene, video_title, source="ai-generated"
                    )
                except Exception as exc:
                    accepted, tier_name, score, hard_reject = False, "AI-UNCERTAIN", 40, False
                    print(f"   [Visual QA] AI check unavailable; using generated image as uncertain: {type(exc).__name__}: {exc}", flush=True)
                if accepted:
                    used_hashes.add(_hash_image(bot, normalized))
                    seg["visual_verified"] = True
                    seg["visual_rescue_reason"] = ""
                    seg["visual_fallback_reason"] = ""
                    seg["visual_query_used"] = prompt_text
                    seg["visual_verification_attempts"] = verification_attempts
                    return Image.open(io.BytesIO(normalized)).convert("RGB"), True, "ai-generated"
                if not hard_reject:
                    best_uncertain = (float(score or 40), normalized, "ai-generated", prompt_text)
        else:
            print(f"   [Visual Source] AI image rejected before QA: {reason}", flush=True)

    if best_uncertain is not None:
        score, normalized, source, query = best_uncertain
        image_hash = _hash_image(bot, normalized)
        if image_hash not in used_hashes:
            used_hashes.add(image_hash)
            seg["visual_verified"] = False
            seg["visual_rescue_reason"] = "ai-or-real-source-unverified"
            seg["visual_fallback_reason"] = ""
            seg["visual_query_used"] = query
            seg["visual_verification_attempts"] = verification_attempts
            print(
                f"   [Visual Source] {source} | USED-UNVERIFIED-FALLBACK | score={score:.0f} | query='{query}' | "
                f"QA={verification_attempts}/{max_verification} hard_rejections={hard_rejections}",
                flush=True,
            )
            return Image.open(io.BytesIO(normalized)).convert("RGB"), source == "ai-generated", source

    rescue = make_visual_rescue(entity or factual_entity, visual_type)
    seg["visual_verified"] = False
    seg["visual_rescue_reason"] = "real-and-ai-sources-exhausted"
    seg["visual_fallback_reason"] = ""
    seg["visual_query_used"] = ""
    seg["visual_verification_attempts"] = verification_attempts
    print(
        f"   [Visual Rescue] Real sources exhausted; generated guaranteed non-blank visual | "
        f"type={visual_type} phrases={len(queries)} provider_checks={provider_checks} "
        f"qa={verification_attempts}/{max_verification}",
        flush=True,
    )
    return rescue, False, "visual-rescue"
