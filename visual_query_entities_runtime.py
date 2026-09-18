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
from visual_taxonomy_runtime import classify_visual_genre

_INVALID = {"", "none", "unknown", "na", "n/a"}
_MAX_QUERY_BUDGET = 5
_SEARCH_ACTIONS = {"lift", "lifts", "lifted", "lifting", "celebrate", "celebrates", "celebrated", "celebrating", "discuss", "discusses", "discussed", "discussing", "appear", "appears", "appeared", "show", "shows", "showed"}
_ROLE_LABELS = {
    "person", "organization", "organisation", "company", "corporation", "product", "device",
    "location", "geography", "concept", "process", "event", "document", "quote", "quotation",
    "statistic", "comparison", "timeline", "scientific", "technical", "abstract", "team", "members",
    "venue", "conference",
}


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
            or token_key in _ROLE_LABELS
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

    if terms:
        compact = clean_text(" ".join([anchor, *terms[:3]]))
        if compact and compact.casefold() != anchor.casefold():
            queries.append(compact)

        for term in (terms[-1], terms[0]):
            query = clean_text(f"{anchor} {term}")
            if query and query.casefold() not in {q.casefold() for q in queries}:
                queries.append(query)
            if len(queries) >= _MAX_QUERY_BUDGET:
                break

    core_anchor_words = tokens(anchor)
    while len(core_anchor_words) > 1 and key(core_anchor_words[-1]) in VISUAL_DESCRIPTORS:
        core_anchor_words.pop()
    core_anchor = clean_text(" ".join(core_anchor_words))
    if core_anchor and core_anchor.casefold() not in {q.casefold() for q in queries} and len(queries) < _MAX_QUERY_BUDGET:
        queries.append(core_anchor)

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
        from visual_search_intent_runtime import resolve_visual_search_intent

        visual_intent = resolve_visual_search_intent(seg, video_title)
        if not visual_intent.subject or not visual_intent.query:
            raise RuntimeError("No grounded visual search intent could be resolved from the scene.")

        # Store the canonical contract so retrieval and QA consume the exact
        # same subject/type/query instead of independently re-classifying it.
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

        return [visual_intent.query], visual_intent.visual_type

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
    factual_entity = clean_text(resolution.get("factual_entity", "") or original_entity or subject)
    visual_subject = clean_text(resolution.get("subject", "") or factual_entity or subject)

    prepared = dict(candidate)
    prepared["factual_primary_entity"] = factual_entity
    prepared["original_primary_entity"] = original_entity
    prepared["factual_voiceover"] = original_voiceover
    prepared["factual_visual_intent"] = original_intent
    prepared["factual_search_prompt"] = original_prompt

    # Explicit visual_type is treated as a hint, not an authority. First infer
    # the role from the actual subject + visual intent; only fall back to the
    # pre-existing type when the generic evidence cannot identify a role.
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

    # Primary retrieval identity is cleaned/grounded, while the original model
    # output remains available under original_primary_entity and factual_voiceover.
    prepared["visual_genre"] = classify_visual_genre(prepared, visual_subject or factual_entity, prepared.get("visual_type", "GENERAL_CONTEXT"))
    prepared["primary_entity"] = visual_subject or factual_entity
    prepared["visual_search_subject"] = visual_subject or factual_entity
    prepared["visual_subject_locked"] = True
    prepared["specific_search_prompt"] = original_prompt
    prepared["visual_intent"] = original_intent
    prepared["visual_context"] = original_context
    prepared["voiceover"] = original_voiceover
    return prepared


def search_slide_visual(visual_runtime_module, bot, scene, category, used_urls, used_hashes, video_title="", manual_query=""):
    _install_runtime_query_guard(visual_runtime_module)
    candidate = build_candidate_scene(scene, lock_visual_subject(scene, video_title), video_title)
    manual_query = clean_text(manual_query)
    if manual_query:
        # Manual input is an explicit visual identity. It becomes the canonical
        # retrieval subject; story facts remain available as context only.
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
    subject = visual_intent.subject
    context = clean_text(candidate.get("specific_search_prompt", "") or candidate.get("visual_context", ""))
    if not subject:
        raise RuntimeError("Visual search refused the scene because no grounded visual identity could be resolved.")

    candidate["sport_or_topic_category"] = category or candidate.get("sport_or_topic_category", "")
    print(
        f"   [Visual Search] Factual subject='{subject}' | "
        f"search context='{context}' | type={candidate.get('visual_type', 'GENERAL_CONTEXT')} "
        f"| genre={candidate.get('visual_genre', 'GENERAL_CONTEXT')}",
        flush=True,
    )
    result = visual_runtime_module._relevant_asset(
        bot, candidate, category, used_urls, used_hashes, video_title
    )

    if isinstance(scene, dict):
        for key_name in (
            "visual_verified", "visual_rescue_reason", "visual_fallback_reason", "visual_query_used",
            "visual_verification_attempts", "visual_type", "visual_genre",
        ):
            if key_name in candidate:
                scene[key_name] = candidate[key_name]
    return result
