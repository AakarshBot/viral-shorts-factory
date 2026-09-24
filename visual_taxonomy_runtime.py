"""Practical visual-search taxonomy and retrieval policy for Viral Shorts Factory.

The factory can publish stories from technology, AI, science, space, business,
finance, world news, India, gaming, sports and other categories. This module
keeps visual *genre* separate from factual entity type so the retrieval stack
can choose the right query language and provider order without multiplying
identity classes.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


VISUAL_GENRES = {
    "PERSON_PORTRAIT",
    "PERSON_ACTION",
    "ORG_BRANDING",
    "ORG_HEADQUARTERS",
    "TEAM_BRANDING",
    "TEAM_ACTION",
    "PRODUCT_PHOTO",
    "PRODUCT_LAUNCH",
    "VEHICLE",
    "ANIMAL",
    "FOOD",
    "LANDMARK",
    "ARCHITECTURE",
    "PLACE_SCENE",
    "EVENT_SCENE",
    "SPORTS_ACTION",
    "SPORTS_MATCH",
    "TROPHY_AWARD",
    "DOCUMENT",
    "SCREENSHOT_UI",
    "CHART_GRAPH",
    "MAP",
    "DIAGRAM",
    "PROCESS",
    "SCIENCE_VISUAL",
    "SPACE_VISUAL",
    "NATURE_LANDSCAPE",
    "HISTORICAL_ARTIFACT",
    "HISTORICAL_PHOTO",
    "MEDIA_ARTWORK",
    "MONEY_CURRENCY",
    "FLAG_SYMBOL",
    "GENERAL_PHOTO",
    "GENERAL_CONTEXT",
}


@dataclass(frozen=True)
class VisualGenrePolicy:
    preferred_sources: tuple[str, ...]
    query_hints: tuple[str, ...] = ()
    trusted_source_kinds: tuple[str, ...] = ()
    allow_ai: bool = False
    description: str = ""


_POLICIES = {
    "PERSON_PORTRAIT": VisualGenrePolicy(
        ("Wikipedia", "Commons", "DDG", "Openverse", "Pexels", "Unsplash", "Pixabay"),
        ("portrait",),
        ("Wikipedia:person", "Commons:person"),
        False,
        "A specific person's portrait or headshot.",
    ),
    "PERSON_ACTION": VisualGenrePolicy(
        ("Commons", "DDG", "Openverse", "Wikipedia", "Pexels", "Unsplash", "Pixabay"),
        (),
        ("Wikipedia:person", "Commons:person"),
        False,
        "A specific person performing an action or appearing in a real scene.",
    ),
    "ORG_BRANDING": VisualGenrePolicy(
        ("Commons", "DDG", "Wikipedia", "Openverse", "Pexels", "Unsplash", "Pixabay"),
        ("logo",),
        ("Commons:branding",),
        False,
        "Logo, crest, badge, emblem, seal or clearly identifiable organisational branding.",
    ),
    "ORG_HEADQUARTERS": VisualGenrePolicy(
        ("Commons", "DDG", "Pexels", "Unsplash", "Openverse", "Pixabay"),
        ("headquarters",),
        (),
        False,
        "Office, headquarters, campus, venue or official organisational setting.",
    ),
    "TEAM_BRANDING": VisualGenrePolicy(
        ("Commons", "DDG", "Openverse", "Wikipedia", "Pexels", "Unsplash", "Pixabay"),
        ("logo", "crest", "jersey"),
        ("Commons:branding",),
        False,
        "Team logo, crest, jersey or other team identity asset.",
    ),
    "TEAM_ACTION": VisualGenrePolicy(
        ("Commons", "DDG", "Pexels", "Unsplash", "Openverse", "Pixabay"),
        (),
        ("Commons:team",),
        False,
        "A team or group in a real action/celebration setting.",
    ),
    "PRODUCT_PHOTO": VisualGenrePolicy(
        ("Commons", "DDG", "Openverse", "Pexels", "Unsplash", "Pixabay"),
        ("product",),
        (),
        False,
        "Specific consumer, industrial or technology product.",
    ),
    "PRODUCT_LAUNCH": VisualGenrePolicy(
        ("Commons", "DDG", "Pexels", "Unsplash", "Openverse", "Pixabay"),
        ("launch",),
        (),
        False,
        "A product launch, unveiling or announcement scene.",
    ),
    "VEHICLE": VisualGenrePolicy(
        ("Commons", "Pexels", "Unsplash", "DDG", "Openverse", "Pixabay"),
        (),
        (),
        False,
        "Specific car, aircraft, spacecraft, train, motorcycle or other vehicle.",
    ),
    "ANIMAL": VisualGenrePolicy(
        ("Pexels", "Unsplash", "Openverse", "DDG", "Commons", "Pixabay"),
        (),
        (),
        False,
        "Specific animal or animal species.",
    ),
    "FOOD": VisualGenrePolicy(
        ("Pexels", "Unsplash", "Openverse", "DDG", "Pixabay", "Commons"),
        (),
        (),
        False,
        "Specific food, dish, drink or ingredient.",
    ),
    "LANDMARK": VisualGenrePolicy(
        ("Commons", "DDG", "Pexels", "Unsplash", "Openverse", "Pixabay"),
        ("landmark",),
        (),
        False,
        "Named landmark or distinctive place.",
    ),
    "ARCHITECTURE": VisualGenrePolicy(
        ("Commons", "Pexels", "Unsplash", "DDG", "Openverse", "Pixabay"),
        ("building",),
        (),
        False,
        "Building, structure or interior architecture.",
    ),
    "PLACE_SCENE": VisualGenrePolicy(
        ("Commons", "Pexels", "Unsplash", "DDG", "Openverse", "Pixabay"),
        (),
        (),
        False,
        "Street, neighbourhood, cityscape, venue or general geographic setting.",
    ),
    "EVENT_SCENE": VisualGenrePolicy(
        ("Commons", "DDG", "Openverse", "Pexels", "Unsplash", "Pixabay"),
        (),
        (),
        False,
        "Named event, conference, ceremony, summit, festival or launch scene.",
    ),
    "SPORTS_ACTION": VisualGenrePolicy(
        ("Commons", "DDG", "Pexels", "Unsplash", "Openverse", "Pixabay"),
        (),
        ("Commons:sports",),
        False,
        "A sport action such as batting, bowling, scoring, racing or competing.",
    ),
    "SPORTS_MATCH": VisualGenrePolicy(
        ("Commons", "DDG", "Openverse", "Pexels", "Unsplash", "Pixabay"),
        (),
        ("Commons:sports",),
        False,
        "A named match, fixture or competition scene.",
    ),
    "TROPHY_AWARD": VisualGenrePolicy(
        ("Commons", "DDG", "Openverse", "Wikipedia", "Pexels", "Unsplash", "Pixabay"),
        ("trophy",),
        (),
        False,
        "Trophy, medal, award or championship object.",
    ),
    "DOCUMENT": VisualGenrePolicy(
        ("Commons", "DDG", "Openverse", "Wikipedia", "Pixabay", "Pexels", "Unsplash"),
        (),
        (),
        False,
        "Document, filing, paper, report or archival page.",
    ),
    "SCREENSHOT_UI": VisualGenrePolicy(
        ("Commons", "DDG", "Openverse", "Pixabay", "Pexels", "Unsplash"),
        ("screenshot",),
        (),
        False,
        "Software interface, website, app or product screenshot.",
    ),
    "CHART_GRAPH": VisualGenrePolicy(
        ("Commons", "DDG", "Openverse", "Pixabay", "Pexels", "Unsplash"),
        ("chart",),
        (),
        False,
        "Chart, graph, data visualisation or statistics graphic.",
    ),
    "MAP": VisualGenrePolicy(
        ("Commons", "DDG", "Openverse", "Wikipedia", "Pixabay", "Pexels", "Unsplash"),
        ("map",),
        (),
        False,
        "Map, route, geographic diagram or location map.",
    ),
    "DIAGRAM": VisualGenrePolicy(
        ("Commons", "DDG", "Openverse", "Wikipedia", "Pixabay", "Pexels", "Unsplash"),
        ("diagram",),
        (),
        False,
        "Diagram, labelled technical illustration or explanatory graphic.",
    ),
    "PROCESS": VisualGenrePolicy(
        ("Commons", "Openverse", "DDG", "Pexels", "Unsplash", "Pixabay"),
        ("process",),
        (),
        True,
        "Process, workflow, mechanism or how-it-works visual.",
    ),
    "SCIENCE_VISUAL": VisualGenrePolicy(
        ("Commons", "Openverse", "DDG", "Wikipedia", "Pexels", "Unsplash", "Pixabay"),
        (),
        (),
        True,
        "Scientific object, laboratory scene, scientific phenomenon or research visual.",
    ),
    "SPACE_VISUAL": VisualGenrePolicy(
        ("Commons", "Openverse", "DDG", "Pexels", "Unsplash", "Pixabay"),
        ("space",),
        (),
        True,
        "Planet, spacecraft, telescope, launch or astronomical visual.",
    ),
    "NATURE_LANDSCAPE": VisualGenrePolicy(
        ("Pexels", "Unsplash", "Commons", "Openverse", "DDG", "Pixabay"),
        (),
        (),
        False,
        "Landscape, weather, environment or natural scene.",
    ),
    "HISTORICAL_ARTIFACT": VisualGenrePolicy(
        ("Commons", "Wikipedia", "Openverse", "DDG", "Pixabay", "Pexels", "Unsplash"),
        ("artifact",),
        (),
        False,
        "Historical object, artefact, relic or museum item.",
    ),
    "HISTORICAL_PHOTO": VisualGenrePolicy(
        ("Commons", "Wikipedia", "Openverse", "DDG", "Pexels", "Unsplash", "Pixabay"),
        ("historical",),
        (),
        False,
        "Archival or historical photograph.",
    ),
    "MEDIA_ARTWORK": VisualGenrePolicy(
        ("Commons", "Openverse", "DDG", "Wikipedia", "Pexels", "Unsplash", "Pixabay"),
        (),
        (),
        False,
        "Film/TV artwork, poster, album art, artwork or other media asset.",
    ),
    "MONEY_CURRENCY": VisualGenrePolicy(
        ("Commons", "Openverse", "DDG", "Wikipedia", "Pexels", "Unsplash", "Pixabay"),
        ("currency",),
        ("Commons:currency",),
        False,
        "Banknote, coin, currency symbol or financial visual.",
    ),
    "FLAG_SYMBOL": VisualGenrePolicy(
        ("Commons", "Wikipedia", "Openverse", "DDG", "Pexels", "Unsplash", "Pixabay"),
        ("flag",),
        ("Commons:symbol",),
        False,
        "National flag, state flag or recognised symbol.",
    ),
    "GENERAL_PHOTO": VisualGenrePolicy(
        ("Pexels", "Unsplash", "Openverse", "DDG", "Commons", "Pixabay"),
        (),
        (),
        False,
        "Real-world photographic context with no more specific genre.",
    ),
    "GENERAL_CONTEXT": VisualGenrePolicy(
        ("Openverse", "Pexels", "Unsplash", "Commons", "DDG", "Pixabay"),
        (),
        (),
        True,
        "Fallback explanatory context.",
    ),
}


_ALIASES = {
    "LOGO": "ORG_BRANDING",
    "BRAND_LOGO": "ORG_BRANDING",
    "PORTRAIT": "PERSON_PORTRAIT",
    "HEADSHOT": "PERSON_PORTRAIT",
    "PERSON": "PERSON_PORTRAIT",
    "ORGANIZATION": "ORG_HEADQUARTERS",
    "ORG": "ORG_HEADQUARTERS",
    "TEAM": "TEAM_ACTION",
    "PRODUCT": "PRODUCT_PHOTO",
    "LOCATION": "PLACE_SCENE",
    "LANDMARK": "LANDMARK",
    "EVENT": "EVENT_SCENE",
    "SPORT": "SPORTS_ACTION",
    "SPORTS": "SPORTS_ACTION",
    "STATISTIC": "CHART_GRAPH",
    "COMPARISON": "CHART_GRAPH",
    "TIMELINE": "DIAGRAM",
    "QUOTE": "PERSON_PORTRAIT",
    "DOCUMENT": "DOCUMENT",
    "CONCEPT": "SCIENCE_VISUAL",
    "PROCESS": "PROCESS",
}


def _clean(value: object) -> str:
    text = str(value or "").replace("_", " ").replace("-", " ")
    text = re.sub(r"\\s+", " ", text).strip().casefold()
    return text


def _tokens(value: object) -> set[str]:
    return {t for t in re.findall(r"[\w]+", _clean(value), flags=re.UNICODE) if t}


def normalize_visual_genre(value: str) -> str:
    key = _clean(value).upper().replace(" ", "_")
    if key in VISUAL_GENRES:
        return key
    return _ALIASES.get(key, "GENERAL_CONTEXT")


def get_visual_genre_policy(genre: str) -> VisualGenrePolicy:
    return _POLICIES.get(normalize_visual_genre(genre), _POLICIES["GENERAL_CONTEXT"])


def preferred_sources(genre: str) -> tuple[str, ...]:
    return get_visual_genre_policy(genre).preferred_sources


def genre_query_hints(genre: str) -> tuple[str, ...]:
    return get_visual_genre_policy(genre).query_hints


def genre_allows_ai(genre: str) -> bool:
    return get_visual_genre_policy(genre).allow_ai


def _has(tokens: set[str], *values: str) -> bool:
    return any(value.casefold() in tokens for value in values)


_PERSON_ACTION_PHRASES = (
    "press conference",
    "media briefing",
    "press briefing",
    "interview",
    "speaking",
    "speaking at",
    "on stage",
    "podium",
    "media interaction",
)


def _contains_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    normalized = _clean(text)
    return any(phrase in normalized for phrase in phrases)


def classify_visual_genre(scene: dict, subject: str = "", visual_type: str = "") -> str:
    scene = scene if isinstance(scene, dict) else {}
    explicit = normalize_visual_genre(scene.get("visual_genre", ""))
    if scene.get("visual_genre") and explicit in VISUAL_GENRES:
        return explicit

    intent = _clean(scene.get("factual_visual_intent") or scene.get("visual_intent", ""))
    prompt = _clean(scene.get("specific_search_prompt") or scene.get("factual_search_prompt", ""))
    context = _clean(scene.get("visual_context", ""))
    voice = _clean(scene.get("voiceover", ""))
    text = " ".join([_clean(subject), intent, prompt, context, voice])
    words = _tokens(text)
    subject_words = _tokens(subject)
    vt = str(visual_type or scene.get("visual_type", "")).upper()

    # Specific asset identities must outrank generic presentation context. A logo
    # mentioned as appearing "on screen" is still branding, not a UI screenshot.
    if _has(words, "logo", "logos", "crest", "emblem", "badge", "seal", "branding", "brand mark"):
        if vt in {"ORGANIZATION", "EVENT"} or _has(words, "team", "club", "federation", "company", "brand"):
            return "ORG_BRANDING" if not _has(words, "team", "club", "squad") else "TEAM_BRANDING"
        return "ORG_BRANDING"
    if _has(words, "screenshot", "screen", "interface", "dashboard", "app", "website", "ui"):
        return "SCREENSHOT_UI"
    if _has(words, "document", "documents", "report", "filing", "filings", "pdf", "paperwork", "contract"):
        return "DOCUMENT"
    if _has(words, "chart", "charts", "graph", "graphs", "statistics", "statistic", "data", "infographic", "visualisation", "visualization"):
        return "CHART_GRAPH"
    if _has(words, "map", "maps", "route", "geographic", "geography", "location map"):
        return "MAP"
    if _has(words, "diagram", "schematic", "blueprint", "flowchart"):
        return "DIAGRAM"
    if _has(words, "flag", "flags", "national flag", "symbol"):
        return "FLAG_SYMBOL"
    if _has(words, "currency", "banknote", "banknotes", "coin", "coins", "rupee", "dollar", "euro", "pound", "yen"):
        return "MONEY_CURRENCY"
    if _has(words, "trophy", "trophies", "medal", "award", "awards", "cup"):
        return "TROPHY_AWARD"
    if _has(words, "poster", "album art", "album cover", "film poster", "movie poster", "artwork", "cover art"):
        return "MEDIA_ARTWORK"


    # A person's requested visual intent outranks broad narration context. A
    # press conference is an action/event scene, while an explicit portrait or
    # headshot request remains a portrait.
    if vt == "PERSON":
        if _has(_tokens(intent), "portrait", "headshot"):
            return "PERSON_PORTRAIT"
        if _contains_phrase(intent, _PERSON_ACTION_PHRASES):
            return "PERSON_ACTION"
        if _contains_phrase(text, _PERSON_ACTION_PHRASES):
            return "PERSON_ACTION"

    sports_action = _has(
        words,
        "batting", "bowling", "batsman", "batter", "wicket", "goal", "scoring",
        "scores", "scored", "shooting", "dribbling", "tackling", "racing",
        "runner", "running", "cycling", "serving", "swimming", "boxing",
        "celebration", "celebrating", "lifting", "playing", "match action",
    )
    sports_match = _has(
        words,
        "match", "fixture", "game", "vs", "versus", "scoreline", "innings",
        "semi final", "quarter final", "final",
    )
    if vt == "PERSON" and (sports_action or _has(words, "interview", "speaking", "speaks", "appearing", "on stage")):
        return "PERSON_ACTION"

    team_terms = _has(words, "team", "squad", "club", "xi", "eleven")
    if sports_action and (vt == "ORGANIZATION" or team_terms):
        return "TEAM_ACTION"
    if sports_action and (vt in {"PERSON", "EVENT"} or _has(words, "player", "athlete")):
        return "SPORTS_ACTION"
    if sports_match:
        return "SPORTS_MATCH"
    if team_terms and _has(words, "logo", "crest", "jersey", "kit", "badge"):
        return "TEAM_BRANDING"

    if _has(words, "spacecraft", "rocket", "satellite", "astronaut", "planet", "moon", "mars", "jupiter", "asteroid", "telescope", "orbit"):
        return "SPACE_VISUAL"
    if _has(words, "laboratory", "lab", "microscope", "dna", "cell", "molecule", "atom", "particle", "fossil", "experiment", "research"):
        return "SCIENCE_VISUAL"
    if _has(words, "process", "workflow", "pipeline", "how it works", "steps", "procedure", "mechanism"):
        return "PROCESS"

    if _has(words, "animal", "dog", "cat", "tiger", "lion", "elephant", "bird", "whale", "shark", "snake", "species", "wildlife"):
        return "ANIMAL"
    if _has(words, "food", "dish", "meal", "recipe", "cuisine", "restaurant", "ingredient", "drink", "beverage"):
        return "FOOD"
    if _has(words, "car", "cars", "vehicle", "motorcycle", "bike", "truck", "train", "aircraft", "airplane", "jet", "helicopter", "ship"):
        return "VEHICLE"

    if _has(words, "historical artifact", "artefact", "relic", "museum object", "ancient object"):
        return "HISTORICAL_ARTIFACT"
    if _has(words, "historical photo", "historic photo", "archival photo", "archive photo", "old photograph"):
        return "HISTORICAL_PHOTO"

    if _has(words, "landmark", "monument", "statue", "bridge", "tower", "temple", "mosque", "church", "palace", "fort"):
        return "LANDMARK"
    if vt == "ORGANIZATION" and _has(words, "headquarters", "office", "campus"):
        return "ORG_HEADQUARTERS"
    if _has(words, "architecture", "building", "buildings", "interior", "office", "headquarters", "campus"):
        return "ARCHITECTURE"

    if vt == "PRODUCT":
        return "PRODUCT_LAUNCH" if _has(words, "launch", "launched", "unveil", "unveiled", "release", "released") else "PRODUCT_PHOTO"
    if vt == "PERSON":
        return "PERSON_ACTION" if sports_action or _has(words, "interview", "speaking", "speaks", "appearing", "on stage") else "PERSON_PORTRAIT"
    if vt == "ORGANIZATION":
        if team_terms:
            return "GENERAL_PHOTO"
        if _has(words, "headquarters", "office", "campus"):
            return "ORG_HEADQUARTERS"
        if _contains_phrase(intent, (
            "product launch", "launch", "press conference", "conference",
            "summit", "meeting", "signing", "unveiling", "unveil",
        )):
            return "EVENT_SCENE"
        if _has(words, "official", "logo", "brand"):
            return "ORG_BRANDING"
        return "GENERAL_PHOTO"
    if vt == "LOCATION":
        return "LANDMARK" if _has(words, "landmark", "monument") else ("ARCHITECTURE" if _has(words, "building", "stadium", "arena") else "PLACE_SCENE")
    if vt == "EVENT":
        return "SPORTS_MATCH" if sports_match else "EVENT_SCENE"
    if vt in {"CONCEPT", "PROCESS"}:
        return "SCIENCE_VISUAL" if _has(words, "science", "scientific", "technology", "technical") else "PROCESS"

    if subject_words:
        if _has(subject_words, "water", "ocean", "mountain", "forest", "desert", "volcano", "river", "beach", "sunset", "storm", "weather", "rain"):
            return "NATURE_LANDSCAPE"

    return "GENERAL_CONTEXT"


def genre_acceptance_rule(genre: str) -> str:
    return get_visual_genre_policy(genre).description


__all__ = [
    "VISUAL_GENRES",
    "VisualGenrePolicy",
    "classify_visual_genre",
    "genre_acceptance_rule",
    "genre_allows_ai",
    "genre_query_hints",
    "get_visual_genre_policy",
    "normalize_visual_genre",
    "preferred_sources",
]
