"""Visual-subject preparation for Viral Shorts Factory.

The authoritative semantic resolver preserves the factual entity while deriving
one precise visual subject for retrieval. Resolution is genre-agnostic and uses
scene role + optional domain context rather than entity-specific exceptions.
"""
from __future__ import annotations

import html
import re

_INVALID = {"none", "unknown", "na", "n/a"}


def _clean(value: object) -> str:
    text = html.unescape(str(value or "")).replace("\u200b", " ")
    return re.sub(r"\s+", " ", text).strip(" ,.;:|\"'()[]{}")


def _key(value: str) -> str:
    return re.sub(r"[^\w]+", "", _clean(value).casefold(), flags=re.UNICODE)


def lock_visual_subject(scene: dict) -> str:
    if not isinstance(scene, dict):
        return ""
    subject = _clean(scene.get("primary_entity", ""))
    return "" if not subject or _key(subject) in _INVALID else subject


def extract_slide_search_subjects(scene: dict) -> list[str]:
    subject = lock_visual_subject(scene)
    return [subject] if subject else []


def classify_search_subject(subject: str) -> str:
    subject = _clean(subject)
    if not subject:
        return "GENERAL_CONTEXT"
    try:
        import visual_retrieval_planner as planner
        return planner.classify_subject_text(subject)
    except Exception:
        return "GENERAL_CONTEXT"


def build_candidate_scene(scene: dict, subject: str) -> dict:
    candidate = dict(scene or {})
    factual_subject = _clean(subject)
    candidate["factual_primary_entity"] = factual_subject

    try:
        import visual_retrieval_planner as planner
        brief = planner.build_scene_visual_brief(
            candidate,
            str(candidate.get("video_title", "")),
            str(candidate.get("sport_or_topic_category", "")),
        )
        visual_subject = _clean(brief.get("subject", "")) or factual_subject
        visual_type = str(brief.get("visual_type") or classify_search_subject(visual_subject)).upper()
    except Exception:
        visual_subject = factual_subject
        visual_type = classify_search_subject(visual_subject)

    # The downstream runtime historically uses primary_entity as its search
    # identity. Give it the resolved visual subject while retaining the original
    # factual entity separately for provenance/debugging.
    candidate["primary_entity"] = visual_subject
    candidate["visual_search_subject"] = visual_subject
    candidate["visual_type"] = visual_type
    candidate["specific_search_prompt"] = visual_subject
    candidate["visual_subject_locked"] = True
    candidate["visual_subject_lock"] = factual_subject
    return candidate


def search_slide_visual(visual_runtime_module, bot, scene, category, used_urls, used_hashes, video_title=""):
    subject = lock_visual_subject(scene)
    if not subject:
        return visual_runtime_module._relevant_asset(bot, scene, category, used_urls, used_hashes, video_title)
    candidate = build_candidate_scene(scene, subject)
    print(
        f"   [Visual Search] Factual subject='{subject}' | "
        f"precise visual subject='{candidate.get('visual_search_subject', subject)}' | "
        f"type={candidate['visual_type']}",
        flush=True,
    )
    return visual_runtime_module._relevant_asset(
        bot, candidate, category, used_urls, used_hashes, video_title
    )
