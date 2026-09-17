"""Authoritative visual-search strategy for Viral Shorts Factory.

The factual ``primary_entity`` is immutable. The search query is not: it is a
bounded, deterministic refinement of that entity using only scene-grounded
visual context. This preserves the strictness of the old system without making
literal entity names such as ``India`` the final search query.
"""
import sys
import types

import visual_retrieval_planner as _planner
from visual_retrieval_planner import *

for _name in dir(_planner):
    if _name.startswith("_") and not _name.startswith("__"):
        globals()[_name] = getattr(_planner, _name)

_VISUAL_STRATEGY_VERSION = "2026-09-17-v17-bounded-semantic-retrieval"


def _exact_slide_subject(scene):
    if not isinstance(scene, dict):
        return ""
    raw = scene.get("primary_entity", "")
    return str(raw or "").strip()


def build_deep_queries(scene, video_title="", visual_type=None):
    """Build a tiny, evidence-grounded retrieval ladder.

    The planner owns semantic refinement. No raw narration is ever sent to an
    image provider. The returned queries must retain the resolved visual subject.
    """
    subject = _exact_slide_subject(scene)
    if not subject:
        return [], visual_type or "GENERAL_CONTEXT"
    try:
        queries, resolved_type = _planner.build_deep_queries(scene, video_title, visual_type)
        return queries[:3], resolved_type or visual_type or "GENERAL_CONTEXT"
    except Exception as exc:
        print(f"   [Visual Strategy] planner fallback: {type(exc).__name__}: {exc}", flush=True)
        return [subject], visual_type or "GENERAL_CONTEXT"


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
    return _planner._normalise(" ".join(values))

try:
    from visual_query_lock_runtime import install as _install_visual_query_lock
    _install_visual_query_lock()
except Exception as exc:
    print(f"   [Visual Strategy] Query-lock compatibility install unavailable: {type(exc).__name__}: {exc}", flush=True)

# Prevent legacy startup code from replacing the authoritative planner.
_AuthoritativeModule = type(
    "_AuthoritativeVisualStrategyModule",
    (types.ModuleType,),
    {
        "__setattr__": lambda self, name, value: types.ModuleType.__setattr__(
            self,
            name,
            build_deep_queries if name == "build_deep_queries" else value,
        )
    },
)
_this_module = sys.modules.get(__name__)
if _this_module is not None and not isinstance(_this_module, _AuthoritativeModule):
    _this_module.__class__ = _AuthoritativeModule
