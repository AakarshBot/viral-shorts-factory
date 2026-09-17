"""Exact visual-subject extraction for Viral Shorts Factory.

The production visual contract is intentionally simple:
    slide -> primary_entity -> one exact image-search query

The narration is used upstream to decide the primary entity. Once that entity
exists, narration, titles, visual intent and scene prose are NOT allowed to
become search-query terms. They remain available to other parts of the
pipeline, but never contaminate the image search.
"""
from __future__ import annotations

import re

_QUERY_MAX = 1


def _clean(value: object) -> str:
    text = str(value or "").replace("\u200b", " ")
    return re.sub(r"\s+", " ", text).strip(" ,.;:|\"'()[]{}")


def _key(value: str) -> str:
    value = _clean(value).casefold()
    return re.sub(r"[^\w]+", "", value, flags=re.UNICODE)


def _append_unique(items: list[str], value: str) -> None:
    value = _clean(value).strip("'")
    if value and value.casefold() not in {item.casefold() for item in items}:
        items.append(value)


def extract_slide_search_subjects(scene: dict) -> list[str]:
    """Return exactly one search subject: the scene's primary_entity.

    This function deliberately does NOT mine the voiceover for additional
    search terms. Mentioned people, organisations and events are context for
    editorial/script logic, not automatic extra image-search targets.
    """
    if not isinstance(scene, dict):
        return []
    primary = _clean(scene.get("primary_entity", ""))
    if not primary or _key(primary) in {"none", "unknown", "na", "n/a"}:
        return []
    return [primary][: _QUERY_MAX]


def classify_search_subject(subject: str) -> str:
    """Classify the already-selected subject for source routing/QA only."""
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
    """Create a subject-focused scene without rewriting its narration."""
    candidate = dict(scene or {})
    subject_type = classify_search_subject(subject)
    candidate["primary_entity"] = subject
    candidate["visual_type"] = subject_type
    # The search prompt is EXACTLY the entity. Never copy narration into it.
    candidate["specific_search_prompt"] = subject
    candidate["visual_intent"] = f"{subject_type.lower()} subject identity"
    candidate["voiceover"] = str(scene.get("voiceover", "") or "")
    return candidate


def search_slide_visual(visual_runtime_module, bot, scene, category, used_urls, used_hashes, video_title=""):
    """Search only the primary visual subject; no query expansion."""
    subjects = extract_slide_search_subjects(scene)
    if not subjects:
        return visual_runtime_module._relevant_asset(bot, scene, category, used_urls, used_hashes, video_title)

    subject = subjects[0]
    candidate = build_candidate_scene(scene, subject)
    subject_type = candidate["visual_type"]
    print(f"   [Visual Search] Subject 1/1 | '{subject}' | type={subject_type}", flush=True)
    return visual_runtime_module._relevant_asset(
        bot, candidate, category, used_urls, used_hashes, video_title
    )
