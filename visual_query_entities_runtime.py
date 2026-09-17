"""Deterministic slide-derived visual search subjects.

A visual search query must describe a concrete subject that is actually present
in the current slide script. This module deliberately does not use titles,
editorial adjectives, dates, or scene instructions as query text.
"""
from __future__ import annotations

import re
import unicodedata


_QUERY_MAX = 3
_NOISE = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "from",
    "this", "that", "these", "those", "is", "was", "were", "are", "be", "been",
    "latest", "breaking", "news", "update", "story", "report", "reports", "reported",
    "according", "says", "said", "today", "yesterday", "tomorrow", "interview",
    "press", "conference", "players", "player", "person", "photo", "image", "picture",
    "editorial", "official", "squad", "selection", "meeting", "2024", "2025", "2026",
}
_ROLE_WORDS = {
    "team", "squad", "board", "council", "association", "committee", "club", "government",
    "ministry", "company", "corporation", "university", "institute", "bank", "agency", "authority",
    "cricket", "football", "soccer", "basketball", "tennis", "hockey", "baseball",
}
_COMMON_STARTERS = {"india", "indian", "england", "english", "australia", "australian", "pakistan", "pakistani", "bangladesh", "bangladeshi", "sri", "south", "new", "united", "world"}


def _clean(value: object) -> str:
    text = str(value or "").replace("\u200b", " ")
    return re.sub(r"\s+", " ", text).strip(" ,.;:|\"'()[]{}")


def _normalise_subject(value: str) -> str:
    value = _clean(value)
    if not value:
        return ""
    return value


def _words(value: str) -> list[str]:
    words: list[str] = []
    current: list[str] = []
    for char in value:
        cat = unicodedata.category(char)
        if char.isalnum() or cat.startswith("M"):
            current.append(char)
        elif char in {"'", "’", "-", "/"} and current:
            current.append(char)
        elif current:
            token = "".join(current).strip("'-/’")
            if token:
                words.append(token)
            current = []
    if current:
        token = "".join(current).strip("'-/’")
        if token:
            words.append(token)
    return words


def _key(value: str) -> str:
    return "".join(c for c in value.casefold() if c.isalnum() or unicodedata.category(c).startswith("M"))


def _append_unique(items: list[str], value: str) -> None:
    value = _normalise_subject(value)
    if not value:
        return
    lowered = value.casefold()
    if lowered not in {item.casefold() for item in items}:
        items.append(value)


def _extract_person_names(text: str) -> list[str]:
    """Find obvious multi-word proper names in English script."""
    tokens = _words(text)
    result: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if not token or not token[0].isupper():
            i += 1
            continue
        run = [token]
        j = i + 1
        while j < len(tokens) and tokens[j][0].isupper() and len(run) < 4:
            run.append(tokens[j])
            j += 1
        candidate = " ".join(run).strip()
        keys = {_key(word) for word in run}
        if len(run) >= 2 and not keys.intersection(_NOISE) and not any(k in _ROLE_WORDS for k in keys):
            if candidate.casefold() not in {x.casefold() for x in result}:
                result.append(candidate)
        i = max(i + 1, j)
    return result


def _extract_group_subjects(text: str) -> list[str]:
    """Find concrete named groups such as 'Indian cricket team'."""
    tokens = _words(text)
    result: list[str] = []
    for i in range(len(tokens) - 1):
        a, b = tokens[i], tokens[i + 1]
        ak, bk = _key(a), _key(b)
        if bk in _ROLE_WORDS and ak in _COMMON_STARTERS:
            _append_unique(result, f"{a} {b}")
        if i + 2 < len(tokens):
            c = tokens[i + 2]
            if _key(c) in {"team", "squad", "board", "association", "government"} and ak in _COMMON_STARTERS:
                _append_unique(result, f"{a} {b} {c}")
    return result


def extract_slide_search_subjects(scene: dict) -> list[str]:
    """Return up to three clean searchable subjects from one slide.

    Primary entity is retained first. Additional subjects come from the actual
    spoken slide script, then concrete group nouns. No title/context rewrite is
    ever returned.
    """
    if not isinstance(scene, dict):
        return []
    subjects: list[str] = []
    primary = _normalise_subject(scene.get("primary_entity", ""))
    if primary:
        _append_unique(subjects, primary)

    script = _clean(scene.get("voiceover", ""))
    # A search prompt may contain explicitly named subjects, but only the
    # concrete subject phrases survive; routing/context words are discarded.
    auxiliary = _clean(scene.get("specific_search_prompt", ""))

    for name in _extract_person_names(script):
        if len(subjects) >= _QUERY_MAX:
            break
        _append_unique(subjects, name)

    for group in _extract_group_subjects(script):
        if len(subjects) >= _QUERY_MAX:
            break
        _append_unique(subjects, group)

    # A common upstream script formatter can put a named person only in the
    # structured prompt. Reuse that prompt strictly as a source of proper names,
    # never as a complete search-query rewrite.
    if len(subjects) < _QUERY_MAX and auxiliary:
        for name in _extract_person_names(auxiliary):
            if len(subjects) >= _QUERY_MAX:
                break
            _append_unique(subjects, name)
        for group in _extract_group_subjects(auxiliary):
            if len(subjects) >= _QUERY_MAX:
                break
            _append_unique(subjects, group)

    return subjects[:_QUERY_MAX]
