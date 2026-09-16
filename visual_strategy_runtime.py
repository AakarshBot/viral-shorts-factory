"""Scene-aware visual strategy for the Shorts factory.

This module decides what kind of visual a scene needs and builds a small,
progressive search ladder. The visual QA layer remains responsible for deciding
whether a returned image is actually relevant.
"""
import re

VISUAL_STRATEGY_RUNTIME_VERSION = "2026-09-17-v6"
MAX_VISUAL_SEARCH_QUERIES = 6

VISUAL_TYPES = {
    "PERSON", "ORGANIZATION", "EVENT", "PRODUCT", "LOCATION", "STATISTIC", "COMPARISON",
    "TIMELINE", "PROCESS", "QUOTE", "DOCUMENT", "CONCEPT", "GENERAL_CONTEXT",
}

# High-confidence entity hints. These are deliberately conservative: they are
# used to override a bad PERSON label when the entity itself is clearly an
# organization or location.
ORGANIZATION_ACRONYMS = {
    "BCCI", "ICC", "PCB", "SLC", "BCB", "ACB", "FIFA", "UEFA", "NBA", "NFL", "ATP", "WTA",
    "BCCI", "ISRO", "NASA", "ESA", "WHO", "UN", "UNESCO", "IMF", "WTO", "SEBI", "RBI",
    "DRDO", "NITI", "BSE", "NSE", "TCS", "IBM", "AMD", "HP", "LG", "BMW", "GOVERNMENT",
}
ORGANIZATION_SUFFIXES = (
    "board", "council", "federation", "association", "committee", "corporation", "company",
    "university", "institute", "foundation", "ministry", "government", "agency", "authority",
    "bank", "club", "party", "commission", "league", "network", "organization", "organisation",
)
LOCATION_NAMES = {
    "Delhi", "New Delhi", "Mumbai", "Bengaluru", "Bangalore", "Hyderabad", "Chennai", "Kolkata",
    "Pune", "Ahmedabad", "Jaipur", "Lucknow", "Surat", "Goa", "Amaravati", "Thiruvananthapuram",
    "London", "Manchester", "Sydney", "Melbourne", "Perth", "Brisbane", "Auckland", "Cape Town",
    "Johannesburg", "Dubai", "Abu Dhabi", "Doha", "Singapore", "India", "Pakistan", "Australia",
    "England", "South Africa", "Sri Lanka", "Bangladesh", "New Zealand", "United States", "USA", "UK",
}
PRODUCT_HINTS = {
    "iphone", "ipad", "galaxy", "pixel", "playstation", "xbox", "switch", "macbook", "laptop", "smartphone",
    "car", "suv", "processor", "gpu", "chip", "watch", "headset", "console", "camera", "phone",
}


def _clean(text):
    return re.sub(r"\s+", " ", str(text or "")).strip(" ,.-")


def _normalise_query(text):
    """Remove internal labels/noisy punctuation without changing the subject."""
    text = _clean(text)
    text = re.sub(
        r"\b(?:editorial_)?(?:person|organization|organisation|event|product|location|statistic|comparison|timeline|process|quote|document|concept|general_context)\b",
        "",
        text,
        flags=re.I,
    )
    return re.sub(r"\s+", " ", text).strip(" ,.-")


def _add_unique(queries, *parts):
    q = _normalise_query(" ".join(_clean(p) for p in parts if _clean(p)))
    if q and q.lower() not in {x.lower() for x in queries}:
        queries.append(q)


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
    # All-caps short names are much more often organizations than people.
    if re.fullmatch(r"[A-Z][A-Z0-9&.-]{1,7}", raw) and len(raw) >= 3:
        return True
    return False


def _looks_like_location(entity):
    raw = _clean(entity)
    if not raw:
        return False
    if raw in LOCATION_NAMES:
        return True
    lower = raw.lower()
    return any(
        lower.endswith(" " + suffix) for suffix in ("city", "state", "province", "country", "island", "county", "district")
    )


def _looks_like_product(entity):
    lower = _clean(entity).lower()
    return any(token in lower for token in PRODUCT_HINTS)


def classify_scene(seg, category=""):
    """Classify the visual subject using entity-first precedence.

    The previous classifier looked heavily at surrounding narration. That meant
    an organization such as BCCI or a location such as Delhi could be labelled
    PERSON merely because the narration also contained words like 'player' or
    'president'. The new order is:
      1. strong entity identity,
      2. explicit visual type when it agrees with the entity,
      3. scene intent/context,
      4. conservative general context fallback.
    """
    explicit = _clean(seg.get("visual_type", "")).upper().replace("-", "_").replace(" ", "_")
    entity = _clean(seg.get("primary_entity", ""))
    intent = _clean(seg.get("visual_intent", "")).lower()
    text = _clean(" ".join([
        str(seg.get("voiceover", "")),
        intent,
        str(seg.get("specific_search_prompt", "")),
        str(category),
    ])).lower()

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
    if any(x in text for x in ("compared with", "versus", "vs ", "higher than", "lower than", "twice", "double", "difference between")):
        return "COMPARISON"
    if any(x in text for x in ("%", "percent", "million", "billion", "trillion", "record of", "reached", "rose to", "fell to", "number of")) or re.search(r"\b\d+(?:\.\d+)?\s*(?:%|million|billion|trillion)\b", text):
        return "STATISTIC"
    if any(x in text for x in ("quote", "said:", "says:", "told reporters", "according to")):
        return "QUOTE"
    if any(x in text for x in ("document", "report", "filing", "paper", "study", "contract")):
        return "DOCUMENT"
    if any(x in text for x in ("how it works", "process", "works by", "mechanism", "steps", "system", "pipeline")):
        return "PROCESS"
    if any(x in text for x in ("map", "located in", "based in", "country", "city", "region", "island")):
        return "LOCATION"
    if any(x in text for x in ("launch", "match", "final", "goal", "championship", "election", "summit", "attack", "discovery", "announcement", "ceremony", "tournament")):
        return "EVENT"
    if any(x in text for x in ("product", "phone", "car", "chip", "console", "device", "model", "prototype")):
        return "PRODUCT"
    if any(x in text for x in ("concept", "idea", "future", "possibility", "abstract", "theory")):
        return "CONCEPT"
    return "GENERAL_CONTEXT"


def _scene_phrase(seg):
    """Extract a compact scene proposition from the narration for search."""
    text = _clean(seg.get("voiceover", ""))
    if not text:
        return ""
    # Keep a concise phrase; full narration is too noisy for image search.
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'/-]*", text)
    stop = {
        "the", "and", "for", "with", "this", "that", "from", "into", "after", "before", "about", "they",
        "their", "there", "here", "when", "what", "which", "where", "while", "have", "has", "had", "will",
        "would", "could", "should", "just", "been", "were", "was", "are", "our", "you", "your", "today",
    }
    meaningful = [w for w in words if w.lower() not in stop]
    return " ".join(meaningful[:12])


def build_deep_queries(seg, video_title="", visual_type=None):
    """Build scene-specific, entity-aware search queries.

    Query order is deliberately recall-first, then context-specific. The search
    query describes the exact scene rather than the entire story, while semantic
    QA remains responsible for rejecting wrong images.
    """
    entity = _clean(seg.get("primary_entity", ""))
    intent = _normalise_query(seg.get("visual_intent", ""))
    prompt = _normalise_query(seg.get("specific_search_prompt", ""))
    title = _clean(video_title)
    category = _clean(seg.get("sport_or_topic_category", ""))
    scene_phrase = _scene_phrase(seg)
    visual_type = visual_type or classify_scene(seg, category)

    queries = []

    if visual_type == "PERSON":
        _add_unique(queries, entity, prompt)
        _add_unique(queries, entity, scene_phrase)
        _add_unique(queries, entity, title)
        _add_unique(queries, entity, category)
        _add_unique(queries, entity, "official photo")
        return queries[:MAX_VISUAL_SEARCH_QUERIES], visual_type

    if visual_type == "ORGANIZATION":
        _add_unique(queries, entity, prompt)
        _add_unique(queries, entity, scene_phrase)
        _add_unique(queries, entity, title)
        _add_unique(queries, entity, category)
        _add_unique(queries, entity, "official")
        _add_unique(queries, entity, "press conference")
        return queries[:MAX_VISUAL_SEARCH_QUERIES], visual_type

    if visual_type == "LOCATION":
        _add_unique(queries, entity, scene_phrase)
        _add_unique(queries, entity, prompt)
        _add_unique(queries, entity, title)
        _add_unique(queries, entity, category)
        _add_unique(queries, entity, "cityscape")
        _add_unique(queries, entity, "landmark")
        return queries[:MAX_VISUAL_SEARCH_QUERIES], visual_type

    modifier_map = {
        "EVENT": ["official event photo", "editorial photo", "press photo", "actual event photo"],
        "PRODUCT": ["official product photo", "product launch photo", "real product image", "press image"],
        "STATISTIC": ["chart", "infographic", "data visualization", "relevant editorial photo"],
        "COMPARISON": ["comparison", "side by side", "chart", "editorial photo"],
        "TIMELINE": ["archive photo", "historical photo", "timeline", "before after"],
        "PROCESS": ["diagram", "process illustration", "how it works", "technical illustration"],
        "QUOTE": ["official statement", "press conference photo", "speaker photo", "document"],
        "DOCUMENT": ["official document", "filing", "report", "study document"],
        "CONCEPT": ["concept illustration", "editorial illustration", "scientific illustration", "documentary context"],
        "GENERAL_CONTEXT": ["editorial photo", "documentary photo", "real world photo", "high resolution photo"],
    }
    modifiers = modifier_map.get(visual_type, modifier_map["GENERAL_CONTEXT"])

    _add_unique(queries, prompt, modifiers[0])
    _add_unique(queries, entity, scene_phrase, modifiers[1])
    _add_unique(queries, entity, intent, modifiers[2])
    _add_unique(queries, entity, category, modifiers[3])
    _add_unique(queries, entity, title, "news photo")
    _add_unique(queries, entity, title)
    return queries[:MAX_VISUAL_SEARCH_QUERIES], visual_type


# Import-time policy install is intentional: visual_runtime imports this module
# immediately before it begins searching a scene, so the legacy hook/outro card
# functions can be corrected for the current production run without another API call.
try:
    from visual_policy_runtime import install_visual_card_policy
    install_visual_card_policy()
except Exception:
    pass
