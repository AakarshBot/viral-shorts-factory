"""Runtime visual-search contract for Viral Shorts Factory.

Production visual contract:
    identify subject -> lock subject -> one exact search -> visual QA

The locked ``primary_entity`` is returned byte-for-byte apart from surrounding
whitespace. No normaliser, Unicode helper, visual cue, title, narration or
legacy planner is allowed to rewrite the search query.
"""

import visual_retrieval_planner as _planner
from visual_retrieval_planner import *

for _name in dir(_planner):
    if _name.startswith("_") and not _name.startswith("__"):
        globals()[_name] = getattr(_planner, _name)

_VISUAL_STRATEGY_VERSION = "2026-09-17-v15-single-authoritative-planner"


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

    # This function is the authoritative planner. It is deliberately marked so
    # the compatibility lock cannot replace it with a second implementation.
    return [subject], resolved_type


build_deep_queries._authoritative_locked_subject_planner = True


def _add_unique(values, value, *parts):
    """Legacy compatibility helper; never used by production search planning."""
    candidate = _planner._normalise(" ".join(str(part) for part in (value,) + parts if str(part).strip()))
    if not candidate:
        return False
    if candidate in values:
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


# Install the retrieval/runtime guard after the authoritative function exists.
# The guard may patch compatibility surfaces, but it must never replace the
# authoritative planner itself.
try:
    from visual_query_lock_runtime import install as _install_visual_query_lock
    _install_visual_query_lock()
except Exception as exc:
    print(
        f"   [Visual Strategy] Query lock auto-install unavailable: {type(exc).__name__}: {exc}",
        flush=True,
    )
finally:
    # The lock installer runs during module import, so explicitly restore our
    # function in case an older installer attempted to overwrite it midway.
    globals()["build_deep_queries"] = build_deep_queries
