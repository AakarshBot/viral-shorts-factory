"""Visual-subject preparation and robust retrieval for Viral Shorts Factory.

The factual entity is preserved for provenance, while the search identity is
resolved by the shared genre-agnostic semantic guard. Retrieval is delegated to
one bounded resilience engine that can exhaust queries/providers without
crashing the factory.
"""
from __future__ import annotations

from visual_semantic_guard_runtime import build_query_ladder, clean_text, infer_role, prepare_scene, resolve_subject
from visual_retrieval_runtime import run_visual_retrieval

_INVALID = {"", "none", "unknown", "na", "n/a"}
_MAX_QUERY_BUDGET = 6
_MIN_QUERY_BUDGET = 3

# Generic presentation terms are fall-through retrieval variants, not factual
# identity. They are selected by visual modality so the logic remains shared
# across genres rather than carrying category-specific search rules.
_QUERY_FALLBACKS = {
    "PERSON": ("portrait", "biography"),
    "ORGANIZATION": ("official", "logo"),
    "PRODUCT": ("product", "specifications"),
    "LOCATION": ("map", "landmark"),
    "EVENT": ("event", "ceremony"),
    "DOCUMENT": ("document", "report"),
    "QUOTE": ("quotation", "statement"),
    "STATISTIC": ("chart", "data"),
    "COMPARISON": ("comparison", "chart"),
    "TIMELINE": ("timeline", "chronology"),
    "PROCESS": ("diagram", "workflow"),
    "CONCEPT": ("diagram", "illustration"),
    "GENERAL_CONTEXT": ("context", "illustration"),
}


def _install_runtime_query_guard(visual_runtime_module):
    """Install the single authoritative generic retrieval boundary."""
    if getattr(visual_runtime_module, "_generic_semantic_query_guard", False):
        return

    # Activate the bounded fetch worker and central budget layer on the active
    # path. This is intentionally runtime-local so import order stays safe.
    try:
        from visual_safety_runtime import install as install_visual_safety
        install_visual_safety()
    except Exception as exc:
        print(f"   [Visual Safety] Runtime installation deferred: {type(exc).__name__}: {exc}", flush=True)

    def guarded_build_search_variants(seg, video_title=""):
        queries, visual_type, resolution = build_query_ladder(seg, video_title)

        # A malformed environment value of 0/1 must not turn retrieval into a
        # single-point-of-failure. Keep a bounded minimum of three attempts and
        # a hard ceiling of six, independent of the content genre.
        try:
            configured_budget = int(getattr(visual_runtime_module, "VISUAL_MAX_SEARCH_QUERIES", _MIN_QUERY_BUDGET))
        except (TypeError, ValueError):
            configured_budget = _MIN_QUERY_BUDGET
        search_budget = min(_MAX_QUERY_BUDGET, max(_MIN_QUERY_BUDGET, configured_budget))
        visual_runtime_module.VISUAL_MAX_SEARCH_QUERIES = search_budget

        if not queries:
            raise RuntimeError(
                "No grounded visual query could be derived from the scene; "
                "refusing to fall back to narration, title, category, or role text."
            )

        # Clean factual identity is the final retrieval anchor. When the normal
        # ladder already has fewer than three unique queries (for example a
        # clean one-word person/entity), add neutral modality descriptors rather
        # than reusing raw narration or expanding into a sentence.
        anchor = clean_text(
            resolution.get("factual_entity")
            or seg.get("factual_primary_entity")
            or resolution.get("subject")
            or seg.get("primary_entity")
        )
        fallback_terms = _QUERY_FALLBACKS.get(str(visual_type).upper(), ("context", "illustration"))
        seen = {clean_text(q).casefold() for q in queries}
        for term in fallback_terms:
            if len(queries) >= _MAX_QUERY_BUDGET or not anchor:
                break
            query = clean_text(f"{anchor} {term}")
            if query and query.casefold() not in seen:
                queries.append(query)
                seen.add(query.casefold())
            if len(queries) >= _MIN_QUERY_BUDGET:
                break

        print(
            f"   [Visual Semantic Guard] original='{resolution.get('original_entity','')}" 
            f"' resolved='{resolution.get('subject','')}' type={visual_type} "
            f"confidence={resolution.get('confidence', 0):.2f}",
            flush=True,
        )
        return queries[:search_budget], visual_type

    def generic_source_plan(bot, visual_type, category=""):
        """Choose sources by visual modality/source strength, never by genre."""
        plan = []
        if visual_type == "PERSON":
            plan.extend([
                ("Wikipedia", getattr(bot, "fetch_wiki_person_image", None)),
                ("Commons", getattr(bot, "fetch_wikimedia_commons", None)),
            ])
        elif visual_type in {"ORGANIZATION", "EVENT", "QUOTE", "DOCUMENT", "LOCATION"}:
            plan.append(("Commons", getattr(bot, "fetch_wikimedia_commons", None)))
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

    def robust_relevant_asset(bot, seg, category, used_urls, used_hashes, video_title=""):
        return run_visual_retrieval(
            visual_runtime_module,
            bot,
            seg,
            category,
            used_urls,
            used_hashes,
            video_title,
        )

    visual_runtime_module._build_search_variants = guarded_build_search_variants
    visual_runtime_module._source_plan = generic_source_plan
    visual_runtime_module._verification_tier = generic_verification_tier
    visual_runtime_module._relevant_asset = robust_relevant_asset
    visual_runtime_module._generic_semantic_query_guard = True
    visual_runtime_module._robust_retrieval_boundary = True


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

    # Resolve from the original scene so the semantic guard sees the complete
    # evidence exactly once and the rendered narration remains untouched.
    prepared = prepare_scene(candidate, video_title)
    visual_subject = clean_text(prepared.get("visual_search_subject", prepared.get("primary_entity", "")))
    if not visual_subject and clean_text(subject):
        visual_subject = clean_text(subject)

    prepared["factual_primary_entity"] = original_entity
    prepared["original_primary_entity"] = original_entity
    prepared["factual_voiceover"] = original_voiceover
    prepared["factual_visual_intent"] = original_intent

    # Recover a strong generic factual role from the resolved subject when the
    # upstream script carries an inconsistent stale visual_type. For example,
    # the word "team" is an organization cue regardless of whether the story
    # is sports, business, science, entertainment, or another genre. Weak/no
    # subject evidence never overrides the explicit upstream type.
    subject_role = infer_role({"primary_entity": visual_subject})
    if subject_role != "GENERAL_CONTEXT":
        prepared["visual_type"] = subject_role

    # Retrieval/AI receives only the compact visual subject, never raw narration.
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
    result = visual_runtime_module._relevant_asset(
        bot, candidate, category, used_urls, used_hashes, video_title
    )

    # Carry retrieval status back to the authoritative scene for rendering/QC.
    if isinstance(scene, dict):
        for key in (
            "visual_verified", "visual_fallback_reason", "visual_query_used",
            "visual_verification_attempts", "visual_type",
        ):
            if key in candidate:
                scene[key] = candidate[key]
    return result