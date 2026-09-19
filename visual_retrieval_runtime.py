"""Genre-agnostic multi-source visual retrieval for the Shorts factory.

The active visual path is deliberately bounded: each scene gets a small set of
search phrases, multiple real image sources, several candidates per source,
cheap image validation, then mandatory semantic verification. No real or
generated candidate is accepted when semantic verification is unavailable or
uncertain. AI generation is reserved for abstract/contextual scenes after
real-source retrieval is exhausted. A tiny built-in visual rescue exists only
as the final renderer guarantee; it is never presented as a factual photograph.

The provider layer is intentionally raw. Provider adapters only search and
return downloadable image bytes; this module is the sole active acceptance
boundary for image decode, dimensions, deduplication and semantic QA.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageFilter
from visual_taxonomy_runtime import classify_visual_genre, genre_allows_ai
from visual_licensing_runtime import (
    ai_provenance,
    candidate_bytes,
    candidate_provenance,
    provenance_is_usable,
    rescue_provenance,
)


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

def _hash_image(bot, img_bytes: bytes) -> str:
    """Return a content-normalized fingerprint for deduplication across sources."""
    data = bytes(img_bytes or b"")
    try:
        image = Image.open(io.BytesIO(data)).convert("RGB")
        # Providers often return the same photo with different encodings or
        # thumbnail dimensions. Hash a fixed-size pixel representation so those
        # copies are treated as the same visual.
        image = image.resize((96, 96), Image.Resampling.LANCZOS)
        return hashlib.sha256(image.tobytes()).hexdigest()
    except Exception:
        resolver = getattr(bot, "get_image_hash", None)
        if callable(resolver):
            try:
                value = str(resolver(data) or "").strip()
                if value:
                    return value
            except Exception:
                pass
        return hashlib.sha256(data).hexdigest()


def _source_plan(_bot, visual_type: str, visual_genre: str = ""):
    """Compatibility boundary backed only by the authoritative raw provider plan."""
    from visual_provider_boundary_runtime import build_raw_source_plan
    return build_raw_source_plan(visual_type, visual_genre)


def _context_fingerprint(intent="", prompt="", voice="", video_title=""):
    raw = " | ".join(str(value or "").strip().lower() for value in (intent, prompt, voice, video_title))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _as_image_bytes(data: Any) -> bytes | None:
    if data is None:
        return None
    candidate = candidate_bytes(data)
    if candidate is not None:
        return candidate
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


def _record_visual_rejection(seg: dict, bucket: str, detail: str = "") -> None:
    """Record why a candidate was discarded without changing acceptance behavior."""
    counts = seg.setdefault("visual_rejection_counts", {})
    key = str(bucket or "unknown").strip() or "unknown"
    counts[key] = int(counts.get(key) or 0) + 1
    if detail:
        details = seg.setdefault("_visual_rejection_details", [])
        if len(details) < 20:
            details.append(str(detail)[:240])


def _candidate_priority(source: str, normalized: bytes, visual_type: str, query: str, visual_genre: str) -> float:
    """Rank candidates cheaply for QA ordering; never changes acceptance."""
    quality_score = 0.0
    try:
        from visual_quality_runtime import inspect_image

        info = inspect_image(normalized)
        if info.get("valid"):
            short_side = float(info.get("short_side") or 0.0)
            crop_loss = float(info.get("crop_loss") or 1.0)
            sharpness = float(info.get("sharpness") or 0.0)
            quality_score = min(35.0, 35.0 * short_side / 1080.0)
            quality_score += max(0.0, 35.0 * (1.0 - crop_loss / 0.72))
            quality_score += min(20.0, sharpness / 18.0)
            if short_side >= 1080:
                quality_score += 10.0
    except Exception:
        pass

    source_score = REAL_SOURCE_SCORES.get(str(source or "").strip().casefold(), 50.0)
    trusted, _tier, trusted_score = _trusted_source_evidence(
        source,
        visual_type,
        query,
        visual_genre,
    )
    return round(
        (quality_score * 0.70)
        + (source_score * 0.20)
        + (trusted_score * 0.10 if trusted else 0.0),
        3,
    )


def _remember_blocked_candidate(
    best: dict | None,
    *,
    source: str,
    query: str,
    candidate_index: int,
    normalized: bytes,
    provenance: dict,
    priority: float,
    reason: str,
) -> dict:
    """Keep the strongest licensed, usable failed candidate for manual review."""
    candidate = {
        "source": str(source or "").strip(),
        "query": str(query or "").strip(),
        "candidate_index": int(candidate_index),
        "bytes": normalized,
        "provenance": dict(provenance or {}),
        "priority": float(priority),
        "reason": str(reason or "automatic visual QC blocked"),
    }
    if best is None or candidate["priority"] > float(best.get("priority") or 0.0):
        return candidate
    return best


def _gate_rejection_bucket(tier_name: str) -> str:
    tier = str(tier_name or "").upper()
    if ":SEMANTIC_NO" in tier:
        return "semantic_no"
    if ":QA_NO_API_KEY" in tier:
        return "qa_unavailable"
    if ":QA_CIRCUIT_BREAKER" in tier:
        return "qa_circuit_breaker"
    if ":QA_VIDEO_BUDGET_EXHAUSTED" in tier or ":QA_SCENE_BUDGET_EXHAUSTED" in tier:
        return "qa_budget_exhausted"
    if ":QA_QUOTA_OR_RATE_LIMIT" in tier:
        return "qa_quota_or_rate_limit"
    if ":QA_REQUEST_EXCEPTION" in tier:
        return "qa_exception"
    if ":QA_AMBIGUOUS_RESPONSE" in tier or ":QA_UNCERTAIN" in tier:
        return "semantic_uncertain"
    if tier == "LOCAL-QUALITY":
        return "quality_gate"
    if tier == "LOCAL-REJECT":
        return "local_reject"
    return "semantic_qc_reject"


_VISUAL_DESCRIPTOR_WORDS = {
    "logo", "logos", "portrait", "portraits", "headshot", "headshots", "icon", "icons",
    "badge", "badges", "emblem", "emblems", "symbol", "symbols", "seal", "seals",
    "map", "maps", "flag", "flags", "screenshot", "screenshots", "poster", "posters",
}


def _trusted_source_evidence(source: str, visual_type: str, query: str, visual_genre: str = "") -> tuple[bool, str, float]:
    """Return source-level evidence used for ranking and related-asset reuse.

    Source authority never bypasses the active semantic-QC acceptance gate.
    """
    source_l = str(source or "").strip().casefold()
    visual_l = str(visual_type or "").strip().upper()
    query_tokens = {re.sub(r"[^a-z0-9]+", "", token.casefold()) for token in re.findall(r"[A-Za-z0-9]+", str(query or ""))}

    genre_l = str(visual_genre or "").strip().upper()
    if genre_l == "PERSON_PORTRAIT" and source_l in {"wikipedia", "commons"}:
        return True, "SOURCE-IDENTITY", REAL_SOURCE_SCORES.get(source_l, 95.0)
    if genre_l in {"ORG_BRANDING", "TEAM_BRANDING"} and source_l == "commons" and query_tokens & _VISUAL_DESCRIPTOR_WORDS:
        return True, "SOURCE-BRANDED", REAL_SOURCE_SCORES.get(source_l, 96.0)
    if genre_l in {"MONEY_CURRENCY", "FLAG_SYMBOL", "TROPHY_AWARD"} and source_l == "commons":
        return True, "SOURCE-ASSET", REAL_SOURCE_SCORES.get(source_l, 94.0)
    return False, "", 0.0



_RELATED_SUBJECT_ASSET_LIMIT = 3


def _related_source_is_safe(source: str, visual_genre: str) -> bool:
    """Allow only real, licensed sources for verified same-subject reuse."""
    source_l = str(source or "").strip().casefold()
    return bool(source_l) and source_l not in {"visual-rescue", "cache", "ai-generated"}


def _record_trusted_related_assets(
    seg: dict,
    bot,
    candidates: list[Any],
    accepted_index: int,
    source: str,
    query: str,
    visual_type: str,
    visual_genre: str,
    used_hashes: set[str],
) -> None:
    """Keep a tiny pool of trusted unused alternatives from the accepted source."""
    existing = list(seg.get("_verified_subject_assets") or [])
    existing_hashes = {str(item.get("hash") or "") for item in existing if isinstance(item, dict)}

    for data in candidates[accepted_index:]:
        if len(existing) >= _RELATED_SUBJECT_ASSET_LIMIT:
            break
        valid, _reason, normalized = _preflight_image(data)
        if not valid or normalized is None:
            continue
        image_hash = _hash_image(bot, normalized)
        if image_hash in used_hashes or image_hash in existing_hashes:
            continue

        trusted, _tier, _score = _trusted_source_evidence(
            source,
            visual_type,
            query,
            visual_genre,
        )
        if not trusted or not _related_source_is_safe(source, visual_genre):
            continue

        record = candidate_provenance(data)
        if not provenance_is_usable(record):
            continue
        existing.append(
            {
                "subject": str(seg.get("primary_entity") or "").strip(),
                "bytes": normalized,
                "hash": image_hash,
                "source": str(source or "").strip(),
                "query": str(query or "").strip(),
                "visual_type": str(visual_type or "").strip().upper(),
                "visual_genre": str(visual_genre or "").strip().upper(),
                "provenance": record,
            }
        )
        existing_hashes.add(image_hash)

    if existing:
        seg["_verified_subject_assets"] = existing

def _qa_stop_tier(tier_name: str) -> bool:
    """Return True when semantic QA says retrieval cannot continue safely."""
    text = str(tier_name or "").upper()
    return any(
        token in text
        for token in (
            "QA_VIDEO_BUDGET_EXHAUSTED",
            "QA_SCENE_BUDGET_EXHAUSTED",
            "QA_CIRCUIT_BREAKER",
            "QA_QUOTA_OR_RATE_LIMIT",
            "QA_NO_API_KEY",
        )
    )


def _record_verified_related_assets(
    seg: dict,
    candidates: list[tuple],
    source: str,
    query: str,
    visual_type: str,
    visual_genre: str,
) -> None:
    """Store a small pool of candidates that passed the same semantic QA gate."""
    existing = list(seg.get("_verified_subject_assets") or [])
    existing_hashes = {
        str(item.get("hash") or "")
        for item in existing
        if isinstance(item, dict)
    }
    if not _related_source_is_safe(source, visual_genre):
        return
    for candidate in candidates:
        if len(existing) >= _RELATED_SUBJECT_ASSET_LIMIT:
            break
        _index, _data, normalized, image_hash, _priority, provenance = candidate
        if image_hash in existing_hashes or not provenance_is_usable(provenance):
            continue
        existing.append(
            {
                "subject": str(seg.get("primary_entity") or "").strip(),
                "bytes": normalized,
                "hash": image_hash,
                "source": str(source or "").strip(),
                "query": str(query or "").strip(),
                "visual_type": str(visual_type or "").strip().upper(),
                "visual_genre": str(visual_genre or "").strip().upper(),
                "provenance": dict(provenance),
            }
        )
        existing_hashes.add(image_hash)
    if existing:
        seg["_verified_subject_assets"] = existing


def run_visual_retrieval(runtime, bot, seg: dict, category: str, used_urls: set[str], used_hashes: set[str], video_title: str = ""):
    """Search grounded phrases through raw providers and apply one QA boundary."""
    entity = str(seg.get("primary_entity", "")).strip()
    factual_entity = str(seg.get("factual_primary_entity") or entity).strip()

    # Resolve the canonical intent exactly once. Query construction belongs to
    # visual_search_intent_runtime; retrieval must not rebuild it through a
    # second strategy function.
    from visual_search_intent_runtime import resolve_visual_search_intent
    visual_intent = seg.get("_visual_search_intent")
    if visual_intent is None:
        visual_intent = resolve_visual_search_intent(seg, video_title)

    visual_anchor = str(visual_intent.subject or "").strip()
    queries = list(getattr(visual_intent, "queries", ()) or ())
    if not queries and visual_intent.query:
        queries = [visual_intent.query]
    # The intent resolver owns the bounded query set. Retrieval must not
    # manufacture another ladder or silently reformulate it.
    visual_type = str(visual_intent.visual_type or "GENERAL_CONTEXT").upper()
    visual_genre = str(visual_intent.visual_genre or classify_visual_genre(seg, visual_anchor, visual_type) or "GENERAL_CONTEXT").upper()
    seg["visual_genre"] = visual_genre
    seg["visual_rejection_counts"] = {}
    seg.pop("_visual_rejection_details", None)

    if not visual_anchor or not queries:
        _record_visual_rejection(seg, "no_grounded_query", "No grounded visual query could be resolved.")
        rescue = make_visual_rescue(visual_anchor or factual_entity, visual_type)
        seg["visual_verified"] = False
        seg["visual_rescue_reason"] = "no-grounded-query"
        seg["visual_fallback_reason"] = ""
        seg["visual_query_used"] = ""
        return rescue, False, "visual-rescue"

    intent = str(seg.get("factual_visual_intent") or seg.get("visual_intent") or "").strip()
    prompt = str(seg.get("specific_search_prompt") or entity).strip()
    voice = str(seg.get("factual_voiceover") or seg.get("voiceover") or "").strip()
    context = _context_fingerprint(intent, prompt, voice, video_title)
    cache_entity = visual_anchor

    best_blocked_candidate = None
    seg["visual_qc_blocked"] = False
    seg["visual_qc_block_reason"] = ""

    cached_img, _cache_path = runtime.get_cached_asset(bot, cache_entity, visual_type, context)
    cached_provenance = {}
    if cached_img is not None:
        try:
            import json
            import os as _os
            meta_path = _os.path.splitext(str(_cache_path))[0] + ".json"
            with open(meta_path, "r", encoding="utf-8") as fh:
                cached_meta = json.load(fh)
            cached_provenance = candidate_provenance(cached_meta.get("provenance") or {})
        except Exception:
            cached_img = None
        if cached_img is not None and not provenance_is_usable(cached_provenance):
            _record_visual_rejection(seg, "cache_provenance", "Cached image had unusable provenance.")
            cached_img = None
    if cached_img is not None:
        buffer = io.BytesIO()
        cached_img.save(buffer, format="JPEG", quality=95)
        cached_bytes = buffer.getvalue()
        cached_hash = _hash_image(bot, cached_bytes)
        if cached_hash in used_hashes:
            _record_visual_rejection(seg, "duplicate", "cache:duplicate")
        if cached_hash not in used_hashes:
            try:
                cached_ok, cached_tier, cached_score, cached_hard_reject = runtime._strict_gate(
                    bot, cached_bytes, seg, video_title, source="cache"
                )
            except Exception as exc:
                _record_visual_rejection(seg, "cache_qc_exception", f"cache:{type(exc).__name__}:{exc}")
                cached_ok = False
                cached_hard_reject = True
                print(
                    f"   [Visual Cache] QC failed; cache candidate rejected: "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )
            if cached_ok:
                used_hashes.add(cached_hash)
                seg["visual_verified"] = True
                seg["visual_rescue_reason"] = ""
                seg["visual_fallback_reason"] = ""
                seg["visual_query_used"] = "cache"
                seg["visual_verification_attempts"] = int(seg.get("visual_verification_attempts") or 0) + 1
                seg["asset_provenance"] = cached_provenance
                return cached_img.convert("RGB"), False, "cached"
            cache_tier = str(cached_tier if "cached_tier" in locals() else "UNKNOWN")
            if _qa_stop_tier(cache_tier):
                _record_visual_rejection(
                    seg,
                    "qa_budget_exhausted",
                    f"cache:{cache_tier}",
                )
                print(
                    f"   [Visual QA] Hard stop at cache boundary: tier={cache_tier}",
                    flush=True,
                )
                return make_visual_rescue(cache_entity, visual_type), False, "visual-rescue"
            if cached_provenance:
                best_blocked_candidate = _remember_blocked_candidate(
                    best_blocked_candidate,
                    source=str(cached_provenance.get("provider") or "cache"),
                    query=str(seg.get("specific_search_prompt") or cache_entity),
                    candidate_index=0,
                    normalized=cached_bytes,
                    provenance=cached_provenance,
                    priority=_candidate_priority(
                        str(cached_provenance.get("provider") or "cache"),
                        cached_bytes,
                        visual_type,
                        str(seg.get("specific_search_prompt") or cache_entity),
                        visual_genre,
                    ),
                    reason=_gate_rejection_bucket(cache_tier),
                )
            _record_visual_rejection(seg, "cache_qc_reject", f"cache:{cache_tier}")
            print(
                f"   [Visual Cache] QC rejected cached candidate | tier={cached_tier if 'cached_tier' in locals() else 'UNKNOWN'}",
                flush=True,
            )

    verification_attempts = 0
    max_verification = max(1, int(getattr(runtime, "VISUAL_MAX_VERIFICATION_ATTEMPTS", 8)))
    try:
        source_plan = _source_plan(bot, visual_type, visual_genre)
    except TypeError:
        # Preserve compatibility with legacy test/runtime shims that only
        # accepted the original (bot, visual_type) source-plan signature.
        source_plan = _source_plan(bot, visual_type)
    max_provider_checks = max(1, min(40, 2 * max(1, len(source_plan))))
    provider_checks = 0
    qa_scene = dict(seg)
    qa_scene["primary_entity"] = visual_intent.subject
    qa_scene["factual_primary_entity"] = visual_intent.subject
    qa_scene["visual_intent"] = visual_intent.intent or intent
    qa_scene["specific_search_prompt"] = visual_intent.query
    qa_scene["voiceover"] = voice
    qa_scene["visual_type"] = visual_intent.visual_type
    qa_scene["visual_genre"] = visual_genre

    print(
        f"   [Visual Strategy] entity='{cache_entity}' type={visual_type} "
        f"search_phrases={len(queries)} sources={len(source_plan)} max_checks={max_provider_checks} "
        f"candidates_per_source={MAX_CANDIDATES_PER_SOURCE}",
        flush=True,
    )

    query_index = 0
    qa_hard_stop = False
    while query_index < len(queries):
        query = queries[query_index]
        query_index += 1
        print(f"   [Visual Search] {query_index}/{len(queries)} | '{query}'", flush=True)

        # Fetch providers concurrently, but keep their URL state isolated until
        # all providers finish. This removes serial provider wait time without
        # introducing a race into the global deduplication set.
        def fetch_provider(source, fetcher):
            local_used_urls = set(used_urls)
            fetch_entity = cache_entity if source == "Wikipedia" else query
            args = (
                (fetch_entity, local_used_urls, query, video_title)
                if source == "Wikipedia"
                else (query, local_used_urls, query, video_title)
            )
            raw_data = runtime._call_fetcher_with_timeout(fetcher, args, source, query)
            return source, local_used_urls, _candidate_items(raw_data)

        provider_results = []
        with ThreadPoolExecutor(
            max_workers=max(1, min(6, len(source_plan))),
            thread_name_prefix="visual-provider",
        ) as pool:
            futures = [
                pool.submit(fetch_provider, source, fetcher)
                for source, fetcher in source_plan
            ]
            for source_index, future in enumerate(futures):
                try:
                    source, local_used_urls, candidates = future.result()
                except Exception as exc:
                    source = source_plan[source_index][0]
                    local_used_urls = set()
                    candidates = []
                    _record_visual_rejection(seg, "provider_task_error", f"{source}:{type(exc).__name__}:{exc}")
                    print(
                        f"   [Visual Source] {source} | provider task failed: "
                        f"{type(exc).__name__}: {exc} | query='{query}'",
                        flush=True,
                    )
                used_urls.update(local_used_urls)
                provider_results.append((source, candidates))

        provider_checks += len(provider_results)

        # Spread the finite semantic-QA budget across providers. A provider
        # returning four candidates must not consume all QA checks before the
        # next provider gets a chance to offer a better image.
        prepared_candidates = []
        for source, candidates in provider_results:
            if not candidates:
                _record_visual_rejection(seg, "provider_empty", f"{source}:{query}")
                print(
                    f"   [Visual Source] {source} | no candidate returned | query='{query}'",
                    flush=True,
                )
                prepared_candidates.append((source, []))
                continue

            valid_candidates = []
            for candidate_index, data in enumerate(candidates, 1):
                valid, reason, normalized = _preflight_image(data)
                if not valid:
                    if reason.startswith("provider-returned-"):
                        _record_visual_rejection(
                            seg,
                            "provider_payload",
                            f"{source}:candidate {candidate_index}:{reason}",
                        )
                        print(
                            f"   [Visual Source] {source} | candidate {candidate_index}/{len(candidates)} "
                            f"unavailable | query='{query}'",
                            flush=True,
                        )
                    else:
                        bucket = (
                            "resolution"
                            if reason.startswith("resolution-too-low")
                            else "aspect_ratio"
                            if reason.startswith("extreme-aspect")
                            else "invalid_image"
                            if reason == "invalid-image"
                            else "preflight_reject"
                        )
                        _record_visual_rejection(
                            seg,
                            bucket,
                            f"{source}:candidate {candidate_index}:{reason}",
                        )
                        print(
                            f"   [Visual Quality] REJECTED | {reason} | source={source} "
                            f"candidate={candidate_index}/{len(candidates)} | query='{query}'",
                            flush=True,
                        )
                    continue
                image_hash = _hash_image(bot, normalized)
                if image_hash in used_hashes:
                    _record_visual_rejection(seg, "duplicate", f"{source}:candidate {candidate_index}")
                    print(
                        f"   [Visual Search] duplicate image skipped | source={source} | query='{query}'",
                        flush=True,
                    )
                    continue
                record = candidate_provenance(data)
                if not provenance_is_usable(record):
                    _record_visual_rejection(
                        seg,
                        "licensing_provenance",
                        f"{source}:candidate {candidate_index}",
                    )
                    print(
                        f"   [Visual Licensing] rejected candidate without usable provenance | source={source}",
                        flush=True,
                    )
                    continue
                priority = _candidate_priority(
                    source,
                    normalized,
                    visual_type,
                    query,
                    visual_genre,
                )
                valid_candidates.append(
                    (candidate_index, data, normalized, image_hash, priority, record)
                )
            valid_candidates.sort(key=lambda item: (-float(item[4]), int(item[0])))
            prepared_candidates.append((source, valid_candidates))

        for candidate_offset in range(MAX_CANDIDATES_PER_SOURCE):
            if verification_attempts >= max_verification or qa_hard_stop:
                break
            for source, candidates in prepared_candidates:
                if verification_attempts >= max_verification or qa_hard_stop:
                    break
                if candidate_offset >= len(candidates):
                    continue

                candidate_index, data, normalized, image_hash, candidate_priority, candidate_provenance_record = candidates[candidate_offset]
                trusted, trusted_tier, trusted_score = _trusted_source_evidence(
                    source, visual_type, query, visual_genre
                )
                verification_attempts += 1
                try:
                    accepted, tier_name, score, hard_reject = runtime._strict_gate(
                        bot, normalized, qa_scene, video_title, source=source
                    )
                except Exception as exc:
                    bucket = "qa_exception"
                    _record_visual_rejection(
                        seg,
                        bucket,
                        f"{source}:candidate {candidate_index}:{type(exc).__name__}:{exc}",
                    )
                    best_blocked_candidate = _remember_blocked_candidate(
                        best_blocked_candidate,
                        source=source,
                        query=query,
                        candidate_index=candidate_index,
                        normalized=normalized,
                        provenance=candidate_provenance_record,
                        priority=candidate_priority,
                        reason=bucket,
                    )
                    print(
                        f"   [Visual QA] candidate check failed; rejecting candidate: "
                        f"{type(exc).__name__}: {exc}",
                        flush=True,
                    )
                    continue

                if _qa_stop_tier(tier_name):
                    _record_visual_rejection(
                        seg,
                        "qa_budget_exhausted",
                        f"{source}:candidate {candidate_index}:{tier_name}",
                    )
                    print(
                        f"   [Visual QA] Hard stop; no further candidates will be checked | "
                        f"source={source} | query='{query}' | tier={tier_name}",
                        flush=True,
                    )
                    qa_hard_stop = True
                    break

                if hard_reject or not accepted:
                    bucket = _gate_rejection_bucket(tier_name)
                    _record_visual_rejection(
                        seg,
                        bucket,
                        f"{source}:candidate {candidate_index}:{tier_name}",
                    )
                    best_blocked_candidate = _remember_blocked_candidate(
                        best_blocked_candidate,
                        source=source,
                        query=query,
                        candidate_index=candidate_index,
                        normalized=normalized,
                        provenance=candidate_provenance_record,
                        priority=candidate_priority,
                        reason=bucket,
                    )
                    print(
                        f"   [Visual QA] semantic/QC rejection | source={source} | "
                        f"query='{query}' | tier={tier_name}",
                        flush=True,
                    )
                    continue

                record = candidate_provenance_record
                try:
                    cache_path = runtime.save_to_cache(
                        bot, normalized, cache_entity, visual_type, source, context
                    )
                    if cache_path:
                        import json
                        meta_path = os.path.splitext(str(cache_path))[0] + ".json"
                        try:
                            with open(meta_path, "r", encoding="utf-8") as fh:
                                meta = json.load(fh)
                        except Exception:
                            meta = {}
                        meta["provenance"] = record
                        meta["verified"] = True
                        with open(meta_path, "w", encoding="utf-8") as fh:
                            json.dump(meta, fh, ensure_ascii=False, indent=2)
                except Exception:
                    pass

                used_hashes.add(image_hash)

                if (
                    bool(seg.get("_related_asset_rescue_eligible"))
                    and candidate_offset + 1 < len(candidates)
                    and verification_attempts < max_verification
                ):
                    related_candidates = []
                    for extra_offset in range(1, min(
                        _RELATED_SUBJECT_ASSET_LIMIT + 1,
                        len(candidates) - candidate_offset,
                    )):
                        if verification_attempts >= max_verification:
                            break
                        extra = candidates[candidate_offset + extra_offset]
                        verification_attempts += 1
                        try:
                            extra_ok, extra_tier, _extra_score, _extra_hard_reject = runtime._strict_gate(
                                bot, extra[2], qa_scene, video_title, source=source
                            )
                        except Exception as exc:
                            print(
                                f"   [Visual QA] Related candidate verification failed: "
                                f"{type(exc).__name__}: {exc}",
                                flush=True,
                            )
                            continue
                        if _qa_stop_tier(extra_tier):
                            _record_visual_rejection(
                                seg,
                                "qa_budget_exhausted",
                                f"{source}:related:{extra[0]}:{extra_tier}",
                            )
                            qa_hard_stop = True
                            break
                        if extra_ok and provenance_is_usable(extra[5]):
                            related_candidates.append(extra)
                    if related_candidates:
                        _record_verified_related_assets(
                            seg,
                            related_candidates,
                            source,
                            query,
                            visual_type,
                            visual_genre,
                        )

                seg["visual_verified"] = True
                seg["visual_rescue_reason"] = ""
                seg["visual_fallback_reason"] = ""
                seg["visual_query_used"] = query
                seg["visual_verification_attempts"] = verification_attempts
                seg["asset_provenance"] = record
                print(
                    f"   [Visual Source] {source} | VERIFIED | tier={tier_name} | score={score} | "
                    f"candidate={candidate_index}/{len(candidates)} | query='{query}'",
                    flush=True,
                )
                return Image.open(io.BytesIO(normalized)).convert("RGB"), False, source

        if qa_hard_stop:
            break

        if provider_checks >= max_provider_checks:
            if verification_attempts >= max_verification:
                _record_visual_rejection(
                    seg,
                    "qa_budget_exhausted",
                    f"semantic QA limit {max_verification}",
                )
            else:
                _record_visual_rejection(
                    seg,
                    "provider_budget_exhausted",
                    f"provider checks {provider_checks}/{max_provider_checks}",
                )
            break

    if (visual_type in ABSTRACT_TYPES or genre_allows_ai(visual_genre)) and callable(getattr(bot, "fetch_hf_ai_image", None)):
        prompt_text = _ai_prompt(entity, visual_type)
        print(f"   [Visual Source] AI attempt | type={visual_type} | prompt='{prompt_text[:180]}'", flush=True)
        ai = runtime._call_fetcher_with_timeout(bot.fetch_hf_ai_image, (prompt_text,), "HF-AI", prompt_text)
        valid, reason, normalized = _preflight_image(ai)
        if not valid or normalized is None:
            _record_visual_rejection(seg, "ai_preflight", f"AI:{reason}")
        else:
            ai_hash = _hash_image(bot, normalized)
            if ai_hash in used_hashes:
                _record_visual_rejection(seg, "duplicate", "AI:duplicate")
            elif verification_attempts < max_verification:
                verification_attempts += 1
                try:
                    accepted, tier_name, score, hard_reject = runtime._strict_gate(
                        bot, normalized, qa_scene, video_title, source="ai-generated"
                    )
                except Exception as exc:
                    _record_visual_rejection(seg, "ai_qa_exception", f"AI:{type(exc).__name__}:{exc}")
                    accepted, tier_name, score, hard_reject = False, "AI-QA-ERROR", 0, True
                    print(
                        f"   [Visual QA] AI check failed; rejecting generated candidate: "
                        f"{type(exc).__name__}: {exc}",
                        flush=True,
                    )
                if accepted:
                    used_hashes.add(ai_hash)
                    seg["visual_verified"] = True
                    seg["visual_rescue_reason"] = ""
                    seg["visual_fallback_reason"] = ""
                    seg["visual_query_used"] = prompt_text
                    seg["visual_verification_attempts"] = verification_attempts
                    seg["asset_provenance"] = ai_provenance()
                    return Image.open(io.BytesIO(normalized)).convert("RGB"), True, "ai-generated"
                _record_visual_rejection(seg, "ai_semantic_qc_reject", f"AI:{tier_name}")
            else:
                _record_visual_rejection(seg, "qa_budget_exhausted", f"AI semantic QA limit {max_verification}")

    if best_blocked_candidate is not None:
        blocked = best_blocked_candidate
        seg["visual_qc_blocked"] = True
        seg["visual_qc_block_reason"] = (
            f"Automatic QC blocked this candidate: {blocked['reason']} "
            f"(source={blocked['source']}, query='{blocked['query']}')."
        )
        seg["visual_verified"] = False
        seg["visual_rescue_reason"] = ""
        seg["visual_fallback_reason"] = ""
        seg["visual_query_used"] = blocked["query"]
        seg["visual_verification_attempts"] = verification_attempts
        seg["asset_provenance"] = dict(blocked["provenance"])
        ordered_rejections = dict(
            sorted(
                (seg.get("visual_rejection_counts") or {}).items(),
                key=lambda item: (-int(item[1]), str(item[0])),
            )
        )
        seg["visual_rejection_counts"] = ordered_rejections
        print(
            f"   [Visual Diagnostics] blocked preview | rejection breakdown={ordered_rejections} | "
            f"selected_source={blocked['source']} priority={blocked['priority']:.2f}",
            flush=True,
        )
        return Image.open(io.BytesIO(blocked["bytes"])).convert("RGB"), False, blocked["source"]

    _record_visual_rejection(seg, "final_rescue", "No accepted real or AI visual remained.")
    rescue = make_visual_rescue(entity or factual_entity, visual_type)
    seg["visual_verified"] = False
    seg["visual_rescue_reason"] = "real-and-ai-sources-exhausted"
    seg["visual_fallback_reason"] = ""
    seg["visual_query_used"] = ""
    seg["visual_verification_attempts"] = verification_attempts
    seg["asset_provenance"] = rescue_provenance()
    ordered_rejections = dict(
        sorted(
            (seg.get("visual_rejection_counts") or {}).items(),
            key=lambda item: (-int(item[1]), str(item[0])),
        )
    )
    seg["visual_rejection_counts"] = ordered_rejections
    print(
        f"   [Visual Diagnostics] rejection breakdown={ordered_rejections}",
        flush=True,
    )
    print(
        f"   [Visual Rescue] Real sources exhausted; generated guaranteed non-blank visual | "
        f"type={visual_type} genre={visual_genre} phrases={len(queries)} provider_checks={provider_checks} "
        f"qa={verification_attempts}/{max_verification}",
        flush=True,
    )
    return rescue, False, "visual-rescue"
