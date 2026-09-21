"""Runtime safeguards and generation contract for compact, information-dense Shorts."""

import json
import os
import re
import urllib.request
from difflib import SequenceMatcher

_RETENTION_BAIT_RE = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bwait\s+(?:until|till|for)\s+(?:the\s+)?end\b",
        r"\bwait\s+for\s+it\b",
        r"\bwait\s+(?:for|until|till)\s+(?:the\s+)?reveal\b",
        r"\b(?:watch|keep|continue)\s+watching\b",
        r"\b(?:stay|stick)\s+(?:around|with\s+me|with\s+us)\b",
        r"\bstay\s+tuned\b",
        r"\bdon['’]?t\s+go\s+anywhere\b",
        r"\b(?:you\s+)?won['’]?t\s+believe\b",
        r"\byou['’]?ll\s+never\s+guess\b",
        r"\bwhat\s+happens\s+(?:next|at\s+the\s+end)\b",
        r"\bfind\s+out\s+(?:at\s+the\s+end|what\s+happens)\b",
        r"\bby\s+the\s+end\s+you['’]?ll\b",
        r"\b(?:and\s+)?that['’]?s\s+not\s+all\b",
        r"\bmore\s+on\s+this\s+(?:later|at\s+the\s+end)\b",
        r"\b(?:prepare|get ready)\s+for\s+(?:this|what['’]?s next)\b",
        r"\b(?:don['’]?t|do not)\s+miss\s+(?:what\s+comes\s+next|the\s+reveal)\b",
        r"\bthe\s+(?:best|biggest|most\s+important)\s+part\s+is\s+(?:coming|later)\b",
        r"\bstay\s+till\s+the\s+end\b",
        r"\b(?:watch|stay|stick)\s+(?:until|till)\s+(?:the\s+)?end\b",
        r"\bfind\s+out\s+later\b",
        r"\b(?:i'll|we'll|we\s+will)\s+reveal\s+(?:it|that)\s+later\b",
    )
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




def contains_retention_bait(text):
    value = str(text or "").strip()
    return [pattern.pattern for pattern in _RETENTION_BAIT_RE if pattern.search(value)]


_NARRATIVE_ROLE_ALIASES = {
    "fact": "hook",
    "headline": "hook",
    "event": "hook",
    "update": "development",
    "body": "development",
    "background": "context",
    "analysis": "context",
    "implication": "consequence",
    "outcome": "consequence",
}


def assess_narrative_completeness(script_data):
    scenes = script_data.get("script", []) if isinstance(script_data, dict) else []
    if not scenes:
        return {"passed": False, "reason": "Script contains no narration scenes.", "roles": {}}

    roles = {}
    for index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            return {"passed": False, "reason": f"Scene {index + 1} is malformed.", "roles": roles}
        if not str(scene.get("voiceover") or "").strip():
            return {"passed": False, "reason": f"Scene {index + 1} is empty.", "roles": roles}

        role = str(scene.get("narrative_role") or "").strip().lower().replace("-", "_").replace(" ", "_")
        role = _NARRATIVE_ROLE_ALIASES.get(role, role)
        if index == 0 and not role:
            role = "hook"
        elif index == len(scenes) - 1 and not role:
            role = "consequence"

        if role in {"hook", "development", "context", "consequence"}:
            roles.setdefault(role, []).append(index + 1)

    missing = sorted({"hook", "development", "context", "consequence"} - set(roles))
    if missing:
        return {
            "passed": False,
            "reason": (
                "Narrative is incomplete; distinct hook, development, context and consequence "
                "beats are missing. Missing: " + ", ".join(missing) + "."
            ),
            "roles": roles,
        }
    return {
        "passed": True,
        "reason": "Narrative covers distinct hook, development, context and consequence beats.",
        "roles": roles,
    }


def _normalise(text): return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", str(text or "").lower())).strip()
def _words(text): return re.findall(r"[A-Za-z0-9]+", str(text or "").lower())

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
    value = _CTA_RE.sub("", value)
    for pattern in _RETENTION_BAIT_RE:
        value = pattern.sub("", value)
    value = re.sub(r"\s+([,.!?])", r"\1", value)
    return re.sub(r"\s{2,}", " ", value).strip(" ,;:-")


def _looks_like_filler(text):
    value = _normalise(text)
    if not value:
        return True
    raw = str(text or "")
    if contains_retention_bait(raw):
        return True
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
        "Rewrite ONLY these voiceover scenes into genuinely original wording. Preserve supported facts and order. "
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

def assess_release_structure(script_data, format_mode="regular"):
    """Check production-ready narrative structure without word/character quotas."""
    assessment = assess_narrative_completeness(script_data)
    if not assessment.get("passed"):
        return False, assessment.get("reason", "Narrative structure is incomplete."), assessment

    scenes = script_data.get("script", []) if isinstance(script_data, dict) else []
    count = len(scenes)
    # Four distinct newsroom beats are the smallest coherent story: hook,
    # development, context and consequence. This prevents 1–3 scene stubs
    # without imposing a word or character target.
    if count < 4:
        return False, "Script is too compressed: it lacks enough distinct narrative beats.", assessment
    if str(format_mode or "").lower() == "top5" and count < 5:
        return False, "Top-5 script is too compressed to present the list structure.", assessment
    return True, "Narrative structure is production-ready.", assessment


def validate_content_density(script_data, story_data, format_mode):
    """Semantic script gate; no scene-count or word-count quotas."""
    if not isinstance(script_data, dict):
        return False, "Script is missing."
    scenes = script_data.get("script")
    if not isinstance(scenes, list) or not scenes:
        return False, "Script contains no narration scenes."

    for index, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict):
            return False, f"Scene {index} is malformed."
        voiceover = str(scene.get("voiceover") or "").strip()
        if not voiceover:
            return False, f"Scene {index} is empty."
        if contains_retention_bait(voiceover):
            return False, f"Scene {index} contains prohibited retention-bait phrasing."
        if _looks_like_filler(voiceover):
            return False, f"Scene {index} contains performative or generic filler."
        if not str(scene.get("primary_entity") or "").strip():
            return False, f"Scene {index} is missing a supported primary entity."
        if not str(scene.get("specific_search_prompt") or "").strip():
            return False, f"Scene {index} is missing a specific visual search prompt."

    editorial_angle = str(script_data.get("editorial_angle") or "").strip()
    if not editorial_angle or _looks_like_filler(editorial_angle) or contains_retention_bait(editorial_angle):
        return False, "Script is missing a genuine editorial angle."

    completeness = assess_narrative_completeness(script_data)
    if not completeness["passed"]:
        return False, completeness["reason"]

    return True, "Passed semantic narrative completeness and anti-retention checks"


def _fallback_source_fragments(*values):
    """Return source-derived prose while rejecting prompt/schema/workflow leakage."""
    try:
        from script_guard_runtime import looks_like_instructional_narration
    except Exception:
        looks_like_instructional_narration = None

    fragments = []
    seen = set()
    for value in values:
        text = re.sub(r"<[^>]+>", " ", str(value or ""))
        text = re.sub(r"https?://\S+", " ", text)
        for raw in re.split(r"(?<=[.!?])\s+|\n+", text):
            sentence = re.sub(r"\s+", " ", raw).strip(" -")
            if not sentence or len(re.findall(r"\b\w+\b", sentence)) < 5:
                continue

            if re.match(
                r"^(?:PHASE 2 EVIDENCE PACK|STATUS|SOURCE HIERARCHY|VERIFIED SOURCE METADATA|CLAIMS|"
                r"CONFLICTS(?:\s+[—-].*)?|DISCOVERY-ONLY SOURCES)\s*:?$",
                sentence,
                re.IGNORECASE,
            ):
                continue
            if re.match(r"^(?:variant|independent sources)\s*:", sentence, re.IGNORECASE):
                continue

            sentence = re.sub(
                r"^\[(?:CORROBORATED|PRIMARY_ONLY|SINGLE_SOURCE|CONFLICTED)\]\s*",
                "",
                sentence,
                flags=re.IGNORECASE,
            )
            sentence = re.sub(
                r"\s+\(independent sources:.*?\)\s*$",
                "",
                sentence,
                flags=re.IGNORECASE,
            )
            if not sentence or len(re.findall(r"\b\w+\b", sentence)) < 5:
                continue
            if looks_like_instructional_narration and looks_like_instructional_narration(sentence):
                continue
            if contains_retention_bait(sentence) or _looks_like_filler(sentence):
                continue

            key = _normalise(sentence)
            if key and key not in seen:
                seen.add(key)
                fragments.append(sentence)
    return fragments


def _extractive_script_fallback(story_data, language_cfg, genre_key, format_mode):
    """Emergency source-only fallback; never fabricate filler or collapse a complete story."""
    story_data = story_data if isinstance(story_data, dict) else {}
    title = re.sub(r"\s+", " ", str(story_data.get("title") or story_data.get("topic") or "Untitled story")).strip()

    raw_parts = [
        str(story_data.get(key) or "")
        for key in ("text", "summary", "description")
        if str(story_data.get(key) or "").strip()
    ]
    research_text = str(story_data.get("research_evidence_text") or "").strip()
    source_fragments = _fallback_source_fragments(*raw_parts)
    # Prefer the actual selected-story prose. Use Phase 2's formatted evidence
    # text only when the selected-story fields do not provide enough narration.
    if len(source_fragments) < 4 and research_text:
        source_fragments.extend(_fallback_source_fragments(research_text))
    raw_source = " ".join(source_fragments)

    if str(format_mode or "").lower() == "top5" and story_data.get("text"):
        try:
            items = json.loads(str(story_data.get("text")))
            if isinstance(items, list):
                parts = []
                for item in items:
                    if isinstance(item, dict):
                        parts.extend(
                            str(item.get(key) or "").strip()
                            for key in ("title", "text", "summary")
                        )
                raw_source = " ".join(part for part in parts if part).strip() or raw_source
        except (TypeError, ValueError, json.JSONDecodeError):
            pass

    sentences = source_fragments
    entity = title.split(":", 1)[0].strip()[:80] or "Selected story"
    category = str(genre_key or "news").replace("_", " ").title()
    scenes = []
    fallback_sentences = list(sentences)
    # The emergency path should still open with the concrete story headline,
    # not with source boilerplate or a generic setup sentence. Replace the
    # first source beat rather than adding a new scene.
    if title and fallback_sentences:
        fallback_sentences[0] = title
    for index, sentence in enumerate(fallback_sentences, 1):
        role = (
            "hook" if index == 1
            else "development" if index == 2
            else "context" if index == 3
            else "consequence" if index == len(fallback_sentences)
            else ""
        )
        scenes.append({
            "voiceover": sentence,
            "primary_entity": entity,
            "visual_intent": "news_event",
            "specific_search_prompt": title or entity,
            "sport_or_topic_category": category,
            "narrative_role": role,
            "scene_id": index,
        })

    result = {
        "step_1_headline": title,
        "step_2_data_points": raw_source,
        "step_3_critique": "Deterministic source-grounded emergency fallback.",
        "step_4_metadata": entity,
        "editorial_angle": "Emergency source-only mode; original editorial analysis was not generated.",
        "titles": [title, f"{title} | What We Know", f"{title} | Latest Facts"],
        "recommended_title_index": 1,
        "seo_description": raw_source[:700],
        "tags": [tag for tag in (entity, category, "Shorts") if tag],
        "pinned_comment": "What do you make of this development?",
        "hook_type": "Direct Factual Headline",
        "hook_style_used": "Direct Factual Headline",
        "structure_used": _story_structure(story_data, format_mode),
        "persona_used": "Analytical Insider",
        "script": scenes,
        "fallback_mode": "extractive_source_grounded",
        "public_publish_blocked": True,
    }
    valid, reason = validate_content_density(result, story_data, format_mode)
    if not valid:
        raise ValueError(f"Source-grounded fallback refused to invent narration: {reason}")
    return result
