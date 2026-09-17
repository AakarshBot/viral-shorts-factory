"""Evidence-first visual retrieval planner for Viral Shorts Factory.

This module deliberately does NOT try to write a perfect natural-language image
query. It builds a tiny retrieval ladder from a resolved visual subject and only
adds context when that context is visually meaningful.

The expensive work happens in visual_runtime: search, candidate checks and
semantic verification. Query generation itself is deterministic and free.
"""
from __future__ import annotations

import html
import re

VISUAL_RETRIEVAL_PLANNER_VERSION = "2026-09-17-v9"
MAX_VISUAL_SEARCH_QUERIES = 3
MAX_QUERY_WORDS = 10
MAX_SCENE_BRIEF_WORDS = 12

VISUAL_TYPES = {
    "PERSON", "ORGANIZATION", "EVENT", "PRODUCT", "LOCATION", "STATISTIC", "COMPARISON",
    "TIMELINE", "PROCESS", "QUOTE", "DOCUMENT", "CONCEPT", "GENERAL_CONTEXT",
}

ORGANIZATION_ACRONYMS = {
    "BCCI", "ICC", "PCB", "SLC", "BCB", "ACB", "FIFA", "UEFA", "NBA", "NFL", "ATP", "WTA",
    "ISRO", "NASA", "ESA", "WHO", "UN", "UNESCO", "IMF", "WTO", "SEBI", "RBI", "DRDO", "NITI",
    "BSE", "NSE", "TCS", "IBM", "AMD", "HP", "LG", "BMW",
}
ORGANIZATION_SUFFIXES = (
    "board", "council", "federation", "association", "committee", "corporation", "company",
    "university", "institute", "foundation", "ministry", "government", "agency", "authority",
    "bank", "club", "party", "commission", "league", "network", "organization", "organisation",
)
LOCATION_NAMES = {
    "Delhi", "New Delhi", "Mumbai", "Bengaluru", "Bangalore", "Hyderabad", "Chennai", "Kolkata",
    "Pune", "Ahmedabad", "Jaipur", "Lucknow", "Surat", "Goa", "London", "Manchester", "Sydney",
    "Melbourne", "Perth", "Brisbane", "Auckland", "Cape Town", "Johannesburg", "Dubai", "Abu Dhabi",
    "Doha", "Singapore", "India", "Pakistan", "Australia", "England", "South Africa", "Sri Lanka",
    "Bangladesh", "New Zealand", "United States", "USA", "UK",
}
PRODUCT_HINTS = {
    "iphone", "ipad", "galaxy", "pixel", "playstation", "xbox", "switch", "macbook", "laptop",
    "smartphone", "suv", "processor", "gpu", "chip", "watch", "headset", "console", "camera", "phone",
}
SPORT_TERMS = {"cricket", "football", "soccer", "basketball", "tennis", "golf", "rugby", "hockey", "baseball"}

# Only terms with a useful visual interpretation are allowed into query 2/3.
# Editorial language such as "stars", "rocket", "latest" and "rankings" is
# intentionally excluded: it describes the story, not what the image should show.
VISUAL_CUES = {
    "cricket": {"t20i": "T20", "t20": "T20", "odi": "ODI", "test match": "Test cricket", "players": "players", "player": "players", "team": "team", "squad": "players", "batting": "batting", "bowling": "bowling", "celebration": "celebration", "celebrating": "celebration", "stadium": "stadium", "trophy": "trophy", "final": "final"},
    "football": {"goal": "goal", "scored": "goal", "players": "players", "player": "players", "team": "team", "celebration": "celebration", "celebrating": "celebration", "stadium": "stadium", "trophy": "trophy", "final": "final", "match": "match"},
    "soccer": {"goal": "goal", "scored": "goal", "players": "players", "player": "players", "team": "team", "celebration": "celebration", "celebrating": "celebration", "stadium": "stadium", "trophy": "trophy", "final": "final", "match": "match"},
    "basketball": {"players": "players", "player": "players", "team": "team", "game": "game", "celebration": "celebration", "stadium": "arena", "final": "final"},
}
GENERIC_VISUAL_CUES = {
    "launch": "launch", "launched": "launch", "announcement": "announcement", "announced": "announcement",
    "press conference": "press conference", "interview": "interview", "speaking": "speaking", "meeting": "meeting",
    "ceremony": "ceremony", "protest": "protest", "fire": "fire", "flood": "flood", "earthquake": "earthquake",
    "rocket": "rocket launch", "satellite": "satellite", "laboratory": "laboratory", "factory": "factory",
    "stadium": "stadium", "trophy": "trophy", "court": "court", "race": "race",
}
NOISE = {"nbsp", "amp", "quot", "apos", "lt", "gt", "latest", "breaking", "news", "update", "story", "article", "headline", "reported", "reports", "according", "says", "said", "today", "yesterday", "tomorrow", "editorial", "official", "photo", "image", "picture", "real", "high", "resolution", "white-ball"}
STOPWORDS = {"the", "and", "for", "with", "this", "that", "from", "into", "after", "before", "about", "they", "their", "there", "here", "when", "what", "which", "where", "while", "have", "has", "had", "will", "would", "could", "should", "just", "been", "were", "was", "are", "our", "you", "your", "is", "a", "an", "to", "of", "in", "on", "as", "it", "its", "these", "those", "who", "how", "why", "or", "but", "up", "down", "over", "under", "very", "more", "most", "than", "also", "can", "may", "might"}


def _clean(text):
    value = html.unescape(str(text or ""))
    value = re.sub(r"\s+", " ", value)
    return value.strip(" ,.-:;|\"'")


def _tokens(text):
    return re.findall(r"[A-Za-z0-9][A-Za-z0-9'/-]*", _clean(text).replace("’", "'").replace("‘", "'"))


def _key(token):
    return re.sub(r"[^a-z0-9]", "", token.lower().replace("'s", ""))


def _normalise(text):
    words, seen = [], set()
    for raw in _tokens(text):
        k = _key(raw)
        if not k or k in NOISE or k in {"none", "unknown", "na"} or k in seen:
            continue
        seen.add(k)
        words.append(raw)
    return " ".join(words[:MAX_QUERY_WORDS])


def _looks_like_organization(entity):
    raw = _clean(entity)
    if not raw:
        return False
    if raw.upper() in ORGANIZATION_ACRONYMS:
        return True
    lower = raw.lower()
    if any(lower.endswith(" " + suffix) or lower == suffix for suffix in ORGANIZATION_SUFFIXES):
        return True
    return bool(re.fullmatch(r"[A-Z][A-Z0-9&.-]{1,7}", raw) and len(raw) >= 3)


def _looks_like_location(entity):
    raw = _clean(entity)
    if raw in LOCATION_NAMES:
        return True
    lower = raw.lower()
    return any(lower.endswith(" " + suffix) for suffix in ("city", "state", "province", "country", "island", "county", "district"))


def _looks_like_product(entity):
    lower = _clean(entity).lower()
    return any(token in lower for token in PRODUCT_HINTS)


def _story_text(seg, category=""):
    return _clean(" ".join([str(seg.get("voiceover", "")), str(seg.get("visual_intent", "")), str(seg.get("specific_search_prompt", "")), str(category or "")])).lower()


def _resolve_visual_subject(entity, seg, category=""):
    entity = _clean(entity)
    text = _story_text(seg, category)
    category_l = _clean(category).lower()
    sport = next((x for x in SPORT_TERMS if x in text or x in category_l), "")
    if entity in LOCATION_NAMES and sport:
        return f"{entity} {sport} team"
    if entity in LOCATION_NAMES and any(x in text for x in ("players", "stars", "squad", "team", "t20i", "odi", "test match")):
        if "cricket" in text or any(x in text for x in ("t20i", "odi", "test match")):
            return f"{entity} cricket team"
    return entity


def classify_scene(seg, category=""):
    explicit = _clean(seg.get("visual_type", "")).upper().replace("-", "_").replace(" ", "_")
    entity = _clean(seg.get("primary_entity", ""))
    intent = _clean(seg.get("visual_intent", "")).lower()
    text = _story_text(seg, category)
    if _looks_like_organization(entity): return "ORGANIZATION"
    if _looks_like_location(entity): return "LOCATION"
    if _looks_like_product(entity): return "PRODUCT"
    if explicit in VISUAL_TYPES: return explicit
    if any(x in intent for x in ("person", "portrait", "player", "president", "ceo", "scientist", "actor", "coach", "founder", "minister", "speaker")): return "PERSON"
    if any(x in intent for x in ("product", "device", "phone", "car", "chip", "console")): return "PRODUCT"
    if any(x in intent for x in ("map", "location", "landmark", "geography")): return "LOCATION"
    if any(x in intent for x in ("process", "mechanism", "how it works", "diagram", "technical")): return "PROCESS"
    if any(x in intent for x in ("quote", "statement", "speaker statement")): return "QUOTE"
    if any(x in text for x in ("timeline", "history", "years ago", "year-by-year")) and re.search(r"\b(19|20)\d{2}\b", text): return "TIMELINE"
    if any(x in text for x in ("compared with", "versus", " vs ", "higher than", "lower than", "twice", "double", "difference between")): return "COMPARISON"
    if any(x in text for x in ("%", "percent", "million", "billion", "trillion", "record of", "reached", "rose to", "fell to", "number of")): return "STATISTIC"
    if any(x in text for x in ("quote", "said:", "says:", "told reporters", "according to")): return "QUOTE"
    if any(x in text for x in ("document", "report", "filing", "paper", "study", "contract")): return "DOCUMENT"
    if any(x in text for x in ("how it works", "process", "works by", "mechanism", "steps", "system", "pipeline")): return "PROCESS"
    if any(x in text for x in ("launch", "match", "final", "goal", "championship", "election", "summit", "attack", "discovery", "announcement", "ceremony", "tournament")): return "EVENT"
    if any(x in text for x in ("product", "phone", "car", "chip", "console", "device", "model", "prototype")): return "PRODUCT"
    if any(x in text for x in ("concept", "idea", "future", "possibility", "abstract", "theory")): return "CONCEPT"
    return "GENERAL_CONTEXT"


def _extract_visual_cues(seg, category=""):
    text = _story_text(seg, category)
    sport = next((x for x in SPORT_TERMS if x in text), "")
    mapping = VISUAL_CUES.get(sport, {})
    found = []
    for phrase, cue in mapping.items():
        if phrase in text and cue not in found:
            found.append(cue)
    for phrase, cue in GENERIC_VISUAL_CUES.items():
        if phrase in text and cue not in found:
            found.append(cue)
    return found[:3]


def _context_entity(seg, category=""):
    """Return a context phrase only when it is a real visual subject/event."""
    text = _story_text(seg, category)
    for phrase in ("World Cup", "Champions League", "Premier League", "T20 World Cup", "World Test Championship"):
        if phrase.lower() in text:
            return phrase
    return ""


def build_scene_visual_brief(seg, video_title="", category=""):
    raw_entity = _clean(seg.get("primary_entity", ""))
    visual_type = classify_scene(seg, category)
    subject = _resolve_visual_subject(raw_entity, seg, category)
    cues = _extract_visual_cues(seg, category)
    context = _context_entity(seg, category)
    return {
        "subject": subject,
        "visual_type": visual_type,
        "scene_action": " ".join(cues[:3]),
        "scene_context": context,
        "scene_index": _clean(seg.get("scene_index", seg.get("scene_number", ""))),
    }


def _acceptable(query, subject, existing):
    q = _normalise(query)
    if not q or len(q.split()) > MAX_QUERY_WORDS:
        return False
    subject_words = _normalise(subject).lower().split()
    q_words = q.lower().split()
    # The complete resolved subject must survive the query; this prevents
    # "India cricket team" from degrading back to just "India".
    if subject_words and not all(word in q_words for word in subject_words):
        return False
    if q.lower() in {str(x).lower() for x in existing}:
        return False
    return True


def _add(queries, subject, *parts):
    q = _normalise(" ".join(_clean(p) for p in parts if _clean(p)))
    if _acceptable(q, subject, queries):
        queries.append(q)
        return True
    return False


def build_deep_queries(seg, video_title="", visual_type=None):
    """Return a progressive retrieval ladder, not six simultaneous rewrites.

    Query 1 is the canonical visual subject. Query 2 adds one or two concrete
    visual cues. Query 3 adds a recognisable event/context only when present.
    visual_runtime searches them in order and stops as soon as a candidate is
    verified, so later queries are not paid for when the first one works.
    """
    category = _clean(seg.get("sport_or_topic_category", ""))
    brief = build_scene_visual_brief(seg, video_title, category)
    subject = brief["subject"]
    visual_type = visual_type or brief["visual_type"]
    queries = []

    # Tier 1: canonical subject. No editorial adjectives. No narration.
    _add(queries, subject, subject)

    # Tier 2: only a concrete visual cue, never a story adjective/statistic.
    cues = brief["scene_action"].split()
    if cues:
        _add(queries, subject, subject, " ".join(cues[:2]))

    # Tier 3: recognisable event/context. This is intentionally separate from
    # the headline so things like "rankings", "latest" and "white-ball stars"
    # cannot leak into image retrieval.
    context = brief["scene_context"]
    if context:
        _add(queries, subject, subject, context)

    return queries[:MAX_VISUAL_SEARCH_QUERIES], visual_type
