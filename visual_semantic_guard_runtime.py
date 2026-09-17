"""Generic semantic guard for visual subjects and image-search queries.

This layer is intentionally domain-neutral. It cleans generated scene metadata,
removes discourse/HTML noise, grounds candidate entities against the scene
when the candidate is malformed, and derives concise visual phrases without
turning clean entities into sentences. It never uses sport-, country-,
celebrity-, product- or genre-specific entity tables.
"""
from __future__ import annotations

import html
import re

MAX_SUBJECT_WORDS = 8
MAX_QUERY_WORDS = 10

GENERIC_NOISE = {
    "nbsp", "amp", "quot", "apos", "lt", "gt", "latest", "breaking", "news",
    "update", "story", "article", "headline", "reported", "reports", "according",
    "says", "said", "today", "yesterday", "tomorrow", "editorial", "official",
    "photo", "image", "picture", "real", "high", "resolution", "event", "item",
    "thing", "stuff", "matter", "point", "one", "off", "oneoff", "kind", "way", "part", "time",
    "next", "year",
}

# These describe how a subject is visually represented. They are not factual
# identity, so they may be removed only for a later fallback query.
VISUAL_DESCRIPTORS = {
    "logo", "logos", "portrait", "portraits", "headshot", "headshots", "icon", "icons",
    "badge", "badges", "emblem", "emblems", "symbol", "symbols", "seal", "seals",
    "map", "maps", "chart", "charts", "graph", "graphs", "diagram", "diagrams",
    "infographic", "infographics", "screenshot", "screenshots", "poster", "posters",
    "flag", "flags",
}

DISCOURSE_PREFIXES = {
    "not", "just", "only", "also", "still", "now", "really", "actually", "basically",
    "clearly", "simply", "perhaps", "maybe", "apparently", "reportedly", "even",
}

STOPWORDS = {
    "the", "and", "for", "with", "from", "this", "that", "into", "after", "before",
    "about", "they", "their", "there", "here", "when", "what", "which", "where", "while",
    "have", "has", "had", "will", "would", "could", "should", "been", "were", "was", "are",
    "our", "you", "your", "is", "a", "an", "to", "of", "in", "on", "as", "it", "its",
    "these", "those", "who", "how", "why", "or", "but", "up", "down", "over", "under",
    "very", "more", "most", "than", "also", "can", "may", "might", "be", "do", "does", "did",
}

AUXILIARY_WORDS = {
    "continue", "continues", "continued", "continuing", "remain", "remains", "remained", "become",
    "becomes", "became", "being", "been", "make", "makes", "made", "get", "gets", "got", "go",
    "goes", "went", "come", "comes", "came", "say", "says", "said", "tell", "tells", "told",
    "report", "reports", "reported", "announce", "announces", "announced", "show", "shows", "showed",
    "host", "hosts", "hosted", "hosting", "return", "returns", "returned", "returning",
    "hold", "holds", "held", "holding",
}

ROLE_CUES = {
    "PERSON": {"person", "portrait", "headshot", "biography", "speaker", "actor", "scientist", "coach", "founder", "minister", "president", "ceo", "individual"},
    "ORGANIZATION": {"organization", "organisation", "institution", "company", "corporation", "agency", "government", "ministry", "parliament", "board", "federation", "association", "committee", "team", "squad", "group", "crew", "department", "collective", "members", "staff"},
    "PRODUCT": {"product", "device", "phone", "smartphone", "tablet", "laptop", "computer", "car", "suv", "chip", "processor", "gpu", "console", "camera", "headset", "prototype", "hardware"},
    "LOCATION": {"location", "geography", "map", "landmark", "address", "venue", "stadium", "arena", "ground", "place", "cityscape"},
    "CONCEPT": {"concept", "theory", "principle", "mechanism", "abstract", "idea", "diagram", "technical", "scientific"},
    "PROCESS": {"process", "workflow", "pipeline", "steps", "procedure", "mechanism", "how"},
    "EVENT": {"event", "competition", "tournament", "summit", "conference", "festival", "ceremony", "election", "launch", "opening", "closing", "meeting", "final"},
    "DOCUMENT": {"document", "report", "filing", "paper", "study", "contract", "record"},
    "QUOTE": {"quote", "quotation", "statement"},
    "STATISTIC": {"statistic", "data", "metric", "percentage", "figure"},
    "COMPARISON": {"comparison", "versus", "difference", "compared"},
    "TIMELINE": {"timeline", "chronology", "history"},
}


def clean_text(value: object) -> str:
    text = html.unescape(str(value or "")).replace("\u00a0", " ").replace("\u200b", " ")
    text = re.sub(r"\b(?:nbsp|amp|quot|apos|lt|gt)\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"[_]+", " ", text)
    return re.sub(r"\s+", " ", text).strip(" ,.;:|\"'()[]{}")


def key(value: str) -> str:
    return re.sub(r"[^\w]+", "", clean_text(value).casefold(), flags=re.UNICODE)


def tokens(value: str) -> list[str]:
    return re.findall(r"[\w][\w'/-]*", clean_text(value), flags=re.UNICODE)


def meaningful_tokens(value: str) -> list[str]:
    result = []
    for token in tokens(value):
        k = key(token)
        if not k or k in GENERIC_NOISE or k in STOPWORDS:
            continue
        result.append(k)
    return result


def sanitize_candidate(value: object) -> str:
    text = clean_text(value)
    words = tokens(text)
    while words and key(words[0]) in DISCOURSE_PREFIXES:
        words.pop(0)
    while words and key(words[-1]) in GENERIC_NOISE:
        words.pop()
    return " ".join(words[:MAX_SUBJECT_WORDS]).strip(" ,.;:|\"'")


def _strip_visual_descriptors(value: str) -> str:
    """Remove only trailing visual-presentation words, preserving factual identity."""
    words = tokens(value)
    while len(words) > 1 and key(words[-1]) in VISUAL_DESCRIPTORS:
        words.pop()
    return sanitize_candidate(" ".join(words))


def _descriptor_present(value: str) -> bool:
    words = tokens(value)
    return bool(words) and any(key(word) in VISUAL_DESCRIPTORS for word in words[1:])


def _subject_role_hint(value: str) -> str:
    """Infer a factual role from the subject itself, including generic descriptors."""
    core = _strip_visual_descriptors(value)
    core_words = {key(word) for word in tokens(core)}
    if not core_words:
        return ""

    # Generic uppercase acronyms are a useful organization signal (e.g. ABC),
    # but are only used when a presentation descriptor made the identity clear.
    core_text = clean_text(core)
    if re.fullmatch(r"[A-Z][A-Z0-9&.-]{1,12}(?:\s+[A-Z][A-Z0-9&.-]{1,12})*", core_text):
        return "ORGANIZATION"

    for role, cues in ROLE_CUES.items():
        if core_words & cues:
            return role
    return ""


def _intent_words(scene: dict) -> list[str]:
    raw = clean_text(scene.get("visual_intent", ""))
    # Compound labels such as news_event, news-event and person/portrait are
    # semantic metadata, not literal search phrases. Normalize separators so
    # the shared role vocabulary can interpret them consistently.
    raw = re.sub(r"[_/-]+", " ", raw)
    return [key(w) for w in tokens(raw) if key(w)]


def infer_role(scene: dict) -> str:
    explicit = clean_text(scene.get("visual_type", "")).upper().replace("-", "_").replace(" ", "_")
    if explicit in ROLE_CUES:
        return explicit

    candidate = sanitize_candidate(scene.get("primary_entity", ""))
    subject_hint = _subject_role_hint(candidate)
    if subject_hint and _descriptor_present(candidate):
        return subject_hint

    intent_words = set(_intent_words(scene))
    for role, cues in ROLE_CUES.items():
        if intent_words & cues:
            return role
    return subject_hint or "GENERAL_CONTEXT"


def evidence_text(scene: dict, video_title: str = "") -> str:
    parts = []
    for field in ("voiceover", "visual_context", "specific_search_prompt", "visual_intent"):
        value = clean_text(scene.get(field, ""))
        if value:
            parts.append(value)
    title = clean_text(video_title or scene.get("video_title", ""))
    if title:
        parts.append(title)
    return " ".join(parts)


def _grounded_context(candidate: str, scene: dict, video_title: str = "") -> str:
    candidate_keys = set(meaningful_tokens(candidate))
    if not candidate_keys:
        return ""
    evidence_words = tokens(evidence_text(scene, video_title))
    keyed = [key(word) for word in evidence_words]
    positions = [i for i, item in enumerate(keyed) if item in candidate_keys]
    if not positions:
        return ""

    start = max(0, min(positions) - 3)
    end = min(len(evidence_words), max(positions) + 4)
    window = evidence_words[start:end]

    kept = []
    candidate_original = {key(word): word for word in tokens(candidate)}
    for word in window:
        k = key(word)
        if not k or k in GENERIC_NOISE or k in STOPWORDS or k in DISCOURSE_PREFIXES or k in AUXILIARY_WORDS:
            continue
        if k not in {key(x) for x in kept}:
            kept.append(word)

    for token_key, original in candidate_original.items():
        if token_key not in {key(x) for x in kept}:
            kept.append(original)

    if not kept:
        return ""
    return " ".join(kept[:MAX_SUBJECT_WORDS])


def _prompt_is_concrete(prompt: str, candidate: str) -> bool:
    cleaned = sanitize_candidate(prompt)
    if not cleaned:
        return False
    keys = set(meaningful_tokens(cleaned))
    candidate_keys = set(meaningful_tokens(candidate))
    if not candidate_keys:
        return False
    return candidate_keys.issubset(keys) and len(keys) >= len(candidate_keys)


def resolve_subject(scene: dict, video_title: str = "") -> dict:
    scene = scene if isinstance(scene, dict) else {}
    original = clean_text(scene.get("primary_entity", ""))
    candidate = sanitize_candidate(original)
    role = infer_role(scene)
    prompt = sanitize_candidate(scene.get("specific_search_prompt", ""))

    prompt_is_explicit = bool(prompt and _prompt_is_concrete(prompt, candidate))
    if prompt_is_explicit:
        subject = prompt
    else:
        subject = candidate

    # Contextual grounding is a repair operation, not a query-expansion step.
    # A clean entity must remain exactly that, even when the narration contains
    # additional facts. Grounding occurs only after detectable entity damage.
    needs_grounding = bool(candidate) and original.casefold() != candidate.casefold() and not prompt_is_explicit
    if needs_grounding and role in {"EVENT", "PROCESS", "CONCEPT", "DOCUMENT", "QUOTE", "GENERAL_CONTEXT"}:
        contextual = _grounded_context(subject, scene, video_title)
        if contextual and len(meaningful_tokens(contextual)) > len(meaningful_tokens(subject)):
            subject = contextual

    subject = sanitize_candidate(subject)
    confidence = 0.97 if subject and subject == candidate else (0.90 if subject else 0.0)
    return {
        "factual_entity": candidate or original,
        "original_entity": original,
        "subject": subject,
        "visual_type": role,
        "confidence": confidence,
    }


def _anchor_for_resolution(resolution: dict) -> str:
    """Return the cleaned factual identity that every fallback must preserve."""
    factual = sanitize_candidate(resolution.get("factual_entity") or resolution.get("original_entity") or "")
    core = _strip_visual_descriptors(factual)
    return core or factual


def _reduce_subject_once(subject: str, anchor: str) -> str:
    """Remove one trailing scene-specific modifier while preserving identity.

    This is intentionally positional rather than genre-driven. It handles
    phrases such as ``Indian athletes Nagoya Asian Games arrival`` by producing
    ``Indian athletes Nagoya Asian Games`` while keeping the factual anchor.
    For a damaged compound such as ``BBL season-opener Chennai`` with anchor
    ``BBL Chennai``, it removes the non-anchor modifier and yields ``BBL Chennai``.
    """
    words = tokens(subject)
    anchor_keys = meaningful_tokens(anchor)
    if not words or not anchor_keys:
        return ""

    protected = set()
    next_anchor = 0
    for index, word in enumerate(words):
        if next_anchor < len(anchor_keys) and key(word) == anchor_keys[next_anchor]:
            protected.add(index)
            next_anchor += 1
    if next_anchor < len(anchor_keys):
        return ""

    removable = [i for i in range(len(words) - 1, -1, -1) if i not in protected]
    if not removable:
        return ""
    reduced = " ".join(word for i, word in enumerate(words) if i != removable[0])
    reduced = sanitize_candidate(reduced)
    if not reduced:
        return ""
    reduced_keys = set(meaningful_tokens(reduced))
    if not all(item in reduced_keys for item in anchor_keys):
        return ""
    return reduced


def build_query_ladder(scene: dict, video_title: str = "") -> tuple[list[str], str, dict]:
    resolution = resolve_subject(scene, video_title)
    subject = resolution["subject"]
    if not subject:
        return [], resolution["visual_type"], resolution

    anchor = _anchor_for_resolution(resolution)
    queries = [subject]

    reduced = _reduce_subject_once(subject, anchor)
    if reduced and reduced.casefold() not in {q.casefold() for q in queries}:
        queries.append(reduced)

    if anchor and anchor.casefold() not in {q.casefold() for q in queries}:
        queries.append(anchor)

    return queries[:3], resolution["visual_type"], resolution


def prepare_scene(scene: dict, video_title: str = "") -> dict:
    resolution = resolve_subject(scene, video_title)
    prepared = dict(scene or {})
    prepared["factual_primary_entity"] = resolution["factual_entity"]
    prepared["original_primary_entity"] = resolution["original_entity"]
    prepared["primary_entity"] = resolution["subject"]
    prepared["visual_search_subject"] = resolution["subject"]
    prepared["visual_type"] = resolution["visual_type"]
    prepared["specific_search_prompt"] = resolution["subject"]
    prepared["visual_subject_confidence"] = resolution["confidence"]
    prepared["visual_subject_locked"] = True
    return prepared
