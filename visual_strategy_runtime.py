"""Compatibility wrapper for the evidence-first visual retrieval planner.

The legacy diagnostics import private planner helpers directly.  ``import *``
intentionally omits underscore-prefixed names, so this wrapper re-exports the
planner's private helpers explicitly and keeps legacy helper names used by
older offline diagnostics.
"""

import visual_retrieval_planner as _planner
from visual_retrieval_planner import *

# Re-export every single-underscore planner helper so future private-helper
# compatibility checks do not fail one attribute at a time.
for _name in dir(_planner):
    if _name.startswith("_") and not _name.startswith("__"):
        globals()[_name] = getattr(_planner, _name)

# Backward-compatible alias used by older diagnostics.
_normalise_query = _planner._normalise


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
    scene mapping and visual-search text.  Prefer specific search intent and
    the resolved entity, then fall back to visual intent and narration.  Extra
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
