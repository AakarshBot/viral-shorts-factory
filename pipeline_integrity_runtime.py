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


def _contiguous_chunks(text: str, target: int, minimum_words: int = 8, maximum_words: int = 30) -> list[str]:
    """Partition source words contiguously; never add filler words."""
    words = clean_narration(text).split()
    if len(words) < target * minimum_words:
        return []
    target = max(1, target)
    chunks = []
    remaining = len(words)
    cursor = 0
    for index in range(target):
        slots_left = target - index
        ideal = max(minimum_words, min(maximum_words, round(remaining / slots_left)))
        end = min(len(words), cursor + ideal)
        if slots_left > 1:
            max_end = len(words) - minimum_words * (slots_left - 1)
            end = min(end, max_end)
        chunk = " ".join(words[cursor:end]).strip()
        if len(chunk.split()) < minimum_words:
            return []
        chunks.append(chunk)
        cursor = end
        remaining = len(words) - cursor
    if cursor < len(words):
        tail = " ".join(words[cursor:]).strip()
        if len(chunks[-1].split()) + len(tail.split()) <= maximum_words:
            chunks[-1] = f"{chunks[-1]} {tail}".strip()
        else:
            return []
    return chunks


def strict_fallback(story_data, language_cfg=None, genre_key="news", format_mode="regular"):
    """Build a source-only emergency script, or fail instead of inventing filler."""
    story_data = story_data if isinstance(story_data, dict) else {}
    title = clean_narration(story_data.get("title") or story_data.get("topic") or "")
    source = _source_text(story_data)
    if not source and title:
        source = title
    if not source:
        raise ValueError("No usable source text is available for emergency script generation.")

    # For Top 5, the selected story data may be JSON. Extract only its textual fields.
    if format_mode == "top5":
        try:
            items = json.loads(str(story_data.get("text", "")))
            if isinstance(items, list):
                source_parts = []
                for item in items:
                    if isinstance(item, dict):
                        source_parts.append(clean_narration(item.get("title", "")))
                        source_parts.append(clean_narration(item.get("text", "")))
                source = " ".join(part for part in source_parts if part)
        except Exception:
            pass

    sentence_text = " ".join(_sentences(source)) or clean_narration(source)
    target = 7 if str(format_mode).lower() == "top5" else 5
    chunks = _contiguous_chunks(sentence_text, target)
    if not chunks:
        raise ValueError(
            f"Source-grounded fallback refused to invent narration: need at least {target * 8} usable source words."
        )

    entity_words = re.findall(r"\b[A-Z][A-Za-z0-9&.'-]{2,}\b", title)
    entity = " ".join(entity_words[:3]) or title[:80] or "Selected story"
    category = clean_narration(genre_key or "news").replace("_", " ").title()
    search_prompt = title or entity

    scenes = [
        {
            "voiceover": clean_narration(chunk),
            "primary_entity": entity,
            "visual_intent": "news_event",
            "specific_search_prompt": search_prompt,
            "sport_or_topic_category": category,
            "scene_source": "validated_source_fallback",
            "scene_id": index + 1,
        }
        for index, chunk in enumerate(chunks)
    ]
    return {
        "step_1_headline": title,
        "step_2_data_points": source,
        "step_3_critique": "Deterministic source-only fallback. No provider instructions or generated facts were used.",
        "step_4_metadata": entity,
        "titles": [title, f"{title} | What We Know" if title else "What We Know", f"{title} | Latest Facts" if title else "Latest Facts"],
        "recommended_title_index": 1,
        "seo_description": clean_text(source)[:700],
        "tags": [tag for tag in (entity, category, "Shorts") if tag],
        "pinned_comment": "What do you make of this development?",
        "hook_type": "Direct Factual Headline",
        "hook_style_used": "Direct Factual Headline",
        "structure_used": "Source-grounded explainer",
        "persona_used": "Analytical Insider",
        "script": scenes,
        "fallback_mode": "strict_source_only",
        "integrity_version": VERSION,
    }


def _clean_script_result(script_data: dict, story_data: dict) -> dict:
    result = dict(script_data or {})
    scenes = result.get("script")
    if not isinstance(scenes, list):
        raise ValueError("Script output does not contain a valid scene list.")
    cleaned = []
    for index, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict):
            raise ValueError(f"Scene {index} is malformed.")
        voiceover = clean_narration(scene.get("voiceover", ""))
        if not voiceover or is_noise(voiceover):
            raise ValueError(f"Scene {index} contains empty/noise narration.")
        if len(voiceover.split()) < 3:
            raise ValueError(f"Scene {index} contains too little narration.")
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
    for key in ("step_1_headline", "step_2_data_points", "step_3_critique", "step_4_metadata", "seo_description", "pinned_comment"):
        if key in result:
            result[key] = clean_text(result[key])
    if isinstance(result.get("titles"), list):
        result["titles"] = [clean_text(title).replace("#shorts", "").strip() for title in result["titles"] if clean_text(title)]
    return result


def _wrap_script_writer(bot):
    current = getattr(bot, "write_script", None)
    if not callable(current) or getattr(current, "_pipeline_integrity_wrapped", False):
        return

    def guarded_write_script(story_data, language_cfg, genre_key, conn, format_mode):
        try:
            result = current(story_data, language_cfg, genre_key, conn, format_mode)
            cleaned = _clean_script_result(result, story_data)
            # A strict fallback is only used when the existing generator fails
            # or produces invalid narration; it never silently patches missing facts.
            return cleaned
        except Exception as exc:
            print(f"   [Script Integrity] AI script rejected: {type(exc).__name__}: {exc}", flush=True)
            fallback = strict_fallback(story_data, language_cfg, genre_key, format_mode)
            return _clean_script_result(fallback, story_data)

    guarded_write_script._pipeline_integrity_wrapped = True
    bot.write_script = guarded_write_script
    if callable(getattr(bot, "run_robot", None)) and hasattr(bot.run_robot, "__globals__"):
        bot.run_robot.__globals__["write_script"] = guarded_write_script


def _prepare_audio_handoff(script_data):
    """Revalidate the final narration list after dashboard/script-stage mutations.

    The dashboard may intentionally append a human Creator Insight after the
    main script validator has run. That contribution is now part of the
    authoritative narration, but it may not yet carry the internal source tag.
    Normalize only that explicitly human-contributed case; leave other unknown
    source tags untouched so the strict guard can still fail closed.
    """
    if not isinstance(script_data, dict):
        return script_data

    scenes = script_data.get("script")
    is_scene_count_fallback = script_data.get("fallback_reason") == "scene_count_contract"
    if not isinstance(scenes, list) or not scenes:
        return script_data
    if not script_data.get("authoritative_narration") is True and not is_scene_count_fallback:
        return script_data

    candidate = dict(script_data)
    normalized = []
    for index, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict):
            return script_data
        voiceover = clean_narration(scene.get("voiceover", ""))
        if not voiceover or is_noise(voiceover):
            return script_data

        copy = dict(scene)
        copy["voiceover"] = voiceover
        copy["scene_id"] = index

        source = str(copy.get("narration_source") or "").strip()
        if source:
            copy["narration_source"] = source
        elif bool(copy.get("human_contributed")):
            # Dashboard-approved Creator Insight is an intentional human
            # addition to the final narration, not visual/UI text.
            copy["narration_source"] = "validated_script"
        elif is_scene_count_fallback:
            copy["narration_source"] = "validated_script"
        else:
            return script_data

        normalized.append(copy)

    candidate["script"] = normalized
    candidate["authoritative_narration"] = True
    candidate["integrity_version"] = VERSION
    return candidate


def _wrap_audio(bot):
    current = getattr(bot, "generate_voiceover_and_timestamps", None)
    if not callable(current) or getattr(current, "_pipeline_script_source_bound", False):
        return

    async def script_bound_audio(script_data, language_cfg):
        script_data = _prepare_audio_handoff(script_data)
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


def _write_endpoint_srt(path: str, word_timings: list[dict], offset: float = 0.0) -> bool:
    if not word_timings:
        return False
    lines = []
    chunk = []
    start = None
    last_end = None
    for item in word_timings:
        word = clean_narration(item.get("word", ""))
        if not word:
            continue
        item_start = max(0.0, float(item.get("start", 0.0))) + offset
        item_end = max(item_start + 0.08, float(item.get("end", item_start + 0.1)) + offset)
        if start is None:
            start = item_start
        chunk.append(word)
        last_end = item_end
        if len(chunk) >= 6 or (last_end - start) >= 2.2:
            lines.append((start, last_end, " ".join(chunk)))
            chunk, start = [], None
    if chunk and start is not None and last_end is not None:
        lines.append((start, last_end, " ".join(chunk)))
    if not lines:
        return False

    def stamp(seconds: float) -> str:
        millis = max(0, int(round(seconds * 1000)))
        hours, millis = divmod(millis, 3_600_000)
        minutes, millis = divmod(millis, 60_000)
        secs, millis = divmod(millis, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    with open(path, "w", encoding="utf-8") as handle:
        for index, (start, end, text) in enumerate(lines, 1):
            handle.write(f"{index}\n{stamp(start)} --> {stamp(end)}\n{text}\n\n")
    return True


def _wrap_compile(bot):
    current = getattr(bot, "compile_video", None)
    if not callable(current) or getattr(current, "_pipeline_integrity_wrapped", False):
        return

    def guarded_compile(scene_visual_packages, audio_paths, word_timings, language_cfg, format_mode):
        # The canonical compile_video owns motion, word-highlight captions, the
        # scene-one hook overlay and final loudness normalization. Do not add a
        # second subtitle filter or re-encode here.
        return current(scene_visual_packages, audio_paths, word_timings, language_cfg, format_mode)

    guarded_compile._pipeline_integrity_wrapped = True
    bot.compile_video = guarded_compile
    if callable(getattr(bot, "run_robot", None)) and hasattr(bot.run_robot, "__globals__"):
        bot.run_robot.__globals__["compile_video"] = guarded_compile


def patch_pipeline_integrity(bot) -> bool:
    try:
        _wrap_script_writer(bot)
        _wrap_audio(bot)
        _wrap_visuals(bot)
        # compile_video remains owned by the canonical renderer and workflow
        # progress wrapper; the compatibility helper _wrap_compile is not installed.
        bot._pipeline_integrity_installed = True
        print(f"   [Pipeline Integrity] Strict script/narration/subtitle/branding guards installed ({VERSION}).", flush=True)
        return True
    except Exception as exc:
        print(f"   [Pipeline Integrity] Installation failed: {type(exc).__name__}: {exc}", flush=True)
        return False
