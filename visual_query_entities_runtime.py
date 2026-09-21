"""Visual-subject preparation and identity-first retrieval binding."""
from __future__ import annotations

from visual_semantic_guard_runtime import clean_text, infer_role, resolve_subject
from visual_retrieval_runtime import run_visual_retrieval
from visual_taxonomy_runtime import classify_visual_genre
from visual_search_intent_runtime import resolve_visual_search_intent

_INVALID = {"", "none", "unknown", "na", "n/a"}


def _build_identity_first_queries(seg: dict, resolution: dict) -> list[str]:
    """Compatibility shim: return the canonical bounded query set."""
    scene = dict(seg or {})
    if resolution and not scene.get("primary_entity"):
        scene["primary_entity"] = resolution.get("factual_entity") or resolution.get("subject", "")
    intent = resolve_visual_search_intent(scene)
    return list(intent.queries)


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
        from visual_search_intent_runtime import resolve_visual_search_intent

        visual_intent = resolve_visual_search_intent(seg, video_title)
        if not visual_intent.subject or not visual_intent.query:
            raise RuntimeError("No grounded visual search intent could be resolved from the scene.")

        if isinstance(seg, dict):
            seg["_visual_search_intent"] = visual_intent
            seg["visual_genre"] = visual_intent.visual_genre

        if visual_intent.manual:
            print(
                f"   [Visual Semantic Guard] MANUAL subject='{visual_intent.subject}' "
                f"type={visual_intent.visual_type} genre={visual_intent.visual_genre} query='{visual_intent.query}'",
                flush=True,
            )
        else:
            print(
                f"   [Visual Semantic Guard] subject='{visual_intent.subject}' "
                f"type={visual_intent.visual_type} genre={visual_intent.visual_genre} query='{visual_intent.query}' "
                f"confidence={visual_intent.confidence:.2f}",
                flush=True,
            )

        return list(visual_intent.queries), visual_intent.visual_type

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
    visual_runtime_module._verification_tier = generic_verification_tier

    # Preserve an existing runtime-provided retrieval hook. Lightweight
    # compatibility doubles use this to control the retrieval boundary in tests
    # and in embedded callers. If no hook exists, install the canonical
    # production boundary.
    if not callable(getattr(visual_runtime_module, "_relevant_asset", None)):
        visual_runtime_module._relevant_asset = robust_relevant_asset
        visual_runtime_module._robust_retrieval_boundary = True

    visual_runtime_module._generic_semantic_query_guard = True


def lock_visual_subject(scene: dict, video_title: str = "") -> str:
    if not isinstance(scene, dict):
        return ""
    resolution = resolve_subject(scene, video_title)
    subject = clean_text(resolution.get("factual_entity") or resolution.get("subject", ""))
    return "" if subject.casefold() in _INVALID else subject


def extract_slide_search_subjects(scene: dict) -> list[str]:
    """Return the one automatic subject used by the current retrieval contract."""
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
    original_prompt = clean_text(candidate.get("specific_search_prompt", ""))
    original_context = clean_text(candidate.get("visual_context", ""))

    resolution = resolve_subject(candidate, video_title)
    factual_entity = clean_text(resolution.get("factual_entity", "") or original_entity or subject)
    visual_subject = clean_text(resolution.get("subject", "") or factual_entity or subject)

    prepared = dict(candidate)
    prepared["factual_primary_entity"] = factual_entity
    prepared["original_primary_entity"] = original_entity
    prepared["factual_voiceover"] = original_voiceover
    prepared["factual_visual_intent"] = original_intent
    prepared["factual_search_prompt"] = original_prompt

    inferred_role = infer_role({
        "primary_entity": visual_subject,
        "visual_intent": original_intent,
    })
    if inferred_role == "GENERAL_CONTEXT":
        inferred_role = infer_role({
            "primary_entity": visual_subject,
            "visual_intent": original_intent,
            "visual_type": prepared.get("visual_type", ""),
        })
    if inferred_role != "GENERAL_CONTEXT":
        prepared["visual_type"] = inferred_role

    prepared["visual_genre"] = classify_visual_genre(
        prepared,
        visual_subject or factual_entity,
        prepared.get("visual_type", "GENERAL_CONTEXT"),
    )
    prepared["primary_entity"] = visual_subject or factual_entity
    prepared["visual_search_subject"] = visual_subject or factual_entity
    prepared["visual_subject_locked"] = True
    prepared["specific_search_prompt"] = original_prompt
    prepared["visual_intent"] = original_intent
    prepared["visual_context"] = original_context
    prepared["voiceover"] = original_voiceover
    return prepared


def search_slide_visual(
    visual_runtime_module,
    bot,
    scene,
    category,
    used_urls,
    used_hashes,
    video_title="",
    manual_query="",
):
    """Use one canonical manual/automatic query path for the scene."""
    _install_runtime_query_guard(visual_runtime_module)
    candidate = build_candidate_scene(
        scene,
        lock_visual_subject(scene, video_title),
        video_title,
    )
    manual_query = clean_text(manual_query or candidate.get("manual_visual_query", ""))
    if not manual_query and candidate.get("visual_entity_grounded") is False:
        raise RuntimeError(
            "Automatic visual identity is not grounded in story evidence; "
            "refusing to search the ungrounded entity."
        )
    if manual_query:
        candidate["manual_visual_query"] = manual_query

    from visual_search_intent_runtime import resolve_visual_search_intent

    visual_intent = resolve_visual_search_intent(candidate, video_title)
    if not visual_intent.subject or not visual_intent.query:
        raise RuntimeError("Visual search refused the scene because no grounded visual intent could be resolved.")

    candidate["_visual_search_intent"] = visual_intent
    candidate["primary_entity"] = visual_intent.subject
    candidate["visual_search_subject"] = visual_intent.subject
    candidate["visual_type"] = visual_intent.visual_type
    candidate["visual_genre"] = visual_intent.visual_genre

    candidate["sport_or_topic_category"] = category or candidate.get("sport_or_topic_category", "")
    print(
        f"   [Visual Search] Factual subject='{visual_intent.subject}' | "
        f"query='{visual_intent.query}' | type={visual_intent.visual_type} "
        f"| genre={visual_intent.visual_genre}",
        flush=True,
    )

    result = visual_runtime_module._relevant_asset(
        bot, candidate, category, used_urls, used_hashes, video_title
    )

    if isinstance(scene, dict):
        for key_name in (
            "visual_verified",
            "visual_rescue_reason",
            "visual_fallback_reason",
            "visual_query_used",
            "visual_verification_attempts",
            "visual_type",
            "visual_genre",
            "visual_selected_hash",
            "visual_original_path",
            "visual_provider_query_used",
            "visual_qc_blocked",
            "visual_qc_block_reason",
            "visual_rejection_counts",
            "asset_provenance",
            "_verified_subject_assets",
        ):
            if key_name in candidate:
                scene[key_name] = candidate[key_name]
    return result
