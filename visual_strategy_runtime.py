"""Small public facade for the canonical visual-search strategy."""
from __future__ import annotations

from visual_semantic_guard_runtime import clean_text, prepare_scene, resolve_subject
from visual_search_intent_runtime import resolve_visual_search_intent


def _scene_category(scene, category=""):
    if str(category or "").strip():
        return str(category).strip()
    if isinstance(scene, dict):
        for key in ("sport_or_topic_category", "topic_category", "domain", "topic"):
            value = clean_text(scene.get(key, ""))
            if value:
                return value
    return ""


def _prepare(scene, video_title=""):
    prepared = prepare_scene(scene, video_title)
    category = _scene_category(prepared)
    if category and not prepared.get("sport_or_topic_category"):
        prepared["sport_or_topic_category"] = category
    return prepared


def classify_scene(scene, category=""):
    """Expose the canonical semantic visual type."""
    prepared = _prepare(scene)
    return str(resolve_subject(prepared, "").get("visual_type") or "GENERAL_CONTEXT").upper()


def build_scene_visual_brief(scene, video_title="", category=""):
    """Return the factual identity and visual type used by retrieval."""
    prepared = _prepare(scene, video_title)
    if category:
        prepared["sport_or_topic_category"] = category
    resolution = resolve_subject(prepared, video_title)
    factual_entity = clean_text(
        prepared.get("factual_primary_entity") or resolution.get("factual_entity", "")
    )
    visual_subject = clean_text(
        prepared.get("visual_search_subject") or resolution.get("subject", "")
    )
    return {
        "subject": factual_entity,
        "visual_subject": visual_subject,
        "visual_type": str(resolution.get("visual_type") or "GENERAL_CONTEXT").upper(),
        "scene_action": "",
        "scene_context": clean_text(prepared.get("visual_context", "")),
        "scene_index": clean_text(
            prepared.get("scene_index", prepared.get("scene_number", ""))
        ),
        "factual_entity": factual_entity,
        "base_type": "GENERAL_CONTEXT",
        "scene_role": str(resolution.get("visual_type") or "GENERAL_CONTEXT").upper(),
        "domain": _scene_category(prepared, category),
        "confidence": resolution.get("confidence", 0.0),
    }


def build_deep_queries(scene, video_title="", visual_type=None):
    """Expose the canonical bounded retrieval query contract."""
    if not isinstance(scene, dict):
        return [], visual_type or "GENERAL_CONTEXT"
    prepared = _prepare(scene, video_title)
    intent = resolve_visual_search_intent(prepared, video_title)
    return list(intent.queries), (visual_type or intent.visual_type or "GENERAL_CONTEXT")
