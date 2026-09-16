"""Runtime safeguards for compact, information-dense Shorts scripts."""

import re
from difflib import SequenceMatcher

_PERFORMATIVE_PATTERNS = (
    r"\bwait\s+(?:for|until|till)\b",
    r"\b(?:you|u)\s+(?:won['’]?t|will not)\s+believe\b",
    r"\bwhat\s+happens\s+(?:next|at the end|later)\b",
    r"\b(?:watch|stay|stick around)\s+(?:until|till)\b",
    r"\b(?:keep|continue)\s+watching\b",
    r"\bdon['’]?t\s+go\s+anywhere\b",
    r"\b(?:stay|stick)\s+with\s+(?:me|us)\b",
    r"\bby\s+the\s+end\s+you['’]?ll\b",
    r"\b(?:the|this)\s+ending\s+will\b",
    r"\b(?:you['’]?re|you are)\s+not\s+ready\s+for\b",
    r"\b(?:prepare|get ready)\s+for\s+(?:this|what['’]?s next)\b",
    r"\bstop\s+scrolling\b",
    r"\b(?:more|another)\s+on\s+this\s+(?:at the end|later)\b",
)

_GENERIC_FILLER = (
    r"^here(?:'s| is) (?:the )?(?:key|main|important) (?:point|development|detail)\.?$",
    r"^the latest facts are worth a closer look\.?$",
    r"^this development deserves attention\.?$",
    r"^let(?:'s| us) (?:break this down|take a closer look|talk about this)\.?$",
    r"^what do you think(?: about this)?\??$",
    r"^would you have expected this\??$",
    r"^is this the future\??$",
    r"^could this change everything\??$",
)

_CTA_RE = re.compile(
    r"(?:like\s*,?\s*share\s*,?\s*(?:and\s*)?subscribe|"
    r"subscribe\s+(?:to|for)\s+(?:more|our)|"
    r"follow\s+(?:for|for more)\s+(?:updates|content))[^.!?]*[.!?]?",
    re.IGNORECASE,
)


def _normalise(text):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", str(text or "").lower())).strip()


def _words(text):
    return re.findall(r"[A-Za-z0-9]+", str(text or "").lower())


def _topic_terms(story_data):
    if not isinstance(story_data, dict): return set()
    fields = [story_data.get("title", ""), story_data.get("topic", ""), story_data.get("summary", ""), story_data.get("description", "")]
    stop = {"the", "and", "for", "with", "from", "that", "this", "into", "after", "before", "about", "over", "under", "their", "they", "them", "have", "has", "had", "will", "would", "could", "should", "what", "when", "where", "which", "while", "news", "latest", "report", "reports"}
    return {w for field in fields for w in _words(field) if len(w) >= 4 and w not in stop}


def _strip_filler(text):
    value = str(text or "").strip()
    value = _CTA_RE.sub("", value)
    for pattern in _PERFORMATIVE_PATTERNS:
        value = re.sub(pattern, "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+([,.!?])", r"\1", value)
    value = re.sub(r"\s{2,}", " ", value).strip(" ,;:-")
    return value


def _looks_like_filler(text):
    value = _normalise(text)
    if not value: return True
    if any(re.search(pattern, value, flags=re.IGNORECASE) for pattern in _PERFORMATIVE_PATTERNS): return True
    return any(re.fullmatch(pattern, value, flags=re.IGNORECASE) for pattern in _GENERIC_FILLER)


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
        duplicate = any(text and _normalise(prev.get("voiceover", "")) and SequenceMatcher(None, text, _normalise(prev.get("voiceover", ""))).ratio() >= 0.90 for prev in deduped)
        if duplicate: removed_scenes += 1
        else: deduped.append(scene)

    result = dict(script_data)
    result["script"] = deduped
    result["cta_required"] = False
    result["script_focus"] = "information_dense_storytelling"
    return result, {"removed_cta": removed_cta, "removed_scenes": removed_scenes, "changed_scenes": changed_scenes}


def validate_content_density(script_data, story_data, format_mode):
    scenes = script_data.get("script", []) if isinstance(script_data, dict) else []
    if not scenes: return False, "Script became empty after removing performative filler."
    topic_terms = _topic_terms(story_data)
    all_words, filler_hits = [], []
    for index, scene in enumerate(scenes, 1):
        text = str(scene.get("voiceover", "")).strip()
        words = _words(text)
        all_words.extend(words)
        if len(words) < 8: return False, f"Scene {index} is too thin after cleanup."
        if _looks_like_filler(text): filler_hits.append(index)
    if filler_hits: return False, "Performative filler remains in scene(s): " + ", ".join(map(str, filler_hits))
    if topic_terms and len(set(all_words) & topic_terms) < min(3, len(topic_terms)):
        return False, "Narration is not sufficiently grounded in the selected topic."
    total_words = len(all_words)
    minimum_words = 40 if format_mode != "top5" else 55
    if total_words < minimum_words: return False, f"Script is too short for a content-dense Short ({total_words} words)."
    return True, "Passed content-density and anti-filler checks"


def wrap_write_script(bot):
    """Patch the legacy generator's output without replacing its research stack."""
    current = getattr(bot, "write_script", None)
    if current is None or getattr(current, "_content_dense_bound", False): return current
    def write_script(story_data, language_cfg, genre_key, conn, format_mode):
        result = current(story_data, language_cfg, genre_key, conn, format_mode)
        cleaned, diagnostics = clean_script_data(result, story_data, format_mode)
        if diagnostics["changed_scenes"] or diagnostics["removed_scenes"]:
            print("   [Script QC] Removed performative filler: " + f"{diagnostics['changed_scenes']} scene(s) edited, {diagnostics['removed_scenes']} scene(s) removed.", flush=True)
        ok, reason = validate_content_density(cleaned, story_data, format_mode)
        if not ok: raise ValueError(f"Content-density gate failed: {reason}")
        return cleaned
    write_script._content_dense_bound = True
    bot.write_script = write_script
    return write_script
