"""Authoritative, genre-agnostic visual-search strategy.

The factual ``primary_entity`` remains provenance. The visual retrieval query is
resolved by the shared semantic planner into a precise subject, then expanded
only through a bounded identity-preserving ladder. The strategy layer does not
contain domain-specific entity tables or branches.
"""
from __future__ import annotations

import sys
import types

import visual_retrieval_planner as _planner
from visual_retrieval_planner import *  # noqa: F401,F403 - legacy public surface

for _name in dir(_planner):
    if _name.startswith("_") and not _name.startswith("__"):
        globals()[_name] = getattr(_planner, _name)

_VISUAL_STRATEGY_VERSION = "2026-09-18-v1-generic-semantic-strategy"


def _exact_slide_subject(scene):
    """Return only the immutable factual entity for provenance/debugging."""
    if not isinstance(scene, dict):
        return ""
    return str(scene.get("primary_entity", "") or "").strip()


def _scene_category(scene, category=""):
    """Use explicit caller/scene domain metadata without guessing a genre."""
    if str(category or "").strip():
        return str(category).strip()
    if isinstance(scene, dict):
        for key in ("sport_or_topic_category", "topic_category", "domain", "topic"):
            value = str(scene.get(key, "") or "").strip()
            if value:
                return value
    return ""


def classify_scene(scene, category=""):
    """Expose the shared semantic classification contract."""
    return _planner.classify_scene(scene, _scene_category(scene, category))


def build_deep_queries(scene, video_title="", visual_type=None):
    """Build the bounded semantic retrieval ladder.

    There is deliberately no raw-entity fallback here. A planner failure is a
    retrieval contract failure and must be visible to the caller rather than
    silently degrading to a vague query.
    """
    if not isinstance(scene, dict):
        return [], visual_type or "GENERAL_CONTEXT"

    queries, resolved_type = _planner.build_deep_queries(
        scene,
        video_title,
        visual_type,
    )
    return queries[:MAX_VISUAL_SEARCH_QUERIES], resolved_type or visual_type or "GENERAL_CONTEXT"


build_deep_queries._authoritative_locked_subject_planner = True


def _add_unique(values, value, *parts):
    candidate = _planner._normalise(
        " ".join(str(part) for part in (value,) + parts if str(part).strip())
    )
    if not candidate or candidate in values:
        return False
    values.append(candidate)
    return True


def _scene_phrase(scene=None, *parts, **kwargs):
    values = []
    if isinstance(scene, dict):
        value = scene.get("primary_entity")
        if value and str(value).strip():
            values.append(str(value))
    elif scene is not None:
        values.append(str(scene))
    values.extend(str(part) for part in parts if str(part).strip())
    return _planner._normalise(" ".join(values))


try:
    from visual_query_lock_runtime import install as _install_visual_query_lock
    _install_visual_query_lock()
except Exception as exc:
    print(
        f"   [Visual Strategy] Query-lock compatibility install unavailable: {type(exc).__name__}: {exc}",
        flush=True,
    )


# Prevent legacy startup code from replacing the authoritative planner.
class _AuthoritativeVisualStrategyModule(types.ModuleType):
    def __setattr__(self, name, value):
        if name == "build_deep_queries":
            return types.ModuleType.__setattr__(self, name, build_deep_queries)
        return types.ModuleType.__setattr__(self, name, value)


_this_module = sys.modules.get(__name__)
if _this_module is not None and not isinstance(_this_module, _AuthoritativeVisualStrategyModule):
    _this_module.__class__ = _AuthoritativeVisualStrategyModule
