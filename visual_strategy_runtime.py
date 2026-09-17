"""Runtime visual-search contract for Viral Shorts Factory.

Production visual contract:
    identify subject -> lock subject -> one exact search -> visual QA

The locked ``primary_entity`` is returned byte-for-byte apart from surrounding
whitespace. No normaliser, Unicode helper, visual cue, title, narration or
legacy planner is allowed to rewrite the search query.
"""

import html
import re

import visual_retrieval_planner as _planner
from visual_retrieval_planner import *
from visual_query_entities_runtime import extract_slide_search_subjects, lock_visual_subject

for _name in dir(_planner):
    if _name.startswith("_") and not _name.startswith("__"):
        globals()[_name] = getattr(_planner, _name)

_VISUAL_STRATEGY_VERSION = "2026-09-17-v13-immutable-subject"


def _exact_slide_subject(scene):
    """Read the locked entity without passing it through any query normaliser."""
    if not isinstance(scene, dict):
        return ""
    # Do not use planner._normalise(), unicode_normalise(), or any query helper
    # here. Those helpers are allowed to normalise text for comparison, but the
    # emitted search query must remain exactly the locked primary_entity.
    raw = scene.get("primary_entity", "")
    if raw is None:
        return ""
    return str(raw).strip()


def build_deep_queries(scene, video_title="", visual_type=None):
    """Return exactly one query: the immutable locked primary entity."""
    subject = _exact_slide_subject(scene)
    if not subject:
        return [], visual_type or "GENERAL_CONTEXT"

    resolved_type = visual_type
    if not resolved_type:
        try:
            resolved_type = _planner.classify_scene(
                scene or {}, str((scene or {}).get("sport_or_topic_category", ""))
            )
        except Exception:
            resolved_type = "GENERAL_CONTEXT"

    # Hard contract: exactly the original locked string. Never lower-case it,
    # tokenise it, deduplicate it, add context, or rewrite it.
    return [subject], resolved_type


def _add_unique(values, value, *parts):
    """Legacy compatibility helper; it cannot create a second search query."""
    candidate = _planner._normalise(" ".join(str(part) for part in (value,) + parts if str(part).strip()))
    if not candidate or values:
        return False
    values.append(candidate)
    return True


def _scene_phrase(scene=None, *parts, **kwargs):
    """Legacy diagnostic helper; search-query generation must not use it."""
    values = []
    if isinstance(scene, dict):
        value = scene.get("primary_entity")
        if value and str(value).strip():
            values.append(str(value))
    elif scene is not None:
        values.append(str(scene))
    return _planner._normalise(" ".join(values))
