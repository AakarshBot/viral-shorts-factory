"""Generic semantic guard for visual subjects and image-search queries.

This layer is intentionally domain-neutral. It cleans generated scene metadata,
removes discourse/HTML noise, grounds candidate entities against the scene
and derives a concise visual phrase from nearby factual context. It never uses
sport-, country-, celebrity-, product- or genre-specific entity tables.
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


def _intent_words(scene: dict) -> list[str]:
    raw = clean_text(scene.get("visual_intent", ""))
    return [key(w) for w in tokens(raw) if key(w)]


def infer_role(scene: dict) -> str:
    explicit = clean_text(scene.get("visual_type", "")).upper().replace("-", "_").replace(" ", "_")
    roles = set(ROLE_CUES)
    if explicit in roles and explicit != "GENERAL_CONTEXT":
        return explicit
    intent_words = set(_intent_words(scene))
    for role, cues in ROLE_CUES.items():
        if intent_words & cues:
            return role
    return "GENERAL_CONTEXT"


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


def _tokenise_evidence(text: str) -> list[str]:
    return [token for token in tokens(text)]


def _grounded_context(candidate: str, scene: dict, video_title: str = "") -> str:
    candidate_keys = set(meaningful_tokens(candidate))
    if not candidate_keys:
        return ""
    evidence = evidence_text(scene, video_title)
    evidence_words = _tokenise_evidence(evidence)
    keyed = [key(word) for word in evidence_words]
    positions = [i for i, item in enumerate(keyed) if item in candidate_keys]
    if not positions:
        return ""

    start = max(0, min(positions) - 2)
    end = min(len(evidence_words), max(positions) + 3)
    window = evidence_words[start:end]

    kept = []
    candidate_original = {key(word): word for word in tokens(candidate)}
    for word in window:
        k = key(word)
        if not k or k in GENERIC_NOISE or k in STOPWORDS or k in DISCOURSE_PREFIXES:
            continue
        # Removing common discourse/auxiliary verbs makes the result a visual
        # phrase rather than a sentence, while preserving concrete actions such
        # as "hosting", "launched", or "opened" when they are informative.
        if k in AUXILIARY_WORDS:
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

    if prompt and _prompt_is_concrete(prompt, candidate):
        subject = prompt
    elif candidate:
        subject = candidate
    else:
        subject = prompt

    # Some roles benefit from nearby factual context because the bare entity is
    # ambiguous (e.g. an event, process or generic contextual scene). This is
    # grounded extraction, not free-form generation: every retained token comes
    # from the supplied scene evidence.
    if role in {"EVENT", "PROCESS", "CONCEPT", "DOCUMENT", "QUOTE", "GENERAL_CONTEXT"} and subject:
        contextual = _grounded_context(subject, scene, video_title)
        if contextual and len(meaningful_tokens(contextual)) > len(meaningful_tokens(subject)):
            subject = contextual

    subject = sanitize_candidate(subject)
    confidence = 0.97 if subject and subject != original else (0.90 if subject else 0.0)
    return {
        "factual_entity": candidate or original,
        "original_entity": original,
        "subject": subject,
        "visual_type": role,
        "confidence": confidence,
    }


def build_query_ladder(scene: dict, video_title: str = "") -> tuple[list[str], str, dict]:
    resolution = resolve_subject(scene, video_title)
    subject = resolution["subject"]
    if not subject:
        return [], resolution["visual_type"], resolution

    queries = [subject]
    # Only add a second search phrase when it is independently grounded in the
    # scene and actually adds information. Never append a domain or role label
    # merely to make the query list longer.
    contextual = _grounded_context(subject, scene, video_title)
    if contextual and contextual.casefold() != subject.casefold():
        candidate = sanitize_candidate(contextual)
        if candidate and candidate.casefold() not in {q.casefold() for q in queries}:
            if len(tokens(candidate)) <= MAX_QUERY_WORDS:
                queries.append(candidate)
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
