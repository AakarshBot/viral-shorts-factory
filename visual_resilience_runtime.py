"""Resilience bridge for entity-aware visual routing."""
from __future__ import annotations

from typing import Any


def _resolved_type(seg: dict[str, Any], category: str = "") -> str:
    """Prefer the authoritative entity classifier when a generic type leaked in."""
    current = str(seg.get("visual_type", "") or "").strip().upper().replace("-", "_").replace(" ", "_")
    if current not in {"", "GENERAL_CONTEXT"}:
        return current
    try:
        from visual_strategy_runtime import classify_scene
        return str(classify_scene(seg, category) or "GENERAL_CONTEXT").upper()
    except Exception:
        return current or "GENERAL_CONTEXT"


def install_visual_resilience() -> bool:
    try:
        import visual_runtime
    except Exception as exc:
        print(f"   [Visual Resilience] Runtime unavailable: {type(exc).__name__}: {exc}", flush=True)
        return False

    if getattr(visual_runtime, "_entity_type_resilience_bound", False):
        return True

    original_relevant = getattr(visual_runtime, "_relevant_asset", None)
    original_plan = getattr(visual_runtime, "_source_plan", None)
    if not callable(original_relevant) or not callable(original_plan):
        return False

    def resilient_plan(bot, visual_type, category):
        plan = list(original_plan(bot, visual_type, category) or [])
        if str(visual_type).upper() == "ORGANIZATION":
            commons = getattr(bot, "fetch_wikimedia_commons", None)
            if callable(commons) and not any(name == "Commons" for name, _ in plan):
                plan.insert(0, ("Commons", commons))
        return plan

    def resilient_relevant(bot, seg, category, used_urls, used_hashes, video_title=""):
        incoming = dict(seg or {})
        resolved = _resolved_type(incoming, category)
        if resolved != str(incoming.get("visual_type", "") or "").strip().upper():
            incoming["visual_type"] = resolved
            print(
                f"   [Visual Resilience] Corrected generic scene type for '{incoming.get('primary_entity', '')}': {resolved}",
                flush=True,
            )
        return original_relevant(bot, incoming, category, used_urls, used_hashes, video_title)

    visual_runtime._source_plan = resilient_plan
    visual_runtime._relevant_asset = resilient_relevant
    visual_runtime._entity_type_resilience_bound = True
    print("   [Visual Resilience] Entity-first routing installed for generic visual types; organizations receive Commons priority.", flush=True)
    return True
