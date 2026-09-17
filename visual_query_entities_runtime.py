"""Exact visual-subject locking for Viral Shorts Factory.

Production visual contract:
    story/script -> identify subject -> lock subject -> search exact subject once -> QA -> use/fail

Once ``primary_entity`` has been selected upstream, this module deliberately
refuses to mine narration, titles, visual intent or scene prose for additional
search subjects. Those fields remain context for QA/editorial logic only.
"""
from __future__ import annotations

import html
import re

_QUERY_MAX = 1
_INVALID = {"none", "unknown", "na", "n/a"}


def _clean(value: object) -> str:
    text = html.unescape(str(value or "")).replace("\u200b", " ")
    return re.sub(r"\s+", " ", text).strip(" ,.;:|\"'()[]{}")


def _key(value: str) -> str:
    return re.sub(r"[^\w]+", "", _clean(value).casefold(), flags=re.UNICODE)


def lock_visual_subject(scene: dict) -> str:
    """Return the already-selected primary entity as the immutable search subject."""
    if not isinstance(scene, dict):
        return ""
    subject = _clean(scene.get("primary_entity", ""))
    if not subject or _key(subject) in _INVALID:
        return ""
    return subject


def extract_slide_search_subjects(scene: dict) -> list[str]:
    """Return exactly one subject: the upstream-selected primary_entity."""
    subject = lock_visual_subject(scene)
    return [subject][: _QUERY_MAX] if subject else []


def classify_search_subject(subject: str) -> str:
    """Classify the locked subject for QA/source metadata only."""
    subject = _clean(subject)
    if not subject:
        return "GENERAL_CONTEXT"
    try:
        import visual_retrieval_planner as planner
        if planner._looks_like_organization(subject):
            return "ORGANIZATION"
        if planner._looks_like_location(subject):
            return "LOCATION"
        if planner._looks_like_product(subject):
            return "PRODUCT"
    except Exception:
        pass

    words = subject.split()
    keys = {_key(word) for word in words}
    if keys.intersection({"cup", "championship", "league", "final", "open", "games", "trophy", "summit", "festival", "tournament", "prix"}):
        return "EVENT"
    if keys.intersection({"team", "squad", "board", "association", "government", "ministry", "committee", "company", "corporation"}):
        return "ORGANIZATION"
    if len(words) >= 2 and all(word[:1].isupper() for word in words if word):
        return "PERSON"
    return "GENERAL_CONTEXT"


def build_candidate_scene(scene: dict, subject: str) -> dict:
    """Create a QA-focused copy without changing the original story/narration."""
    candidate = dict(scene or {})
    locked = _clean(subject)
    candidate["primary_entity"] = locked
    candidate["visual_type"] = classify_search_subject(locked)
    candidate["specific_search_prompt"] = locked
    candidate["visual_subject_locked"] = True
    candidate["visual_subject_lock"] = locked
    return candidate


def search_slide_visual(visual_runtime_module, bot, scene, category, used_urls, used_hashes, video_title=""):
    """Search the one locked subject. QA rejection is terminal; never try another subject."""
    subject = lock_visual_subject(scene)
    if not subject:
        return visual_runtime_module._relevant_asset(bot, scene, category, used_urls, used_hashes, video_title)

    candidate = build_candidate_scene(scene, subject)
    print(f"   [Visual Search] Locked subject | '{subject}' | type={candidate['visual_type']}", flush=True)
    return visual_runtime_module._relevant_asset(
        bot, candidate, category, used_urls, used_hashes, video_title
    )
