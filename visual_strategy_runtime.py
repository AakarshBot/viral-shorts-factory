"""Authoritative, genre-agnostic visual-search strategy.

The strategy layer delegates semantic cleanup, subject resolution and query
construction to the shared generic guard. It contains no domain-specific
entity tables or query padding rules.
"""
from __future__ import annotations

import sys
import types

import visual_retrieval_planner as _planner
from visual_retrieval_planner import *  # noqa: F401,F403
from visual_semantic_guard_runtime import (
    MAX_QUERY_WORDS,
    build_query_ladder,
    clean_text,
    infer_role,
    prepare_scene,
    resolve_subject,
)

for _name in dir(_planner):
    if _name.startswith("_") and not _name.startswith("__"):
        globals()[_name] = getattr(_planner, _name)

_VISUAL_STRATEGY_VERSION = "2026-09-18-v2-generic-semantic-guard"


def _scene_category(scene, category=""):
    if str(category or "").strip():
        return str(category).strip()
    if isinstance(scene, dict):
        for key in ("sport_or_topic_category", "topic_category", "domain", "topic"):
            value = clean_text(scene.get(key, ""))
            if value:
                return value
    return ""


def _prepare(scene, video_title=""):
    prepared = prepare_scene(scene, video_title)
    category = _scene_category(prepared)
    if category and not prepared.get("sport_or_topic_category"):
        prepared["sport_or_topic_category"] = category
    return prepared


def _authoritative_role(prepared, resolved_type=""):
    """Prefer strong factual cues in the resolved subject over stale metadata."""
    subject = clean_text(prepared.get("primary_entity", "")) if isinstance(prepared, dict) else ""
    role = infer_role({"primary_entity": subject}) if subject else "GENERAL_CONTEXT"
    return role if role != "GENERAL_CONTEXT" else (resolved_type or "GENERAL_CONTEXT")


def classify_scene(scene, category=""):
    """Expose the generic semantic role classifier."""
    prepared = _prepare(scene)
    resolved = resolve_subject(prepared, "").get("visual_type") or "GENERAL_CONTEXT"
    return _authoritative_role(prepared, resolved)


def build_scene_visual_brief(scene, video_title="", category=""):
    """Return the generic guarded visual brief expected by runtime callers."""
    prepared = _prepare(scene, video_title)
    if category:
        prepared["sport_or_topic_category"] = category
    resolution = resolve_subject(prepared, video_title)
    resolved_type = _authoritative_role(prepared, resolution["visual_type"])
    return {
        "subject": resolution["subject"],
        "visual_type": resolved_type,
        "scene_action": "",
        "scene_context": clean_text(prepared.get("visual_context", "")),
        "scene_index": clean_text(prepared.get("scene_index", prepared.get("scene_number", ""))),
        "factual_entity": resolution["factual_entity"],
        "base_type": "GENERAL_CONTEXT",
        "scene_role": resolved_type,
        "domain": _scene_category(prepared, category),
        "confidence": resolution["confidence"],
    }


def build_deep_queries(scene, video_title="", visual_type=None):
    """Build only grounded, non-padded search queries."""
    if not isinstance(scene, dict):
        return [], visual_type or "GENERAL_CONTEXT"
    prepared = _prepare(scene, video_title)
    queries, resolved_type, _resolution = build_query_ladder(prepared, video_title)
    resolved_type = _authoritative_role(prepared, resolved_type)
    return queries[:3], (visual_type or resolved_type or "GENERAL_CONTEXT")


build_deep_queries._authoritative_locked_subject_planner = True


def _exact_slide_subject(scene):
    """Return the original factual entity for compatibility/provenance."""
    if not isinstance(scene, dict):
        return ""
    return clean_text(scene.get("primary_entity", ""))


def _add_unique(values, value, *parts):
    candidate = _planner._normalise(" ".join(str(part) for part in (value,) + parts if str(part).strip()))
    if not candidate or len(candidate.split()) > MAX_QUERY_WORDS or candidate in values:
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


class _AuthoritativeVisualStrategyModule(types.ModuleType):
    def __setattr__(self, name, value):
        if name == "build_deep_queries":
            return types.ModuleType.__setattr__(self, name, build_deep_queries)
        return types.ModuleType.__setattr__(self, name, value)


_this_module = sys.modules.get(__name__)
if _this_module is not None and not isinstance(_this_module, _AuthoritativeVisualStrategyModule):
    _this_module.__class__ = _AuthoritativeVisualStrategyModule