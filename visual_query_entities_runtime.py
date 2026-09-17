"""Deterministic subject extraction for slide-derived visual search.

The rendered slide's cut script is the only source of additional search
subjects. Search queries are exact subject phrases: names, places, events,
organizations, teams, and other concrete entities. Titles and visual prompts
are never appended as query noise.
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
    "editorial", "official", "selection", "meeting", "details", "detail", "also", "just",
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
_EVENT_SUFFIXES = {
    "cup", "championship", "league", "final", "open", "games", "trophy", "summit",
    "festival", "tournament", "grand", "prix",
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
    value = _clean(value).strip("'")
    if value and value.casefold() not in {item.casefold() for item in items}:
        items.append(value)


def _extract_person_names(text: str) -> list[str]:
    """Find multi-word proper-name spans in the cut script."""
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
        while j < len(tokens) and len(run) < 4:
            nxt = tokens[j]
            if nxt[0].isupper():
                run.append(nxt)
                j += 1
                continue
            break
        keys = {_key(word) for word in run}
        if (
            len(run) >= 2
            and not keys.intersection(_NOISE)
            and not keys.intersection(_ROLE_WORDS)
            and not keys.intersection(_EVENT_SUFFIXES)
        ):
            _append_unique(result, " ".join(run))
        i = max(i + 1, j)
    return result


def _extract_group_subjects(text: str) -> list[str]:
    """Find concrete named groups such as 'Indian cricket team'."""
    tokens = _words(text)
    result: list[str] = []
    for i in range(len(tokens)):
        if i + 2 < len(tokens):
            a, b, c = tokens[i], tokens[i + 1], tokens[i + 2]
            ak, ck = _key(a), _key(c)
            if ak in _COMMON_STARTERS and ck in {"team", "squad", "board", "association", "government"}:
                _append_unique(result, f"{a.rstrip(\"'s\").rstrip(\"’s\")} {b} {c}")
        if i + 1 < len(tokens):
            a, b = tokens[i], tokens[i + 1]
            if _key(a) in _COMMON_STARTERS and _key(b) in _ROLE_WORDS:
                _append_unique(result, f"{a.rstrip(\"'s\").rstrip(\"’s\")} {b}")
    return result


def _extract_event_subjects(text: str) -> list[str]:
    """Find named events without adding action/context words."""
    tokens = _words(text)
    result: list[str] = []
    for i, token in enumerate(tokens):
        if _key(token) not in _EVENT_SUFFIXES:
            continue
        start = i
        while start > 0 and i - start < 4:
            previous = tokens[start - 1]
            if previous[0].isupper() or _key(previous) in {"of", "the", "and", "vs", "v"} or previous.isdigit():
                start -= 1
                continue
            break
        candidate = " ".join(tokens[start:i + 1])
        candidate = re.sub(r"^the\s+", "", candidate, flags=re.IGNORECASE)
        if len(_words(candidate)) >= 2:
            _append_unique(result, candidate)
    return result


def _extract_known_entities(text: str) -> list[str]:
    """Find known places/organizations while preserving their script spelling."""
    try:
        import visual_retrieval_planner as planner
        known = set(getattr(planner, "LOCATION_NAMES", set()))
        known_orgs = set(getattr(planner, "ORGANIZATION_ACRONYMS", set()))
    except Exception:
        known, known_orgs = set(), set()

    found: list[tuple[int, str]] = []
    for name in sorted(known, key=len, reverse=True):
        pattern = re.compile(r"(?<!\w)" + re.escape(name) + r"(?:['’]s)?(?!\w)", re.IGNORECASE)
        match = pattern.search(text)
        if match:
            value = re.sub(r"['’]s$", "", match.group(0), flags=re.IGNORECASE)
            found.append((match.start(), value))
    for name in sorted(known_orgs, key=len, reverse=True):
        pattern = re.compile(r"(?<!\w)" + re.escape(name) + r"(?!\w)", re.IGNORECASE)
        match = pattern.search(text)
        if match:
            found.append((match.start(), match.group(0)))

    found.sort(key=lambda item: item[0])
    result: list[str] = []
    for _, value in found:
        _append_unique(result, value)
    return result


def extract_slide_search_subjects(scene: dict) -> list[str]:
    """Return up to three exact search subjects from the current slide.

    Priority is deterministic: primary entity first, then named people, named
    groups/events/organizations/places in the cut script. The video title and
    structured search prompt are deliberately ignored.
    """
    if not isinstance(scene, dict):
        return []

    subjects: list[str] = []
    _append_unique(subjects, scene.get("primary_entity", ""))
    script = _clean(scene.get("voiceover", ""))
    if not script:
        return subjects[:_QUERY_MAX]

    candidates: list[tuple[int, str]] = []
    for extractor in (_extract_person_names, _extract_group_subjects, _extract_event_subjects, _extract_known_entities):
        for candidate in extractor(script):
            index = script.casefold().find(candidate.casefold())
            if index >= 0:
                candidates.append((index, candidate))

    candidates.sort(key=lambda item: (item[0], -len(_words(item[1]))))
    for _, candidate in candidates:
        if len(subjects) >= _QUERY_MAX:
            break
        _append_unique(subjects, candidate)

    return subjects[:_QUERY_MAX]


def classify_search_subject(subject: str) -> str:
    """Classify an extracted subject for source/QA routing only."""
    subject = _clean(subject)
    if not subject:
        return "GENERAL_CONTEXT"
    try:
        import visual_retrieval_planner as planner
        if planner._looks_like_organization(subject) or _key(subject).split() and _key(subject).split()[0] in {"bcci", "icc", "pcb", "fifa", "uefa", "nasa", "isro", "who", "un"}:
            return "ORGANIZATION"
        if planner._looks_like_location(subject):
            return "LOCATION"
        if planner._looks_like_product(subject):
            return "PRODUCT"
    except Exception:
        pass
    keys = {_key(word) for word in _words(subject)}
    if keys.intersection(_EVENT_SUFFIXES):
        return "EVENT"
    if "team" in keys or "squad" in keys or "board" in keys or "association" in keys or "government" in keys:
        return "ORGANIZATION"
    words = _words(subject)
    if len(words) >= 2 and all(word[:1].isupper() for word in words if word):
        return "PERSON"
    return "GENERAL_CONTEXT"


def build_candidate_scene(scene: dict, subject: str) -> dict:
    """Create the subject-only scene passed into the visual runtime."""
    candidate = dict(scene or {})
    subject_type = classify_search_subject(subject)
    candidate["primary_entity"] = subject
    candidate["visual_type"] = subject_type
    candidate["visual_intent"] = f"{subject_type.lower()} subject identity"
    candidate["specific_search_prompt"] = subject
    candidate["voiceover"] = subject
    return candidate


def search_slide_visual(visual_runtime_module, bot, scene, category, used_urls, used_hashes, video_title=""):
    """Try the slide's clean subjects in order; never rewrite a query."""
    subjects = extract_slide_search_subjects(scene)
    if not subjects:
        return visual_runtime_module._relevant_asset(bot, scene, category, used_urls, used_hashes, video_title)

    last_error = None
    for index, subject in enumerate(subjects, 1):
        candidate = build_candidate_scene(scene, subject)
        subject_type = candidate["visual_type"]
        print(f"   [Visual Search] Subject {index}/{len(subjects)} | '{subject}' | type={subject_type}", flush=True)
        try:
            return visual_runtime_module._relevant_asset(
                bot, candidate, category, used_urls, used_hashes, video_title
            )
        except RuntimeError as exc:
            last_error = exc
            print(f"   [Visual Search] Subject '{subject}' produced no usable visual; falling back to next slide subject.", flush=True)

    if last_error is not None:
        raise RuntimeError(
            f"No usable visual found for slide subjects {subjects!r}. Last failure: {last_error}"
        ) from last_error
    raise RuntimeError(f"No usable visual found for slide subjects {subjects!r}.")
