"""Runtime safeguards and generation contract for compact, information-dense Shorts."""

import re
from difflib import SequenceMatcher

_PERFORMATIVE_PATTERNS = (
    r"\bwait\s+(?:for|until|till)\b", r"\b(?:you|u)\s+(?:won['’]?t|will not)\s+believe\b",
    r"\bwhat\s+happens\s+(?:next|at the end|later)\b", r"\b(?:watch|stay|stick around)\s+(?:until|till)\b",
    r"\b(?:keep|continue)\s+watching\b", r"\bdon['’]?t\s+go\s+anywhere\b",
    r"\b(?:stay|stick)\s+with\s+(?:me|us)\b", r"\bby\s+the\s+end\s+you['’]?ll\b",
    r"\b(?:the|this)\s+ending\s+will\b", r"\b(?:you['’]?re|you are)\s+not\s+ready\s+for\b",
    r"\b(?:prepare|get ready)\s+for\s+(?:this|what['’]?s next)\b", r"\bstop\s+scrolling\b",
    r"\b(?:more|another)\s+on\s+this\s+(?:at the end|later)\b",
)

_GENERIC_FILLER = (
    r"^here(?:'s| is) (?:the )?(?:key|main|important) (?:point|development|detail)\.?$",
    r"^the latest facts are worth a closer look\.?$", r"^this development deserves attention\.?$",
    r"^let(?:'s| us) (?:break this down|take a closer look|talk about this)\.?$",
    r"^what do you think(?: about this)?\??$", r"^would you have expected this\??$",
    r"^is this the future\??$", r"^could this change everything\??$",
)

_CTA_RE = re.compile(
    r"(?:like\s*,?\s*share\s*,?\s*(?:and\s*)?subscribe|subscribe\s+(?:to|for)\s+(?:more|our)|"
    r"follow\s+(?:for|for more)\s+(?:updates|content))[^.!?]*[.!?]?",
    re.IGNORECASE,
)

_STRUCTURE_HINTS = {
    "comparison": "headline → establish both sides → give the defining difference → explain why it matters",
    "timeline": "headline → key starting point → pivotal development → current consequence",
    "ranking": "headline → identify the subject → strongest evidence/details → why the ranking matters",
    "how_to": "headline → explain the mechanism/process → key evidence → practical consequence",
    "explainer": "headline → core facts → useful context → important development → consequence",
}


def _normalise(text): return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", str(text or "").lower())).strip()
def _words(text): return re.findall(r"[A-Za-z0-9]+", str(text or "").lower())


def _topic_terms(story_data):
    if not isinstance(story_data, dict): return set()
    fields = [story_data.get("title", ""), story_data.get("topic", ""), story_data.get("summary", ""), story_data.get("description", "")]
    stop = {"the", "and", "for", "with", "from", "that", "this", "into", "after", "before", "about", "over", "under", "their", "they", "them", "have", "has", "had", "will", "would", "could", "should", "what", "when", "where", "which", "while", "news", "latest", "report", "reports"}
    return {w for field in fields for w in _words(field) if len(w) >= 4 and w not in stop}


def _story_structure(story_data, format_mode):
    text = " ".join(
        str(story_data.get(key, ""))
        for key in ("title", "topic", "summary", "description", "category")
    ).lower() if isinstance(story_data, dict) else ""
    if any(term in text for term in (" vs ", " versus ", "comparison", "compared", "beats", "surpasses")):
        return _STRUCTURE_HINTS["comparison"]
    if any(term in text for term in ("how ", "how-to", "how to", "works", "process", "explained")):
        return _STRUCTURE_HINTS["how_to"]
    if any(term in text for term in ("history", "timeline", "since", "after", "before", "years later")):
        return _STRUCTURE_HINTS["timeline"]
    if format_mode == "top5" or re.search(r"\btop\s*\d+\b|\bnumber\s+\d+\b", text):
        return _STRUCTURE_HINTS["ranking"]
    return _STRUCTURE_HINTS["explainer"]


def _strip_filler(text):
    value = str(text or "").strip()
    if not value:
        return ""
    for pattern in _PERFORMATIVE_PATTERNS:
        if re.match(r"^\s*" + pattern, value, flags=re.IGNORECASE):
            return ""
    value = _CTA_RE.sub("", value)
    for pattern in _PERFORMATIVE_PATTERNS:
        value = re.sub(pattern, "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+([,.!?])", r"\1", value)
    return re.sub(r"\s{2,}", " ", value).strip(" ,;:-")


def _looks_like_filler(text):
    value = _normalise(text)
    if not value: return True
    raw = str(text or "")
    if any(re.search(pattern, raw, flags=re.IGNORECASE) for pattern in _PERFORMATIVE_PATTERNS): return True
    return any(re.fullmatch(pattern, value, flags=re.IGNORECASE) for pattern in _GENERIC_FILLER)


def _clean_titles(script_data):
    titles = script_data.get("titles")
    if not isinstance(titles, list): return
    cleaned = []
    for title in titles:
        value = re.sub(r"\s*#shorts\b", "", str(title or ""), flags=re.IGNORECASE)
        value = re.sub(r"\bsubscribe\b.*$", "", value, flags=re.IGNORECASE).strip(" -:|•")
        if value:
            cleaned.append(value)
    script_data["titles"] = cleaned


def _add_editorial_contract(story_data, format_mode):
    if not isinstance(story_data, dict): return story_data
    structure = _story_structure(story_data, format_mode)
    brief = (
        "EDITORIAL SCRIPT CONTRACT — DO NOT OUTPUT THIS BLOCK.\n"
        "Write an information-first Short. The viewer should learn something in every spoken sentence.\n"
        "CORE SHAPE: Start with the strongest factual headline. Then deliver the relevant facts, useful context, the important development, and its concrete consequence. "
        "Use this story-specific structure unless the supplied facts clearly call for a better structure: " + structure + ".\n"
        "ORIGINAL CONTRIBUTION: Add one useful contribution supported by the supplied material — for example an overlooked detail, meaningful comparison, cause-and-effect explanation, timeline, number in context, mechanism, or sourced implication. "
        "Do not merely paraphrase the source article. Do not manufacture an opinion, motive, prediction, quote, statistic, or causal link.\n"
        "PACING: Prefer short, natural sentences with high factual density. Remove throat-clearing. Do not pad to reach a target duration or word count. Stop when the useful information is exhausted.\n"
        "HOOK: The opening must itself contain topic information. Curiosity is allowed only when the sentence also delivers a real fact or specific development. Never use a retention-only hook.\n"
        "ENDING: End on the most useful consequence, implication, comparison, or final fact. Never ask the viewer to wait, watch until the end, stay tuned, or come back for more.\n"
        "CTA: Do not include a spoken like/share/subscribe/follow request. A creator comment may handle engagement separately.\n"
        "STYLE: No canned catchphrases, persona slogans, fake urgency, exaggerated certainty, or generic internet filler. Never write 'this changes everything' unless the supplied facts literally establish that scale of change.\n\n"
    )
    copy = dict(story_data)
    if "text" in copy: copy["text"] = brief + str(copy.get("text", ""))
    return copy


def clean_script_data(script_data, story_data, format_mode):
    if not isinstance(script_data, dict):
        return script_data, {"removed_cta": False, "removed_scenes": 0, "changed_scenes": 0}
    scenes = script_data.get("script")
    if not isinstance(scenes, list):
        return script_data, {"removed_cta": False, "removed_scenes": 0, "changed_scenes": 0}
    cleaned, removed_cta, removed_scenes, changed_scenes = [], False, 0, 0
    for scene in scenes:
        if not isinstance(scene, dict): continue
        copy = dict(scene)
        before = str(copy.get("voiceover", "")).strip()
        after = _strip_filler(before)
        if after != before: changed_scenes += 1
        if not after or _looks_like_filler(after):
            removed_scenes += 1
            removed_cta = removed_cta or bool(_CTA_RE.search(before))
            continue
        if _CTA_RE.search(before) and not _CTA_RE.search(after): removed_cta = True
        copy["voiceover"] = after
        cleaned.append(copy)

    deduped = []
    for scene in cleaned:
        text = _normalise(scene.get("voiceover", ""))
        duplicate = any(
            text and _normalise(prev.get("voiceover", "")) and
            SequenceMatcher(None, text, _normalise(prev.get("voiceover", ""))).ratio() >= 0.90
            for prev in deduped
        )
        if duplicate:
            removed_scenes += 1
        else:
            deduped.append(scene)

    result = dict(script_data)
    result["script"] = deduped
    result["cta_required"] = False
    result["script_focus"] = "information_dense_storytelling"
    result["script_structure"] = _story_structure(story_data, format_mode)
    _clean_titles(result)
    return result, {"removed_cta": removed_cta, "removed_scenes": removed_scenes, "changed_scenes": changed_scenes}


def validate_content_density(script_data, story_data, format_mode):
    scenes = script_data.get("script", []) if isinstance(script_data, dict) else []
    if not scenes:
        return False, "Script became empty after removing performative filler."
    topic_terms = _topic_terms(story_data)
    all_words = []
    filler_hits = []
    for index, scene in enumerate(scenes, 1):
        text = str(scene.get("voiceover", "")).strip()
        words = _words(text)
        all_words.extend(words)
        if len(words) < 3:
            return False, f"Scene {index} contains too little usable narration."
        if _looks_like_filler(text):
            filler_hits.append(index)
    if filler_hits:
        return False, "Performative filler remains in scene(s): " + ", ".join(map(str, filler_hits))
    if topic_terms and len(set(all_words) & topic_terms) < min(2, len(topic_terms)):
        return False, "Narration is not sufficiently grounded in the selected topic."
    return True, "Passed story-specific content-density and anti-filler checks"


def wrap_write_script(bot):
    current = getattr(bot, "write_script", None)
    if current is None or getattr(current, "_content_dense_bound", False): return current

    def write_script(story_data, language_cfg, genre_key, conn, format_mode):
        contracted_story = _add_editorial_contract(story_data, format_mode)
        result = current(contracted_story, language_cfg, genre_key, conn, format_mode)
        cleaned, diagnostics = clean_script_data(result, story_data, format_mode)
        if diagnostics["changed_scenes"] or diagnostics["removed_scenes"]:
            print(
                "   [Script QC] Structural cleanup: "
                f"{diagnostics['changed_scenes']} scene(s) edited, {diagnostics['removed_scenes']} scene(s) removed.",
                flush=True,
            )
        ok, reason = validate_content_density(cleaned, story_data, format_mode)
        if not ok:
            raise ValueError(f"Content-density gate failed: {reason}")
        return cleaned

    write_script._content_dense_bound = True
    write_script._research_layer_live = bool(getattr(current, "_research_wrapped", False))
    bot.write_script = write_script
    return write_script
