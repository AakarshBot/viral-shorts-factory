"""Compatibility layer for the visual query lock.

The factual subject remains locked, but the search string may be refined by the
bounded retrieval planner. This module must never collapse retrieval to one
query, one provider, or one candidate.
"""
from __future__ import annotations

_VERSION = "2026-09-17-v15-subject-lock-bounded-provider-fallback"
_INSTALLED = False


def install() -> bool:
    global _INSTALLED
    if _INSTALLED:
        return True
    try:
        import visual_runtime as runtime
    except Exception as exc:
        print(f"   [Visual Query Lock] Compatibility install unavailable: {exc}", flush=True)
        return False

    original_source_plan = getattr(runtime, "_source_plan", None)
    if callable(original_source_plan) and not getattr(original_source_plan, "_semantic_lock_wrapped", False):
        def source_plan_with_entity_support(bot, visual_type, category):
            plan = list(original_source_plan(bot, visual_type, category))
            if str(visual_type).upper() == "ORGANIZATION":
                commons = getattr(bot, "fetch_wikimedia_commons", None)
                if callable(commons) and not any(name == "Commons" for name, _ in plan):
                    plan.insert(0, ("Commons", commons))
            return plan
        source_plan_with_entity_support._semantic_lock_wrapped = True
        runtime._source_plan = source_plan_with_entity_support

    runtime._visual_query_lock_version = _VERSION
    _INSTALLED = True
    print(
        f"   [Visual Query Lock] Compatibility mode | version={_VERSION} | "
        "factual subject locked + bounded query/source fallback enabled",
        flush=True,
    )
    return True
