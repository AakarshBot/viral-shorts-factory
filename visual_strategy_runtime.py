"""Runtime visual-search contract for Viral Shorts Factory.

Production visual contract:
    identify subject -> lock subject -> one exact search -> visual QA

The locked ``primary_entity`` is returned byte-for-byte apart from surrounding
whitespace. No normaliser, Unicode helper, visual cue, title, narration or
legacy planner is allowed to rewrite the search query.
"""

import sys
import types

import visual_retrieval_planner as _planner
from visual_retrieval_planner import *

for _name in dir(_planner):
    if _name.startswith("_") and not _name.startswith("__"):
        globals()[_name] = getattr(_planner, _name)

_VISUAL_STRATEGY_VERSION = "2026-09-17-v16-immutable-planner-binding"


def _exact_slide_subject(scene):
    """Read the locked entity without passing it through any query normaliser."""
    if not isinstance(scene, dict):
        return ""
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

    return [subject], resolved_type


build_deep_queries._authoritative_locked_subject_planner = True


def _add_unique(values, value, *parts):
    """Legacy compatibility helper; never used by production search planning."""
    candidate = _planner._normalise(
        " ".join(str(part) for part in (value,) + parts if str(part).strip())
    )
    if not candidate or candidate in values:
        return False
    values.append(candidate)
    return True


def _scene_phrase(scene=None, *parts, **kwargs):
    """Legacy diagnostic helper; production query generation never uses it."""
    values = []
    if isinstance(scene, dict):
        value = scene.get("primary_entity")
        if value and str(value).strip():
            values.append(str(value))
    elif scene is not None:
        values.append(str(scene))
    return _planner._normalise(" ".join(values))


# Install the compatibility/runtime guard after the authoritative function exists.
try:
    from visual_query_lock_runtime import install as _install_visual_query_lock
    _install_visual_query_lock()
except Exception as exc:
    print(
        f"   [Visual Strategy] Query lock auto-install unavailable: {type(exc).__name__}: {exc}",
        flush=True,
    )
finally:
    globals()["build_deep_queries"] = build_deep_queries


# Some historical startup code attempted to rebind this module's
# ``build_deep_queries`` back to the legacy planner. Intercept that assignment.
# The rest of the module remains normally mutable.
_AuthoritativeModule = type(
    "_AuthoritativeVisualStrategyModule",
    (types.ModuleType,),
    {
        "__setattr__": lambda self, name, value: types.ModuleType.__setattr__(
            self,
            name,
            build_deep_queries
            if name == "build_deep_queries"
            else 1
            if name == "MAX_VISUAL_SEARCH_QUERIES"
            else value,
        )
    },
)
_this_module = sys.modules.get(__name__)
if _this_module is not None and not isinstance(_this_module, _AuthoritativeModule):
    _this_module.__class__ = _AuthoritativeModule
