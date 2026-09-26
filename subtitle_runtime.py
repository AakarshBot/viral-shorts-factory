"""Build the subtitle data handed from audio timing to the render stage.

This module owns only caption grouping and timing. Rendering decides fonts,
position, colour, animation, and final video composition.
"""
from __future__ import annotations

import re
from typing import Any


def _clean_word(value: Any) -> str:
    text = str(value or "").replace("\u00a0", " ").strip()
    text = re.sub(r"(?<![A-Za-z0-9])_arrow(?:_(?:right|left|up|down))?(?![A-Za-z0-9])", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def build_subtitle_plan(
    word_timings: list[list[dict[str, Any]]] | None,
    *,
    max_words: int = 4,
    max_chars: int = 22,
    max_duration: float = 2.2,
    max_gap: float = 0.6,
    min_duration: float = 0.35,
) -> list[list[dict[str, Any]]]:
    """Group authoritative word timings into render-ready caption cues.

    Output is scene-indexed. Each cue contains:
      start, end, text, words[{text, start, end}]
    No visual styling or rendering decisions are made here.
    """
    plan: list[list[dict[str, Any]]] = []
    for scene in word_timings or []:
        words = []
        for item in scene or []:
            if not isinstance(item, dict):
                continue
            text = _clean_word(item.get("word"))
            if not text:
                continue
            try:
                start = max(0.0, float(item.get("start", 0.0)))
                end = max(start, float(item.get("end", start)))
            except (TypeError, ValueError):
                continue
            words.append({"text": text, "start": start, "end": end})

        cues: list[dict[str, Any]] = []
        current: list[dict[str, Any]] = []

        def flush() -> None:
            if not current:
                return
            start = current[0]["start"]
            end = max(current[-1]["end"], start + min_duration)
            cues.append({
                "start": round(start, 3),
                "end": round(end, 3),
                "text": " ".join(w["text"] for w in current),
                "words": [dict(w) for w in current],
            })
            current.clear()

        for word in words:
            prev = current[-1] if current else None
            if prev is not None:
                phrase_chars = len(" ".join(w["text"] for w in current))
                too_many = len(current) >= max_words
                too_long = phrase_chars + 1 + len(word["text"]) > max_chars
                too_slow = word["end"] - current[0]["start"] > max_duration
                too_big_gap = word["start"] - prev["end"] > max_gap
                sentence_end = prev["text"].rstrip()[-1:] in ".!?"
                if too_many or too_long or too_slow or too_big_gap or sentence_end:
                    flush()
            current.append(word)
        flush()

        for previous, following in zip(cues, cues[1:]):
            if previous["end"] > following["start"]:
                previous["end"] = round(max(previous["start"] + 0.1, following["start"] - 0.01), 3)

        plan.append(cues)

    return plan
