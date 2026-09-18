"""Canonical visual-search intent for the Shorts factory.

One scene gets one retrieval intent. Query generation, provider selection and
semantic QA must consume this same object; downstream layers must not
re-classify the manual/factual subject independently.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from visual_semantic_guard_runtime import clean_text, resolve_subject


@dataclass(frozen=True)
class VisualSearchIntent:
    subject: str
    visual_type: str
    query: str
    intent: str
    context: str
    confidence: float
    manual: bool = False


def _clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def resolve_visual_search_intent(scene: dict, video_title: str = "") -> VisualSearchIntent:
    """Resolve the single subject/type/query contract for one scene."""
    scene = scene if isinstance(scene, dict) else {}
    manual = _clean(scene.get("manual_visual_query", ""))
    base = dict(scene)

    if manual:
        # Manual visual queries are explicit retrieval identities. Resolve their
        # role once, but do not mix them with the story's factual entity.
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
        query = _initial_query(subject, base, visual_type)

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
        query=query,
        intent=intent,
        context=context,
        confidence=confidence,
        manual=bool(manual),
    )


def _initial_query(subject: str, scene: dict, visual_type: str) -> str:
    """Build one high-signal query; do not manufacture a query ladder."""
    if not subject:
        return ""

    # Prefer the explicit search prompt only when it contains the resolved
    # subject. Otherwise use the subject plus a small amount of visual intent.
    prompt = _clean(scene.get("specific_search_prompt") or scene.get("factual_search_prompt"))
    if prompt:
        subject_tokens = {x.casefold() for x in re.findall(r"[\w][\w'/-]*", subject)}
        prompt_tokens = re.findall(r"[\w][\w'/-]*", prompt)
        if subject_tokens and subject_tokens.issubset({x.casefold() for x in prompt_tokens}):
            return prompt[:240]

    visual_intent = _clean(scene.get("factual_visual_intent") or scene.get("visual_intent"))
    if visual_intent:
        # Keep only the first short descriptive clause. Long narration is noise.
        clause = re.split(r"[.;:!?]", visual_intent, maxsplit=1)[0].strip()
        if clause:
            return _clean(f"{subject} {clause}")[:240]

    return subject[:240]


def reformulate_visual_query(intent: VisualSearchIntent, reason: str) -> str:
    """Make one evidence-driven retry query after a failed first retrieval."""
    subject = intent.subject
    reason = _clean(reason).casefold()

    if not subject:
        return ""

    # If the first query was too broad, add the strongest available contextual
    # signal. If it was too specific, fall back to the canonical identity.
    if "no candidate" in reason or "no candidates" in reason:
        if intent.context:
            context = re.split(r"[.;:!?]", intent.context, maxsplit=1)[0].strip()
            query = _clean(f"{subject} {context}")
            if query.casefold() != intent.query.casefold():
                return query[:240]

    if "mismatch" in reason or "ambiguous" in reason or "unverified" in reason:
        if intent.visual_type == "PERSON":
            return subject
        if intent.visual_type == "ORGANIZATION":
            return _clean(f"{subject} official")
        if intent.visual_type == "LOCATION":
            return _clean(f"{subject} landmark")
        if intent.visual_type == "EVENT":
            return _clean(f"{subject} event")
        if intent.intent:
            return _clean(f"{subject} {intent.intent}")[:240]

    return subject


__all__ = ["VisualSearchIntent", "resolve_visual_search_intent", "reformulate_visual_query"]
