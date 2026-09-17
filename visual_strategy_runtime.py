"""Runtime visual-search contract for the legacy factory.

The production visual path searches subjects extracted from the current slide's
cut script. Each returned query is a clean subject phrase. Video titles,
context prose, scene instructions, and editorial adjectives are never appended
to a query.

The legacy diagnostics import private planner helpers directly, so this module
still re-exports the planner surface and keeps the older helper names intact.
"""

import html
import re

import visual_retrieval_planner as _planner
from visual_retrieval_planner import *
from visual_query_entities_runtime import extract_slide_search_subjects

# Re-export every single-underscore planner helper so future private-helper
# compatibility checks do not fail one attribute at a time.
for _name in dir(_planner):
    if _name.startswith("_") and not _name.startswith("__"):
        globals()[_name] = getattr(_planner, _name)

_normalise_query = _planner._normalise

_SEARCH_INTERNAL_NOISE = re.compile(
    r"\b(?:editorial_(?:person|event)|news_event|stadium_event|"
    r"speaker_statement|conceptual|general_context)\b",
    flags=re.IGNORECASE,
)


def _exact_slide_subject(scene):
    """Return only the scene's canonical primary subject."""
    if not isinstance(scene, dict):
        return ""
    value = html.unescape(str(scene.get("primary_entity", "") or ""))
    value = value.replace("\u200b", " ")
    value = re.sub(r"\s+", " ", value).strip(" ,.-:;|\"'")
    value = _SEARCH_INTERNAL_NOISE.sub(" ", value)
    value = re.sub(r"\s+", " ", value).strip(" ,.-:;|\"'")
    if not value:
        return ""
    words = value.split()
    if len(words) % 2 == 0:
        half = len(words) // 2
        if [w.casefold() for w in words[:half]] == [w.casefold() for w in words[half:]]:
            value = " ".join(words[:half])
    return value


def build_deep_queries(scene, video_title="", visual_type=None):
    """Return exact subject queries derived from the current slide script.

    The primary entity is always first. Additional queries are concrete names,
    places, events, organizations, or groups actually present in the cut
    slide's voiceover. No title/context rewrite is permitted.
    """
    category = str(scene.get("sport_or_topic_category", "")) if isinstance(scene, dict) else ""
    resolved_type = visual_type or _planner.classify_scene(scene or {}, category)
    subjects = extract_slide_search_subjects(scene or {})
    if not subjects:
        primary = _exact_slide_subject(scene)
        subjects = [primary] if primary else []
    return subjects[:3], resolved_type


def _add_unique(values, value, *parts):
    """Append a normalised query once and report whether it was added."""
    candidate = _planner._normalise(" ".join(str(part) for part in (value,) + parts if str(part).strip()))
    if not candidate:
        return False
    existing = {str(item).strip().lower() for item in values}
    if candidate.lower() in existing:
        return False
    values.append(candidate)
    return True


def _scene_phrase(scene=None, *parts, **kwargs):
    """Build the legacy scene phrase for compatibility diagnostics only."""
    values = []
    if isinstance(scene, dict):
        for key in (
            "primary_entity", "specific_search_prompt", "visual_intent",
            "scene_action", "scene_context", "voiceover",
        ):
            value = scene.get(key)
            if value and str(value).strip():
                values.append(str(value))
    elif scene is not None:
        values.append(str(scene))
    for key in (
        "primary_entity", "specific_search_prompt", "visual_intent",
        "scene_action", "scene_context", "voiceover",
    ):
        value = kwargs.get(key)
        if value and str(value).strip():
            values.append(str(value))
    values.extend(str(part) for part in parts if str(part).strip())
    return _planner._normalise(" ".join(values))
