"""Visual-subject preparation for Viral Shorts Factory.

The factual entity is preserved for provenance, while the search identity is
cleaned and resolved by the shared genre-agnostic semantic guard. The visual
runtime boundary never receives raw narration as a search/AI prompt and never
selects providers from hard-coded genre names.
"""
from __future__ import annotations

from visual_semantic_guard_runtime import build_query_ladder, clean_text, prepare_scene, resolve_subject

_INVALID = {"", "none", "unknown", "na", "n/a"}


def _install_runtime_query_guard(visual_runtime_module):
    """Remove legacy raw-text fallback and genre-specific provider heuristics."""
    if getattr(visual_runtime_module, "_generic_semantic_query_guard", False):
        return

    def guarded_build_search_variants(seg, video_title=""):
        queries, visual_type, resolution = build_query_ladder(seg, video_title)
        if not queries:
            raise RuntimeError(
                "No grounded visual query could be derived from the scene; "
                "refusing to fall back to narration, title, category, or role text."
            )
        print(
            f"   [Visual Semantic Guard] original='{resolution.get('original_entity','')}' "
            f"resolved='{resolution.get('subject','')}' type={visual_type} "
            f"confidence={resolution.get('confidence', 0):.2f}",
            flush=True,
        )
        return queries[:visual_runtime_module.VISUAL_MAX_SEARCH_QUERIES], visual_type

    def generic_source_plan(bot, visual_type, category=""):
        """Choose sources from visual modality, never from genre keywords."""
        plan = []
        if visual_type == "PERSON":
            plan.extend([
                ("Wikipedia", getattr(bot, "fetch_wiki_person_image", None)),
                ("Commons", getattr(bot, "fetch_wikimedia_commons", None)),
            ])
        elif visual_type in {"ORGANIZATION", "EVENT", "QUOTE", "DOCUMENT", "LOCATION"}:
            plan.append(("Commons", getattr(bot, "fetch_wikimedia_commons", None)))

        # Broad image discovery is available to every visual type. The semantic
        # guard, not the provider choice, determines relevance.
        plan.extend([
            ("DDG", getattr(bot, "fetch_duckduckgo", None)),
            ("Pexels", getattr(bot, "fetch_pexels", None)),
            ("Unsplash", getattr(bot, "fetch_unsplash", None)),
        ])
        return [(name, fn) for name, fn in plan if callable(fn)]

    def generic_verification_tier(seg, visual_type, source):
        source_l = str(source or "").strip().lower()
        if visual_type == "PERSON" and source_l in {"wikipedia", "commons"}:
            return "STRICT(person)"
        if visual_type == "EVENT":
            return "STRICT(event)"
        if visual_type in {"PROCESS", "CONCEPT"}:
            return "STRICT(concept)"
        return "STRICT"

    visual_runtime_module._build_search_variants = guarded_build_search_variants
    visual_runtime_module._source_plan = generic_source_plan
    visual_runtime_module._verification_tier = generic_verification_tier
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

    # Resolve from the original scene, not from a pre-resolved label, so the
    # semantic guard has the maximum available evidence exactly once.
    prepared = prepare_scene(candidate, video_title)
    visual_subject = clean_text(prepared.get("visual_search_subject", prepared.get("primary_entity", "")))
    if not visual_subject and clean_text(subject):
        visual_subject = clean_text(subject)

    prepared["factual_primary_entity"] = original_entity
    prepared["original_primary_entity"] = original_entity
    prepared["factual_voiceover"] = original_voiceover
    prepared["factual_visual_intent"] = original_intent

    # Retrieval and AI-image generation receive a compact visual brief, never
    # the full narration or raw category metadata.
    prepared["primary_entity"] = visual_subject
    prepared["visual_search_subject"] = visual_subject
    prepared["voiceover"] = visual_subject
    prepared["specific_search_prompt"] = visual_subject
    prepared["visual_intent"] = visual_subject
    prepared["visual_context"] = visual_subject
    return prepared


def search_slide_visual(visual_runtime_module, bot, scene, category, used_urls, used_hashes, video_title=""):
    _install_runtime_query_guard(visual_runtime_module)
    candidate = build_candidate_scene(scene, lock_visual_subject(scene, video_title), video_title)
    subject = clean_text(candidate.get("visual_search_subject", "") or candidate.get("primary_entity", ""))
    if not subject:
        raise RuntimeError(
            "Visual search refused the scene because no grounded visual subject could be resolved."
        )

    candidate["sport_or_topic_category"] = category or candidate.get("sport_or_topic_category", "")
    print(
        f"   [Visual Search] Factual subject='{candidate.get('original_primary_entity', subject)}' | "
        f"precise visual subject='{subject}' | type={candidate.get('visual_type', 'GENERAL_CONTEXT')}",
        flush=True,
    )
    return visual_runtime_module._relevant_asset(
        bot, candidate, category, used_urls, used_hashes, video_title
    )
