"""Canonical visual-search intent for the Shorts factory.

Automatic queries are built for image retrieval rather than prose. The factual
identity is mandatory; scene evidence is used only for a compact searchable
refinement. Manual queries remain exact for provider retrieval while their
entity anchor is separated for entity-only verification.

Retrieval is intentionally bounded to one primary query plus one compact
evidence-backed refinement. Query construction never invents facts or fans
out into a blind ladder.
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
from visual_taxonomy_runtime import classify_visual_genre


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


def _clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


_MANUAL_SCENE_WORDS = {
    "logo", "logos", "portrait", "portraits", "headshot", "headshots",
    "press", "conference", "interview", "speech", "speaking", "meeting",
    "match", "game", "batting", "bowling", "training", "stadium", "arena",
    "final", "finals", "opening", "ceremony", "celebrate", "celebration", "celebrating",
    "crowd", "fans", "night", "day", "action", "action-shot", "screenshot",
    "map", "flag", "flags", "poster", "posters", "chart", "graph", "document",
    "documents", "office", "headquarters",
}


def _manual_entity_from_query(manual: str, resolved_subject: str) -> str:
    """Keep the manual search phrase intact while separating its entity anchor."""
    query = _clean(manual)
    resolved = clean_text(resolved_subject or "")
    if resolved:
        query_tokens = set(tokens(query))
        resolved_tokens = set(tokens(resolved))
        overlap = query_tokens & resolved_tokens
        if overlap and (
            len(resolved_tokens) == 1
            or len(overlap) >= 2
            or len(overlap) >= len(resolved_tokens)
        ):
            return resolved

    words = [
        word
        for word in re.findall(r"[\w-]+", query, flags=re.UNICODE)
        if word.casefold() not in _MANUAL_SCENE_WORDS
    ]
    return " ".join(words[:4]).strip() or query


def canonical_manual_entity_anchor(manual: str, resolved_subject: str = "") -> str:
    """Canonicalize only named-team manual queries; preserve all other manual anchors."""
    query = _clean(manual)
    anchor = _manual_entity_from_query(query, resolved_subject)
    if not anchor:
        return query

    lowered = query.casefold()
    is_named_team = bool(
        re.search(r"\bwomen'?s\b", lowered)
        and re.search(r"\bnational\b", lowered)
        and re.search(r"\bteam\b", lowered)
    )
    if not is_named_team:
        return anchor

    try:
        from visual_provider_boundary_runtime import resolve_wikidata_entity
        resolved = resolve_wikidata_entity(anchor)
        label = clean_text((resolved or {}).get("label") or "")
        if label:
            return label
    except Exception:
        pass
    return anchor


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
    "batting", "bowling", "fielding", "wicket", "playing", "cricket", "innings", "match", "odi", "t20", "test",
    "series", "qualifier", "semifinal", "final", "trophy", "award", "medal",
    "ceremony", "presentation", "conference", "summit", "launch", "opening",
    "closing", "meeting", "hearing", "rally", "protest", "demonstration",
    "interview", "speech", "press", "stadium", "arena", "laboratory", "lab",
    "factory", "office", "stage", "podium", "court", "parliament", "museum",
    "landmark", "building", "street", "hospital", "airport", "campus", "camp",
    "vehicle", "aircraft", "rocket", "satellite", "product", "device", "screen",
    "map", "chart", "diagram", "document", "signing", "signing", "testing",
    "research", "experiment", "demonstration", "performance", "concert",
    "festival", "parade", "exhibition", "premiere", "ceremony",
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
    ("factual_visual_intent", 5),
    ("visual_context", 5),
    ("visual_intent", 4),
    ("factual_search_prompt", 2),
    ("specific_search_prompt", 2),
    ("factual_voiceover", 1),
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
        # A single capitalized token is too easily just a sentence start.
    if current and len(current) >= 2:
        results.append((" ".join(current), 8.0 + min(4, len(current))))
    return results


def _ranked_scene_terms(scene: dict, subject: str, limit: int = 5) -> list[str]:
    """Rank compact searchable anchors from the scene's actual evidence."""
    subject_keys = {key(word) for word in tokens(subject)}
    candidates: dict[str, tuple[float, str]] = {}

    for field, field_weight in _FIELDS:
        text = _clean(scene.get(field, ""))
        if not text:
            continue

        # Named multi-word anchors are especially useful for image indexes.
        for phrase, score in _capitalized_phrases(text, subject_keys):
            normalized = key(phrase)
            if normalized:
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
                score += 6.0
            elif re.search(r"(?:ing|tion|ment|ance|ence|al|ary|ism|ity)$", token_key):
                score += 1.5

            # A concrete adjacent pair is preferable to two unrelated terms.
            if index + 1 < len(raw_words):
                nxt = raw_words[index + 1]
                if not _blocked(nxt, subject_keys):
                    nxt_key = key(nxt)
                    if nxt_key in _SEARCH_WEAK:
                        pass
                    elif nxt_key in _SEARCH_STRONG:
                        phrase = f"{word} {nxt}"
                        candidates[key(phrase)] = (
                            score + field_weight + 4.0,
                            phrase,
                        )

            prior = candidates.get(token_key)
            if prior is None or score > prior[0]:
                candidates[token_key] = (score, word)

    ranked = sorted(candidates.values(), key=lambda item: (-item[0], len(item[1].split()), item[1].casefold()))
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


def _scene_terms(scene: dict, subject: str) -> list[str]:
    """Return enough evidence-backed anchors to preserve concrete action terms."""
    return _ranked_scene_terms(scene, subject, limit=6)


def _compose_query(subject: str, *anchors: str, max_words: int = 7) -> str:
    """Compose a compact image-search query without rewriting the locked subject."""
    subject = _clean(subject)
    if not subject:
        return ""

    subject_keys = {key(word) for word in tokens(subject)}
    words = list(tokens(subject))
    additions: list[str] = []
    remaining = max(0, int(max_words) - len(words))
    if remaining <= 0:
        return subject

    added: set[str] = set()
    for anchor in anchors:
        for word in tokens(anchor):
            token_key = key(word)
            if not token_key or token_key in subject_keys or token_key in added:
                continue
            additions.append(word)
            added.add(token_key)
            remaining -= 1
            if remaining <= 0:
                break
        if remaining <= 0:
            break

    return _clean(" ".join([subject, *additions]))


def _primary_visual_anchor(scene_terms: list[str]) -> str:
    """Prefer one concrete multi-word visual anchor over scattered prose."""
    if not scene_terms:
        return ""

    def strength(term: str) -> tuple[int, int, float]:
        parts = tokens(term)
        keys = [key(part) for part in parts]
        strong_count = sum(item in _SEARCH_STRONG for item in keys)
        weak_count = sum(item in _SEARCH_WEAK for item in keys)
        # Multi-word concrete phrases such as "press conference" should beat
        # isolated terms such as "announcement" or "players".
        phrase_bonus = 3 if len(parts) >= 2 and strong_count >= 2 else 0
        return (phrase_bonus, strong_count, -float(weak_count))

    ranked = sorted(
        enumerate(scene_terms),
        key=lambda item: (
            strength(item[1])[0],
            strength(item[1])[1],
            strength(item[1])[2],
            -item[0],
        ),
        reverse=True,
    )
    for _, term in ranked:
        keys = {key(part) for part in tokens(term)}
        if keys & _SEARCH_STRONG:
            return term
    return ""


def _genre_hint_anchor(genre: str, scene_terms: list[str]) -> str:
    """Use only visual-form words that are explicit in the requested scene."""
    genre = str(genre or "").upper()
    explicit_hints = {
        "PERSON_PORTRAIT": "portrait",
        "ORG_BRANDING": "logo",
        "TEAM_BRANDING": "logo",
    }
    hint = explicit_hints.get(genre, "")
    if hint:
        return hint
    return _primary_visual_anchor(scene_terms)


def resolve_visual_search_intent(scene: dict, video_title: str = "") -> VisualSearchIntent:
    """Resolve one compact visual query plus a bounded identity-preserving fallback ladder."""
    scene = scene if isinstance(scene, dict) else {}
    manual = _clean(scene.get("manual_visual_query", ""))
    base = dict(scene)

    if manual:
        scene_resolution = resolve_subject(base, video_title)
        resolved_subject = scene_resolution.get("subject") or scene_resolution.get("factual_entity") or ""
        subject = _manual_entity_from_query(manual, resolved_subject)
        visual_type = str(scene_resolution.get("visual_type") or "GENERAL_CONTEXT").upper()

        # Manual queries are authoritative for retrieval, but stale generated
        # genre/context metadata must not leak into the replacement scene.
        manual_genre_scene = {
            "primary_entity": manual,
            "visual_intent": _clean(scene.get("visual_intent", "")),
            "specific_search_prompt": manual,
        }
        visual_genre = classify_visual_genre(manual_genre_scene, manual, visual_type)
        confidence = 1.0
        query = manual
        queries = [manual]
    else:
        resolution = resolve_subject(base, video_title)
        subject = clean_text(resolution.get("subject") or resolution.get("factual_entity", ""))
        visual_type = str(resolution.get("visual_type") or "GENERAL_CONTEXT").upper()
        confidence = float(resolution.get("confidence") or 0.0)
        scene_terms = _scene_terms(scene, subject)
        visual_genre = classify_visual_genre(scene, subject, visual_type)

        # Portraits are an identity problem, not a scene-description problem.
        # Search the locked person name first, then allow one provider-friendly
        # portrait refinement. Do not burn the bounded query budget on narrative
        # terms such as "conceptual", "tournament" or "single T20".
        if visual_genre == "PERSON_PORTRAIT":
            queries = [subject] if subject else []
            portrait_query = _compose_query(subject, "portrait")
            if portrait_query and portrait_query.casefold() not in {item.casefold() for item in queries}:
                queries.append(portrait_query)
            query = queries[0] if queries else ""
        else:
            # Search the factual subject first. Only add one compact scene
            # refinement later when the first entity search does not produce
            # enough verified images. This keeps the query path quick and
            # avoids burning provider calls on long narrative phrases.
            query = subject
            queries = [subject] if subject else []
            if visual_genre in {"ORG_BRANDING", "TEAM_BRANDING"}:
                anchor = _genre_hint_anchor(visual_genre, scene_terms)
            else:
                anchor = _primary_visual_anchor(scene_terms)

            refined = _compose_query(subject, anchor)
            if visual_genre == "PERSON_ACTION" and refined:
                queries = [refined]
                if subject and subject.casefold() != refined.casefold():
                    queries.append(subject)
            else:
                queries = [subject] if subject else []
                if refined and refined.casefold() not in {item.casefold() for item in queries}:
                    queries.append(refined)
                queries = queries[:2]

    if manual:
        manual_intent = _clean(scene.get("visual_intent", ""))
        intent = manual_intent or manual
        context = manual_intent or manual
    else:
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
        visual_genre=visual_genre if manual else classify_visual_genre(scene, subject, visual_type),
        query=query,
        queries=tuple(queries),
        intent=intent,
        context=context,
        confidence=confidence,
        manual=bool(manual),
    )

__all__ = ["VisualSearchIntent", "resolve_visual_search_intent", "canonical_manual_entity_anchor"]
