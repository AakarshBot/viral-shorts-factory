"""Deterministic subject extraction for slide-derived visual search.

The rendered slide's cut script is the source for visual subjects. Queries are
exact subject phrases: names, places, events, organizations, teams, and other
concrete entities. Model-provided visual prompts never become queries.
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
_GROUP_MIDDLE_WORDS = {
    "cricket", "football", "soccer", "basketball", "tennis", "hockey", "baseball",
    "national", "mens", "women", "men", "women's", "womens",
}
_COMMON_STARTERS = {
    "india", "indian", "england", "english", "australia", "australian", "pakistan", "pakistani",
    "bangladesh", "bangladeshi", "sri", "south", "new", "united", "world",
}
_EVENT_SUFFIXES = {
    "cup", "championship", "league", "final", "open", "games", "trophy", "summit",
    "festival", "tournament", "grand", "prix",
}
_SENTINELS = {"none", "unknown", "na", "n/a"}


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


def _known_sets():
    try:
        import visual_retrieval_planner as planner
        return (
            set(getattr(planner, "LOCATION_NAMES", set())),
            set(getattr(planner, "ORGANIZATION_ACRONYMS", set())),
        )
    except Exception:
        return set(), set()


def _append_unique(items: list[str], value: str) -> None:
    value = _clean(value).strip("'")
    if value and value.casefold() not in {item.casefold() for item in items}:
        items.append(value)


def _extract_person_names(text: str) -> list[str]:
    """Find multi-word proper-name spans without turning formats into names."""
    tokens = _words(text)
    locations, organizations = _known_sets()
    known_locs = {_key(x) for x in locations}
    known_orgs = {_key(x) for x in organizations}
    result: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if not token or not token[0].isupper():
            i += 1
            continue
        run = [token]
        j = i + 1
        while j < len(tokens) and len(run) < 4 and tokens[j][0].isupper():
            run.append(tokens[j])
            j += 1
        keys = [_key(word) for word in run]
        candidate = " ".join(run)
        candidate_key = "".join(keys)
        second_is_all_caps = len(run) == 2 and run[1].isupper()
        if (
            len(run) >= 2
            and candidate_key not in known_locs
            and candidate_key not in known_orgs
            and not set(keys).intersection(_NOISE)
            and not set(keys).intersection(_ROLE_WORDS)
            and not set(keys).intersection(_EVENT_SUFFIXES)
            and not keys[0] in known_orgs
            and not (second_is_all_caps and len(run[0]) > 2)
        ):
            _append_unique(result, candidate)
        i = max(i + 1, j)
    return result


def _extract_group_subjects(text: str) -> list[str]:
    """Find concrete named groups such as 'Indian cricket team'."""
    tokens = _words(text)
    result: list[str] = []
    for i in range(len(tokens)):
        if i + 2 < len(tokens):
            a, b, c = tokens[i], tokens[i + 1], tokens[i + 2]
            ak, bk, ck = _key(a), _key(b), _key(c)
            if ak in _COMMON_STARTERS and bk in _GROUP_MIDDLE_WORDS and ck in {"team", "squad"}:
                first = re.sub(r"['’]s$", "", a, flags=re.IGNORECASE)
                _append_unique(result, f"{first} {b} {c}")
        if i + 1 < len(tokens):
            a, b = tokens[i], tokens[i + 1]
            ak, bk = _key(a), _key(b)
            if ak in _COMMON_STARTERS and bk in {
                "government", "ministry", "board", "council", "association", "committee", "company", "corporation",
            }:
                first = re.sub(r"['’]s$", "", a, flags=re.IGNORECASE)
                _append_unique(result, f"{first} {b}")
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
    locations, organizations = _known_sets()
    found: list[tuple[int, str]] = []
    for name in sorted(locations, key=len, reverse=True):
        pattern = re.compile(r"(?<!\w)" + re.escape(name) + r"(?:['’]s)?(?!\w)", re.IGNORECASE)
        match = pattern.search(text)
        if match:
            value = re.sub(r"['’]s$", "", match.group(0), flags=re.IGNORECASE)
            found.append((match.start(), value))
    for name in sorted(organizations, key=len, reverse=True):
        pattern = re.compile(r"(?<!\w)" + re.escape(name) + r"(?!\w)", re.IGNORECASE)
        match = pattern.search(text)
        if match:
            found.append((match.start(), match.group(0)))
    found.sort(key=lambda item: item[0])
    result: list[str] = []
    for _, value in found:
        _append_unique(result, value)
    return result


def _script_position(script: str, candidate: str) -> int:
    """Locate a normalized subject, including possessive spelling in the script."""
    direct = script.casefold().find(candidate.casefold())
    if direct >= 0:
        return direct
    parts = _words(candidate)
    if not parts:
        return -1
    pattern_parts = []
    for index, part in enumerate(parts):
        escaped = re.escape(part)
        if index == 0:
            escaped += r"(?:['’]s)?"
        pattern_parts.append(escaped)
    match = re.search(r"(?<!\w)" + r"\s+".join(pattern_parts) + r"(?!\w)", script, flags=re.IGNORECASE)
    return match.start() if match else -1


def extract_cut_script_subjects(scene: dict) -> list[str]:
    """Return up to three subjects using only the current slide voiceover.

    ``primary_entity`` is used only as a preferred first subject when the same
    subject actually occurs in the cut voiceover. It is never allowed to inject
    a subject that the slide script does not contain.
    """
    if not isinstance(scene, dict):
        return []
    script = _clean(scene.get("voiceover", ""))
    if not script:
        return []

    candidates: list[tuple[int, int, str]] = []
    extractors = (
        (0, _extract_person_names),
        (1, _extract_group_subjects),
        (2, _extract_event_subjects),
        (3, _extract_known_entities),
    )
    for priority, extractor in extractors:
        for candidate in extractor(script):
            index = _script_position(script, candidate)
            if index >= 0:
                candidates.append((index, priority, candidate))

    candidates.sort(key=lambda item: (item[0], item[1], -len(_words(item[2]))))
    ordered: list[str] = []
    for _, _, candidate in candidates:
        _append_unique(ordered, candidate)

    primary = _clean(scene.get("primary_entity", ""))
    primary_valid = bool(primary) and _key(primary) not in _SENTINELS and _script_position(script, primary) >= 0
    if primary_valid:
        ordered = [primary] + [value for value in ordered if value.casefold() != primary.casefold()]

    return ordered[:_QUERY_MAX]


def extract_slide_search_subjects(scene: dict) -> list[str]:
    """Backward-compatible entry point for the strict cut-script extractor."""
    return extract_cut_script_subjects(scene)


def classify_search_subject(subject: str) -> str:
    """Classify an extracted subject for source/QA routing only."""
    subject = _clean(subject)
    if not subject:
        return "GENERAL_CONTEXT"
    try:
        import visual_retrieval_planner as planner
        if planner._looks_like_organization(subject):
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
    if keys.intersection({"team", "squad", "board", "association", "government", "ministry", "committee", "company", "corporation"}):
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
    """Try clean slide subjects in order; never rewrite a query."""
    subjects = extract_cut_script_subjects(scene)
    if not subjects:
        raise RuntimeError("No concrete visual subject could be identified from the slide cut script.")

    last_error = None
    reset_scene_budget = getattr(visual_runtime_module, "start_visual_qa_scene", None)
    for index, subject in enumerate(subjects, 1):
        candidate = build_candidate_scene(scene, subject)
        subject_type = candidate["visual_type"]
        if callable(reset_scene_budget) and index > 1:
            reset_scene_budget()
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
