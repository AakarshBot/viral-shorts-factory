"""Runtime visual-search contract for the legacy factory.

Production visual search is derived from the scene being rendered. A slide may
contain several concrete subjects, so search uses the primary entity plus other
clean entities/groups explicitly present in that slide's spoken script. Query
text never includes title/context/noise.
"""

import visual_retrieval_planner as _planner
from visual_retrieval_planner import *
from visual_query_entities_runtime import extract_slide_search_subjects

# Re-export every single-underscore planner helper so future private-helper
# compatibility checks do not fail one attribute at a time.
for _name in dir(_planner):
    if _name.startswith("_") and not _name.startswith("__"):
        globals()[_name] = getattr(_planner, _name)

# Backward-compatible alias used by older diagnostics.
_normalise_query = _planner._normalise


def build_deep_queries(scene, video_title="", visual_type=None):
    """Return clean subjects actually present in the current slide script.

    ``video_title`` is intentionally ignored as query input. The spoken slide
    script may contribute additional concrete person/group subjects, while
    editorial instructions, dates and narrative wording never become queries.
    """
    category = str(scene.get("sport_or_topic_category", "")) if isinstance(scene, dict) else ""
    resolved_type = visual_type or _planner.classify_scene(scene or {}, category)
    return extract_slide_search_subjects(scene), resolved_type


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
    """Build the legacy scene phrase used by compatibility diagnostics."""
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
