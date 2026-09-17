"""Hard production guard for the visual-search query contract.

The factory must never let a retrieval planner turn a slide's primary_entity
into multiple search queries. This patch is deliberately deterministic and
cost-free: every visual search receives exactly the primary entity, unchanged.
"""
from __future__ import annotations

import html
import re


_VERSION = "2026-09-17-v11-exact-query-lock"
_INSTALLED = False


def _clean(value: object) -> str:
    text = html.unescape(str(value or "")).replace("\u200b", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ,.-:;|\"'")


def _primary(scene) -> str:
    if not isinstance(scene, dict):
        return ""
    return _clean(scene.get("primary_entity", ""))


def install() -> bool:
    global _INSTALLED
    if _INSTALLED:
        return True

    try:
        import visual_strategy_runtime as strategy
        import visual_retrieval_planner as planner
    except Exception as exc:
        print(f"   [Visual Query Lock] Could not install: {exc}", flush=True)
        return False

    def exact_strategy_queries(scene, video_title="", visual_type=None):
        subject = _primary(scene)
        resolved_type = visual_type
        if not resolved_type:
            try:
                resolved_type = planner.classify_scene(scene or {}, str((scene or {}).get("sport_or_topic_category", "")))
            except Exception:
                resolved_type = "GENERAL_CONTEXT"
        return ([subject] if subject else []), resolved_type

    def exact_planner_queries(scene, video_title="", visual_type=None):
        subject = _primary(scene)
        resolved_type = visual_type
        if not resolved_type:
            try:
                resolved_type = planner.classify_scene(scene or {}, str((scene or {}).get("sport_or_topic_category", "")))
            except Exception:
                resolved_type = "GENERAL_CONTEXT"
        return ([subject] if subject else []), resolved_type

    strategy.build_deep_queries = exact_strategy_queries
    planner.build_deep_queries = exact_planner_queries

    # Some callers import the planner function directly after importing the
    # strategy module. Keep both module surfaces locked.
    strategy.MAX_VISUAL_SEARCH_QUERIES = 1
    planner.MAX_VISUAL_SEARCH_QUERIES = 1

    _INSTALLED = True
    print(f"   [Visual Query Lock] Installed | version={_VERSION} | exactly one query per slide", flush=True)
    return True
