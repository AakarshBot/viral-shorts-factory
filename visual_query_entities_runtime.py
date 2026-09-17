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
_COMMON_STARTERS = {
    "india", "indian", "england", "english", "australia", "australian", "pakistan", "pakistani",
    "bangladesh", "bangladeshi", "sri", "south", "new", "united", "world",
}


def _clean(value: object) -> str:
    text = str(value or "").replace("\u200b", " ")
    return re.sub(r"\s+", " ", text).strip(" ,.;:|\"'()[]{}")


def _words(value: str) -> list[str]:
    words: list[str] = []
    current: list[str] = []
    for char in value:
        category = unicodedata.category(char)
        if char.isalnum() or category.startswith("M"):
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
    value = value.casefold()
    if value.endswith("'s") or value.endswith("’s"):
        value = value[:-2]
    return "".join(c for c in value if c.isalnum() or unicodedata.category(c).startswith("M"))


def _append_unique(items: list[str], value: str) -> None:
    value = _clean(value)
    if value and value.casefold() not in {item.casefold() for item in items}:
        items.append(value)


def _extract_person_names(text: str) -> list[str]:
    """Find obvious multi-word proper names in English prose."""
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
        keys = {_key(word) for word in run}
        if len(run) >= 2 and not keys.intersection(_NOISE) and not keys.intersection(_ROLE_WORDS):
            _append_unique(result, " ".join(run).strip("'"))
        i = max(i + 1, j)
    return result


def _extract_group_subjects(text: str) -> list[str]:
    """Find concrete named groups such as 'Indian cricket team'."""
    tokens = _words(text)
    result: list[str] = []
    i = 0
    while i < len(tokens):
        if i + 2 < len(tokens):
            a, b, c = tokens[i], tokens[i + 1], tokens[i + 2]
            if _key(a) in _COMMON_STARTERS and _key(c) in {"team", "squad", "board", "association", "government"}:
                _append_unique(result, f"{a} {b} {c}")
                i += 3
                continue
        if i + 1 < len(tokens):
            a, b = tokens[i], tokens[i + 1]
            if _key(a) in _COMMON_STARTERS and _key(b) in _ROLE_WORDS:
                _append_unique(result, f"{a} {b}")
        i += 1
    return result


def extract_slide_search_subjects(scene: dict) -> list[str]:
    """Return up to three concrete search subjects from one rendered slide."""
    if not isinstance(scene, dict):
        return []
    subjects: list[str] = []
    _append_unique(subjects, scene.get("primary_entity", ""))
    script = _clean(scene.get("voiceover", ""))
    auxiliary = _clean(scene.get("specific_search_prompt", ""))

    for name in _extract_person_names(script):
        if len(subjects) >= _QUERY_MAX:
            break
        _append_unique(subjects, name)
    for group in _extract_group_subjects(script):
        if len(subjects) >= _QUERY_MAX:
            break
        _append_unique(subjects, group)

    # A structured prompt may contain a named subject omitted from narration.
    # Extract only concrete person/group subjects; never use the prompt verbatim.
    if len(subjects) < _QUERY_MAX:
        for name in _extract_person_names(auxiliary):
            if len(subjects) >= _QUERY_MAX:
                break
            _append_unique(subjects, name)
        for group in _extract_group_subjects(auxiliary):
            if len(subjects) >= _QUERY_MAX:
                break
            _append_unique(subjects, group)

    return subjects[:_QUERY_MAX]
