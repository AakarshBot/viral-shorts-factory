"""Runtime visual-search contract for Viral Shorts Factory.

The production path is intentionally simple:
    identify subject -> lock subject -> one exact search -> visual QA

No title, narration, visual intent, action, category or editorial adjective is
allowed to rewrite the locked image-search query.
"""

import html
import re

import visual_retrieval_planner as _planner
from visual_retrieval_planner import *
from visual_query_entities_runtime import extract_slide_search_subjects, lock_visual_subject

for _name in dir(_planner):
    if _name.startswith("_") and not _name.startswith("__"):
        globals()[_name] = getattr(_planner, _name)

_normalise_query = _planner._normalise


def _exact_slide_subject(scene):
    return lock_visual_subject(scene)


def build_deep_queries(scene, video_title="", visual_type=None):
    """Return exactly one query: the locked primary entity."""
    subject = _exact_slide_subject(scene)
    if not subject:
        return [], visual_type or "GENERAL_CONTEXT"
    resolved_type = visual_type
    if not resolved_type:
        try:
            resolved_type = _planner.classify_scene(scene or {}, str((scene or {}).get("sport_or_topic_category", "")))
        except Exception:
            resolved_type = "GENERAL_CONTEXT"
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
