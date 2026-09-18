"""Evidence-first, genre-agnostic visual retrieval planner.

The planner separates factual identity from the scene role that identity plays
and from the final visual subject used for retrieval. Role inference is driven
by explicit scene semantics (visual intent/type) and optional domain context;
it does not maintain sport-, country-, celebrity-, or product-specific tables.
"""
from __future__ import annotations

from dataclasses import dataclass
import html
import re

VISUAL_RETRIEVAL_PLANNER_VERSION = "2026-09-18-v2-generic-semantic-resolver"
MAX_VISUAL_SEARCH_QUERIES = 5
MAX_QUERY_WORDS = 10

VISUAL_TYPES = {
    "PERSON", "ORGANIZATION", "EVENT", "PRODUCT", "LOCATION", "STATISTIC",
    "COMPARISON", "TIMELINE", "PROCESS", "QUOTE", "DOCUMENT", "CONCEPT",
    "GENERAL_CONTEXT",
}

# Generic linguistic signals only. No named-entity catalogue is required for
# semantic resolution; explicit scene role remains authoritative.
ORGANIZATION_SUFFIXES = (
    "board", "council", "federation", "association", "committee", "corporation",
    "company", "university", "institute", "foundation", "ministry", "government",
    "agency", "authority", "bank", "club", "party", "commission", "league",
    "network", "organization", "organisation", "parliament", "congress", "department",
    "team", "squad", "group", "crew", "ensemble", "collective",
)
PRODUCT_HINTS = {
    "product", "device", "phone", "smartphone", "tablet", "laptop", "computer",
    "processor", "gpu", "chip", "console", "camera", "headset", "watch", "car",
    "suv", "model", "prototype",
}
EVENT_HINTS = {
    "event", "summit", "conference", "festival", "ceremony", "election",
    "tournament", "competition", "championship", "final", "launch", "opening",
    "closing", "meeting",
}
GENERIC_NOISE = {
    "nbsp", "amp", "quot", "apos", "lt", "gt", "latest", "breaking", "news",
    "update", "story", "article", "headline", "reported", "reports", "according",
    "says", "said", "today", "yesterday", "tomorrow", "editorial", "official",
    "photo", "image", "picture", "real", "high", "resolution", "unknown", "none", "na",
}
# Backward-compatible alias used by legacy Unicode/runtime wrappers. It is the
# same generic noise set and does not add any domain-specific behavior.
NOISE = GENERIC_NOISE
STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "into", "after", "before",
    "about", "they", "their", "there", "here", "when", "what", "which", "where",
    "while", "have", "has", "had", "will", "would", "could", "should", "just",
    "been", "were", "was", "are", "our", "you", "your", "is", "a", "an", "to", "of",
    "in", "on", "as", "it", "its", "these", "those", "who", "how", "why", "or",
    "but", "up", "down", "over", "under", "very", "more", "most", "than", "also",
    "can", "may", "might",
}

ROLE_PATTERNS = (
    ("PERSON", (
        "person portrait", "portrait", "headshot", "person", "biography", "speaker",
        "actor", "scientist", "coach", "founder", "minister", "president", "ceo",
        "individual",
    )),
    ("LOCATION", (
        "location", "geography", "map", "landmark", "address", "venue", "stadium",
        "arena", "ground", "building exterior", "place", "cityscape",
    )),
    ("ORGANIZATION", (
        "organization", "organisation", "institution", "company", "corporation",
        "agency", "government", "ministry", "parliament", "board", "federation",
        "association", "committee", "team", "squad", "group", "crew", "department",
        "collective", "staff", "members",
    )),
    ("PRODUCT", (
        "product", "device", "phone", "smartphone", "tablet", "laptop", "computer",
        "car", "suv", "chip", "processor", "gpu", "console", "camera", "headset",
        "prototype", "hardware",
    )),
    ("CONCEPT", (
        "scientific concept", "concept", "theory", "principle", "mechanism", "abstract",
        "idea", "technical process", "technical concept", "diagram",
    )),
    ("PROCESS", (
        "process", "how it works", "mechanism", "workflow", "pipeline", "steps", "procedure",
    )),
    ("EVENT", (
        "actual event", "event", "competition", "tournament", "summit", "conference",
        "festival", "ceremony", "election", "launch event", "match event",
    )),
    ("DOCUMENT", (
        "document", "report", "filing", "paper", "study", "contract", "record",
    )),
    ("QUOTE", (
        "quote", "quotation", "statement", "speaker statement",
    )),
    ("STATISTIC", (
        "statistic", "data point", "metric", "percentage", "figure",
    )),
    ("COMPARISON", (
        "comparison", "versus", "vs", "difference", "compared", "comparison graphic",
    )),
    ("TIMELINE", (
        "timeline", "history timeline", "chronology",
    )),
)


@dataclass(frozen=True)
class VisualResolution:
    factual_entity: str
    base_type: str
    scene_role: str
    domain: str
    visual_subject: str
    visual_type: str
    confidence: float


def _clean(text):
    value = html.unescape(str(text or ""))
    value = value.replace("\u200b", " ").replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", value).strip(" ,.-:;|\"'")


def _tokens(text):
    return re.findall(r"[\w][\w'/-]*", _clean(text).replace("’", "'").replace("‘", "'"), flags=re.UNICODE)


def _key(token):
    return re.sub(r"[^\w]", "", str(token or "").casefold(), flags=re.UNICODE).replace("'s", "")


def _normalise(text):
    words = []
    seen = set()
    for raw in _tokens(text):
        key = _key(raw)
        if not key or key in GENERIC_NOISE or key in {"unknown", "none", "na"} or key in seen:
            continue
        seen.add(key)
        words.append(raw)
        if len(words) >= MAX_QUERY_WORDS:
            break
    return " ".join(words)


def _looks_like_organization(entity):
    raw = _clean(entity)
    if not raw:
        return False
    lower = raw.casefold()
    if any(lower == suffix or lower.endswith(" " + suffix) for suffix in ORGANIZATION_SUFFIXES):
        return True
    return bool(re.fullmatch(r"[A-Z][A-Z0-9&.-]{1,12}", raw))


def _looks_like_location(entity):
    raw = _clean(entity)
    if not raw:
        return False
    lower = raw.casefold()
    return any(lower.endswith(" " + suffix) or lower == suffix for suffix in (
        "city", "state", "province", "country", "island", "county", "district",
        "region", "capital",
    ))


def _looks_like_product(entity):
    lower = _clean(entity).casefold()
    return any(re.search(r"\b" + re.escape(hint) + r"\b", lower) for hint in PRODUCT_HINTS)


def classify_subject_text(subject):
    """Classify an already-resolved subject using only generic lexical cues."""
    value = _clean(subject)
    if not value:
        return "GENERAL_CONTEXT"
    if _looks_like_organization(value):
        return "ORGANIZATION"
    if _looks_like_location(value):
        return "LOCATION"
    if _looks_like_product(value):
        return "PRODUCT"
    lower = value.casefold()
    if any(re.search(r"\b" + re.escape(hint) + r"\b", lower) for hint in EVENT_HINTS):
        return "EVENT"
    return "GENERAL_CONTEXT"


def _story_text(seg, category=""):
    parts = [
        seg.get("visual_intent", ""),
        seg.get("specific_search_prompt", ""),
        seg.get("voiceover", ""),
        seg.get("visual_context", ""),
    ]
    if category:
        parts.append(category)
    return _clean(" ".join(str(part) for part in parts if part))


def _intent_text(seg):
    return _clean(seg.get("visual_intent", "")).casefold()


def _role_from_patterns(text):
    lower = _clean(text).casefold()
    for role, patterns in ROLE_PATTERNS:
        for pattern in patterns:
            if re.search(r"(?<!\w)" + re.escape(pattern) + r"(?!\w)", lower):
                return role
    return ""


def _explicit_role(seg):
    explicit = _clean(seg.get("visual_type", "")).upper().replace("-", "_").replace(" ", "_")
    if explicit in VISUAL_TYPES and explicit != "GENERAL_CONTEXT":
        return explicit
    return _role_from_patterns(_intent_text(seg))


def _role_noun(intent):
    lower = _clean(intent).casefold()
    candidates = (
        "team", "squad", "group", "crew", "collective", "department", "company",
        "organization", "institution", "venue", "stadium", "arena", "landmark",
        "conference", "summit", "festival", "ceremony", "product", "device",
        "prototype", "portrait", "headshot", "person", "event", "document", "report",
        "concept", "process", "diagram",
    )
    for candidate in candidates:
        if re.search(r"(?<!\w)" + re.escape(candidate) + r"(?!\w)", lower):
            return candidate
    return ""


def _domain(seg, category=""):
    value = _clean(category or seg.get("sport_or_topic_category", ""))
    return value.casefold()


def _subject_for_resolution(entity, seg, role, domain):
    entity = _clean(entity)
    if not entity:
        return ""
    intent = _intent_text(seg)
    role_noun = _role_noun(intent)

    if role == "ORGANIZATION" and role_noun in {"team", "squad", "group", "crew", "collective", "department"}:
        if domain and domain not in intent and domain.casefold() not in entity.casefold():
            return _normalise(f"{entity} {domain} {role_noun}")
        return _normalise(f"{entity} {role_noun}") if role_noun not in entity.casefold() else entity

    if role == "LOCATION" and role_noun in {"venue", "stadium", "arena", "landmark"}:
        descriptor = role_noun
        if domain and domain.casefold() not in entity.casefold() and domain.casefold() not in intent:
            return _normalise(f"{entity} {domain} {descriptor}")
        return _normalise(f"{entity} {descriptor}") if descriptor not in entity.casefold() else entity

    return entity


def _base_type(entity):
    if _looks_like_organization(entity):
        return "ORGANIZATION"
    if _looks_like_location(entity):
        return "LOCATION"
    if _looks_like_product(entity):
        return "PRODUCT"
    return "GENERAL_CONTEXT"


def resolve_visual_semantics(seg, category=""):
    """Resolve one authoritative semantic contract for a scene."""
    if not isinstance(seg, dict):
        seg = {}
    entity = _clean(seg.get("primary_entity", ""))
    domain = _domain(seg, category)
    role = _explicit_role(seg)
    base = _base_type(entity)
    if not role or role == "GENERAL_CONTEXT":
        role = base
    if not role or role == "GENERAL_CONTEXT":
        role = classify_subject_text(entity)
    if not role:
        role = "GENERAL_CONTEXT"

    visual_subject = _subject_for_resolution(entity, seg, role, domain)
    visual_type = role if role in VISUAL_TYPES else "GENERAL_CONTEXT"
    confidence = 0.95 if _explicit_role(seg) else (0.78 if base != "GENERAL_CONTEXT" else 0.62)
    return VisualResolution(
        factual_entity=entity,
        base_type=base,
        scene_role=role,
        domain=domain,
        visual_subject=visual_subject,
        visual_type=visual_type,
        confidence=confidence,
    )


def _resolve_visual_subject(entity, seg, category=""):
    payload = dict(seg or {})
    payload["primary_entity"] = entity
    return resolve_visual_semantics(payload, category).visual_subject


def classify_scene(seg, category=""):
    return resolve_visual_semantics(seg, category).visual_type


def _extract_visual_cues(seg, category=""):
    intent = _intent_text(seg)
    role = _explicit_role(seg)
    found = []
    for phrase in re.findall(r"[A-Za-z][A-Za-z0-9 -]{2,30}", intent):
        phrase = _clean(phrase)
        if len(phrase.split()) <= 3 and phrase.casefold() not in GENERIC_NOISE and phrase.casefold() not in STOPWORDS:
            if phrase.casefold() not in {x.casefold() for x in found}:
                found.append(phrase)
        if len(found) >= 2:
            break
    if role and role.casefold() not in {x.casefold() for x in found}:
        found.append(role)
    return found[:3]


def _context_entity(seg, category=""):
    for field in ("visual_context", "specific_search_prompt"):
        text = _clean(seg.get(field, ""))
        if text:
            cleaned = _normalise(text)
            subject = _clean(seg.get("primary_entity", ""))
            if cleaned and cleaned.casefold() != subject.casefold():
                return cleaned
    return ""


def build_scene_visual_brief(seg, video_title="", category=""):
    resolution = resolve_visual_semantics(seg, category)
    cues = _extract_visual_cues(seg, category)
    context = _context_entity(seg, category)
    return {
        "subject": resolution.visual_subject,
        "visual_type": resolution.visual_type,
        "scene_action": " ".join(cues[:2]),
        "scene_context": context,
        "scene_index": _clean(seg.get("scene_index", seg.get("scene_number", ""))),
        "factual_entity": resolution.factual_entity,
        "base_type": resolution.base_type,
        "scene_role": resolution.scene_role,
        "domain": resolution.domain,
        "confidence": resolution.confidence,
    }


def _acceptable(query, subject, existing):
    q = _normalise(query)
    if not q or len(q.split()) > MAX_QUERY_WORDS:
        return False
    required = _tokens(subject)
    q_keys = {_key(token) for token in _tokens(q)}
    if required and not all(_key(token) in q_keys for token in required):
        return False
    normalized_existing = {_normalise(item).casefold() for item in existing}
    return q.casefold() not in normalized_existing


def _add(queries, subject, *parts):
    query = _normalise(" ".join(_clean(part) for part in parts if _clean(part)))
    if _acceptable(query, subject, queries):
        queries.append(query)
        return True
    return False


def build_deep_queries(seg, video_title="", visual_type=None):
    """Compatibility entry point delegated to the canonical visual intent."""
    if not isinstance(seg, dict):
        return [], visual_type or "GENERAL_CONTEXT"
    from visual_search_intent_runtime import resolve_visual_search_intent
    intent = resolve_visual_search_intent(seg, video_title)
    return list(intent.queries), (visual_type or intent.visual_type or "GENERAL_CONTEXT")
