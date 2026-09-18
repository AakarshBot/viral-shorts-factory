"""Canonical visual-search intent for the Shorts factory.

One scene gets one authoritative retrieval intent. The first automatic query is
the clean visual subject itself. If that exact query genuinely fails to produce
a usable candidate, retrieval may perform at most one deterministic,
evidence-based refinement. Manual queries remain exact and authoritative.
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
from visual_taxonomy_runtime import classify_visual_genre


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


def _context_terms(text: str, subject: str) -> list[str]:
    """Extract only a few useful refinement terms; never copy the prompt."""
    subject_keys = {key(word) for word in tokens(subject)}
    seen = set()
    terms: list[str] = []
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
            or token_key in {
                "person", "people", "organization", "organisation", "company",
                "product", "device", "location", "geography", "concept",
                "process", "event", "document", "quote", "quotation",
                "statistic", "comparison", "timeline", "scientific",
                "technical", "abstract", "team", "members", "venue",
                "conference",
            }
        ):
            continue
        if token_key not in seen:
            seen.add(token_key)
            terms.append(word)
        if len(terms) >= 3:
            break
    return terms


def _automatic_query_ladder(scene: dict, subject: str) -> list[str]:
    """Build the bounded identity-first ladder used by the visual search."""
    subject = clean_text(subject)
    if not subject:
        return []
    context = _clean(
        scene.get("factual_search_prompt")
        or scene.get("specific_search_prompt")
        or scene.get("factual_visual_intent")
        or scene.get("visual_intent")
        or scene.get("visual_context")
    )
    terms = _context_terms(context, subject)
    queries = [subject]
    if terms:
        compact = _clean(" ".join([subject, *terms[:3]]))
        if compact.casefold() != subject.casefold():
            queries.append(compact)
        for term in (terms[-1], terms[0]):
            candidate = _clean(f"{subject} {term}")
            if candidate.casefold() not in {item.casefold() for item in queries}:
                queries.append(candidate)
            if len(queries) >= 5:
                break
    return queries[:5]


def resolve_visual_search_intent(scene: dict, video_title: str = "") -> VisualSearchIntent:
    """Resolve one scene's subject, bounded query ladder and visual type."""
    scene = scene if isinstance(scene, dict) else {}
    manual = _clean(scene.get("manual_visual_query", ""))
    resolution = resolve_subject(dict(scene), video_title)
    auto_subject = clean_text(
        resolution.get("subject") or resolution.get("factual_entity") or scene.get("primary_entity", "")
    )
    visual_type = str(resolution.get("visual_type") or "GENERAL_CONTEXT").upper()
    confidence = float(resolution.get("confidence") or 0.0)

    if manual:
        subject = manual
        query = manual
        queries = [manual]
        # The first supplied manual query owns the opening scene. Only if that
        # exact query produces no usable image may the normal automatic ladder
        # take over for that scene. Other manual queries stay authoritative.
        if int(scene.get("manual_visual_query_index", 0) or 0) == 1 and auto_subject:
            for fallback in _automatic_query_ladder(scene, auto_subject):
                if fallback.casefold() not in {item.casefold() for item in queries}:
                    queries.append(fallback)
    else:
        subject = auto_subject
        query = subject
        queries = _automatic_query_ladder(scene, subject)

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
        queries=tuple(queries),
        intent=intent,
        context=context,
        confidence=confidence,
        manual=bool(manual),
    )


__all__ = ["VisualSearchIntent", "resolve_visual_search_intent"]
