"""Runtime safeguards and generation contract for compact, information-dense Shorts."""

import json
import os
import re
import urllib.request
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
    "comparison": "headline → establish both sides → defining difference → evidence → consequence",
    "timeline": "headline → starting point → pivotal development → what changed → current consequence",
    "ranking": "headline → establish subject → strongest evidence → comparison/context → why the ranking matters",
    "how_to": "headline → explain mechanism/process → evidence → practical consequence",
    "explainer": "headline → core facts → useful context → important development → what it means",
}

SCRIPT_MIN_SCENES = 6
SCRIPT_MAX_SCENES = 8
SCENE_MIN_WORDS = 12
SCENE_MAX_WORDS = 48
SCRIPT_MIN_TOTAL_WORDS = 110


def _originality_words(text):
    return re.findall(r"[A-Za-z0-9]+(?:['’][A-Za-z0-9]+)?", str(text or "").casefold())


def _originality_sources(story_data):
    story = story_data if isinstance(story_data, dict) else {}
    values = []
    def collect(value):
        if isinstance(value, str):
            if value.strip(): values.append(value)
        elif isinstance(value, dict):
            for key in ("text","content","extracted_text","body","summary","snippet","title","claim","claims","evidence","source_text","sources","articles","items"):
                if key in value: collect(value[key])
        elif isinstance(value, (list, tuple)):
            for item in value: collect(item)
    pack = story.get("research_evidence_pack")
    collect(pack.get("sources") if isinstance(pack, dict) else pack)
    for key in ("research_evidence_text","research_bundle","text","summary","description"):
        collect(story.get(key))
    seen, unique = set(), []
    for value in values:
        clean = re.sub(r"\s+", " ", value).strip()
        if clean and clean not in seen:
            seen.add(clean); unique.append(clean)
    return unique


def _longest_originality_run(left, right):
    previous = [0] * (len(right) + 1)
    best = 0
    for token in left:
        current = [0]
        for index, other in enumerate(right, 1):
            current.append(previous[index - 1] + 1 if token == other else 0)
            best = max(best, current[-1])
        previous = current
    return best


def check_script_originality(script_data, story_data):
    sources = _originality_sources(story_data)
    failures = []
    for scene_index, scene in enumerate(script_data.get("script", []) if isinstance(script_data, dict) else [], 1):
        if not isinstance(scene, dict) or scene.get("human_contributed"): continue
        words = _originality_words(scene.get("voiceover"))
        sixgrams = {tuple(words[i:i+6]) for i in range(max(0, len(words)-5))}
        for source_index, source in enumerate(sources):
            source_words = _originality_words(source)
            source_sixgrams = {tuple(source_words[i:i+6]) for i in range(max(0, len(source_words)-5))}
            longest = _longest_originality_run(words, source_words)
            ratio = len(sixgrams & source_sixgrams) / max(1, len(sixgrams))
            if longest >= 10 or (longest >= 7 and ratio > 0.20):
                failures.append({"scene": scene_index, "source_index": source_index, "longest_run": longest, "sixgram_ratio": ratio})
                break
    return {"passed": not failures, "failures": failures, "source_count": len(sources)}


def _normalise(text): return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", str(text or "").lower())).strip()
def _words(text): return re.findall(r"[A-Za-z0-9]+", str(text or "").lower())

def _script_scene_bounds(format_mode):
    return (
        (7, 7)
        if str(format_mode or "").lower() == "top5"
        else (SCRIPT_MIN_SCENES, SCRIPT_MAX_SCENES)
    )


def _scene_word_count(text):
    return len(str(text or "").split())


def _split_scene_text(text, min_words=SCENE_MIN_WORDS, max_words=SCENE_MAX_WORDS):
    value = re.sub(r"\s+", " ", str(text or "").strip())
    if not value:
        return []

    atomic = []
    sentence_parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+", value) if part.strip()]
    if not sentence_parts:
        sentence_parts = [value]

    for sentence in sentence_parts:
        words = sentence.split()
        if len(words) <= max_words:
            atomic.append(sentence)
            continue

        clauses = [part.strip() for part in re.split(r"(?<=[,;:—–-])\s+", sentence) if part.strip()]
        if len(clauses) > 1 and all(len(part.split()) <= max_words for part in clauses):
            atomic.extend(clauses)
            continue

        chunk_count = max(2, (len(words) + max_words - 1) // max_words)
        chunk_size = max(min_words, (len(words) + chunk_count - 1) // chunk_count)
        for start in range(0, len(words), chunk_size):
            atomic.append(" ".join(words[start:start + chunk_size]))

    packed = []
    pending = ""
    for part in atomic:
        candidate = f"{pending} {part}".strip() if pending else part
        if pending and _scene_word_count(candidate) > max_words:
            packed.append(pending)
            pending = part
        else:
            pending = candidate
    if pending:
        packed.append(pending)

    index = 0
    while index < len(packed):
        if _scene_word_count(packed[index]) >= min_words:
            index += 1
            continue
        if index + 1 < len(packed) and _scene_word_count(packed[index] + " " + packed[index + 1]) <= max_words:
            packed[index:index + 2] = [packed[index] + " " + packed[index + 1]]
            continue
        if index > 0 and _scene_word_count(packed[index - 1] + " " + packed[index]) <= max_words:
            packed[index - 1:index + 1] = [packed[index - 1] + " " + packed[index]]
            index = max(0, index - 1)
            continue
        index += 1

    return [part.strip() for part in packed if min_words <= _scene_word_count(part) <= max_words]


def _split_scene_at_midpoint(text, min_words=SCENE_MIN_WORDS, max_words=SCENE_MAX_WORDS):
    words = str(text or "").split()
    if len(words) < min_words * 2:
        return []
    midpoint = len(words) // 2
    low = min_words
    high = len(words) - min_words

    candidates = []
    for cut in range(low, high + 1):
        left = " ".join(words[:cut])
        if re.search(r"[.!?,;:—–-]$", left):
            candidates.append(cut)
    cut = min(candidates, key=lambda value: abs(value - midpoint)) if candidates else midpoint

    left, right = " ".join(words[:cut]).strip(), " ".join(words[cut:]).strip()
    if not (min_words <= len(left.split()) <= max_words and min_words <= len(right.split()) <= max_words):
        return []
    return [left, right]


def repair_script_structure(script_data, format_mode):
    """Repair scene-count/word-count defects without inventing narration."""
    if not isinstance(script_data, dict) or not isinstance(script_data.get("script"), list):
        return None, {"changed": False, "reason": "script is missing or malformed"}

    minimum, maximum = _script_scene_bounds(format_mode)
    original_scenes = [scene for scene in script_data.get("script") if isinstance(scene, dict)]
    if not original_scenes:
        return None, {"changed": False, "reason": "script contains no scenes"}

    if (
        minimum <= len(original_scenes) <= maximum
        and all(
            SCENE_MIN_WORDS <= _scene_word_count(scene.get("voiceover")) <= SCENE_MAX_WORDS
            for scene in original_scenes
        )
    ):
        return script_data, {"changed": False, "reason": "scene contract already satisfied"}

    expanded = []
    for scene in original_scenes:
        voiceover = str(scene.get("voiceover") or "").strip()
        parts = _split_scene_text(voiceover)
        if not parts:
            parts = [voiceover] if SCENE_MIN_WORDS <= _scene_word_count(voiceover) <= SCENE_MAX_WORDS else []
        for part in parts:
            copy = dict(scene)
            copy["voiceover"] = part
            expanded.append(copy)

    if not expanded:
        return None, {"changed": False, "reason": "no scene text can satisfy the word contract"}

    while len(expanded) < minimum:
        candidate_index = max(
            range(len(expanded)),
            key=lambda index: _scene_word_count(expanded[index].get("voiceover")),
            default=-1,
        )
        if candidate_index < 0:
            break
        pieces = _split_scene_at_midpoint(expanded[candidate_index].get("voiceover"))
        if not pieces:
            break
        original = expanded[candidate_index]
        expanded[candidate_index:candidate_index + 1] = [
            dict(original, voiceover=pieces[0]),
            dict(original, voiceover=pieces[1]),
        ]

    while len(expanded) > maximum:
        best_pair = None
        best_size = None
        for index in range(len(expanded) - 1):
            combined = (
                str(expanded[index].get("voiceover") or "").strip()
                + " "
                + str(expanded[index + 1].get("voiceover") or "").strip()
            ).strip()
            size = _scene_word_count(combined)
            if SCENE_MIN_WORDS <= size <= SCENE_MAX_WORDS and (best_size is None or size < best_size):
                best_pair = index
                best_size = size
        if best_pair is None:
            break
        left = expanded[best_pair]
        right = expanded[best_pair + 1]
        merged = dict(
            left,
            voiceover=(
                str(left.get("voiceover") or "").strip()
                + " "
                + str(right.get("voiceover") or "").strip()
            ).strip(),
        )
        expanded[best_pair:best_pair + 2] = [merged]

    if len(expanded) < minimum or len(expanded) > maximum:
        return None, {
            "changed": False,
            "reason": f"could not safely reach {minimum}-{maximum} scenes from supplied narration",
        }
    if not all(
        SCENE_MIN_WORDS <= _scene_word_count(scene.get("voiceover")) <= SCENE_MAX_WORDS
        for scene in expanded
    ):
        return None, {"changed": False, "reason": "repaired scenes still violate the word contract"}

    repaired = dict(script_data)
    repaired["script"] = expanded
    repaired["script_structure_repaired"] = True
    return repaired, {
        "changed": True,
        "original_scene_count": len(original_scenes),
        "final_scene_count": len(expanded),
    }



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


def _ground_visual_scene_entities(script_data, story_data):
    """Deterministically lock automatic visual identities to the supplied story."""
    scenes = script_data.get("script", []) if isinstance(script_data, dict) else []
    if not isinstance(scenes, list):
        return 0

    try:
        from visual_entity_grounding_runtime import ground_scene_entity
    except Exception:
        return 0

    changed = 0
    for index, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict):
            continue
        original = str(
            scene.get("primary_entity")
            or scene.get("visual_search_subject")
            or ""
        ).strip()
        if not original:
            continue

        result = ground_scene_entity(scene, story_data if isinstance(story_data, dict) else {})
        entity = str(result.get("entity") or original).strip()
        grounded = bool(result.get("grounded"))

        if result.get("changed") and entity and entity.casefold() != original.casefold():
            scene["original_primary_entity"] = original
            scene["primary_entity"] = entity
            scene["visual_search_subject"] = entity

            # Never let a repaired identity keep an old, unsupported search phrase
            # such as "Rashid Khan bowling...". The visual retrieval layer can add
            # genre-specific context safely after this identity lock.
            scene["specific_search_prompt"] = entity
            scene["visual_context"] = ""
            scene["visual_entity_grounding"] = "SCRIPT_REPAIR"
            scene["visual_entity_grounded"] = True
            scene["visual_entity_original"] = original
            scene["visual_entity_grounding_reason"] = str(result.get("reason") or "")
            scene["visual_entity_grounding_confidence"] = float(result.get("confidence") or 0.0)
            changed += 1
            print(
                f"""   [Script Visual Grounding] Scene {index} | REPAIRED | "{original}" -> "{entity}" | "{result.get("reason", "")}" """.strip(),
                flush=True,
            )
        elif not grounded:
            scene["visual_entity_grounded"] = False
            scene["visual_entity_grounding"] = "UNGROUNDED"
            scene["visual_entity_original"] = original
            scene["visual_entity_grounding_reason"] = str(result.get("reason") or "")
            scene["visual_entity_grounding_confidence"] = 0.0
            print(
                f"""   [Script Visual Grounding] Scene {index} | UNGROUNDED | entity="{original}" | "{result.get("reason", "")}" """.strip(),
                flush=True,
            )
        else:
            scene["visual_entity_grounded"] = True

    return changed


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


def _originality_json(raw):
    text = str(raw or "").strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except Exception:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        try:
            value = json.loads(match.group(0)) if match else None
            return value if isinstance(value, dict) else None
        except Exception:
            return None


def _originality_llm(url, payload, headers):
    try:
        request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode())
        return _originality_json(body.get("choices", [{}])[0].get("message", {}).get("content", ""))
    except Exception:
        return None


def _normalise_critique(value, provider):
    unsupported = value.get("unsupported_claims") if isinstance(value.get("unsupported_claims"), list) else []
    exaggerations = value.get("exaggerations") if isinstance(value.get("exaggerations"), list) else []
    fixes = value.get("fixes") if isinstance(value.get("fixes"), list) else []
    try: score = float(value.get("score"))
    except (TypeError, ValueError): score = None
    return {"score": score, "unsupported_claims": [str(x).strip() for x in unsupported if str(x).strip()], "exaggerations": [str(x).strip() for x in exaggerations if str(x).strip()], "fixes": [str(x).strip() for x in fixes if str(x).strip()], "provider": provider}


def _run_real_critique(script_data, story_data):
    script_text = "\n".join(str(s.get("voiceover") or "").strip() for s in script_data.get("script") or [] if isinstance(s, dict) and not s.get("human_contributed"))
    evidence = "\n\n".join(_originality_sources(story_data)[:12])
    prompt = ("Return ONLY JSON with keys score, unsupported_claims, exaggerations, fixes. "
              "unsupported_claims are claims not supported by evidence; exaggerations are overstated wording; fixes are concrete corrections. "
              "Do not invent criticism.\n\nSCRIPT:\n" + script_text + "\n\nEVIDENCE:\n" + evidence[:18000])
    groq = str(os.getenv("GROQ_API_KEY") or "").strip()
    if groq:
        result = _originality_llm("https://api.groq.com/openai/v1/chat/completions",
            {"model":"openai/gpt-oss-120b","messages":[{"role":"system","content":prompt},{"role":"user","content":prompt}],"response_format":{"type":"json_object"},"temperature":0},
            {"Authorization":"Bearer "+groq,"Content-Type":"application/json"})
        if result is not None: return _normalise_critique(result, "groq")
    gemini = str(os.getenv("GEMINI_API_KEY") or "").strip()
    if gemini:
        result = _originality_llm("https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent",
            {"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"responseMimeType":"application/json","temperature":0}},
            {"x-goog-api-key":gemini,"Content-Type":"application/json"})
        if result is not None: return _normalise_critique(result, "gemini")
    return {"score": None, "unsupported_claims": ["Critique provider unavailable."], "exaggerations": [], "fixes": ["Run critique with Groq or Gemini."], "provider": "unavailable"}


def append_research_sources(description, research_sources, max_chars=5000):
    lines, seen = [], set()
    for source in research_sources if isinstance(research_sources, list) else []:
        if not isinstance(source, dict):
            continue
        publisher = str(source.get("publisher") or source.get("source_name") or source.get("source") or source.get("domain") or "Publisher").strip()
        url = str(source.get("url") or source.get("link") or source.get("source_url") or "").strip()
        if url and (publisher, url) not in seen:
            seen.add((publisher, url))
            lines.append(publisher + " – " + url)
    if not lines:
        return str(description or "").strip()
    suffix = "\n\nSources:\n" + "\n".join(lines)
    base = str(description or "").strip()
    return base[:max(0, max_chars - len(suffix))].rstrip() + suffix


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
    grounding_changed = _ground_visual_scene_entities(result, story_data)
    result["visual_entity_grounding_changes"] = grounding_changed
    result["cta_required"] = False
    result["script_focus"] = "information_dense_storytelling"
    result["script_structure"] = _story_structure(story_data, format_mode)
    _clean_titles(result)
    return result, {
        "removed_cta": removed_cta,
        "removed_scenes": removed_scenes,
        "changed_scenes": changed_scenes,
        "visual_entity_grounding_changes": grounding_changed,
    }


def _rewrite_for_originality_once(script_data, story_data, overlap):
    scenes = [{"index": i, "voiceover": str(s.get("voiceover") or "")} for i, s in enumerate(script_data.get("script") or [], 1) if isinstance(s, dict) and not s.get("human_contributed")]
    evidence = "\n\n".join(_originality_sources(story_data)[:10])
    prompt = (
        "Rewrite ONLY these voiceover scenes into genuinely original wording. Preserve supported facts, scene count and narrative meaning. "
        "Do not add facts or quote sources. Return JSON with script entries containing index and voiceover.\n"
        "Detected overlap:" + json.dumps(overlap) + "\nSCENES:\n" + json.dumps(scenes, ensure_ascii=False)
        + "\nEVIDENCE:\n" + evidence[:16000]
    )

    def call_provider(provider_name, url, payload, headers):
        print(f"   [Script Originality] Trying {provider_name} rewrite.", flush=True)
        result = _originality_llm(url, payload, headers)
        if isinstance(result, dict) and isinstance(result.get("script"), list):
            return result
        print(f"   [Script Originality] {provider_name} rewrite unavailable.", flush=True)
        return None

    groq = str(os.getenv("GROQ_API_KEY") or "").strip()
    if groq:
        result = call_provider(
            "Groq",
            "https://api.groq.com/openai/v1/chat/completions",
            {
                "model": "openai/gpt-oss-120b",
                "messages": [
                    {"role": "system", "content": "Rewrite for originality while preserving facts."},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.2,
            },
            {"Authorization": "Bearer " + groq, "Content-Type": "application/json"},
        )
    else:
        result = None

    if result is None:
        openrouter = str(os.getenv("OPENROUTER_API_KEY") or "").strip()
        if openrouter:
            result = call_provider(
                "OpenRouter free",
                "https://openrouter.ai/api/v1/chat/completions",
                {
                    "model": "openrouter/free",
                    "messages": [
                        {"role": "system", "content": "Rewrite for originality while preserving facts."},
                        {"role": "user", "content": prompt},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.2,
                },
                {
                    "Authorization": "Bearer " + openrouter,
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/AakarshBot/viral-shorts-factory",
                    "X-Title": "Viral Shorts Factory",
                },
            )

    if result is None:
        gemini = str(os.getenv("GEMINI_API_KEY") or "").strip()
        if gemini:
            result = call_provider(
                "Gemini",
                "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent",
                {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
                },
                {"x-goog-api-key": gemini, "Content-Type": "application/json"},
            )

    if not isinstance(result, dict) or not isinstance(result.get("script"), list):
        return None

    replacements = {
        int(x.get("index")): str(x.get("voiceover") or "").strip()
        for x in result["script"]
        if isinstance(x, dict) and str(x.get("index") or "").isdigit()
    }
    rewritten = dict(script_data)
    rewritten["script"] = [
        dict(s, voiceover=replacements.get(i, s.get("voiceover", "")))
        for i, s in enumerate(script_data.get("script") or [], 1)
    ]
    rewritten["originality_rewrite_attempted"] = True
    return rewritten

def validate_content_density(script_data, story_data, format_mode):
    scenes = script_data.get("script", []) if isinstance(script_data, dict) else []
    if not scenes:
        return False, "Script became empty after removing performative filler."

    minimum, maximum = _script_scene_bounds(format_mode)
    if not (minimum <= len(scenes) <= maximum):
        return False, f"Script has {len(scenes)} scenes; required {minimum}-{maximum}."

    topic_terms = _topic_terms(story_data)
    grounding_words = []
    filler_hits = []
    total_words = 0

    for index, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict):
            return False, f"Scene {index} is malformed."
        text = str(scene.get("voiceover", "")).strip()
        words = _words(text)
        total_words += len(words)
        grounding_words.extend(words)
        grounding_words.extend(_words(scene.get("primary_entity", "")))
        grounding_words.extend(_words(scene.get("specific_search_prompt", "")))

        if len(words) < SCENE_MIN_WORDS:
            return False, f"Scene {index} has {len(words)} words; minimum is {SCENE_MIN_WORDS}."
        if len(words) > SCENE_MAX_WORDS:
            return False, f"Scene {index} has {len(words)} words; maximum is {SCENE_MAX_WORDS}."
        if _looks_like_filler(text):
            filler_hits.append(index)

    if total_words < SCRIPT_MIN_TOTAL_WORDS:
        return False, f"Script contains only {total_words} narration words; minimum is {SCRIPT_MIN_TOTAL_WORDS}."

    angle = str(script_data.get("editorial_angle") or "").strip()
    if len(_words(angle)) < 8:
        return False, "Script is missing a substantive editorial angle."

    if filler_hits:
        return False, "Performative filler remains in scene(s): " + ", ".join(map(str, filler_hits))

    if topic_terms:
        overlap = len(set(grounding_words) & topic_terms)
        required = 1 if any(
            set(_words(str(scene.get("primary_entity", "")))) & topic_terms
            for scene in scenes
        ) else min(2, len(topic_terms))
        if overlap < required:
            return False, "Narration is not sufficiently grounded in the selected topic."

    return True, "Passed story-specific content-density and anti-filler checks"


def _extractive_script_fallback(story_data, language_cfg, genre_key, format_mode):
    """Build a source-only emergency script without weakening the script contract."""
    story_data = story_data if isinstance(story_data, dict) else {}
    title = re.sub(r"\s+", " ", str(story_data.get("title") or story_data.get("topic") or "Untitled story")).strip()
    raw_source = " ".join(
        str(story_data.get(key) or "")
        for key in ("research_evidence_text", "text", "summary", "description")
    )
    raw_source = re.sub(r"<[^>]+>", " ", raw_source)
    raw_source = re.sub(r"https?://\S+", " ", raw_source)
    raw_source = re.sub(r"\s+", " ", raw_source).strip()

    source_words = raw_source.split()
    minimum, maximum = _script_scene_bounds(format_mode)
    if len(source_words) < minimum * SCENE_MIN_WORDS:
        raise ValueError(
            f"Source-grounded fallback needs at least {minimum * SCENE_MIN_WORDS} usable source words; "
            f"only {len(source_words)} were available."
        )

    target = max(minimum, min(maximum, int(round(len(source_words) / 18.0))))
    while target > minimum and len(source_words) // target < SCENE_MIN_WORDS:
        target -= 1
    if len(source_words) // target > SCENE_MAX_WORDS:
        target = maximum

    base, extra = divmod(len(source_words), target)
    if not (SCENE_MIN_WORDS <= base <= SCENE_MAX_WORDS):
        raise ValueError("Source-grounded fallback cannot distribute evidence within the scene word envelope.")

    chunks = []
    cursor = 0
    for index in range(target):
        size = base + (1 if index < extra else 0)
        if not (SCENE_MIN_WORDS <= size <= SCENE_MAX_WORDS):
            raise ValueError("Source-grounded fallback exceeded the scene word envelope.")
        chunks.append(" ".join(source_words[cursor:cursor + size]).strip())
        cursor += size

    entity = title.split(":", 1)[0].strip()[:80] or "Selected story"
    category_label = str(genre_key or "news").replace("_", " ").title()
    search_prompt = re.sub(r"[|#]+", " ", title).strip() or entity

    scenes = [
        {
            "voiceover": chunk,
            "primary_entity": entity,
            "visual_intent": "news_event" if index > 1 else "editorial_person",
            "specific_search_prompt": search_prompt,
            "sport_or_topic_category": category_label,
            "scene_id": index,
        }
        for index, chunk in enumerate(chunks, 1)
    ]

    result = {
        "step_1_headline": title,
        "step_2_data_points": raw_source or title,
        "step_3_critique": "Deterministic source-grounded fallback used because script providers were exhausted.",
        "step_4_metadata": entity,
        "editorial_angle": "Emergency source-only mode provides no original editorial analysis.",
        "titles": [title[:100].strip(), f"{title[:80].strip()} | What We Know", f"{title[:80].strip()} | Latest Facts"],
        "recommended_title_index": 1,
        "seo_description": re.sub(r"\s+", " ", raw_source or title).strip()[:700],
        "tags": [tag for tag in (entity, category_label, "Shorts") if tag],
        "pinned_comment": "What do you make of this development?",
        "hook_type": "Direct Factual Headline",
        "hook_style_used": "Direct Factual Headline",
        "structure_used": "Source-grounded explainer",
        "persona_used": "Analytical Insider",
        "script": scenes,
        "fallback_mode": "extractive_source_grounded",
        "public_publish_blocked": True,
    }
    valid, reason = validate_content_density(result, story_data, format_mode)
    if not valid:
        raise ValueError(f"Source-grounded fallback failed script contract: {reason}")
    return result


def wrap_write_script(bot):
    current = getattr(bot, "write_script", None)
    if current is None or getattr(current, "_content_dense_bound", False): return current

    def write_script(story_data, language_cfg, genre_key, conn, format_mode):
        # The primary writer owns the editorial instructions. Do not mix hidden
        # prompt text into source material used for grounding or originality checks.
        result = current(story_data, language_cfg, genre_key, conn, format_mode)

        cleaned, diagnostics = clean_script_data(result, story_data, format_mode)
        repaired, structure_diag = repair_script_structure(cleaned, format_mode)
        if repaired is not None:
            cleaned = repaired
            if structure_diag.get("changed"):
                print(
                    "   [Script QC] Local structure repair: "
                    f"{structure_diag.get('original_scene_count')} -> {structure_diag.get('final_scene_count')} scenes.",
                    flush=True,
                )
        if diagnostics["changed_scenes"] or diagnostics["removed_scenes"]:
            print(
                "   [Script QC] Structural cleanup: "
                f"{diagnostics['changed_scenes']} scene(s) edited, {diagnostics['removed_scenes']} scene(s) removed.",
                flush=True,
            )

        ok, reason = validate_content_density(cleaned, story_data, format_mode)
        if not ok:
            print(
                f"   [Script Fallback] AI script unavailable/invalid ({reason}). "
                "Using deterministic source-grounded fallback.",
                flush=True,
            )
            fallback = _extractive_script_fallback(story_data, language_cfg, genre_key, format_mode)
            cleaned, fallback_diag = clean_script_data(fallback, story_data, format_mode)
            ok, reason = validate_content_density(cleaned, story_data, format_mode)
            if not ok:
                raise ValueError(f"Content-density gate failed after deterministic fallback: {reason}")
            cleaned["fallback_diagnostics"] = fallback_diag
            cleaned["originality_overlap"] = check_script_originality(cleaned, story_data)
            cleaned["originality_critique"] = {"score": None, "unsupported_claims": ["Extractive source-grounded fallback is not eligible for public publication."], "exaggerations": [], "fixes": [], "provider": "fallback"}
            return cleaned

        originality = check_script_originality(cleaned, story_data)
        if not originality["passed"]:
            print(
                f"   [Script Originality] Overlap detected: {len(originality['failures'])} scene(s). Requesting one rewrite.",
                flush=True,
            )
            rewritten = _rewrite_for_originality_once(cleaned, story_data, originality)
            if rewritten is None:
                cleaned["originality_rewrite_diagnostics"] = {
                    "available": False,
                    "reason": "No configured rewrite provider was available.",
                }
                cleaned["public_publish_blocked"] = True
                cleaned["originality_overlap"] = originality
                print(
                    "   [Script Originality] Rewrite provider unavailable; keeping the validated script preview-only.",
                    flush=True,
                )
                return cleaned
            cleaned, rewrite_diag = clean_script_data(rewritten, story_data, format_mode)
            repaired, structure_diag = repair_script_structure(cleaned, format_mode)
            if repaired is not None:
                cleaned = repaired
            ok, reason = validate_content_density(cleaned, story_data, format_mode)
            if not ok:
                raise ValueError(f"Originality rewrite failed script validation: {reason}")
            originality = check_script_originality(cleaned, story_data)
            cleaned["originality_rewrite_diagnostics"] = rewrite_diag
            if not originality["passed"]:
                cleaned["public_publish_blocked"] = True
                print(
                    "   [Script Originality] Rewrite did not clear overlap; keeping the script preview-only.",
                    flush=True,
                )
                return cleaned
        cleaned["originality_overlap"] = originality

        critique = _run_real_critique(cleaned, story_data)
        cleaned["originality_critique"] = critique
        if critique.get("unsupported_claims"):
            cleaned["public_publish_blocked"] = True
            print(
                "   [Script Critique] Unsupported claims or unavailable critique provider; keeping the script preview-only.",
                flush=True,
            )
            return cleaned

        return cleaned

    write_script._content_dense_bound = True
    write_script._research_layer_live = bool(getattr(current, "_research_wrapped", False))
    bot.write_script = write_script
    return write_script
