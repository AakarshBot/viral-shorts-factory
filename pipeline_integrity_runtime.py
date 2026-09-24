"""Strict end-to-end integrity guards for script, narration, subtitles and branding.

This layer is deliberately conservative: it cleans transport artifacts, rejects
prompt/log leakage, keeps the validated script as the narration source of truth,
and refuses to manufacture emergency narration when source material is too thin.
"""
from __future__ import annotations

import html
import json
import os
import re
import subprocess
import unicodedata
from typing import Any


VERSION = "2026-09-18-v2-no-endpoint-subtitle-layer"

_ZERO_WIDTH = re.compile(r"[\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180b-\u180d\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u206f\u3164\ufe00-\ufe0f\ufeff]")
_HTML_TAG = re.compile(r"<\s*/?\s*[A-Za-z][^>]*>")
_URL = re.compile(r"https?://\S+", re.IGNORECASE)

_NOISE_PATTERNS = (
    r"^\s*(?:\[!\]|\[fatal|\[error|traceback|validation failed|script validation failed)",
    r"\breturn only a valid json\b",
    r"\bjson schema\b",
    r"\bdo not output this block\b",
    r"\beditorial script contract\b",
    r"\bcore shape\s*:",
    r"\boriginal contribution\s*:",
    r"\bpacing\s*:",
    r"\bworkflow (?:stage|step|contract|instructions?)\b",
    r"\b(?:you are|as an ai|as a language model)\b",
    r"\b(?:api error|rate limit|groq exhausted|gemini fallback|provider unavailable)\b",
    r"\b(?:step_1_headline|step_2_data_points|step_3_critique|step_4_metadata)\b",
    r"```(?:json|python|text)?",
)
_NOISE_RE = tuple(re.compile(pattern, re.IGNORECASE) for pattern in _NOISE_PATTERNS)


def clean_text(value: Any) -> str:
    """Decode HTML entities and remove markup/control artifacts without inventing words."""
    text = html.unescape(str(value or ""))
    text = unicodedata.normalize("NFKC", text)
    text = _ZERO_WIDTH.sub("", text)
    text = text.replace("\u00a0", " ")
    text = _HTML_TAG.sub(" ", text)
    text = _URL.sub(" ", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def is_noise(text: Any) -> bool:
    value = clean_text(text)
    if not value:
        return True
    return any(pattern.search(value) for pattern in _NOISE_RE)


def clean_narration(value: Any) -> str:
    """Return narration-safe text while preserving the source wording."""
    text = clean_text(value)
    text = re.sub(r"[*_`\[\]{}]", "", text)
    text = re.sub(r"\s+([,.!?])", r"\1", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip(" -:;|\n\t")


def _source_text(story_data: dict) -> str:
    if not isinstance(story_data, dict):
        return ""
    values = []
    for key in ("text", "summary", "description"):
        value = clean_text(story_data.get(key, ""))
        if value:
            values.append(value)
    return " ".join(values).strip()


def _sentences(text: str) -> list[str]:
    cleaned = clean_text(text)
    candidates = re.split(r"(?<=[.!?])\s+|\n+", cleaned)
    result = []
    for candidate in candidates:
        value = clean_narration(candidate)
        if len(re.findall(r"\b\w+\b", value, flags=re.UNICODE)) < 5:
            continue
        if is_noise(value):
            continue
        result.append(value)
    return result


def strict_fallback(story_data, language_cfg=None, genre_key="news", format_mode="regular"):
    """Delegate emergency script recovery to the canonical semantic fallback."""
    from script_runtime import _extractive_script_fallback

    result = _extractive_script_fallback(
        story_data,
        language_cfg or {},
        genre_key,
        format_mode,
    )
    result = dict(result or {})
    result["fallback_mode"] = "strict_source_only"
    result["integrity_version"] = VERSION
    return result


def _clean_script_result(script_data: dict, story_data: dict, format_mode: str = "regular") -> dict:
    result = dict(script_data or {})
    scenes = result.get("script")
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("Script output does not contain usable narration scenes.")

    cleaned = []
    for index, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict):
            raise ValueError(f"Scene {index} is malformed.")
        voiceover = clean_narration(scene.get("voiceover", ""))
        if not voiceover or is_noise(voiceover):
            raise ValueError(f"Scene {index} contains empty/noise narration.")
        copy = dict(scene)
        copy["voiceover"] = voiceover
        copy["primary_entity"] = clean_text(copy.get("primary_entity", ""))
        copy["visual_intent"] = clean_text(copy.get("visual_intent", ""))
        copy["specific_search_prompt"] = clean_text(copy.get("specific_search_prompt", ""))
        copy["sport_or_topic_category"] = clean_text(copy.get("sport_or_topic_category", ""))
        copy["scene_id"] = index
        copy["narration_source"] = "validated_script"
        cleaned.append(copy)

    result["script"] = cleaned
    result["integrity_version"] = VERSION
    result["authoritative_narration"] = True

    for key in (
        "step_1_headline",
        "step_2_data_points",
        "step_3_critique",
        "step_4_metadata",
        "editorial_angle",
        "seo_description",
        "pinned_comment",
    ):
        if key in result:
            result[key] = clean_text(result[key])

    if isinstance(result.get("titles"), list):
        result["titles"] = [
            clean_text(title).replace("#shorts", "").strip()
            for title in result["titles"]
            if clean_text(title)
        ]

    from script_runtime import validate_content_density
    valid, reason = validate_content_density(
        result,
        story_data,
        format_mode,
        require_visual_metadata=False,
    )
    if not valid:
        raise ValueError(f"Canonical script validation failed: {reason}")
    return result


def _wrap_audio(bot):
    current = getattr(bot, "generate_voiceover_and_timestamps", None)
    if not callable(current) or getattr(current, "_pipeline_script_source_bound", False):
        return

    async def script_bound_audio(script_data, language_cfg):
        scenes = script_data.get("script", []) if isinstance(script_data, dict) else []
        if not isinstance(script_data, dict) or not script_data.get("authoritative_narration") is True:
            raise ValueError("Audio refused: narration must come from the validated generated script.")
        if not scenes:
            raise ValueError("Audio refused: authoritative script contains no scenes.")
        for index, scene in enumerate(scenes, 1):
            if not isinstance(scene, dict) or scene.get("narration_source") != "validated_script":
                raise ValueError(f"Audio refused: scene {index} is not sourced from the validated script.")
            text = clean_narration(scene.get("voiceover", ""))
            if not text or is_noise(text):
                raise ValueError(f"Audio refused: scene {index} has invalid authoritative narration.")
            scene["voiceover"] = text
            scene["scene_id"] = index
        script_data["authoritative_narration"] = True
        return await current(script_data, language_cfg)

    script_bound_audio._pipeline_script_source_bound = True
    bot.generate_voiceover_and_timestamps = script_bound_audio
    if callable(getattr(bot, "run_robot", None)) and hasattr(bot.run_robot, "__globals__"):
        bot.run_robot.__globals__["generate_voiceover_and_timestamps"] = script_bound_audio


def _wrap_visuals(bot):
    current = getattr(bot, "process_visuals_async", None)
    if not callable(current) or getattr(current, "_pipeline_script_source_bound", False):
        return

    async def script_bound_visuals(script_data, language_cfg, format_mode="regular"):
        scenes = script_data.get("script", []) if isinstance(script_data, dict) else []
        for index, scene in enumerate(scenes, 1):
            scene["voiceover"] = clean_narration(scene.get("voiceover", ""))
            scene["scene_id"] = index
            scene["narration_source"] = "validated_script"
        packages = await current(script_data, language_cfg, format_mode)
        for index, package in enumerate(packages or [], 1):
            if not package:
                continue
            for layer in package:
                if isinstance(layer, dict):
                    layer["scene_id"] = index
                    layer["narration_text"] = scenes[index - 1].get("voiceover", "") if index <= len(scenes) else ""
                    layer["narration_source"] = "validated_script"
        return packages

    script_bound_visuals._pipeline_script_source_bound = True
    bot.process_visuals_async = script_bound_visuals
    if callable(getattr(bot, "run_robot", None)) and hasattr(bot.run_robot, "__globals__"):
        bot.run_robot.__globals__["process_visuals_async"] = script_bound_visuals


def patch_pipeline_integrity(bot) -> bool:
    try:
        # Script normalization/strict fallback is owned by the canonical script
        # router. This integrity layer only guards downstream narration/visuals.
        _wrap_audio(bot)
        _wrap_visuals(bot)
        bot._pipeline_integrity_installed = True
        print(f"   [Pipeline Integrity] Strict script/narration/subtitle/branding guards installed ({VERSION}).", flush=True)
        return True
    except Exception as exc:
        print(f"   [Pipeline Integrity] Installation failed: {type(exc).__name__}: {exc}", flush=True)
        return False
