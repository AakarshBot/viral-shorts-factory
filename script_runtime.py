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
    "comparison": "headline → establish both sides → give the defining difference → explain why it matters",
    "timeline": "headline → key starting point → pivotal development → current consequence",
    "ranking": "headline → identify the subject → strongest evidence/details → why the ranking matters",
    "how_to": "headline → explain the mechanism/process → key evidence → practical consequence",
    "explainer": "headline → core facts → useful context → important development → consequence",
}


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
            if longest >= 8 or ratio > 0.15:
                failures.append({"scene": scene_index, "source_index": source_index, "longest_run": longest, "sixgram_ratio": ratio})
                break
    return {"passed": not failures, "failures": failures, "source_count": len(sources)}


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
    prompt = ("Rewrite ONLY these voiceover scenes into genuinely original wording. Preserve supported facts and order. "
              "Do not add facts or quote sources. Return JSON with script entries containing index and voiceover.\nDetected overlap:"
              + json.dumps(overlap) + "\nSCENES:\n" + json.dumps(scenes, ensure_ascii=False) + "\nEVIDENCE:\n" + evidence[:16000])
    groq = str(os.getenv("GROQ_API_KEY") or "").strip()
    gemini = str(os.getenv("GEMINI_API_KEY") or "").strip()
    if groq:
        result = _originality_llm("https://api.groq.com/openai/v1/chat/completions",
            {"model":"openai/gpt-oss-120b","messages":[{"role":"system","content":"Rewrite for originality while preserving facts."},{"role":"user","content":prompt}],"response_format":{"type":"json_object"},"temperature":0.2},
            {"Authorization":"Bearer "+groq,"Content-Type":"application/json"})
    elif gemini:
        result = _originality_llm("https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent",
            {"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"responseMimeType":"application/json","temperature":0.2}},
            {"x-goog-api-key":gemini,"Content-Type":"application/json"})
    else:
        return None
    if not isinstance(result, dict) or not isinstance(result.get("script"), list):
        return None
    replacements = {int(x.get("index")): str(x.get("voiceover") or "").strip() for x in result["script"] if isinstance(x, dict) and str(x.get("index") or "").isdigit()}
    rewritten = dict(script_data)
    rewritten["script"] = [dict(s, voiceover=replacements.get(i, s.get("voiceover", ""))) for i, s in enumerate(script_data.get("script") or [], 1)]
    rewritten["originality_rewrite_attempted"] = True
    return rewritten


def validate_content_density(script_data, story_data, format_mode):
    scenes = script_data.get("script", []) if isinstance(script_data, dict) else []
    if not scenes:
        return False, "Script became empty after removing performative filler."
    topic_terms = _topic_terms(story_data)
    grounding_words = []
    filler_hits = []
    for index, scene in enumerate(scenes, 1):
        text = str(scene.get("voiceover", "")).strip()
        words = _words(text)
        grounding_words.extend(words)
        grounding_words.extend(_words(scene.get("primary_entity", "")))
        grounding_words.extend(_words(scene.get("specific_search_prompt", "")))
        if len(words) < 3:
            return False, f"Scene {index} contains too little usable narration."
        if _looks_like_filler(text):
            filler_hits.append(index)
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
    """Build a strictly source-grounded emergency script without an LLM.

    This is used only when all configured script providers are exhausted or
    produce unusable output. It never invents facts; it reuses the selected
    story title/source text and gives every scene explicit visual metadata.
    """
    story_data = story_data if isinstance(story_data, dict) else {}
    title = re.sub(r"\s+", " ", str(story_data.get("title") or story_data.get("topic") or "Untitled story")).strip()
    raw_source = " ".join(
        str(story_data.get(key) or "")
        for key in ("text", "summary", "description")
    )
    raw_source = re.sub(r"<[^>]+>", " ", raw_source)
    raw_source = re.sub(r"https?://\S+", " ", raw_source)
    raw_source = re.sub(r"\s+", " ", raw_source).strip()

    combined = " ".join(part for part in (title, raw_source) if part).strip()
    words = combined.split()

    # Keep chunks contiguous and source-derived. We prefer five distinct
    # chunks; when the source is short, use the title as limited overlap.
    chunks = []
    if len(words) >= 40:
        chunk_size = max(8, (len(words) + 4) // 5)
        for start in range(0, len(words), chunk_size):
            chunk = " ".join(words[start:start + chunk_size]).strip()
            if chunk:
                chunks.append(chunk)
            if len(chunks) == 5:
                break
    else:
        sentences = [
            re.sub(r"\s+", " ", s).strip(" -")
            for s in re.split(r"(?<=[.!?])\s+", raw_source)
            if len(re.findall(r"[A-Za-z0-9]+", s)) >= 5
        ]
        chunks.extend(sentences[:5])
        if title and len(chunks) < 5:
            chunks.insert(0, title)
        # Pad only with source-derived combinations, never new factual claims.
        source_fragments = [title] + [s for s in sentences if s != title]
        cursor = 0
        while len(chunks) < 5 and source_fragments:
            a = source_fragments[cursor % len(source_fragments)]
            b = source_fragments[(cursor + 1) % len(source_fragments)]
            candidate = re.sub(r"\s+", " ", f"{a} {b}").strip()
            if candidate and candidate not in chunks:
                chunks.append(candidate)
            cursor += 1
            if cursor > 12:
                break

    # Guarantee five renderable scenes. The selected headline is the only
    # source we may repeat when upstream text is unusually short.
    fallback_seed = title or "Selected story"
    while len(chunks) < 5:
        chunks.append(fallback_seed)

    def fit_words(value, minimum=8, maximum=30):
        parts = value.split()
        if len(parts) > maximum:
            value = " ".join(parts[:maximum])
            parts = value.split()
        if len(parts) < minimum:
            seed_parts = fallback_seed.split()
            while len(parts) < minimum and seed_parts:
                parts.append(seed_parts[(len(parts) - minimum) % len(seed_parts)])
        return " ".join(parts[:maximum]).strip()

    # Use the strongest obvious entity token from the headline/source.
    entity = ""
    for token in re.findall(r"\b[A-Z][A-Za-z0-9&.-]{2,}\b", title):
        if token.lower() not in {"The", "This", "After", "Report", "Latest"}:
            entity = token
            break
    if not entity:
        entity = title.split(":", 1)[0].strip()[:80] or "Selected story"

    category_label = str(genre_key or "news").replace("_", " ").title()
    search_prompt = re.sub(r"[|#]+", " ", title).strip()
    if len(search_prompt.split()) < 3:
        search_prompt = f"{search_prompt} {category_label}".strip()

    scenes = []
    for idx in range(5):
        voiceover = fit_words(chunks[idx])
        scenes.append({
            "voiceover": voiceover,
            "primary_entity": entity,
            "visual_intent": "news_event" if idx else "editorial_person",
            "specific_search_prompt": search_prompt,
            "sport_or_topic_category": category_label,
        })

    clean_title = title[:92].strip()
    description_source = re.sub(r"\s+", " ", raw_source or title).strip()
    description_source = description_source[:700]
    return {
        "step_1_headline": title,
        "step_2_data_points": raw_source or title,
        "step_3_critique": "Deterministic source-grounded fallback used because script providers were unavailable.",
        "step_4_metadata": entity,
        "titles": [
            f"{clean_title} #shorts",
            f"{clean_title} | Latest Update #shorts",
            f"{clean_title} | What We Know #shorts",
        ],
        "recommended_title_index": 1,
        "seo_description": f"{description_source}\n\n#News #Sports #Trending",
        "tags": [entity, category_label, "Shorts"],
        "pinned_comment": "What do you make of this latest development?",
        "hook_type": "Direct Factual Headline",
        "hook_style_used": "Direct Factual Headline",
        "structure_used": "Source-grounded explainer",
        "persona_used": "Analytical Insider",
        "script": scenes,
        "fallback_mode": "extractive_source_grounded",
    }

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
            return cleaned

        return cleaned

    write_script._content_dense_bound = True
    write_script._research_layer_live = bool(getattr(current, "_research_wrapped", False))
    bot.write_script = write_script
    return write_script
