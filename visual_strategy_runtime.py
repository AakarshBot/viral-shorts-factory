"""Runtime visual-search contract for the legacy factory.

The production visual path must search from the scene that is being rendered.
For each slide, ``primary_entity`` is the canonical search subject. Search
queries must never inherit the video title, narration, category, editorial
labels, or contextual prose. A provider/search layer may retry that exact
query once; image de-duplication remains a separate responsibility in the
visual runtime.

The legacy diagnostics import private planner helpers directly, so this module
still re-exports the planner surface and keeps the older helper names intact.
"""

import html
import re

import visual_retrieval_planner as _planner
from visual_retrieval_planner import *

# Re-export every single-underscore planner helper so future private-helper
# compatibility checks do not fail one attribute at a time.
for _name in dir(_planner):
    if _name.startswith("_") and not _name.startswith("__"):
        globals()[_name] = getattr(_planner, _name)

# Backward-compatible alias used by older diagnostics.
_normalise_query = _planner._normalise


_SEARCH_INTERNAL_NOISE = re.compile(
    r"\b(?:editorial_(?:person|event)|news_event|stadium_event|"
    r"speaker_statement|conceptual|general_context)\b",
    flags=re.IGNORECASE,
)


def _exact_slide_subject(scene):
    """Return only the scene's canonical visual subject, preserving its script text."""
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
    """Build the production search query strictly from this slide's subject.

    ``video_title`` is intentionally ignored. ``voiceover``,
    ``specific_search_prompt``, ``visual_intent`` and category are intentionally
    ignored as search-query inputs. The same exact query may be retried once;
    no alternate noisy rewrite is generated here.
    """
    category = str(scene.get("sport_or_topic_category", "")) if isinstance(scene, dict) else ""
    resolved_type = visual_type or _planner.classify_scene(scene or {}, category)
    subject = _exact_slide_subject(scene)
    return ([subject] if subject else []), resolved_type


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
    """Build the legacy scene phrase from structured scene fields.

    Older diagnostics used ``_scene_phrase`` as the small bridge between a
    scene mapping and visual-search text. Prefer specific search intent and
    the resolved entity, then fall back to visual intent and narration. Extra
    positional/keyword text is accepted for compatibility with older callers.
    The result is normalised through the same planner rules used by current
    retrieval queries, so editorial noise cannot leak into search text.
    """
    values = []
    if isinstance(scene, dict):
        for key in (
            "primary_entity",
            "specific_search_prompt",
            "visual_intent",
            "scene_action",
            "scene_context",
            "voiceover",
        ):
            value = scene.get(key)
            if value and str(value).strip():
                values.append(str(value))
    elif scene is not None:
        values.append(str(scene))

    for key in (
        "primary_entity",
        "specific_search_prompt",
        "visual_intent",
        "scene_action",
        "scene_context",
        "voiceover",
    ):
        value = kwargs.get(key)
        if value and str(value).strip():
            values.append(str(value))

    values.extend(str(part) for part in parts if str(part).strip())
    return _planner._normalise(" ".join(values))
