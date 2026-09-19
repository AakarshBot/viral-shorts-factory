"""Generic semantic guard for visual subjects and image-search queries."""
from __future__ import annotations

import html
import re
import unicodedata

MAX_SUBJECT_WORDS = 8
MAX_QUERY_WORDS = 10

_TRAILING_IDENTITY_CONJUNCTIONS = {"or", "and", "but"}

GENERIC_NOISE = {
    "nbsp", "amp", "quot", "apos", "lt", "gt", "latest", "breaking", "news",
    "update", "story", "article", "headline", "reported", "reports", "according",
    "says", "said", "today", "yesterday", "tomorrow", "editorial", "official",
    "photo", "image", "picture", "real", "high", "resolution", "event", "item",
    "thing", "stuff", "matter", "point", "one", "off", "oneoff", "kind", "way", "part", "time",
    "next", "year",
}

VISUAL_DESCRIPTORS = {
    "logo", "logos", "portrait", "portraits", "headshot", "headshots", "icon", "icons",
    "badge", "badges", "emblem", "emblems", "symbol", "symbols", "seal", "seals",
    "map", "maps", "chart", "charts", "graph", "graphs", "diagram", "diagrams",
    "infographic", "infographics", "screenshot", "screenshots", "poster", "posters",
    "flag", "flags",
}

DESCRIPTOR_ROLE_CUES = {
    "PERSON": {"portrait", "portraits", "headshot", "headshots"},
    "ORGANIZATION": {"logo", "logos", "icon", "icons", "emblem", "emblems", "seal", "seals", "badge", "badges"},
    "LOCATION": {"map", "maps", "landmark", "landmarks"},
    "DOCUMENT": {"document", "documents", "report", "reports", "filing", "filings", "paper", "papers"},
}

DISCOURSE_PREFIXES = {
    "not", "just", "only", "also", "still", "now", "really", "actually", "basically",
    "clearly", "simply", "perhaps", "maybe", "apparently", "reportedly", "even",
}

STOPWORDS = {
    "the", "and", "for", "with", "from", "this", "that", "into", "after", "before",
    "about", "they", "their", "there", "here", "when", "what", "which", "where", "while",
    "have", "has", "had", "will", "would", "could", "should", "been", "were", "was", "are",
    "our", "you", "your", "is", "a", "an", "to", "of", "in", "on", "as", "it", "its",
    "these", "those", "who", "how", "why", "or", "but", "up", "down", "over", "under",
    "very", "more", "most", "than", "also", "can", "may", "might", "be", "do", "does", "did",
}

AUXILIARY_WORDS = {
    "continue", "continues", "continued", "continuing", "remain", "remains", "remained", "become",
    "becomes", "became", "being", "been", "make", "makes", "made", "get", "gets", "got", "go",
    "goes", "went", "come", "comes", "came", "say", "says", "said", "tell", "tells", "told",
    "report", "reports", "reported", "announce", "announces", "announced", "show", "shows", "showed",
    "host", "hosts", "hosted", "hosting", "return", "returns", "returned", "returning",
    "hold", "holds", "held", "holding",
}

ROLE_CUES = {
    "PERSON": {"person", "portrait", "headshot", "biography", "speaker", "actor", "scientist", "coach", "founder", "minister", "president", "ceo", "individual"},
    "ORGANIZATION": {"organization", "organisation", "institution", "company", "corporation", "agency", "government", "ministry", "parliament", "board", "federation", "association", "committee", "team", "squad", "group", "crew", "department", "collective", "members", "staff"},
    "PRODUCT": {"product", "device", "phone", "smartphone", "tablet", "laptop", "computer", "car", "suv", "chip", "processor", "gpu", "console", "camera", "headset", "prototype", "hardware"},
    "LOCATION": {"location", "geography", "map", "landmark", "address", "venue", "stadium", "arena", "ground", "place", "cityscape"},
    "CONCEPT": {"concept", "theory", "principle", "mechanism", "abstract", "idea", "diagram", "technical", "scientific"},
    "PROCESS": {"process", "workflow", "pipeline", "steps", "procedure", "mechanism", "how"},
    "EVENT": {"event", "competition", "tournament", "summit", "conference", "forum", "festival", "ceremony", "election", "launch", "opening", "closing", "meeting", "final"},
    "DOCUMENT": {"document", "report", "filing", "paper", "study", "contract", "record"},
    "QUOTE": {"quote", "quotation", "statement"},
    "STATISTIC": {"statistic", "data", "metric", "percentage", "figure"},
    "COMPARISON": {"comparison", "versus", "difference", "compared"},
    "TIMELINE": {"timeline", "chronology", "history"},
}

_STABLE_IDENTITY_ROLES = {"PERSON", "ORGANIZATION", "PRODUCT", "LOCATION"}


def clean_text(value: object) -> str:
    text = html.unescape(str(value or "")).replace("\u00a0", " ").replace("\u200b", " ")
    text = re.sub(r"\b(?:nbsp|amp|quot|apos|lt|gt)\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"[_]+", " ", text)
    return re.sub(r"\s+", " ", text).strip(" ,.;:|\"'()[]{}")


def key(value: str) -> str:
    return "".join(
        char
        for char in clean_text(value).casefold()
        if char.isalnum() or unicodedata.category(char).startswith("M")
    )


def tokens(value: str) -> list[str]:
    """Tokenise multilingual text without stripping Unicode combining marks."""
    text = clean_text(value).replace("’", "'").replace("‘", "'")
    words: list[str] = []
    current: list[str] = []
    for char in text:
        category = unicodedata.category(char)
        if char.isalnum() or category.startswith("M"):
            current.append(char)
            continue
        if char in {"'", "-", "/"} and current:
            current.append(char)
            continue
        if current:
            token = "".join(current).strip("'-/’")
            if token:
                words.append(token)
            current = []
    if current:
        token = "".join(current).strip("'-/’")
        if token:
            words.append(token)
    return words


def meaningful_tokens(value: str) -> list[str]:
    result: list[str] = []
    for token in tokens(value):
        k = key(token)
        if not k or k in GENERIC_NOISE or k in STOPWORDS:
            continue
        result.append(k)
    return result


def sanitize_candidate(value: object) -> str:
    """Remove discourse/noise while preserving clean entity spelling and punctuation."""
    text = clean_text(value)
    words = tokens(text)
    if not words:
        return ""
    start = 0
    end = len(words)
    while start < end and key(words[start]) in DISCOURSE_PREFIXES:
        start += 1
    while end > start and (
        key(words[end - 1]) in GENERIC_NOISE
        or key(words[end - 1]) in _TRAILING_IDENTITY_CONJUNCTIONS
    ):
        end -= 1
    filtered = words[start:end]
    if not filtered:
        return ""
    if start == 0 and end == len(words) and len(filtered) <= MAX_SUBJECT_WORDS:
        return text
    return " ".join(filtered[:MAX_SUBJECT_WORDS]).strip(" ,.;:|\"'")


def _strip_visual_descriptors(value: str) -> str:
    words = tokens(value)
    while len(words) > 1 and key(words[-1]) in VISUAL_DESCRIPTORS:
        words.pop()
    return sanitize_candidate(" ".join(words))


def _descriptor_role_hint(value: str) -> str:
    descriptor_keys = {key(word) for word in tokens(value)}
    for role, cues in DESCRIPTOR_ROLE_CUES.items():
        if descriptor_keys & cues:
            return role
    return ""


def _descriptor_present(value: str) -> bool:
    words = tokens(value)
    return bool(words) and any(key(word) in VISUAL_DESCRIPTORS for word in words[1:])


def _subject_role_hint(value: str) -> str:
    core = _strip_visual_descriptors(value)
    core_words = {key(word) for word in tokens(core)}
    if not core_words:
        return ""
    core_text = clean_text(core)
    if re.fullmatch(r"[A-Z][A-Z0-9&.-]{1,12}(?:\\s+[A-Z][A-Z0-9&.-]{1,12})*", core_text):
        return "ORGANIZATION"

    # Bare manual visual identities often arrive without an explicit semantic
    # role (for example, "Kapil Dev" or "Mohammad Rizwan"). Resolve the role
    # generically here so retrieval and QA share the same semantic anchor.
    words = tokens(core)

    # Strong lexical identity cues must beat the generic title-case person
    # heuristic. This matters for names such as "Northstar Labs", "Central
    # City" and "Nova Phone 8", which otherwise look like people's names.
    organization_suffixes = {
        "board", "federation", "association", "committee", "foundation",
        "institute", "institution", "corporation", "company", "agency",
        "ministry", "department", "council", "club", "team", "squad",
        "network", "studio", "university", "college", "lab", "labs", "hall",
    }
    if len(words) >= 2 and key(words[-1]) in organization_suffixes:
        return "ORGANIZATION"

    location_suffixes = {
        "city", "town", "village", "state", "province", "country", "island",
        "county", "district", "region", "capital", "stadium", "arena",
    }
    if len(words) >= 2 and key(words[-1]) in location_suffixes:
        return "LOCATION"

    product_cues = {
        "product", "device", "phone", "smartphone", "tablet", "laptop",
        "computer", "car", "suv", "chip", "processor", "gpu", "console",
        "camera", "headset", "prototype", "hardware",
    }
    if core_words & product_cues:
        return "PRODUCT"

    event_cues = {
        "event", "competition", "tournament", "championship", "summit",
        "conference", "festival", "ceremony", "election", "launch", "opening",
        "closing", "meeting", "final",
    }
    if core_words & event_cues:
        return "EVENT"

    # Explicit semantic cues remain stronger than the generic person heuristic.
    for role, cues in ROLE_CUES.items():
        if core_words & cues:
            return role

    # Keep the proper-name heuristic deliberately narrow: multi-word,
    # title-cased identities only. Single-word terms and descriptive phrases
    # remain GENERAL_CONTEXT unless stronger role evidence exists.
    if 2 <= len(words) <= 4:
        alpha_words = [word for word in words if any(char.isalpha() for char in word)]
        if alpha_words and all(word[:1].isupper() for word in alpha_words):
            return "PERSON"

    return ""


def _intent_words(scene: dict) -> list[str]:
    raw = clean_text(scene.get("visual_intent", ""))
    raw = re.sub(r"[_/-]+", " ", raw)
    return [key(word) for word in tokens(raw) if key(word)]


def infer_role(scene: dict) -> str:
    explicit = clean_text(scene.get("visual_type", "")).upper().replace("-", "_").replace(" ", "_")
    candidate = sanitize_candidate(scene.get("primary_entity", ""))
    descriptor_hint = _descriptor_role_hint(candidate)
    subject_hint = _subject_role_hint(candidate)

    # Visual descriptors such as "logo" or "map" are stronger evidence than
    # the generic title-case person heuristic. Check them before subject-name
    # inference so identities like "Deccan Herald logo" stay ORGANIZATION.
    if descriptor_hint:
        return descriptor_hint
    if subject_hint:
        return subject_hint
    if explicit in ROLE_CUES:
        return explicit
    intent_words = set(_intent_words(scene))
    for role, cues in ROLE_CUES.items():
        if intent_words & cues:
            return role
    return "GENERAL_CONTEXT"


def evidence_text(scene: dict, video_title: str = "") -> str:
    parts: list[str] = []
    for field in ("voiceover", "visual_context", "specific_search_prompt", "visual_intent"):
        value = clean_text(scene.get(field, ""))
        if value:
            parts.append(value)
    title = clean_text(video_title or scene.get("video_title", ""))
    if title:
        parts.append(title)
    return " ".join(parts)


def _grounded_context(candidate: str, scene: dict, video_title: str = "") -> str:
    candidate_keys = set(meaningful_tokens(candidate))
    if not candidate_keys:
        return ""
    evidence_words = tokens(evidence_text(scene, video_title))
    keyed = [key(word) for word in evidence_words]
    positions = [i for i, item in enumerate(keyed) if item in candidate_keys]
    if not positions:
        return ""
    start = max(0, min(positions) - 3)
    end = min(len(evidence_words), max(positions) + 4)
    window = evidence_words[start:end]
    kept: list[str] = []
    candidate_original = {key(word): word for word in tokens(candidate)}
    for word in window:
        k = key(word)
        if not k or k in GENERIC_NOISE or k in STOPWORDS or k in DISCOURSE_PREFIXES or k in AUXILIARY_WORDS:
            continue
        if k not in {key(x) for x in kept}:
            kept.append(word)
    for token_key, original in candidate_original.items():
        if token_key not in {key(x) for x in kept}:
            kept.append(original)
    if not kept:
        return ""
    return " ".join(kept[:MAX_SUBJECT_WORDS])


def _prompt_is_concrete(prompt: str, candidate: str) -> bool:
    cleaned = sanitize_candidate(prompt)
    if not cleaned:
        return False
    keys = set(meaningful_tokens(cleaned))
    candidate_keys = set(meaningful_tokens(candidate))
    if not candidate_keys:
        return False
    return candidate_keys.issubset(keys) and len(keys) >= len(candidate_keys)


def _prompt_can_define_subject(role: str, candidate: str, prompt: str) -> bool:
    """Allow descriptive prompts only when they do not threaten stable identity."""
    if role in _STABLE_IDENTITY_ROLES:
        return False
    if _subject_role_hint(candidate) or _descriptor_role_hint(candidate):
        return False
    return _prompt_is_concrete(prompt, candidate)


def _stable_identity_from_prompt(candidate: str, prompt: str, role: str) -> str:
    """Recover a clean stable entity when the model appended headline/context text.

    Stable identities (person, organisation, product, location) must not inherit
    headline clauses such as "dropped over ...", "amid ...", dates, or actions.
    When the original candidate is noisy, use only the contiguous candidate words
    that are actually evidenced by the concrete search prompt. This is generic
    and deliberately conservative: if the prompt does not corroborate a shorter
    identity, keep the original candidate rather than guessing.
    """
    if role not in _STABLE_IDENTITY_ROLES:
        return candidate
    candidate_words = tokens(candidate)
    prompt_words = tokens(prompt)
    if len(candidate_words) <= 2 or not prompt_words:
        return candidate

    # Model-generated person identities sometimes absorb the beginning of an
    # action/headline, e.g. "Gautam Gambhir Makes Stunning".  Existing auxiliary
    # verbs already used by the query ranker are a safe generic boundary: keep
    # the proper-name prefix and never make the action part of the identity.
    if role == "PERSON":
        for index, word in enumerate(candidate_words[2:], start=2):
            if key(word) in AUXILIARY_WORDS:
                candidate_words = candidate_words[:index]
                break

    prompt_keys = [key(word) for word in prompt_words]
    candidate_keys = [key(word) for word in candidate_words]
    best: list[str] = []
    current: list[str] = []
    for word, token_key in zip(candidate_words, candidate_keys):
        if token_key and token_key in prompt_keys:
            current.append(word)
        else:
            if len(current) > len(best):
                best = current
            current = []
    if len(current) > len(best):
        best = current

    if not best:
        return candidate

    # A one-word stable identity is acceptable only when the prompt explicitly
    # corroborates it; this handles names such as "Macklemore" without guessing.
    recovered = sanitize_candidate(" ".join(best))
    if recovered and len(meaningful_tokens(recovered)) >= 1:
        return recovered
    return candidate


def _contextual_query_variant(subject: str, anchor: str) -> str:
    """Build a shorter context-rich query while preserving the factual anchor."""
    subject_words = tokens(subject)
    anchor_keys = meaningful_tokens(anchor)
    if not subject_words or not anchor_keys:
        return ""
    positions: list[int] = []
    next_anchor = 0
    for index, word in enumerate(subject_words):
        if next_anchor < len(anchor_keys) and key(word) == anchor_keys[next_anchor]:
            positions.append(index)
            next_anchor += 1
    if next_anchor < len(anchor_keys):
        return ""
    context_words: list[str] = []
    for index, word in enumerate(subject_words):
        if index in positions:
            continue
        k = key(word)
        if not k or k in GENERIC_NOISE or k in STOPWORDS or k in DISCOURSE_PREFIXES or k in AUXILIARY_WORDS:
            continue
        context_words.append(word)
    if not context_words:
        return ""
    return sanitize_candidate(" ".join([*tokens(anchor), *context_words[:4]]))


def resolve_subject(scene: dict, video_title: str = "") -> dict:
    scene = scene if isinstance(scene, dict) else {}
    original = clean_text(scene.get("primary_entity", ""))
    candidate = sanitize_candidate(original)
    role = infer_role(scene)
    prompt = sanitize_candidate(scene.get("specific_search_prompt", ""))
    candidate = _stable_identity_from_prompt(candidate, prompt, role)
    prompt_is_explicit = bool(prompt and _prompt_can_define_subject(role, candidate, prompt))
    subject = prompt if prompt_is_explicit else candidate

    needs_grounding = bool(candidate) and original.casefold() != candidate.casefold() and not prompt_is_explicit
    if needs_grounding and role in {"EVENT", "PROCESS", "CONCEPT", "DOCUMENT", "QUOTE", "GENERAL_CONTEXT"}:
        contextual = _grounded_context(subject, scene, video_title)
        if contextual and len(meaningful_tokens(contextual)) > len(meaningful_tokens(subject)):
            subject = contextual

    subject = sanitize_candidate(subject)
    confidence = 0.97 if subject and subject == candidate else (0.90 if subject else 0.0)
    return {
        "factual_entity": candidate or original,
        "original_entity": original,
        "subject": subject,
        "visual_type": role,
        "confidence": confidence,
    }


def prepare_scene(scene: dict, video_title: str = "") -> dict:
    resolution = resolve_subject(scene, video_title)
    prepared = dict(scene or {})
    prepared["factual_primary_entity"] = resolution["factual_entity"]
    prepared["original_primary_entity"] = resolution["original_entity"]
    prepared["primary_entity"] = resolution["subject"]
    prepared["visual_search_subject"] = resolution["subject"]
    prepared["visual_type"] = resolution["visual_type"]
    prepared["specific_search_prompt"] = clean_text(scene.get("specific_search_prompt", ""))
    prepared["visual_subject_confidence"] = resolution["confidence"]
    prepared["visual_subject_locked"] = True
    return prepared
