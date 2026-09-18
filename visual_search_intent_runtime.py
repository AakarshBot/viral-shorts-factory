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
from visual_taxonomy_runtime import classify_visual_genre, genre_query_hints


@dataclass(frozen=True)
class VisualSearchIntent:
    subject: str
    visual_type: str
    visual_genre: str
    query: str
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


def resolve_visual_search_intent(scene: dict, video_title: str = "") -> VisualSearchIntent:
    """Resolve the single subject/type/query contract for one scene."""
    scene = scene if isinstance(scene, dict) else {}
    manual = _clean(scene.get("manual_visual_query", ""))
    base = dict(scene)

    if manual:
        resolution = resolve_subject({"primary_entity": manual}, video_title)
        subject = clean_text(resolution.get("subject") or manual)
        visual_type = str(resolution.get("visual_type") or "GENERAL_CONTEXT").upper()
        confidence = 1.0
        query = subject
    else:
        resolution = resolve_subject(base, video_title)
        subject = clean_text(resolution.get("subject") or resolution.get("factual_entity", ""))
        visual_type = str(resolution.get("visual_type") or "GENERAL_CONTEXT").upper()
        confidence = float(resolution.get("confidence") or 0.0)
        # The automatic first query is intentionally exact. Retrieval quality is
        # improved by provider fan-out and candidate selection, not by stuffing
        # narration, titles or model prompts into the opening search.
        query = subject

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
