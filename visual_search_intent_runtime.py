"""Canonical visual-search intent for the Shorts factory.

Each automatic scene gets one factual identity plus a compact scene-specific
visual query. The exact identity remains available as the first fallback, and
retrieval may use at most one additional evidence-based refinement. Manual
queries remain exact and authoritative.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from visual_semantic_guard_runtime import (
    AUXILIARY_WORDS,
    DISCOURSE_PREFIXES,
    GENERIC_NOISE,
    STOPWORDS,
    VISUAL_DESCRIPTORS,
    clean_text,
    key,
    resolve_subject,
    tokens,
)
from visual_taxonomy_runtime import classify_visual_genre, genre_query_hints


@dataclass(frozen=True)
class VisualSearchIntent:
    subject: str
    visual_type: str
    visual_genre: str
    query: str
    queries: tuple[str, ...]
    intent: str
    context: str
    confidence: float
    manual: bool = False


def _clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _context_terms(text: str, subject: str, limit: int = 3) -> list[str]:
    """Extract a few concrete scene terms without copying editorial prose."""
    subject_keys = {key(word) for word in tokens(subject)}
    seen = set()
    terms: list[str] = []
    generic_terms = {
        "person", "people", "organization", "organisation", "company", "product",
        "device", "location", "geography", "concept", "process", "event",
        "document", "quote", "quotation", "statistic", "comparison", "timeline",
        "scientific", "technical", "abstract", "team", "members", "venue",
        "conference", "show", "showing", "scene", "visual", "image", "photo",
        "picture", "editorial", "footage", "moment", "shot", "view", "looks",
        "look", "appears", "appearing", "depict", "depicting", "someone",
        "individual", "realistic",
    }
    for word in tokens(text):
        token_key = key(word)
        if (
            not token_key
            or token_key in subject_keys
            or token_key in GENERIC_NOISE
            or token_key in STOPWORDS
            or token_key in DISCOURSE_PREFIXES
            or token_key in AUXILIARY_WORDS
            or token_key in VISUAL_DESCRIPTORS
            or token_key in generic_terms
        ):
            continue
        if token_key not in seen:
            seen.add(token_key)
            terms.append(word)
        if len(terms) >= limit:
            break
    return terms


def _scene_terms(scene: dict, subject: str) -> list[str]:
    """Prefer explicit visual semantics, then grounded context, then voiceover."""
    seen = set()
    terms: list[str] = []
    fields = (
        "factual_visual_intent", "visual_intent", "visual_context",
        "factual_search_prompt", "specific_search_prompt",
        "factual_voiceover", "voiceover",
    )
    for field in fields:
        for term in _context_terms(_clean(scene.get(field, "")), subject, limit=3):
            token_key = key(term)
            if token_key not in seen:
                seen.add(token_key)
                terms.append(term)
            if len(terms) >= 3:
                return terms
    return terms


def resolve_visual_search_intent(scene: dict, video_title: str = "") -> VisualSearchIntent:
    """Resolve the single subject/type/query contract for one scene."""
    scene = scene if isinstance(scene, dict) else {}
    manual = _clean(scene.get("manual_visual_query", ""))
    base = dict(scene)

    if manual:
        # Manual search text is user-authored and therefore exact. Resolve only
        # the scene's visual type; never rewrite the manual query through the
        # factual-subject resolver.
        scene_resolution = resolve_subject(base, video_title)
        subject = manual
        visual_type = str(scene_resolution.get("visual_type") or "GENERAL_CONTEXT").upper()
        confidence = 1.0
        query = manual
        queries = [manual]
    else:
        resolution = resolve_subject(base, video_title)
        subject = clean_text(resolution.get("subject") or resolution.get("factual_entity", ""))
        visual_type = str(resolution.get("visual_type") or "GENERAL_CONTEXT").upper()
        confidence = float(resolution.get("confidence") or 0.0)
        # Keep the factual identity mandatory, but use this slide's
        # concrete visual evidence so different scenes do not collapse to the
        # same entity-only query. The bare identity remains the first fallback.
        scene_terms = _scene_terms(scene, subject)
        query = _clean(" ".join([subject, *scene_terms]))
        queries = [query]
        if query.casefold() != subject.casefold():
            queries.append(subject)
        for term in scene_terms:
            candidate = _clean(f"{subject} {term}")
            if candidate.casefold() not in {item.casefold() for item in queries}:
                queries.append(candidate)
            if len(queries) >= 3:
                break

    intent = _clean(scene.get("factual_visual_intent") or scene.get("visual_intent"))
    context = _clean(
        scene.get("visual_context")
        or scene.get("factual_search_prompt")
        or scene.get("specific_search_prompt")
        or scene.get("factual_voiceover")
        or scene.get("voiceover")
        or video_title
    )

    return VisualSearchIntent(
        subject=subject,
        visual_type=visual_type,
        visual_genre=classify_visual_genre(scene, subject, visual_type),
        query=query,
        intent=intent,
        context=context,
        confidence=confidence,
        manual=bool(manual),
    )


def reformulate_visual_query(intent: VisualSearchIntent, reason: str) -> str:
    """Return at most one compact, evidence-based refinement.

    This is deliberately not a generic query ladder. The fallback may only add
    a few concrete terms already present in the scene evidence, and manual
    queries never reach this path.
    """
    subject = _clean(intent.subject)
    reason = _clean(reason).casefold()
    if not subject or intent.manual:
        return ""

    if "no candidate" in reason or "no candidates" in reason:
        terms = _context_terms(intent.context, subject)
        if terms:
            query = _clean(" ".join([subject, *terms]))
            if query.casefold() != intent.query.casefold():
                return query[:240]

    if "mismatch" in reason or "ambiguous" in reason or "unverified" in reason:
        # Genre hints are used only when they are short, generic visual
        # descriptors and do not invent a factual identity.
        hints = genre_query_hints(intent.visual_genre)
        if hints:
            terms = _context_terms(" ".join(hints), subject)
            if terms:
                query = _clean(" ".join([subject, *terms[:2]]))
                if query.casefold() != intent.query.casefold():
                    return query[:240]

    return ""


__all__ = ["VisualSearchIntent", "resolve_visual_search_intent", "reformulate_visual_query"]
