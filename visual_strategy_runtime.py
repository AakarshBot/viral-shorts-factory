"""Scene-aware visual strategy for the Shorts factory.

This module is deliberately deterministic and cheap. It decides what kind of
visual a scene needs and builds progressively deeper search queries. It does
not decide whether an image is relevant; visual_runtime performs the strict
pixel-level QA.
"""
import re

VISUAL_TYPES = {
    "PERSON", "EVENT", "PRODUCT", "LOCATION", "STATISTIC", "COMPARISON",
    "TIMELINE", "PROCESS", "QUOTE", "DOCUMENT", "CONCEPT", "GENERAL_CONTEXT",
}


def _clean(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


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
    entity = _clean(seg.get("primary_entity", ""))
    intent = _clean(seg.get("visual_intent", ""))
    prompt = _clean(seg.get("specific_search_prompt", ""))
    voice = _clean(seg.get("voiceover", ""))
    title = _clean(video_title)
    category = _clean(seg.get("sport_or_topic_category", ""))
    visual_type = visual_type or classify_scene(seg, category)

    seeds = [
        prompt,
        f"{entity} {intent}",
        f"{entity} {title}",
        f"{entity} {voice[:180]}",
        f"{entity} {category} {intent}",
    ]

    modifiers = {
        "PERSON": ["official portrait", "press photo", "match/event photo", "Getty-style editorial photo"],
        "EVENT": ["official event photo", "editorial photo", "press photo", "actual event footage still"],
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
    }.get(visual_type, ["editorial photo", "documentary photo", "high resolution photo"])

    queries = []
    for seed in seeds:
        seed = _clean(seed)
        if not seed:
            continue
        for modifier in modifiers:
            q = _clean(f"{seed} {modifier}")
            if q and q not in queries:
                queries.append(q)

    # A few deliberately broader searches are useful when the LLM's first
    # visual prompt is too specific to match a provider's index.
    for q in (
        f"{entity} {category} real photo",
        f"{entity} {title} news photo",
        f"{entity} Wikimedia Commons",
        f"{entity} Wikipedia image",
        f"{entity} official photo",
    ):
        q = _clean(q)
        if q and q not in queries:
            queries.append(q)

    return queries[:28], visual_type
