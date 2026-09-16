"""Scene-aware visual strategy for the Shorts factory.

This module is deliberately deterministic and cheap. It decides what kind of
visual a scene needs and builds a small, progressive search ladder. It does
not decide whether an image is relevant; visual_runtime performs strict QA.
"""
import re

VISUAL_TYPES = {
    "PERSON", "EVENT", "PRODUCT", "LOCATION", "STATISTIC", "COMPARISON",
    "TIMELINE", "PROCESS", "QUOTE", "DOCUMENT", "CONCEPT", "GENERAL_CONTEXT",
}


def _clean(text):
    return re.sub(r"\s+", " ", str(text or "")).strip(" ,.-")


def _normalise_query(text):
    """Remove internal labels/noisy punctuation without changing the subject."""
    text = _clean(text)
    text = re.sub(r"\b(?:editorial_)?(?:person|event|product|location|statistic|comparison|timeline|process|quote|document|concept|general_context)\b", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" ,.-")
    return text


def _add_unique(queries, *parts):
    q = _normalise_query(" ".join(_clean(p) for p in parts if _clean(p)))
    if q and q.lower() not in {x.lower() for x in queries}:
        queries.append(q)


def classify_scene(seg, category=""):
    explicit = _clean(seg.get("visual_type", "")).upper().replace("-", "_").replace(" ", "_")
    if explicit in VISUAL_TYPES:
        return explicit

    text = _clean(" ".join([
        str(seg.get("voiceover", "")),
        str(seg.get("visual_intent", "")),
        str(seg.get("specific_search_prompt", "")),
        str(category),
    ])).lower()
    entity = _clean(seg.get("primary_entity", "")).lower()
    intent = _clean(seg.get("visual_intent", "")).lower()

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

    if any(x in text for x in ("timeline", "history", "in ", "years ago", "year-by-year")):
        if re.search(r"\b(19|20)\d{2}\b", text) and any(x in text for x in ("then", "before", "after", "later", "since")):
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
    if entity and len(entity.split()) <= 4 and not any(x in entity for x in ("world cup", "fifa", "nasa")):
        if any(x in text for x in ("player", "president", "ceo", "scientist", "actor", "coach", "founder", "minister", "person")):
            return "PERSON"
    if any(x in text for x in ("concept", "idea", "future", "possibility", "abstract", "theory")):
        return "CONCEPT"
    return "GENERAL_CONTEXT"


def build_deep_queries(seg, video_title="", visual_type=None):
    """Build a short, ranked search ladder instead of a Cartesian query explosion.

    The first queries describe the actual scene. Later queries broaden the
    wording or move to authoritative image indexes. This keeps easy entities
    such as well-known people from triggering dozens of redundant searches and
    therefore dozens of unnecessary semantic-QA candidates.
    """
    entity = _clean(seg.get("primary_entity", ""))
    intent = _normalise_query(seg.get("visual_intent", ""))
    prompt = _normalise_query(seg.get("specific_search_prompt", ""))
    voice = _clean(seg.get("voiceover", ""))
    title = _clean(video_title)
    category = _clean(seg.get("sport_or_topic_category", ""))
    visual_type = visual_type or classify_scene(seg, category)

    queries = []

    if visual_type == "PERSON":
        # People are usually easy to source. Prefer exact scene/entity queries
        # and authoritative image indexes; do not multiply every seed by every
        # modifier (which previously produced ~25 queries for a single person).
        _add_unique(queries, prompt, "photo")
        _add_unique(queries, entity, intent, category, "match photo" if category.lower() in {"sports", "sport", "cricket", "football"} else "news photo")
        _add_unique(queries, entity, title, "photo")
        _add_unique(queries, entity, category, "editorial photo")
        _add_unique(queries, entity, "Wikimedia Commons")
        _add_unique(queries, entity, "official photo")
        return queries[:6], visual_type

    modifier_map = {
        "EVENT": ["official event photo", "editorial photo", "press photo", "actual event photo"],
        "PRODUCT": ["official product photo", "product launch photo", "real product image", "press image"],
        "LOCATION": ["location photo", "aerial photo", "landmark photo", "official map"],
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

    # Progressive tiers: exact scene -> story context -> authoritative/broad.
    _add_unique(queries, prompt, modifiers[0])
    _add_unique(queries, entity, title, modifiers[1])
    _add_unique(queries, entity, intent, modifiers[2])
    _add_unique(queries, entity, category, modifiers[3])
    _add_unique(queries, entity, "Wikimedia Commons")
    _add_unique(queries, entity, title, "news photo")
    return queries[:6], visual_type
