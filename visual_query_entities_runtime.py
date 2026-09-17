"""Visual-subject preparation for Viral Shorts Factory.

The factual entity is preserved for provenance, while the search identity is
cleaned and resolved by the shared genre-agnostic semantic guard. The visual
runtime boundary never receives raw narration as a search/AI prompt.
"""
from __future__ import annotations

from visual_semantic_guard_runtime import build_query_ladder, clean_text, prepare_scene, resolve_subject

_INVALID = {"", "none", "unknown", "na", "n/a"}


def _install_runtime_query_guard(visual_runtime_module):
    """Remove the legacy raw-text fallback from visual_runtime search planning."""
    if getattr(visual_runtime_module, "_generic_semantic_query_guard", False):
        return

    def guarded_build_search_variants(seg, video_title=""):
        queries, visual_type, resolution = build_query_ladder(seg, video_title)
        if not queries:
            raise RuntimeError(
                "No grounded visual query could be derived from the scene; "
                "refusing to fall back to narration or category text."
            )
        print(
            f"   [Visual Semantic Guard] original='{resolution.get('original_entity','')}' "
            f"resolved='{resolution.get('subject','')}' type={visual_type} "
            f"confidence={resolution.get('confidence', 0):.2f}",
            flush=True,
        )
        return queries[:visual_runtime_module.VISUAL_MAX_SEARCH_QUERIES], visual_type

    visual_runtime_module._build_search_variants = guarded_build_search_variants
    visual_runtime_module._generic_semantic_query_guard = True


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
    return str(resolve_subject({"primary_entity": subject}).get("visual_type") or "GENERAL_CONTEXT").upper()


def build_candidate_scene(scene: dict, subject: str, video_title: str = "") -> dict:
    candidate = dict(scene or {})
    original_entity = clean_text(candidate.get("primary_entity", ""))
    original_voiceover = clean_text(candidate.get("voiceover", ""))
    original_intent = clean_text(candidate.get("visual_intent", ""))

    candidate["primary_entity"] = clean_text(subject)
    prepared = prepare_scene(candidate, video_title)
    prepared["factual_primary_entity"] = original_entity
    prepared["original_primary_entity"] = original_entity

    # Retrieval and AI-image generation receive a compact visual brief, never
    # the full narration. The original narration remains available for rendering
    # and subtitles under a separate field.
    visual_subject = clean_text(prepared.get("visual_search_subject", prepared.get("primary_entity", "")))
    prepared["factual_voiceover"] = original_voiceover
    prepared["factual_visual_intent"] = original_intent
    prepared["voiceover"] = visual_subject
    prepared["specific_search_prompt"] = visual_subject
    prepared["visual_intent"] = visual_subject
    prepared["visual_context"] = visual_subject
    return prepared


def search_slide_visual(visual_runtime_module, bot, scene, category, used_urls, used_hashes, video_title=""):
    _install_runtime_query_guard(visual_runtime_module)
    raw_subject = lock_visual_subject(scene, video_title)
    candidate = build_candidate_scene(scene, raw_subject, video_title)
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
