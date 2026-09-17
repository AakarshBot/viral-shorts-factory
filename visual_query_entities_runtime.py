"""Visual-subject preparation and identity-first multi-source retrieval binding."""
from __future__ import annotations

from visual_semantic_guard_runtime import (
    AUXILIARY_WORDS,
    DISCOURSE_PREFIXES,
    GENERIC_NOISE,
    STOPWORDS,
    VISUAL_DESCRIPTORS,
    clean_text,
    infer_role,
    key,
    resolve_subject,
    tokens,
)
from visual_retrieval_runtime import _source_plan, run_visual_retrieval

_INVALID = {"", "none", "unknown", "na", "n/a"}
_MAX_QUERY_BUDGET = 5
_SEARCH_ACTIONS = {"lift", "lifts", "lifted", "lifting", "celebrate", "celebrates", "celebrated", "celebrating", "discuss", "discusses", "discussed", "discussing", "appear", "appears", "appeared", "show", "shows", "showed"}


def _simple_context_terms(text: str, anchor: str) -> list[str]:
    """Extract a few simple context words; never turn the prompt into identity."""
    anchor_keys = {key(word) for word in tokens(anchor)}
    terms = []
    for word in tokens(text):
        token_key = key(word)
        if (
            not token_key
            or token_key in anchor_keys
            or token_key in GENERIC_NOISE
            or token_key in STOPWORDS
            or token_key in DISCOURSE_PREFIXES
            or token_key in AUXILIARY_WORDS
            or token_key in _SEARCH_ACTIONS
            or token_key in VISUAL_DESCRIPTORS
        ):
            continue
        if token_key not in {key(item) for item in terms}:
            terms.append(word)
    return terms


def _build_identity_first_queries(seg: dict, resolution: dict) -> list[str]:
    """Search the simplest factual identity first, then a few simple context variants."""
    factual_anchor = clean_text(
        seg.get("factual_primary_entity")
        or resolution.get("factual_entity")
        or seg.get("primary_entity")
        or resolution.get("subject")
    )
    if not factual_anchor:
        return []

    # A malformed/discourse-heavy entity may be repaired by resolve_subject()
    # using grounded scene evidence (for example, adding a verified location).
    # In that one case the repaired subject is the factual identity we should
    # search first. Do not use a concrete search prompt this way: prompts stay
    # context only and never outrank the simple identity. Scene preparation
    # preserves both original_primary_entity and factual_primary_entity so the
    # repair can still be detected after it locks primary_entity to the subject.
    original_entity = clean_text(
        seg.get("original_primary_entity") or resolution.get("original_entity", "")
    )
    resolved_factual = clean_text(
        seg.get("factual_primary_entity") or resolution.get("factual_entity", "")
    )
    grounded_subject = clean_text(resolution.get("subject", ""))
    was_grounded = bool(
        original_entity
        and resolved_factual
        and original_entity.casefold() != resolved_factual.casefold()
        and grounded_subject
        and grounded_subject.casefold() != resolved_factual.casefold()
    )
    anchor = grounded_subject if was_grounded else factual_anchor

    queries = [anchor]
    contexts = (
        seg.get("factual_visual_intent"),
        seg.get("visual_intent"),
        seg.get("factual_search_prompt"),
        seg.get("specific_search_prompt"),
        seg.get("visual_context"),
    )
    terms = []
    for context in contexts:
        candidate_terms = _simple_context_terms(clean_text(context), anchor)
        if candidate_terms:
            terms = candidate_terms
            break

    if not terms:
        return queries

    # First use the simplest visual-intent combination. This remains ahead of
    # any prompt-derived phrase and is intentionally capped at three terms.
    compact = clean_text(" ".join([anchor, *terms[:3]]))
    if compact and compact.casefold() != anchor.casefold():
        queries.append(compact)

    for term in (terms[-1], terms[0]):
        query = clean_text(f"{anchor} {term}")
        if query and query.casefold() not in {q.casefold() for q in queries}:
            queries.append(query)
        if len(queries) >= _MAX_QUERY_BUDGET:
            break

    # Only if budget remains, add one compact phrase from the explicit search
    # prompt. Never copy the prompt sentence; keep at most three useful terms.
    if len(queries) < _MAX_QUERY_BUDGET:
        prompt_terms = []
        for context in (seg.get("factual_search_prompt"), seg.get("specific_search_prompt")):
            candidate_terms = _simple_context_terms(clean_text(context), anchor)
            if candidate_terms:
                prompt_terms = candidate_terms
                break
        if prompt_terms:
            prompt_compact = clean_text(" ".join([anchor, *prompt_terms[:3]]))
            if prompt_compact and prompt_compact.casefold() not in {q.casefold() for q in queries}:
                queries.append(prompt_compact)

    return queries[:_MAX_QUERY_BUDGET]


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
        resolution = resolve_subject(seg, video_title)
        queries = _build_identity_first_queries(seg, resolution)
        visual_type = str(resolution.get("visual_type") or seg.get("visual_type") or "GENERAL_CONTEXT").upper()
        if not queries:
            raise RuntimeError("No grounded visual identity could be derived from the scene.")

        print(
            f"   [Visual Semantic Guard] factual='{queries[0]}' "
            f"context_variants={len(queries) - 1} type={visual_type}",
            flush=True,
        )
        return queries, visual_type

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
    subject = clean_text(resolution.get("factual_entity") or resolution.get("subject", ""))
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
    original_prompt = clean_text(candidate.get("specific_search_prompt", ""))
    original_context = clean_text(candidate.get("visual_context", ""))

    resolution = resolve_subject(candidate, video_title)
    factual_entity = clean_text(original_entity or resolution.get("factual_entity", ""))
    visual_subject = clean_text(resolution.get("subject", "") or factual_entity or subject)

    prepared = dict(candidate)
    prepared["factual_primary_entity"] = factual_entity
    prepared["original_primary_entity"] = original_entity
    prepared["factual_voiceover"] = original_voiceover
    prepared["factual_visual_intent"] = original_intent
    prepared["factual_search_prompt"] = original_prompt

    subject_role = infer_role({"primary_entity": visual_subject, "visual_type": prepared.get("visual_type", "")})
    if subject_role != "GENERAL_CONTEXT":
        prepared["visual_type"] = subject_role

    prepared["primary_entity"] = factual_entity or visual_subject
    prepared["visual_search_subject"] = visual_subject
    prepared["specific_search_prompt"] = original_prompt
    prepared["visual_intent"] = original_intent
    prepared["visual_context"] = original_context
    prepared["voiceover"] = original_voiceover
    return prepared


def search_slide_visual(visual_runtime_module, bot, scene, category, used_urls, used_hashes, video_title=""):
    _install_runtime_query_guard(visual_runtime_module)
    candidate = build_candidate_scene(scene, lock_visual_subject(scene, video_title), video_title)
    subject = clean_text(candidate.get("factual_primary_entity", "") or candidate.get("primary_entity", ""))
    context = clean_text(candidate.get("specific_search_prompt", "") or candidate.get("visual_context", ""))
    if not subject:
        raise RuntimeError("Visual search refused the scene because no grounded visual identity could be resolved.")

    candidate["sport_or_topic_category"] = category or candidate.get("sport_or_topic_category", "")
    print(
        f"   [Visual Search] Factual subject='{subject}' | "
        f"search context='{context}' | type={candidate.get('visual_type', 'GENERAL_CONTEXT')}",
        flush=True,
    )
    result = visual_runtime_module._relevant_asset(
        bot, candidate, category, used_urls, used_hashes, video_title
    )

    if isinstance(scene, dict):
        for key_name in (
            "visual_verified", "visual_rescue_reason", "visual_fallback_reason", "visual_query_used",
            "visual_verification_attempts", "visual_type",
        ):
            if key_name in candidate:
                scene[key_name] = candidate[key_name]
    return result
