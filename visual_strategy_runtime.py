"""Scene-aware visual retrieval strategy for Viral Shorts Factory.

The visual search layer is deliberately retrieval-oriented:
1. resolve the visual subject from the raw entity + story context;
2. build a compact visual proposition rather than copying narration/headlines;
3. generate a small ladder of distinct retrieval intents;
4. hard-filter noisy/repetitive queries before they reach the search provider.

Image relevance is still decided downstream by the semantic visual-QA layer.
"""
from __future__ import annotations

import html
import re

VISUAL_STRATEGY_RUNTIME_VERSION = "2026-09-17-v8"
MAX_VISUAL_SEARCH_QUERIES = 6
MAX_SCENE_BRIEF_WORDS = 18
MAX_QUERY_WORDS = 12

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

NOISE_TOKENS = {
    "nbsp", "amp", "quot", "apos", "lt", "gt", "html", "www", "http", "https", "news", "update",
    "latest", "breaking", "story", "article", "headline", "reported", "reports", "according", "says",
    "said", "today", "yesterday", "tomorrow", "scene", "visual", "subject", "primary", "entity",
    "photo", "image", "picture", "pictures", "editorial", "official", "real", "high", "resolution",
}
STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "into", "after", "before", "about", "they",
    "their", "there", "here", "when", "what", "which", "where", "while", "have", "has", "had", "will",
    "would", "could", "should", "just", "been", "were", "was", "are", "our", "you", "your", "is", "a",
    "an", "to", "of", "in", "on", "as", "it", "its", "these", "those", "who", "how", "why", "or", "but",
    "up", "down", "over", "under", "very", "more", "most", "than", "also", "can", "may", "might",
}


def _clean(text):
    value = html.unescape(str(text or ""))
    value = re.sub(r"\s+", " ", value)
    return value.strip(" ,.-:;|\"'")


def _tokens(text):
    value = _clean(text).replace("’", "'").replace("‘", "'")
    return re.findall(r"[A-Za-z0-9][A-Za-z0-9'/-]*", value)


def _token_key(token):
    return re.sub(r"[^a-z0-9]", "", token.lower().replace("'s", ""))


def _normalise_query(text):
    """Normalise a query without changing its intended subject."""
    words = []
    seen = set()
    for raw in _tokens(text):
        token = raw.strip(".,!?;:")
        key = _token_key(token)
        if not key or key in NOISE_TOKENS or key in {"none", "unknown", "na", "n/a"}:
            continue
        if key in seen:
            continue
        seen.add(key)
        words.append(token)
    return " ".join(words[:MAX_QUERY_WORDS]).strip()


def _compact_terms(text, exclude=(), limit=5):
    excluded = {_token_key(x) for x in exclude if _token_key(x)}
    terms = []
    seen = set()
    for raw in _tokens(text):
        key = _token_key(raw)
        if not key or key in STOPWORDS or key in NOISE_TOKENS or key in excluded:
            continue
        if len(key) <= 1 or key in seen:
            continue
        seen.add(key)
        terms.append(raw)
        if len(terms) >= limit:
            break
    return " ".join(terms)


def _add_unique(queries, *parts):
    q = _normalise_query(" ".join(_clean(p) for p in parts if _clean(p)))
    if q and q.lower() not in {x.lower() for x in queries}:
        queries.append(q)
        return True
    return False


def _looks_like_organization(entity):
    raw = _clean(entity)
    if not raw:
        return False
    upper = raw.upper()
    if upper in ORGANIZATION_ACRONYMS:
        return True
    lower = raw.lower()
    if any(lower.endswith(" " + suffix) or lower == suffix for suffix in ORGANIZATION_SUFFIXES):
        return True
    return bool(re.fullmatch(r"[A-Z][A-Z0-9&.-]{1,7}", raw) and len(raw) >= 3)


def _looks_like_location(entity):
    raw = _clean(entity)
    if not raw:
        return False
    if raw in LOCATION_NAMES:
        return True
    lower = raw.lower()
    return any(lower.endswith(" " + suffix) for suffix in ("city", "state", "province", "country", "island", "county", "district"))


def _looks_like_product(entity):
    lower = _clean(entity).lower()
    return any(token in lower for token in PRODUCT_HINTS)


def _story_text(seg, category=""):
    return _clean(" ".join([
        str(seg.get("voiceover", "")),
        str(seg.get("visual_intent", "")),
        str(seg.get("specific_search_prompt", "")),
        str(category or ""),
    ])).lower()


def _resolve_visual_subject(entity, seg, category=""):
    """Resolve ambiguous raw entities into the thing an image should show."""
    entity = _clean(entity)
    text = _story_text(seg, category)
    category_l = _clean(category).lower()
    sports = next((sport for sport in SPORT_TERMS if sport in text or sport in category_l), "")

    if entity in LOCATION_NAMES and sports:
        return f"{entity} {sports} team"
    if entity in LOCATION_NAMES and any(term in text for term in ("players", "stars", "squad", "team", "t20i", "odi", "test match")):
        return f"{entity} cricket team" if "cricket" in text or any(x in text for x in ("t20i", "odi", "test match")) else entity
    return entity


def classify_scene(seg, category=""):
    """Classify the visual subject using entity-first precedence."""
    explicit = _clean(seg.get("visual_type", "")).upper().replace("-", "_").replace(" ", "_")
    entity = _clean(seg.get("primary_entity", ""))
    intent = _clean(seg.get("visual_intent", "")).lower()
    text = _story_text(seg, category)

    if _looks_like_organization(entity):
        return "ORGANIZATION"
    if _looks_like_location(entity):
        return "LOCATION"
    if _looks_like_product(entity):
        return "PRODUCT"
    if explicit in VISUAL_TYPES:
        return explicit
    if any(x in intent for x in ("person", "portrait", "player", "president", "ceo", "scientist", "actor", "coach", "founder", "minister", "speaker")):
        return "PERSON"
    if any(x in intent for x in ("product", "device", "phone", "car", "chip", "console")):
        return "PRODUCT"
    if any(x in intent for x in ("map", "location", "landmark", "geography")):
        return "LOCATION"
    if any(x in intent for x in ("process", "mechanism", "how it works", "diagram", "technical")):
        return "PROCESS"
    if any(x in intent for x in ("quote", "statement", "speaker statement")):
        return "QUOTE"
    if any(x in text for x in ("timeline", "history", "years ago", "year-by-year")) and re.search(r"\b(19|20)\d{2}\b", text):
        return "TIMELINE"
    if any(x in text for x in ("compared with", "versus", " vs ", "higher than", "lower than", "twice", "double", "difference between")):
        return "COMPARISON"
    if any(x in text for x in ("%", "percent", "million", "billion", "trillion", "record of", "reached", "rose to", "fell to", "number of")):
        return "STATISTIC"
    if any(x in text for x in ("quote", "said:", "says:", "told reporters", "according to")):
        return "QUOTE"
    if any(x in text for x in ("document", "report", "filing", "paper", "study", "contract")):
        return "DOCUMENT"
    if any(x in text for x in ("how it works", "process", "works by", "mechanism", "steps", "system", "pipeline")):
        return "PROCESS"
    if any(x in text for x in ("launch", "match", "final", "goal", "championship", "election", "summit", "attack", "discovery", "announcement", "ceremony", "tournament")):
        return "EVENT"
    if any(x in text for x in ("product", "phone", "car", "chip", "console", "device", "model", "prototype")):
        return "PRODUCT"
    if any(x in text for x in ("concept", "idea", "future", "possibility", "abstract", "theory")):
        return "CONCEPT"
    return "GENERAL_CONTEXT"


def _scene_phrase(seg, entity=""):
    return _compact_terms(seg.get("voiceover", ""), exclude=(entity,), limit=6)


def build_scene_visual_brief(seg, video_title="", category=""):
    """Create a deterministic visual proposition without an additional API call."""
    raw_entity = _clean(seg.get("primary_entity", ""))
    visual_type = classify_scene(seg, category)
    subject = _resolve_visual_subject(raw_entity, seg, category)
    phrase = _scene_phrase(seg, subject)
    prompt_terms = _compact_terms(seg.get("specific_search_prompt", ""), exclude=(raw_entity, subject), limit=5)
    intent_terms = _compact_terms(seg.get("visual_intent", ""), exclude=(raw_entity, subject), limit=3)
    scene_action = _normalise_query(" ".join(x for x in (prompt_terms, phrase, intent_terms) if x))
    title_terms = _compact_terms(video_title, exclude=(raw_entity, subject), limit=4)
    context = _normalise_query(" ".join(x for x in (category, title_terms) if x))
    return {
        "subject": subject,
        "visual_type": visual_type,
        "scene_action": " ".join(scene_action.split()[:MAX_SCENE_BRIEF_WORDS]),
        "scene_context": context,
        "scene_index": _clean(seg.get("scene_index", seg.get("scene_number", ""))),
    }


def _query_is_acceptable(query, subject, existing):
    q = _normalise_query(query)
    if not q or len(q.split()) > MAX_QUERY_WORDS:
        return False
    subject_key = _normalise_query(subject).lower()
    if subject_key and subject_key not in q.lower():
        return False
    if len(q.split()) >= 9 and len(set(_token_key(w) for w in q.split())) < len(q.split()) * 0.72:
        return False
    return q.lower() not in {str(old).strip().lower() for old in existing}


def _candidate(queries, subject, *parts):
    raw = " ".join(_clean(p) for p in parts if _clean(p))
    if not _query_is_acceptable(raw, subject, queries):
        return False
    return _add_unique(queries, raw)


def build_deep_queries(seg, video_title="", visual_type=None):
    """Build a bounded retrieval ladder with distinct search intents.

    The six slots are not six rewrites of the same sentence. They represent
    identity, action, context, editorial, official/press and fallback retrieval.
    """
    category = _clean(seg.get("sport_or_topic_category", ""))
    brief = build_scene_visual_brief(seg, video_title, category)
    subject = brief["subject"]
    visual_type = visual_type or brief["visual_type"]
    action = brief["scene_action"]
    context = brief["scene_context"]
    queries = []

    story = _story_text(seg, category)
    sport = next((x for x in SPORT_TERMS if x in story), "")
    domain = sport or _compact_terms(category, limit=2)
    action_terms = " ".join(action.split()[:5])

    # 1. Identity: safest retrieval.
    identity_mod = {
        "PERSON": "photo", "ORGANIZATION": "official", "LOCATION": "photo",
        "PRODUCT": "official product photo", "EVENT": "event photo",
    }.get(visual_type, "")
    _candidate(queries, subject, subject, domain, identity_mod)

    # 2. Action/scene: compact terms only, never the full narration.
    if action_terms:
        _candidate(queries, subject, subject, action_terms, "photo")

    # 3. Context/event: compact title/category context.
    if context:
        _candidate(queries, subject, subject, context, "photo")

    modifiers = {
        "PERSON": ("editorial photo", "press photo"),
        "ORGANIZATION": ("official photo", "press conference"),
        "LOCATION": ("landmark photo", "cityscape"),
        "EVENT": ("editorial event photo", "official event photo"),
        "PRODUCT": ("product launch photo", "press image"),
        "STATISTIC": ("chart", "data visualization"),
        "COMPARISON": ("comparison chart", "side by side"),
        "TIMELINE": ("historical photo", "archive photo"),
        "PROCESS": ("diagram", "technical illustration"),
        "QUOTE": ("press conference photo", "official statement"),
        "DOCUMENT": ("official document", "report"),
        "CONCEPT": ("concept illustration", "documentary context"),
        "GENERAL_CONTEXT": ("editorial photo", "documentary photo"),
    }
    mod1, mod2 = modifiers.get(visual_type, modifiers["GENERAL_CONTEXT"])

    # 4. Editorial retrieval.
    _candidate(queries, subject, subject, domain, mod1)
    # 5. Official/press retrieval.
    _candidate(queries, subject, subject, domain, mod2)
    # 6. Broad but still grounded fallback.
    fallback = sport or _compact_terms(category, limit=1)
    _candidate(queries, subject, subject, fallback, "real photo" if visual_type in {"PERSON", "LOCATION", "EVENT", "ORGANIZATION"} else "photo")

    return queries[:MAX_VISUAL_SEARCH_QUERIES], visual_type


try:
    from visual_policy_runtime import install_visual_card_policy
    install_visual_card_policy()
except Exception:
    pass
