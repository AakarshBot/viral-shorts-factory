"""Canonical visual-search intent for the Shorts factory.

Automatic queries are built for image retrieval rather than prose. The factual
identity is mandatory; scene evidence is ranked for searchable visual anchors
such as named organisations/events, concrete actions, locations and
photographable contexts. Manual queries remain exact and authoritative.

Automatic retrieval uses a small set of evidence-backed reformulations rather
than a blind query ladder: a primary identity+scene query, a distinct
identity+context variant when available, and the exact identity fallback.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from visual_semantic_guard_runtime import (
    AUXILIARY_WORDS,
    DISCOURSE_PREFIXES,
    GENERIC_NOISE,
    STOPWORDS,
    VISUAL_DESCRIPTORS,
    clean_text,
    key,
    resolve_subject,
    tokens,
)
from visual_taxonomy_runtime import classify_visual_genre, genre_query_hints


@dataclass(frozen=True)
class VisualSearchIntent:
    subject: str
    visual_type: str
    visual_genre: str
    query: str
    queries: tuple[str, ...]
    intent: str
    context: str
    confidence: float
    manual: bool = False
    query_strategy: str = "automatic"


def _clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


# These are retrieval-quality filters, not domain rules. They remove words that
# usually describe the script rather than help an image index identify a frame.
_SEARCH_WEAK = {
    "young", "old", "new", "recent", "former", "current", "main", "key", "major",
    "important", "notable", "famous", "popular", "latest", "first", "second",
    "third", "many", "several", "some", "someone", "people", "person", "individual",
    "player", "athlete", "batsman", "batter", "official", "winner", "recipient",
    "speaker", "member", "members", "man", "woman", "men", "women",
    "receiving", "getting", "being", "appears", "appearing", "looks", "showing",
    "shows", "pictured", "depicting", "depicts", "scene", "moment", "shot", "view",
    "visual", "footage", "image", "photo", "picture", "editorial", "realistic",
}

# Concrete visual nouns/actions that are useful when they are actually present
# in the scene evidence. This is deliberately cross-genre rather than
# sport/celebrity/product specific.
_SEARCH_STRONG = {
    "batting", "bowling", "training", "match", "trophy", "award", "medal",
    "ceremony", "presentation", "conference", "summit", "launch", "opening",
    "closing", "meeting", "hearing", "rally", "protest", "demonstration",
    "interview", "speech", "press", "stadium", "arena", "laboratory", "lab",
    "factory", "office", "stage", "podium", "court", "parliament", "museum",
    "landmark", "building", "street", "hospital", "airport", "campus", "camp",
    "vehicle", "aircraft", "rocket", "satellite", "product", "device", "screen",
    "map", "chart", "diagram", "document", "signing", "testing", "research",
    "experiment", "performance", "concert", "festival", "parade", "exhibition",
    "premiere", "celebration", "celebrating", "victory", "winning",
}

_GENERIC_TERMS = {
    "person", "people", "organization", "organisation", "company", "product",
    "device", "location", "geography", "concept", "process", "event", "document",
    "quote", "quotation", "statistic", "comparison", "timeline", "scientific",
    "technical", "abstract", "team", "members", "venue", "show", "showing",
    "scene", "visual", "image", "photo", "picture", "editorial", "footage",
    "moment", "shot", "view", "looks", "look", "appears", "appearing", "depict",
    "depicting", "someone", "individual", "realistic",
}

_FIELDS = (
    ("factual_visual_intent", 6),
    ("visual_context", 6),
    ("visual_intent", 5),
    ("factual_search_prompt", 3),
    ("specific_search_prompt", 3),
    ("factual_voiceover", 2),
    ("voiceover", 1),
)


def _blocked(word: str, subject_keys: set[str]) -> bool:
    token_key = key(word)
    return (
        not token_key
        or token_key in subject_keys
        or token_key in GENERIC_NOISE
        or token_key in STOPWORDS
        or token_key in DISCOURSE_PREFIXES
        or token_key in AUXILIARY_WORDS
        or token_key in VISUAL_DESCRIPTORS
        or token_key in _GENERIC_TERMS
    )


def _capitalized_phrases(text: str, subject_keys: set[str]) -> list[tuple[str, float]]:
    """Find likely named visual anchors without treating sentence starts as facts."""
    words = re.findall(r"[A-Za-z][A-Za-z0-9'/-]*", str(text or ""))
    results: list[tuple[str, float]] = []
    current: list[str] = []
    for index, word in enumerate(words):
        is_cap = bool(re.match(r"^[A-Z][A-Za-z0-9'/-]*$", word))
        if is_cap and not _blocked(word, subject_keys):
            current.append(word)
            continue
        if current:
            phrase = " ".join(current)
            keys = [key(item) for item in current]
            if len(current) >= 2 and not all(item in subject_keys for item in keys):
                results.append((phrase, 8.0 + min(4, len(current))))
            current = []
    if current and len(current) >= 2:
        results.append((" ".join(current), 8.0 + min(4, len(current))))
    return results


def _location_phrases(text: str, subject_keys: set[str]) -> list[tuple[str, float]]:
    """Recover compact place anchors that are often useful in image search."""
    words = re.findall(r"[A-Za-z][A-Za-z0-9'/-]*", str(text or ""))
    results: list[tuple[str, float]] = []
    lowered = [key(word) for word in words]
    for index, token in enumerate(lowered):
        if token not in {"in", "at", "near", "outside", "inside", "from"}:
            continue
        collected = []
        for candidate in words[index + 1:index + 5]:
            if not re.match(r"^[A-Z][A-Za-z0-9'/-]*$", candidate):
                break
            if _blocked(candidate, subject_keys):
                break
            collected.append(candidate)
        if collected:
            results.append((" ".join(collected), 10.0 + min(3.0, len(collected))))
    return results


def _ranked_scene_terms(scene: dict, subject: str, limit: int = 6) -> list[str]:
    """Rank compact searchable anchors from the scene's actual evidence."""
    subject_keys = {key(word) for word in tokens(subject)}
    candidates: dict[str, tuple[float, str]] = {}

    for field, field_weight in _FIELDS:
        text = _clean(scene.get(field, ""))
        if not text:
            continue

        for phrase, score in _capitalized_phrases(text, subject_keys):
            normalized = key(phrase)
            if normalized:
                value = score + field_weight
                prior = candidates.get(normalized)
                if prior is None or value > prior[0]:
                    candidates[normalized] = (value, phrase)

        for phrase, score in _location_phrases(text, subject_keys):
            normalized = key(phrase)
            if normalized and normalized not in subject_keys:
                value = score + field_weight
                prior = candidates.get(normalized)
                if prior is None or value > prior[0]:
                    candidates[normalized] = (value, phrase)

        raw_words = tokens(text)
        for index, word in enumerate(raw_words):
            if _blocked(word, subject_keys):
                continue
            token_key = key(word)
            score = float(field_weight)
            if token_key in _SEARCH_WEAK:
                continue
            if token_key in _SEARCH_STRONG:
                score += 7.0
            elif re.search(r"(?:ing|tion|ment|ance|ence|al|ary|ism|ity)$", token_key):
                score += 1.5

            if index + 1 < len(raw_words):
                nxt = raw_words[index + 1]
                if not _blocked(nxt, subject_keys):
                    nxt_key = key(nxt)
                    if nxt_key in _SEARCH_STRONG:
                        phrase = f"{word} {nxt}"
                        phrase_key = key(phrase)
                        candidates[phrase_key] = (
                            score + field_weight + 4.0,
                            phrase,
                        )

            prior = candidates.get(token_key)
            if prior is None or score > prior[0]:
                candidates[token_key] = (score, word)

    ranked = sorted(
        candidates.values(),
        key=lambda item: (-item[0], -len(item[1].split()), item[1].casefold()),
    )
    terms: list[str] = []
    seen = set()
    for _, term in ranked:
        normalized = key(term)
        if normalized in seen:
            continue
        seen.add(normalized)
        terms.append(term)
        if len(terms) >= limit:
            break
    return terms


def _context_terms(text: str, subject: str, limit: int = 4) -> list[str]:
    """Compatibility helper using the same retrieval-aware ranking."""
    return _ranked_scene_terms({"visual_context": text}, subject, limit=limit)


def _scene_terms(scene: dict, subject: str) -> list[str]:
    """Return the strongest evidence-backed visual anchors for this slide."""
    return _ranked_scene_terms(scene, subject, limit=6)


def _query_clean(parts: list[str], max_words: int = 8) -> str:
    """Build a compact query while preserving the supplied phrase order."""
    words = []
    seen = set()
    for part in parts:
        for word in tokens(part):
            token_key = key(word)
            if not token_key or token_key in seen:
                continue
            if token_key in GENERIC_NOISE or token_key in STOPWORDS:
                continue
            seen.add(token_key)
            words.append(word)
            if len(words) >= max_words:
                return " ".join(words)
    return " ".join(words)


def _query_contains_subject(query: str, subject: str) -> bool:
    query_keys = {key(word) for word in tokens(query)}
    subject_keys = {key(word) for word in tokens(subject)}
    return bool(subject_keys) and subject_keys.issubset(query_keys)


def _append_query(queries: list[str], subject: str, parts: list[str]) -> None:
    query = _query_clean([subject, *parts], max_words=8)
    if not query or not _query_contains_subject(query, subject):
        return
    normalized = key(query)
    if normalized and normalized not in {key(item) for item in queries}:
        queries.append(query)


def _genre_strategy(genre: str) -> tuple[str, tuple[str, ...]]:
    """Return the query construction mode plus safe visual descriptors."""
    hints = tuple(str(item).strip() for item in genre_query_hints(genre) if str(item).strip())
    if genre in {"PERSON_PORTRAIT", "ORG_BRANDING", "TEAM_BRANDING", "TROPHY_AWARD",
                 "SCREENSHOT_UI", "CHART_GRAPH", "MAP", "DIAGRAM", "DOCUMENT",
                 "MONEY_CURRENCY", "FLAG_SYMBOL", "HISTORICAL_ARTIFACT", "MEDIA_ARTWORK"}:
        return "asset", hints
    if genre in {"PERSON_ACTION", "TEAM_ACTION", "SPORTS_ACTION", "SPORTS_MATCH",
                 "EVENT_SCENE", "ORG_HEADQUARTERS", "PRODUCT_LAUNCH", "PLACE_SCENE",
                 "LANDMARK", "ARCHITECTURE", "VEHICLE", "ANIMAL", "FOOD",
                 "SCIENCE_VISUAL", "SPACE_VISUAL", "NATURE_LANDSCAPE", "HISTORICAL_PHOTO",
                 "GENERAL_PHOTO"}:
        return "scene", hints
    return "context", hints


def _automatic_queries(scene: dict, subject: str, visual_genre: str) -> tuple[str, ...]:
    """Create a small, evidence-backed set of useful reformulations."""
    scene_terms = _scene_terms(scene, subject)
    strategy, hints = _genre_strategy(visual_genre)
    queries: list[str] = []

    # Exact visual asset genres benefit from an explicit asset descriptor when
    # the taxonomy provides one. This is safe because it describes the target
    # visual form rather than inventing a fact.
    if visual_genre == "PERSON_PORTRAIT":
        # A portrait slide should stay a portrait search even when the narration
        # mentions a related event. This keeps the identity precise and avoids
        # turning a clean headshot request into an event-scene query.
        _append_query(queries, subject, [*hints[:1]])
    elif strategy == "asset":
        _append_query(queries, subject, [*hints[:1], *scene_terms[:1]])
        _append_query(queries, subject, [*hints[:1]])
    elif strategy == "scene":
        # For action/event slides, the first query should reflect the actual
        # scene evidence instead of collapsing to identity-only retrieval.
        _append_query(queries, subject, scene_terms[:2])
        _append_query(queries, subject, [scene_terms[2], *hints[:1]] if len(scene_terms) >= 3 else list(hints[:1]))
    else:
        _append_query(queries, subject, scene_terms[:3])

    # Every automatic query set ends with an identity fallback. This keeps
    # retrieval resilient when a contextual query is too restrictive for a
    # provider's index.
    _append_query(queries, subject, [])

    return tuple(queries[:3])


def resolve_visual_search_intent(scene: dict, video_title: str = "") -> VisualSearchIntent:
    """Resolve one factual identity and a compact adaptive retrieval strategy."""
    scene = scene if isinstance(scene, dict) else {}
    manual = str(scene.get("manual_visual_query") or "").strip()
    base = dict(scene)

    if manual:
        scene_resolution = resolve_subject(base, video_title)
        manual_resolution = resolve_subject(
            {
                "primary_entity": manual,
                "visual_intent": scene.get("visual_intent", ""),
                "visual_context": scene.get("visual_context", ""),
            },
            video_title,
        )
        visual_type = str(
            manual_resolution.get("visual_type")
            if manual_resolution.get("visual_type") != "GENERAL_CONTEXT"
            else scene_resolution.get("visual_type")
            or "GENERAL_CONTEXT"
        ).upper()
        subject = manual
        visual_genre = classify_visual_genre(
            {
                **scene,
                "primary_entity": manual,
                "visual_type": visual_type,
                "specific_search_prompt": manual,
            },
            manual,
            visual_type,
        )
        confidence = 1.0
        query = manual
        queries = [manual]
        query_strategy = "manual-exact"
    else:
        resolution = resolve_subject(base, video_title)
        subject = clean_text(resolution.get("subject") or resolution.get("factual_entity", ""))
        visual_type = str(resolution.get("visual_type") or "GENERAL_CONTEXT").upper()
        visual_genre = classify_visual_genre(scene, subject, visual_type)
        confidence = float(resolution.get("confidence") or 0.0)
        queries = list(_automatic_queries(scene, subject, visual_genre))
        query = queries[0] if queries else subject
        query_strategy = f"automatic-{visual_genre.lower()}"

    intent = _clean(scene.get("factual_visual_intent") or scene.get("visual_intent"))
    context = _clean(" ".join(
        _clean(scene.get(field, ""))
        for field in (
            "factual_visual_intent", "visual_intent", "visual_context",
            "factual_search_prompt", "specific_search_prompt",
            "factual_voiceover", "voiceover",
        )
        if _clean(scene.get(field, ""))
    ) or video_title)

    return VisualSearchIntent(
        subject=subject,
        visual_type=visual_type,
        visual_genre=visual_genre,
        query=query,
        queries=tuple(queries),
        intent=intent,
        context=context,
        confidence=confidence,
        manual=bool(manual),
        query_strategy=query_strategy,
    )


__all__ = ["VisualSearchIntent", "resolve_visual_search_intent"]
