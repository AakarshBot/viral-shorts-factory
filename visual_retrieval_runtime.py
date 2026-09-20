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
MAX_CANDIDATES_PER_SOURCE = max(1, min(12, int(os.getenv("VISUAL_CANDIDATES_PER_SOURCE", "10"))))
MAX_ENTITY_BANK_PER_QUERY = max(3, min(10, int(os.getenv("VISUAL_ENTITY_BANK_PER_QUERY", "10"))))
INITIAL_CANDIDATE_POOL = max(10, min(20, int(os.getenv("VISUAL_INITIAL_CANDIDATE_POOL", "20"))))
MANUAL_QUERY_VERIFIED_TARGET = max(2, min(5, int(os.getenv("VISUAL_MANUAL_QUERY_VERIFIED_TARGET", "5"))))
MANUAL_POOL_MAX = max(5, min(20, int(os.getenv("VISUAL_MANUAL_POOL_MAX", "20"))))
MANUAL_QUERY_RAW_POOL = max(10, min(20, int(os.getenv("VISUAL_MANUAL_QUERY_RAW_POOL", "20"))))
HARD_MIN_IMAGE_SIDE = max(240, min(540, int(os.getenv("VISUAL_HARD_MIN_IMAGE_SIDE", "360"))))
SOFT_MIN_IMAGE_SIDE = max(HARD_MIN_IMAGE_SIDE, min(900, int(os.getenv("VISUAL_SOFT_MIN_IMAGE_SIDE", "540"))))
ENTITY_CHECK_PRIMARY_POOL = 10
REFINEMENT_CANDIDATE_POOL = max(6, min(12, int(os.getenv("VISUAL_REFINEMENT_CANDIDATE_POOL", "10"))))
INITIAL_SOURCE_LIMIT = max(1, min(3, int(os.getenv("VISUAL_INITIAL_SOURCE_LIMIT", "3"))))
REFINEMENT_SOURCE_LIMIT = max(1, min(2, int(os.getenv("VISUAL_REFINEMENT_SOURCE_LIMIT", "2"))))

_VISUAL_DESCRIPTOR_WORDS = {
    "logo", "logos", "badge", "badges", "emblem", "emblems",
    "crest", "crests", "branding", "brand", "brands", "symbol", "symbols",
}

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
        short_side = min(width, height)
        if short_side < HARD_MIN_IMAGE_SIDE:
            return False, f"resolution-too-low:{width}x{height}", None
        resolution_note = (
            f"resolution-soft:{width}x{height}"
            if short_side < SOFT_MIN_IMAGE_SIDE
            else "image-decodable"
        )
        # Aspect ratio is intentionally not a rejection criterion. The existing
        # Shorts crop/fit stage can handle portrait, landscape and other usable
        # source shapes without throwing away a valid entity visual.
        return True, resolution_note, normalized
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


def _candidate_search_text(data: Any) -> str:
    """Return searchable provider metadata preserved with each candidate."""
    if not isinstance(data, dict):
        return ""
    values = []
    for field in ("search_title", "search_description", "search_tags", "search_caption"):
        value = data.get(field)
        if value:
            if isinstance(value, (list, tuple, set)):
                values.extend(str(item) for item in value)
            else:
                values.append(str(value))
    provenance = data.get("provenance")
    if isinstance(provenance, dict):
        values.extend(
            str(provenance.get(field) or "")
            for field in ("author",)
        )
    return re.sub(r"\s+", " ", " ".join(values)).strip()


def _candidate_relevance_score(data: Any, query: str) -> float:
    """Score how closely provider metadata matches the exact search query."""
    metadata = _candidate_search_text(data)
    query_text = re.sub(r"\s+", " ", str(query or "")).strip().casefold()
    if not metadata or not query_text:
        return 0.0

    def words(value: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[\w-]+", str(value or "").casefold(), flags=re.UNICODE)
            if len(token) > 2
        }

    query_words = words(query_text)
    metadata_words = words(metadata)
    if not query_words:
        return 0.0

    overlap = len(query_words & metadata_words) / max(1, len(query_words))
    score = overlap * 72.0
    normalized_metadata = re.sub(r"[^\w]+", " ", metadata.casefold(), flags=re.UNICODE).strip()
    normalized_query = re.sub(r"[^\w]+", " ", query_text, flags=re.UNICODE).strip()
    if normalized_query and normalized_query in normalized_metadata:
        score += 22.0
    return round(min(100.0, score), 3)


def _candidate_priority(
    source: str,
    normalized: bytes,
    visual_type: str,
    query: str,
    visual_genre: str,
    data: Any = None,
) -> float:
    """Rank candidates for QA using relevance first, then technical quality."""
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

    relevance_score = _candidate_relevance_score(data, query)
    source_score = REAL_SOURCE_SCORES.get(str(source or "").strip().casefold(), 50.0)
    trusted, _tier, trusted_score = _trusted_source_evidence(
        source,
        visual_type,
        query,
        visual_genre,
    )
    position = 0.0
    if isinstance(data, dict):
        try:
            provider_position = int(data.get("search_position") or 0)
            if provider_position > 0:
                position = max(0.0, 5.0 - provider_position * 0.5)
        except (TypeError, ValueError):
            pass

    return round(
        (relevance_score * 0.60)
        + (quality_score * 0.30)
        + (source_score * 0.08)
        + (trusted_score * 0.02 if trusted else 0.0)
        + position,
        3,
    )

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


def _provider_search_query(
    source: str,
    query: str,
    identity: str,
    visual_type: str,
    visual_genre: str,
    identity_qid: str = "",
    query_index: int = 1,
    identity_label: str = "",
) -> str:
    """Translate canonical intent into the vocabulary each provider searches best."""
    source_l = str(source or "").strip().casefold()
    visual_l = str(visual_type or "").strip().upper()
    genre_l = str(visual_genre or "").strip().upper()
    identity = str(identity or "").strip()
    query = str(query or "").strip()
    identity_label = str(identity_label or "").strip()

    # Wikipedia is an identity resolver, not a scene search engine. Keep its
    # query anchored to the locked person on every intent round.
    if visual_l == "PERSON" and source_l == "wikipedia" and identity:
        return identity

    # For portraits, Commons has structured Wikidata depicts data that survives
    # spelling variants and transliterations. Use that identity query first,
    # then fall back to the plain name so older/unstructured files still work.
    if visual_l == "PERSON" and genre_l == "PERSON_PORTRAIT" and source_l == "commons":
        if query_index == 1 and identity_qid:
            return f"haswbstatement:P180={identity_qid}"
        if identity:
            return identity_label or identity

    if visual_l == "PERSON" and genre_l == "PERSON_PORTRAIT" and identity_label:
        if query_index == 1:
            return identity_label
        return f"{identity_label} portrait"

    return query or identity




_VISUAL_REFINE_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "for",
    "with", "from", "by", "is", "are", "was", "were", "be", "been", "this",
    "that", "these", "those", "as", "into", "over", "after", "before", "during",
    "about", "their", "his", "her", "its", "they", "them", "he", "she", "it",
    "will", "would", "could", "should", "has", "have", "had", "not", "but",
    "than", "then", "also", "very", "more", "news", "latest", "today", "story",
    "report", "reports", "says", "said", "show", "shows", "image", "photo",
    "portrait", "picture",
}


def _scene_refinement_query(seg: dict, entity: str) -> str:
    """Build one compact entity + 1-2-word scene refinement without inventing facts."""
    entity_text = str(entity or "").strip()
    if not entity_text:
        return ""
    entity_tokens = {
        token.casefold()
        for token in re.findall(r"[\w-]+", entity_text, flags=re.UNICODE)
    }
    source_text = " ".join(
        str(seg.get(field) or "")
        for field in (
            "visual_intent",
            "factual_visual_intent",
            "visual_context",
            "specific_search_prompt",
            "factual_search_prompt",
            "voiceover",
        )
    )
    candidates = []
    for token in re.findall(r"[\w-]+", source_text, flags=re.UNICODE):
        lowered = token.casefold()
        if len(lowered) < 3 or lowered in _VISUAL_REFINE_STOPWORDS or lowered in entity_tokens:
            continue
        if lowered not in candidates:
            candidates.append(lowered)
    if not candidates:
        return ""
    return f"{entity_text} {' '.join(candidates[:2])}".strip()




def _manual_scene_text(scene: dict) -> str:
    return " ".join(
        str(scene.get(field) or "")
        for field in (
            "factual_primary_entity",
            "primary_entity",
            "visual_search_subject",
            "voiceover",
            "visual_intent",
            "specific_search_prompt",
            "factual_visual_intent",
            "visual_context",
        )
    )


def _manual_candidate_scene_score(asset: dict, scene: dict) -> float:
    """Rank a verified manual-query candidate for a slide without another AI call."""
    query = str(asset.get("query") or "")
    metadata = str(asset.get("search_text") or "")
    scene_text = _manual_scene_text(scene)

    def words(value: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[\w-]+", value.casefold(), flags=re.UNICODE)
            if len(token) > 2 and token not in _VISUAL_REFINE_STOPWORDS
        }

    q_words = words(query)
    metadata_words = words(metadata)
    scene_words = words(scene_text)
    entity = str(
        scene.get("factual_primary_entity")
        or scene.get("primary_entity")
        or scene.get("visual_search_subject")
        or ""
    ).strip()
    entity_words = words(entity)

    score = float(asset.get("priority") or 0.0) * 0.20
    if entity_words:
        score += len(q_words & entity_words) * 36.0
        score += len(metadata_words & entity_words) * 18.0
    score += len(q_words & scene_words) * 12.0
    score += len(metadata_words & scene_words) * 6.0
    if entity and entity.casefold() in query.casefold():
        score += 90.0
    return round(score, 3)


def select_manual_visual_candidate(
    assets: list[dict],
    scene: dict,
    used_hashes: set[str] | None = None,
):
    """Choose the strongest unused manual-pool image; soft-resolution assets are last resort."""
    used_hashes = used_hashes or set()
    candidates = [
        asset
        for asset in assets or []
        if isinstance(asset, dict)
        and str(asset.get("hash") or "").strip() not in used_hashes
    ]
    if not candidates:
        candidates = [asset for asset in assets or [] if isinstance(asset, dict)]
    if not candidates:
        return None

    normal = [asset for asset in candidates if asset.get("status") != "factory-rejected-resolution"]
    pool = normal or candidates
    return sorted(
        pool,
        key=lambda asset: (
            -_manual_candidate_scene_score(asset, scene),
            str(asset.get("query") or "").casefold(),
            str(asset.get("source") or "").casefold(),
        ),
    )[0]


def materialize_manual_visual_pool(bot, assets, pool_id: str = "manual") -> list[dict]:
    """Persist a shared manual-query pool once; dashboard layers reuse these paths."""
    root = getattr(bot, "ASSETS_DIR", None)
    if not root:
        return []
    try:
        os.makedirs(root, exist_ok=True)
    except Exception:
        return []

    output = []
    for position, asset in enumerate(assets or [], 1):
        if not isinstance(asset, dict) or not asset.get("bytes"):
            continue
        image_hash = str(asset.get("hash") or "").strip()
        if not image_hash:
            continue
        path = os.path.join(
            root,
            f"visual_manual_pool_{re.sub(r'[^A-Za-z0-9_-]+', '_', str(pool_id))}_{image_hash[:12]}.jpg",
        )
        try:
            image = Image.open(io.BytesIO(asset["bytes"])).convert("RGB")
            image.save(path, "JPEG", quality=90)
        except Exception as exc:
            print(
                f"   [Visual Manual Pool] Could not materialize candidate {position}: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            continue
        output.append(
            {
                "path": path,
                "subject": str(asset.get("subject") or "").strip(),
                "hash": image_hash,
                "source": str(asset.get("source") or "").strip(),
                "query": str(asset.get("query") or "").strip(),
                "visual_type": str(asset.get("visual_type") or "").strip().upper(),
                "visual_genre": str(asset.get("visual_genre") or "").strip().upper(),
                "provenance": dict(asset.get("provenance") or {}),
                "priority": float(asset.get("priority") or 0.0),
                "search_text": str(asset.get("search_text") or "").strip(),
                "status": str(asset.get("status") or "entity-verified"),
                "used": False,
            }
        )
    return output
def materialize_visual_bank(bot, seg: dict, scene_index: int = 0) -> list[dict]:
    """Persist entity-verified alternatives as lightweight dashboard-ready files."""
    assets = list(seg.get("_verified_subject_assets") or [])
    root = getattr(bot, "ASSETS_DIR", None)
    if not root:
        return []
    try:
        os.makedirs(root, exist_ok=True)
    except Exception:
        return []

    output = []
    for position, asset in enumerate(assets, 1):
        if not isinstance(asset, dict) or not asset.get("bytes"):
            continue
        image_hash = str(asset.get("hash") or "").strip()
        if not image_hash:
            continue
        if image_hash == str(seg.get("visual_selected_hash") or "").strip():
            continue
        path = os.path.join(root, f"visual_bank_scene_{int(scene_index)}_{image_hash[:12]}.jpg")
        try:
            image = Image.open(io.BytesIO(asset["bytes"])).convert("RGB")
            image.thumbnail((1400, 1400), Image.Resampling.LANCZOS)
            image.save(path, "JPEG", quality=88)
        except Exception as exc:
            print(
                f"   [Visual Bank] Could not materialize candidate {position}: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            continue
        output.append({
            "path": path,
            "subject": str(asset.get("subject") or "").strip(),
            "hash": image_hash,
            "source": str(asset.get("source") or "").strip(),
            "query": str(asset.get("query") or "").strip(),
            "visual_type": str(asset.get("visual_type") or "").strip().upper(),
            "visual_genre": str(asset.get("visual_genre") or "").strip().upper(),
            "provenance": dict(asset.get("provenance") or {}),
            "status": "entity-verified-unused",
            "used": False,
        })
    return output






def _manual_query_visual_context(query: str, scenes: list[dict]) -> tuple[str, str]:
    """Infer provider ordering only; never rewrite the exact manual query."""
    from visual_semantic_guard_runtime import resolve_subject
    from visual_taxonomy_runtime import classify_visual_genre

    query_text = str(query or "").strip()
    query_tokens = {
        token.casefold()
        for token in re.findall(r"[\w-]+", query_text, flags=re.UNICODE)
        if len(token) > 2
    }
    best_scene = None
    best_overlap = -1
    for scene in scenes or []:
        if not isinstance(scene, dict):
            continue
        scene_text = _manual_scene_text(scene)
        scene_tokens = {
            token.casefold()
            for token in re.findall(r"[\w-]+", scene_text, flags=re.UNICODE)
            if len(token) > 2
        }
        overlap = len(query_tokens & scene_tokens)
        if overlap > best_overlap:
            best_scene = scene
            best_overlap = overlap

    context = dict(best_scene or {})
    context.update(
        {
            "manual_visual_query": query_text,
            "primary_entity": query_text,
            "factual_primary_entity": query_text,
            "specific_search_prompt": query_text,
            "visual_intent": query_text,
        }
    )
    resolution = resolve_subject(context, "")
    visual_type = str(resolution.get("visual_type") or "GENERAL_CONTEXT").upper()
    visual_genre = str(
        classify_visual_genre(context, query_text, visual_type) or "GENERAL_CONTEXT"
    ).upper()
    return visual_type, visual_genre


def collect_manual_visual_pool(
    runtime,
    bot,
    scenes: list[dict],
    manual_queries: list[str],
    video_title: str = "",
    used_hashes: set[str] | None = None,
) -> dict:
    """Build one shared entity-verified pool from the user's exact manual queries.

    Each query tries to contribute up to five entity-verified images. The shared
    pool stops at twenty. Scene selection happens later and never deletes a
    candidate from this pool.
    """
    from visual_qa_runtime import (
        GEMINI_VISUAL_BATCH_SIZE,
        start_visual_qa_scene,
        strict_gemini_check_batch,
    )

    used_hashes = used_hashes or set()
    assets = []
    seen_hashes = set(used_hashes)
    query_stats = []
    rejected_counts = {
        "entity_no": 0,
        "entity_uncertain": 0,
        "resolution_soft": 0,
        "monetization": 0,
        "invalid_image": 0,
        "duplicate": 0,
    }

    for query_index, raw_query in enumerate(manual_queries or [], 1):
        exact_query = str(raw_query or "").strip()
        if not exact_query:
            continue
        if len(assets) >= MANUAL_POOL_MAX:
            break

        # Reset only the QA-per-scene counter between manual query batches.
        # The global video budget remains shared and bounded.
        start_visual_qa_scene()

        visual_type, visual_genre = _manual_query_visual_context(exact_query, scenes)
        try:
            source_plan = _source_plan(bot, visual_type, visual_genre)
        except TypeError:
            source_plan = _source_plan(bot, visual_type)

        candidates = []
        attempted_sources = set()
        for source_index, (source, fetcher) in enumerate(source_plan):
            if source_index >= INITIAL_SOURCE_LIMIT:
                break
            if not callable(fetcher) or len(candidates) >= MANUAL_QUERY_RAW_POOL:
                break

            source_name = str(source or "").strip()
            source_key = source_name.casefold()
            if not source_key or source_key in attempted_sources:
                continue
            attempted_sources.add(source_key)

            # Manual retrieval is intentionally exact. Provider-specific semantic
            # expansion is not allowed to rewrite the user's search phrase.
            raw_data = runtime._call_fetcher_with_timeout(
                fetcher,
                (exact_query, set(), exact_query, video_title),
                source_name,
                exact_query,
            )
            for candidate_index, data in enumerate(_candidate_items(raw_data), 1):
                valid, reason, normalized = _preflight_image(data)
                if not valid:
                    rejected_counts["invalid_image"] += 1
                    continue

                image_hash = _hash_image(bot, normalized)
                if image_hash in seen_hashes or any(item[3] == image_hash for item in candidates):
                    rejected_counts["duplicate"] += 1
                    continue

                record = candidate_provenance(data)
                if not provenance_is_usable(record):
                    # Commercial-use incompatibility is a factory-hard rejection.
                    # It never enters the reviewer pool or consumes entity-QA budget.
                    rejected_counts["monetization"] += 1
                    continue

                priority = _candidate_priority(
                    source_name,
                    normalized,
                    visual_type,
                    exact_query,
                    visual_genre,
                    data=data,
                )
                candidates.append(
                    (
                        candidate_index,
                        data,
                        normalized,
                        image_hash,
                        priority,
                        record,
                        source_name,
                        reason,
                    )
                )
                if len(candidates) >= MANUAL_QUERY_RAW_POOL:
                    break

        candidates.sort(
            key=lambda item: (
                -float(item[4]),
                str(item[6]).casefold(),
                int(item[0]),
            )
        )
        primary_count = min(ENTITY_CHECK_PRIMARY_POOL, len(candidates))
        groups = [candidates[:primary_count]]
        if len(candidates) > primary_count:
            groups.append(candidates[primary_count:])

        verdicts = {}
        qa_requests = 0
        entity_passes = 0
        batch_size = max(2, int(GEMINI_VISUAL_BATCH_SIZE))

        for group_index, group in enumerate(groups):
            if not group or entity_passes >= MANUAL_QUERY_VERIFIED_TARGET:
                break
            for offset in range(0, len(group), batch_size):
                batch = group[offset : offset + batch_size]
                if not batch:
                    continue
                result_map = strict_gemini_check_batch(
                    [item[2] for item in batch],
                    exact_query,
                    os.getenv("GEMINI_API_KEY"),
                    tier="IDENTITY",
                    visual_type=visual_type,
                    visual_genre=visual_genre,
                )
                qa_requests += 1
                base_offset = (0 if group_index == 0 else primary_count) + offset
                for local_index, verdict in result_map.items():
                    verdicts[base_offset + int(local_index)] = verdict
                entity_passes = sum(value is True for value in verdicts.values())
                if entity_passes >= MANUAL_QUERY_VERIFIED_TARGET:
                    break
            if entity_passes >= MANUAL_QUERY_VERIFIED_TARGET:
                break

        query_verified = 0
        query_resolution_rejected = 0
        for local_index, candidate in enumerate(candidates):
            verdict = verdicts.get(local_index)
            if verdict is True:
                status = (
                    "factory-rejected-resolution"
                    if str(candidate[7]).startswith("resolution-soft:")
                    else "entity-verified"
                )
                if status == "factory-rejected-resolution":
                    rejected_counts["resolution_soft"] += 1
                    query_resolution_rejected += 1

                assets.append(
                    {
                        "subject": exact_query,
                        "bytes": candidate[2],
                        "hash": candidate[3],
                        "source": candidate[6],
                        "query": exact_query,
                        "visual_type": visual_type,
                        "visual_genre": visual_genre,
                        "provenance": dict(candidate[5]),
                        "priority": float(candidate[4]),
                        "search_text": _candidate_search_text(candidate[1]),
                        "status": status,
                        "manual_query_index": query_index,
                    }
                )
                seen_hashes.add(candidate[3])
                query_verified += 1
                if len(assets) >= MANUAL_POOL_MAX:
                    break
            elif verdict is False:
                rejected_counts["entity_no"] += 1
            elif local_index in verdicts:
                rejected_counts["entity_uncertain"] += 1

        query_stats.append(
            {
                "query": exact_query,
                "verified": query_verified,
                "resolution_rejected": query_resolution_rejected,
                "qa_requests": qa_requests,
                "visual_type": visual_type,
                "visual_genre": visual_genre,
            }
        )
        print(
            f"   [Manual Visual Pool] query {query_index}/{len(manual_queries)} | "
            f"'{exact_query}' | entity-verified={query_verified} | "
            f"shared-pool={len(assets)}/{MANUAL_POOL_MAX} | Gemini={qa_requests}",
            flush=True,
        )

    deduped = {}
    for asset in sorted(assets, key=lambda item: -float(item.get("priority") or 0.0)):
        deduped.setdefault(str(asset.get("hash") or ""), asset)
    final_assets = list(deduped.values())[:MANUAL_POOL_MAX]

    return {
        "assets": final_assets,
        "queries": [str(item).strip() for item in manual_queries or [] if str(item).strip()],
        "query_stats": query_stats,
        "rejection_counts": rejected_counts,
    }

def run_visual_retrieval(runtime, bot, seg: dict, category: str, used_urls: set[str], used_hashes: set[str], video_title: str = ""):
    """Retrieve a useful entity image bank with a small number of batched AI checks."""
    entity = str(seg.get("primary_entity", "")).strip()
    factual_entity = str(seg.get("factual_primary_entity") or entity).strip()

    from visual_search_intent_runtime import resolve_visual_search_intent
    from visual_qa_runtime import GEMINI_VISUAL_BATCH_SIZE, strict_gemini_check_batch

    visual_intent = seg.get("_visual_search_intent")
    if visual_intent is None:
        visual_intent = resolve_visual_search_intent(seg, video_title)

    manual_query = str(seg.get("manual_visual_query") or "").strip()
    visual_anchor = str(visual_intent.subject or manual_query or "").strip()
    base_query = str(manual_query or visual_intent.query or "").strip()

    visual_type = str(visual_intent.visual_type or "GENERAL_CONTEXT").upper()
    visual_genre = str(
        visual_intent.visual_genre
        or classify_visual_genre(seg, visual_anchor, visual_type)
        or "GENERAL_CONTEXT"
    ).upper()
    seg["visual_genre"] = visual_genre
    seg["visual_rejection_counts"] = {}
    seg.pop("_visual_rejection_details", None)
    seg.pop("_verified_subject_assets", None)

    if not visual_anchor or not base_query:
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
    seg["visual_qc_blocked"] = False
    seg["visual_qc_block_reason"] = ""
    seg["visual_verification_attempts"] = 0

    # The cache is retained for automatic scenes. Manual queries intentionally
    # perform a fresh search so the dashboard receives a real image bank.
    if not manual_query:
        cached_img, _cache_path = runtime.get_cached_asset(bot, cache_entity, visual_type, context)
        if cached_img is not None:
            buffer = io.BytesIO()
            cached_img.save(buffer, format="JPEG", quality=95)
            cached_bytes = buffer.getvalue()
            cached_hash = _hash_image(bot, cached_bytes)
            if cached_hash not in used_hashes:
                try:
                    cached_ok, _cached_tier, _score, hard_reject = runtime._strict_gate(
                        bot, cached_bytes, seg, video_title, source="cache"
                    )
                except Exception:
                    cached_ok, hard_reject = False, True
                if cached_ok and not hard_reject:
                    used_hashes.add(cached_hash)
                    seg["visual_verified"] = True
                    seg["visual_rescue_reason"] = ""
                    seg["visual_fallback_reason"] = ""
                    seg["visual_query_used"] = "cache"
                    seg["visual_verification_attempts"] = 1
                    try:
                        original_path = os.path.join(
                            bot.ASSETS_DIR,
                            f"visual_original_cache_{cached_hash[:16]}.jpg",
                        )
                        cached_img.convert("RGB").save(original_path, "JPEG", quality=92)
                        seg["visual_original_path"] = original_path
                    except Exception:
                        seg["visual_original_path"] = ""
                    return cached_img.convert("RGB"), False, "cached"

    try:
        source_plan = _source_plan(bot, visual_type, visual_genre)
    except TypeError:
        source_plan = _source_plan(bot, visual_type)

    verified_assets = []
    verified_hashes = set()
    verification_attempts = 0
    last_round = ""

    query_rounds = [base_query]
    refinement = _scene_refinement_query(seg, visual_anchor)
    if refinement and refinement.casefold() != base_query.casefold():
        query_rounds.append(refinement)

    for round_index, query in enumerate(query_rounds[:2], 1):
        raw_target = INITIAL_CANDIDATE_POOL if round_index == 1 else REFINEMENT_CANDIDATE_POOL
        source_limit = INITIAL_SOURCE_LIMIT if round_index == 1 else REFINEMENT_SOURCE_LIMIT
        query_candidates = []
        attempted_for_round = set()

        print(
            f"   [Visual Search] round {round_index}/{min(2, len(query_rounds))} | "
            f"'{query}' | raw_target={raw_target} | sources={source_limit}",
            flush=True,
        )

        for source_index, (source, fetcher) in enumerate(source_plan):
            if source_index >= source_limit:
                break
            if not callable(fetcher) or len(query_candidates) >= raw_target:
                break

            source_query = _provider_search_query(
                source,
                query,
                cache_entity,
                visual_type,
                visual_genre,
                "",
                round_index,
                identity_label="",
            )
            if manual_query and round_index == 1 and str(source or "").strip().casefold() != "wikipedia":
                source_query = str(query or "").strip()
            source_key = (str(source or "").strip().casefold(), str(source_query or "").strip().casefold())
            if not source_query or source_key in attempted_for_round:
                continue
            attempted_for_round.add(source_key)

            fetch_entity = cache_entity if str(source).casefold() == "wikipedia" else source_query
            local_used_urls = set(used_urls)
            args = (
                (fetch_entity, local_used_urls, query, video_title)
                if str(source).casefold() == "wikipedia"
                else (source_query, local_used_urls, query, video_title)
            )
            raw_data = runtime._call_fetcher_with_timeout(fetcher, args, source, source_query)
            used_urls.update(local_used_urls)
            candidates = _candidate_items(raw_data)
            if not candidates:
                _record_visual_rejection(seg, "provider_empty", f"{source}:{source_query}")
                continue

            for candidate_index, data in enumerate(candidates, 1):
                valid, reason, normalized = _preflight_image(data)
                if not valid:
                    bucket = (
                        "resolution"
                        if reason.startswith("resolution-too-low")
                        else "invalid_image"
                        if reason == "invalid-image"
                        else "provider_payload"
                        if reason.startswith("provider-returned-")
                        else "preflight_reject"
                    )
                    _record_visual_rejection(seg, bucket, f"{source}:candidate {candidate_index}:{reason}")
                    continue

                image_hash = _hash_image(bot, normalized)
                if image_hash in used_hashes or any(item[3] == image_hash for item in query_candidates):
                    _record_visual_rejection(seg, "duplicate", f"{source}:candidate {candidate_index}")
                    continue

                record = candidate_provenance(data)
                if not provenance_is_usable(record):
                    _record_visual_rejection(seg, "licensing_provenance", f"{source}:candidate {candidate_index}")
                    continue

                priority = _candidate_priority(
                    source, normalized, visual_type, query, visual_genre, data=data
                )
                query_candidates.append(
                    (candidate_index, data, normalized, image_hash, priority, record, str(source), str(query))
                )
                if len(query_candidates) >= raw_target:
                    break

        if not query_candidates:
            continue

        query_candidates.sort(key=lambda item: (-float(item[4]), str(item[6]).casefold(), int(item[0])))
        # First inspect only the strongest 10 in one batch. If that does not
        # produce at least three entity-approved images, inspect the next 10.
        # This normally costs one Gemini call per search term and never requires
        # scene-level verification.
        check_candidates = query_candidates[:raw_target]
        primary_count = min(ENTITY_CHECK_PRIMARY_POOL, len(check_candidates))
        batches = [check_candidates[:primary_count]]
        if len(check_candidates) > primary_count:
            batches.append(check_candidates[primary_count:])
        batch_size = max(2, int(GEMINI_VISUAL_BATCH_SIZE))
        local_results = {}

        for batch_number, batch_group in enumerate(batches):
            if not batch_group:
                continue
            for offset in range(0, len(batch_group), batch_size):
                batch = batch_group[offset : offset + batch_size]
                result_map = strict_gemini_check_batch(
                    [item[2] for item in batch],
                    cache_entity,
                    os.getenv("GEMINI_API_KEY"),
                    tier="IDENTITY",
                    visual_type=visual_type,
                    visual_genre=visual_genre,
                )
                verification_attempts += 1
                base_index = (0 if batch_number == 0 else primary_count) + offset
                for local_index, verdict in result_map.items():
                    local_results[base_index + int(local_index)] = verdict
                if verification_attempts >= 4:
                    break
            if verification_attempts >= 4:
                break

            # Three usable alternatives are enough to keep every slide healthy.
            # Ten is the bank target, not a reason to spend another AI call.
            if len(
                [value for value in local_results.values() if value is True]
            ) >= 3:
                break

        round_verified = 0
        for local_index, item in enumerate(check_candidates):
            verdict = local_results.get(local_index)
            if verdict is True:
                before = len(verified_assets)
                verified_assets.append(
                    {
                        "subject": cache_entity,
                        "bytes": item[2],
                        "hash": item[3],
                        "source": item[6],
                        "query": item[7],
                        "visual_type": visual_type,
                        "visual_genre": visual_genre,
                        "provenance": dict(item[5]),
                        "priority": float(item[4]),
                    }
                )
                if len(verified_assets) > before:
                    round_verified += 1
            elif verdict is False:
                _record_visual_rejection(seg, "semantic_no", f"{item[6]}:candidate {item[0]}:ENTITY_NO")
            elif local_index in local_results:
                _record_visual_rejection(seg, "semantic_uncertain", f"{item[6]}:candidate {item[0]}:ENTITY_UNCERTAIN")

        last_round = str(query)
        print(
            f"   [Visual QA] entity batch complete | query='{query}' | "
            f"verified={round_verified} | bank={len(verified_assets)}",
            flush=True,
        )

        if len(verified_assets) >= MAX_ENTITY_BANK_PER_QUERY or len(verified_assets) >= 3:
            break

    if verified_assets:
        # Keep the strongest entity-approved candidates. The first becomes the
        # active visual; the rest become a dashboard-visible fallback bank.
        deduped = {}
        for asset in sorted(verified_assets, key=lambda item: -float(item.get("priority", 0.0))):
            deduped.setdefault(str(asset.get("hash") or ""), asset)
        bank_assets = list(deduped.values())[:MAX_ENTITY_BANK_PER_QUERY]
        seg["_verified_subject_assets"] = bank_assets

        selected = next(
            (
                asset
                for asset in bank_assets
                if str(asset.get("hash") or "").strip()
                and str(asset.get("hash") or "").strip() not in used_hashes
            ),
            None,
        )
        selected_hash = str(selected.get("hash") or "").strip() if selected else ""
        selected_bytes = selected.get("bytes") if selected else None
        if selected_bytes and selected_hash:
            try:
                cache_path = runtime.save_to_cache(
                    bot, selected_bytes, cache_entity, visual_type,
                    selected.get("source", "visual"), context, verified=True
                )
                if cache_path:
                    import json
                    meta_path = os.path.splitext(str(cache_path))[0] + ".json"
                    try:
                        with open(meta_path, "r", encoding="utf-8") as fh:
                            meta = json.load(fh)
                    except Exception:
                        meta = {}
                    meta["provenance"] = dict(selected.get("provenance") or {})
                    meta["visual_genre"] = visual_genre
                    meta["verified"] = True
                    with open(meta_path, "w", encoding="utf-8") as fh:
                        json.dump(meta, fh, ensure_ascii=False, indent=2)
            except Exception:
                pass

            try:
                original_path = os.path.join(
                    bot.ASSETS_DIR,
                    f"visual_original_{selected_hash[:16]}.jpg",
                )
                Image.open(io.BytesIO(selected_bytes)).convert("RGB").save(
                    original_path,
                    "JPEG",
                    quality=92,
                )
                seg["visual_original_path"] = original_path
            except Exception:
                seg["visual_original_path"] = ""

            used_hashes.add(selected_hash)
            seg["visual_verified"] = True
            seg["visual_qc_blocked"] = False
            seg["visual_qc_block_reason"] = ""
            seg["visual_rescue_reason"] = ""
            seg["visual_fallback_reason"] = ""
            seg["visual_query_used"] = str(selected.get("query") or last_round)
            seg["visual_provider_query_used"] = str(selected.get("query") or last_round)
            seg["visual_verification_attempts"] = verification_attempts
            seg["visual_selected_hash"] = selected_hash
            seg["asset_provenance"] = dict(selected.get("provenance") or {})
            return Image.open(io.BytesIO(selected_bytes)).convert("RGB"), False, str(selected.get("source") or "visual")

    if (visual_type in ABSTRACT_TYPES or genre_allows_ai(visual_genre)) and callable(getattr(bot, "fetch_hf_ai_image", None)):
        prompt_text = _ai_prompt(entity, visual_type)
        print(f"   [Visual Source] AI attempt | type={visual_type} | prompt='{prompt_text[:180]}'", flush=True)
        ai = runtime._call_fetcher_with_timeout(bot.fetch_hf_ai_image, (prompt_text,), "HF-AI", prompt_text)
        valid, reason, normalized = _preflight_image(ai)
        if valid and normalized is not None:
            result_map = strict_gemini_check_batch(
                [normalized],
                cache_entity,
                os.getenv("GEMINI_API_KEY"),
                tier="IDENTITY",
                visual_type=visual_type,
                visual_genre=visual_genre,
            )
            verification_attempts += 1
            if result_map.get(0) is True:
                seg["visual_verified"] = True
                seg["visual_rescue_reason"] = ""
                seg["visual_fallback_reason"] = ""
                seg["visual_query_used"] = prompt_text
                seg["visual_verification_attempts"] = verification_attempts
                seg["asset_provenance"] = ai_provenance()
                return Image.open(io.BytesIO(normalized)).convert("RGB"), True, "ai-generated"
            _record_visual_rejection(seg, "ai_semantic_qc_reject", "AI:ENTITY_NO_OR_UNCERTAIN")
        else:
            _record_visual_rejection(seg, "ai_preflight", f"AI:{reason}")

    _record_visual_rejection(seg, "final_rescue", "No accepted real or AI visual remained.")
    rescue = make_visual_rescue(entity or factual_entity or visual_anchor, visual_type)
    seg["visual_verified"] = False
    seg["visual_rescue_reason"] = "real-and-ai-sources-exhausted"
    seg["visual_fallback_reason"] = ""
    seg["visual_query_used"] = ""
    seg["visual_verification_attempts"] = verification_attempts
    seg["visual_selected_hash"] = ""
    seg["asset_provenance"] = rescue_provenance()
    seg["visual_rejection_counts"] = dict(
        sorted(
            (seg.get("visual_rejection_counts") or {}).items(),
            key=lambda item: (-int(item[1]), str(item[0])),
        )
    )
    return rescue, False, "visual-rescue"
