"""Canonical visual-search intent for the Shorts factory.

Automatic queries are built for image retrieval rather than prose. The factual
identity is mandatory; scene evidence is ranked for searchable visual anchors
such as named organizations/events, concrete actions, and photographable
contexts. Manual queries remain exact and authoritative.

Retrieval is intentionally bounded to one primary query plus the exact factual
identity fallback. Query construction never invents facts or fan-outs into a
blind ladder.
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


def _context_terms(text: str, subject: str, limit: int = 4) -> list[str]:
    """Compatibility helper using the same retrieval-aware ranking."""
    return _ranked_scene_terms({"visual_context": text}, subject, limit=limit)


def _scene_terms(scene: dict, subject: str) -> list[str]:
    """Return the strongest evidence-backed visual anchors for this slide."""
    return _ranked_scene_terms(scene, subject, limit=4)


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

    ranked = sorted(scene_terms, key=lambda term: strength(term), reverse=True)
    for term in ranked:
        keys = {key(part) for part in tokens(term)}
        if keys & _SEARCH_STRONG:
            return term
    return ""


def _genre_hint_anchor(genre: str, scene_terms: list[str]) -> str:
    """Use a visual-form hint only when the genre itself calls for one."""
    hints = {
        "PERSON_PORTRAIT": ("portrait",),
        "ORG_BRANDING": ("logo",),
        "TEAM_BRANDING": ("logo",),
        "ORG_HEADQUARTERS": ("headquarters",),
        "LANDMARK": ("landmark",),
        "ARCHITECTURE": ("building",),
        "MAP": ("map",),
        "CHART_GRAPH": ("chart",),
        "DIAGRAM": ("diagram",),
        "SCREENSHOT_UI": ("screenshot",),
        "TROPHY_AWARD": ("trophy",),
        "HISTORICAL_PHOTO": ("historical",),
    }
    for hint in hints.get(str(genre or "").upper(), ()):
        if hint:
            return hint
    return _primary_visual_anchor(scene_terms)


def resolve_visual_search_intent(scene: dict, video_title: str = "") -> VisualSearchIntent:
    """Resolve one compact visual query plus one exact-identity fallback."""
    scene = scene if isinstance(scene, dict) else {}
    manual = _clean(scene.get("manual_visual_query", ""))
    base = dict(scene)

    if manual:
        scene_resolution = resolve_subject(base, video_title)
        subject = manual
        visual_type = str(scene_resolution.get("visual_type") or "GENERAL_CONTEXT").upper()
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
        anchor = _primary_visual_anchor(scene_terms)

        # Keep image queries short and photographic. For a person/action slide,
        # the best query is normally "identity + concrete scene anchor" rather
        # than the entire natural-language prompt.
        if visual_genre == "PERSON_ACTION":
            anchor = anchor or _primary_visual_anchor(scene_terms)
        elif visual_genre == "PERSON_PORTRAIT":
            anchor = _genre_hint_anchor(visual_genre, scene_terms)
        elif not anchor:
            anchor = _genre_hint_anchor(visual_genre, scene_terms)

        query = _compose_query(subject, anchor)
        queries = [query] if query else []
        if subject and query.casefold() != subject.casefold():
            queries.append(subject)

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
        visual_genre=classify_visual_genre(scene, subject, visual_type),
        query=query,
        queries=tuple(queries),
        intent=intent,
        context=context,
        confidence=confidence,
        manual=bool(manual),
    )

__all__ = ["VisualSearchIntent", "resolve_visual_search_intent"]
