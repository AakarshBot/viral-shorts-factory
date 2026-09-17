"""Visual-subject preparation for Viral Shorts Factory.

The factual entity is preserved for provenance, while the search identity is
cleaned and resolved by the shared genre-agnostic semantic guard.
"""
from __future__ import annotations

from visual_semantic_guard_runtime import clean_text, prepare_scene, resolve_subject


_INVALID = {"", "none", "unknown", "na", "n/a"}


def lock_visual_subject(scene: dict, video_title: str = "") -> str:
    if not isinstance(scene, dict):
        return ""
    resolution = resolve_subject(scene, video_title)
    subject = clean_text(resolution.get("subject", ""))
    return "" if subject.casefold() in _INVALID else subject


def extract_slide_search_subjects(scene: dict) -> list[str]:
    subject = lock_visual_subject(scene)
    return [subject] if subject else []


def classify_search_subject(subject: str) -> str:
    subject = clean_text(subject)
    if not subject:
        return "GENERAL_CONTEXT"
    prepared = {"primary_entity": subject}
    return str(resolve_subject(prepared).get("visual_type") or "GENERAL_CONTEXT").upper()


def build_candidate_scene(scene: dict, subject: str, video_title: str = "") -> dict:
    candidate = dict(scene or {})
    candidate["primary_entity"] = clean_text(subject)
    prepared = prepare_scene(candidate, video_title)
    prepared["factual_primary_entity"] = clean_text(subject)
    prepared["original_primary_entity"] = clean_text(scene.get("primary_entity", "")) if isinstance(scene, dict) else clean_text(subject)
    return prepared


def search_slide_visual(visual_runtime_module, bot, scene, category, used_urls, used_hashes, video_title=""):
    candidate = build_candidate_scene(scene, lock_visual_subject(scene, video_title), video_title)
    subject = clean_text(candidate.get("visual_search_subject", "") or candidate.get("primary_entity", ""))
    if not subject:
        return visual_runtime_module._relevant_asset(bot, scene, category, used_urls, used_hashes, video_title)

    candidate["sport_or_topic_category"] = category or candidate.get("sport_or_topic_category", "")
    print(
        f"   [Visual Search] Factual subject='{candidate.get('original_primary_entity', subject)}' | "
        f"precise visual subject='{subject}' | type={candidate.get('visual_type', 'GENERAL_CONTEXT')}",
        flush=True,
    )
    return visual_runtime_module._relevant_asset(
        bot, candidate, category, used_urls, used_hashes, video_title
    )
