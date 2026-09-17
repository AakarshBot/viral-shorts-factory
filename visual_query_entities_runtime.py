"""Visual-subject preparation and multi-query/multi-source retrieval binding."""
from __future__ import annotations

from visual_semantic_guard_runtime import build_query_ladder, clean_text, infer_role, prepare_scene, resolve_subject
from visual_retrieval_runtime import _source_plan, run_visual_retrieval

_INVALID = {"", "none", "unknown", "na", "n/a"}
_MAX_QUERY_BUDGET = 6
_MIN_QUERY_BUDGET = 3

# These are visual-modality descriptors, not genre terms. They provide distinct
# retrieval wording only after the factual identity/query ladder has been tried.
_QUERY_VARIANTS = {
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
    """Install the sole active generic visual retrieval boundary."""
    if getattr(visual_runtime_module, "_generic_semantic_query_guard", False):
        return

    try:
        from visual_safety_runtime import install as install_visual_safety
        install_visual_safety()
    except Exception as exc:
        print(f"   [Visual Safety] Runtime installation deferred: {type(exc).__name__}: {exc}", flush=True)

    def guarded_build_search_variants(seg, video_title=""):
        queries, visual_type, resolution = build_query_ladder(seg, video_title)
        try:
            configured_budget = int(getattr(visual_runtime_module, "VISUAL_MAX_SEARCH_QUERIES", _MAX_QUERY_BUDGET))
        except (TypeError, ValueError):
            configured_budget = _MAX_QUERY_BUDGET
        search_budget = min(_MAX_QUERY_BUDGET, max(_MIN_QUERY_BUDGET, configured_budget))

        if not queries:
            raise RuntimeError(
                "No grounded visual query could be derived from the scene; "
                "refusing to use narration, title, category, or generic padding as identity."
            )

        anchor = clean_text(
            resolution.get("factual_entity")
            or seg.get("factual_primary_entity")
            or resolution.get("subject")
            or seg.get("primary_entity")
        )
        variants = _QUERY_VARIANTS.get(str(visual_type).upper(), ("context", "illustration"))
        seen = {clean_text(q).casefold() for q in queries}

        # First fill the ladder with modality-neutral transformations that keep
        # the exact grounded subject. The identity anchor must remain present.
        if anchor:
            for term in variants:
                if len(queries) >= min(search_budget, _MAX_QUERY_BUDGET):
                    break
                query = clean_text(f"{anchor} {term}")
                if query and query.casefold() not in seen:
                    queries.append(query)
                    seen.add(query.casefold())
                if len(queries) >= _MIN_QUERY_BUDGET:
                    break

        # If the planner returned a short ladder, use one reversed presentation
        # variant to change token ordering without adding factual content.
        if anchor and len(queries) < min(search_budget, _MAX_QUERY_BUDGET):
            for term in variants:
                query = clean_text(f"{term} {anchor}")
                if query and query.casefold() not in seen:
                    queries.append(query)
                    seen.add(query.casefold())
                    break

        print(
            f"   [Visual Semantic Guard] original='{resolution.get('original_entity','')}' "
            f"resolved='{resolution.get('subject','')}' type={visual_type} "
            f"confidence={resolution.get('confidence', 0):.2f} search_phrases={min(len(queries), search_budget)}",
            flush=True,
        )
        return queries[:search_budget], visual_type

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
    visual_runtime_module._source_plan = _source_plan
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

    prepared = prepare_scene(candidate, video_title)
    visual_subject = clean_text(prepared.get("visual_search_subject", prepared.get("primary_entity", "")))
    if not visual_subject and clean_text(subject):
        visual_subject = clean_text(subject)

    prepared["factual_primary_entity"] = original_entity
    prepared["original_primary_entity"] = original_entity
    prepared["factual_voiceover"] = original_voiceover
    prepared["factual_visual_intent"] = original_intent

    subject_role = infer_role({"primary_entity": visual_subject})
    if subject_role != "GENERAL_CONTEXT":
        prepared["visual_type"] = subject_role

    # Retrieval receives compact search context; narration itself stays intact
    # on the authoritative scene and is never used as a raw query.
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
        raise RuntimeError("Visual search refused the scene because no grounded visual subject could be resolved.")

    candidate["sport_or_topic_category"] = category or candidate.get("sport_or_topic_category", "")
    print(
        f"   [Visual Search] Factual subject='{candidate.get('original_primary_entity', subject)}' | "
        f"precise visual subject='{subject}' | type={candidate.get('visual_type', 'GENERAL_CONTEXT')}",
        flush=True,
    )
    result = visual_runtime_module._relevant_asset(
        bot, candidate, category, used_urls, used_hashes, video_title
    )

    if isinstance(scene, dict):
        for key in (
            "visual_verified", "visual_rescue_reason", "visual_fallback_reason", "visual_query_used",
            "visual_verification_attempts", "visual_type",
        ):
            if key in candidate:
                scene[key] = candidate[key]
    return result
