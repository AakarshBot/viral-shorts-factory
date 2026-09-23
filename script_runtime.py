"""Runtime safeguards and generation contract for compact, information-dense Shorts."""

import json
import os
import re
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
    try:
        from unicode_runtime import unicode_words
        return unicode_words(text)
    except Exception:
        return re.findall(r"\b[\w]+(?:['’][\w]+)?\b", str(text or "").casefold(), flags=re.UNICODE)


def _originality_sources(story_data):
    story = story_data if isinstance(story_data, dict) else {}
    values = []

    def collect(value):
        if isinstance(value, str):
            if value.strip():
                values.append(value)
        elif isinstance(value, dict):
            for key in (
                "text", "content", "extracted_text", "body", "summary", "snippet",
                "title", "claim", "claims", "evidence", "source_text", "sources",
                "articles", "items",
            ):
                if key in value:
                    collect(value[key])
        elif isinstance(value, (list, tuple)):
            for item in value:
                collect(item)

    pack = story.get("research_evidence_pack")
    collect(pack.get("sources") if isinstance(pack, dict) else pack)
    for key in ("research_evidence_text", "research_bundle", "text", "summary", "description"):
        collect(story.get(key))

    seen, unique = set(), []
    for value in values:
        clean = re.sub(r"\s+", " ", value).strip()
        if clean and clean not in seen:
            seen.add(clean)
            unique.append(clean)
    return unique


def _normalise_originality_sentence(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").casefold()).strip()


def _source_sentences(story_data):
    sentences = set()
    for source in _originality_sources(story_data):
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", str(source or "")):
            sentence = re.sub(r"\s+", " ", sentence).strip()
            normalised = _normalise_originality_sentence(sentence)
            if len(_originality_words(normalised)) >= 6:
                sentences.add(normalised)
    return sentences


def check_script_originality(script_data, story_data):
    """Reject only narration sentences copied verbatim from the researched sources."""
    source_sentences = _source_sentences(story_data)
    failures = []
    scenes = script_data.get("script", []) if isinstance(script_data, dict) else []
    for scene_index, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict) or scene.get("human_contributed"):
            continue
        voiceover = str(scene.get("voiceover") or "")
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", voiceover):
            normalised = _normalise_originality_sentence(sentence)
            if normalised and normalised in source_sentences:
                failures.append({
                    "scene": scene_index,
                    "copied_sentence": sentence.strip(),
                })
                break
    return {
        "passed": not failures,
        "failures": failures,
        "source_count": len(_originality_sources(story_data)),
    }

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
    """Require a real hook, middle development/context beat and payoff."""
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
        if index == len(scenes) - 1 and not role:
            role = "consequence"
        if role in {"hook", "development", "context", "consequence"}:
            roles.setdefault(role, []).append(index + 1)

    if len(scenes) < 3:
        return {
            "passed": False,
            "reason": "Narrative is incomplete: a regular Short needs a hook, a substantive middle beat, and a payoff.",
            "roles": roles,
        }

    first_role = next(iter(roles), "")
    first_scene_role = str(scenes[0].get("narrative_role") or "").strip().lower().replace("-", "_").replace(" ", "_")
    first_scene_role = _NARRATIVE_ROLE_ALIASES.get(first_scene_role, first_scene_role)
    last_scene_role = str(scenes[-1].get("narrative_role") or "").strip().lower().replace("-", "_").replace(" ", "_")
    last_scene_role = _NARRATIVE_ROLE_ALIASES.get(last_scene_role, last_scene_role)

    if first_scene_role != "hook":
        return {
            "passed": False,
            "reason": "Scene 1 must be the factual retention hook.",
            "roles": roles,
        }

    if last_scene_role != "consequence":
        return {
            "passed": False,
            "reason": "The final scene must deliver the consequence, payoff or closing implication.",
            "roles": roles,
        }

    if not ({"development", "context"} & set(roles)):
        return {
            "passed": False,
            "reason": "Narrative needs at least one development or context beat between the hook and payoff.",
            "roles": roles,
        }

    return {
        "passed": True,
        "reason": "Narrative contains a clear hook, a substantive middle beat and a payoff.",
        "roles": roles,
    }

def _normalise(text): return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", str(text or "").lower())).strip()
def _words(text): return re.findall(r"[A-Za-z0-9]+", str(text or "").lower())

NARRATION_BASE_WPM = 150.0
NARRATION_IDEAL_MIN_SECONDS = 20.0
NARRATION_IDEAL_MAX_SECONDS = 28.0
NARRATION_ACCEPTABLE_MAX_SECONDS = 30.0

# The initial writer uses a deliberately conservative spoken-word ceiling.
# 60 words leaves margin below 30s even at the slowest configured narrator profile.
INITIAL_SCRIPT_MAX_WORDS = 60
SCENE_1_MAX_WORDS = 14


def estimate_narration_duration(script_data, persona_profile=None, base_wpm=NARRATION_BASE_WPM):
    """Estimate spoken duration before TTS using the selected persona's Edge-TTS rate."""
    scenes = script_data.get("script", []) if isinstance(script_data, dict) else []
    text = " ".join(
        str(scene.get("voiceover") or "").strip()
        for scene in scenes
        if isinstance(scene, dict)
    ).strip()
    words = _originality_words(text)
    word_count = len(words)
    try:
        rate = float(str((persona_profile or {}).get("rate", "0")).replace("%", "").strip() or 0)
    except (TypeError, ValueError):
        rate = 0.0
    try:
        wpm = max(60.0, float(base_wpm) * (1.0 + rate / 100.0))
    except (TypeError, ValueError):
        wpm = NARRATION_BASE_WPM
    sentence_count = len(re.findall(r"[.!?]+(?=\s|$)", text))
    duration = (word_count / wpm) * 60.0 + max(0, sentence_count - 1) * 0.08
    return {
        "seconds": round(max(0.0, duration), 2),
        "word_count": word_count,
        "effective_wpm": round(wpm, 2),
        "persona_rate": rate,
        "sentence_count": sentence_count,
    }


def classify_narration_duration(seconds):
    """Classify the pre-TTS estimate without forcing padding or post-approval rewrites."""
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return "unknown"
    if value >= NARRATION_ACCEPTABLE_MAX_SECONDS:
        return "too_long"
    if value >= NARRATION_IDEAL_MIN_SECONDS:
        return "ideal_or_acceptable"
    return "short_but_valid"


def measure_audio_duration(audio_paths):
    """Measure the actual duration of TTS output files without regenerating them."""
    durations = []
    for path in audio_paths if isinstance(audio_paths, (list, tuple)) else []:
        if not path or not os.path.exists(path):
            raise FileNotFoundError(f"Audio file not found: {path}")
        try:
            from moviepy import AudioFileClip
            clip = AudioFileClip(path)
            try:
                durations.append(float(clip.duration or 0.0))
            finally:
                clip.close()
        except Exception as exc:
            raise RuntimeError(f"Could not measure synthesized audio duration: {path}") from exc
    return {
        "scene_durations": [round(value, 3) for value in durations],
        "total_seconds": round(sum(durations), 3),
        "scene_count": len(durations),
    }


def validate_tts_duration(estimated_seconds, actual_seconds, tolerance_ratio=0.15, minimum_tolerance=2.0):
    """Fail closed only when synthesized audio materially disagrees with the estimate."""
    try:
        estimated = float(estimated_seconds)
        actual = float(actual_seconds)
    except (TypeError, ValueError):
        return {"passed": False, "reason": "TTS duration values are unavailable."}
    tolerance = max(float(minimum_tolerance), abs(estimated) * float(tolerance_ratio))
    delta = actual - estimated
    return {
        "passed": abs(delta) <= tolerance,
        "estimated_seconds": round(estimated, 2),
        "actual_seconds": round(actual, 2),
        "delta_seconds": round(delta, 2),
        "tolerance_seconds": round(tolerance, 2),
        "reason": (
            "Synthesized duration is within the expected variance."
            if abs(delta) <= tolerance
            else "Synthesized duration materially differs from the pre-TTS estimate."
        ),
    }



def classify_hook_style(value):
    """Classify the actual opening hook family for channel learning and diagnostics."""
    if isinstance(value, dict):
        scenes = value.get("script") or []
        if scenes and isinstance(scenes[0], dict):
            text = str(scenes[0].get("voiceover") or "").strip()
        else:
            text = str(value.get("title") or value.get("topic") or "").strip()
    else:
        text = str(value or "").strip()

    lower = text.casefold()
    if not lower:
        return "Unknown"

    conflict_terms = (
        "accused", "accusation", "criticized", "criticised", "slammed",
        "blasted", "arrogant", "controversy", "dispute", "feud", "clash",
        "mocked", "insulted", "hits back", "hit back", "rivalry", "warned",
    )
    result_terms = (
        "won", "wins", "lost", "loses", "beat", "beats", "defeated",
        "clinched", "clinches", "qualified", "qualifies", "eliminated",
        "eliminates", "secured", "secures", "record", "milestone",
        "first", "fastest", "youngest", "oldest", "200th", "100th", "50th",
    )
    quote_terms = (
        "said", "says", "called", "claimed", "claims", "declared",
        "praised", "hailed", "revealed", "admitted", "responded",
    )
    surprise_terms = (
        "unexpected", "surprise", "stuns", "stunned", "comeback",
        "debut", "rare", "unprecedented", "unlikely", "uncapped",
    )

    if "?" in text:
        return "Curiosity Question"
    if any(term in lower for term in conflict_terms):
        return "Conflict / Accusation"
    if any(term in lower for term in quote_terms) or bool(re.search(r'["“”]', text)):
        return "Bold Quote / Statement"
    if any(term in lower for term in surprise_terms):
        return "Surprise / Human Angle"
    if any(term in lower for term in result_terms):
        return "Result / Record"
    return "Direct Factual Headline"


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

_EDITORIAL_ANGLE_STRATEGIES = {
    "confrontation_led": (
        "Lead with the documented disagreement, criticism, accusation, clash, or response. "
        "Name the relevant people/teams and the concrete claim or action, then explain what triggered it "
        "and what the evidence actually establishes."
    ),
    "result_or_record_led": (
        "Lead with the verified result, record, milestone, qualification, elimination, win/loss, or other "
        "concrete outcome. Put the defining number or achievement early, then explain the context and consequence."
    ),
    "comparison_led": (
        "Use the documented comparison as the spine: establish the two sides or benchmarks, identify the "
        "measured difference, and explain why that difference matters. Do not invent superiority beyond the evidence."
    ),
    "unexpected_person_led": (
        "Center the unusual person-level development: an unexpected debut, comeback, first-time achievement, "
        "youngest/oldest milestone, outsider, unlikely participant, or other clearly supported human surprise. "
        "Explain what makes the person's role unusual rather than merely calling it shocking."
    ),
    "why_it_matters_led": (
        "Lead with the consequential change or decision, then explain who or what is affected, the immediate "
        "implication, and the strongest evidence-backed context. Do not predict unsupported future outcomes."
    ),
    "timeline_led": (
        "Use the verified sequence of events as the narrative spine. Start with the pivotal change, then move "
        "through only the earlier facts needed to understand how the story reached this point."
    ),
    "evidence_explainer": (
        "Use the clearest factual development as the opening and organize the evidence into a simple hook, "
        "development, context and consequence. Prefer concrete details over generic background."
    ),
}


def choose_editorial_angle(story_data, format_mode="regular"):
    """Choose one evidence-backed narrative lens without making another model/API call."""
    story = story_data if isinstance(story_data, dict) else {}
    text = " ".join(
        str(story.get(key) or "")
        for key in (
            "title", "topic", "summary", "description", "snippet",
            "event_search_text", "research_evidence_text", "text",
        )
    ).casefold()
    title = str(story.get("title") or story.get("topic") or "").casefold()
    actions = {
        str(action).strip().casefold()
        for action in (story.get("event_actions") or [])
        if str(action).strip()
    }
    entities = [
        str(entity).strip()
        for entity in (story.get("event_entities") or [])
        if str(entity).strip()
    ]

    def count(patterns):
        return sum(1 for pattern in patterns if re.search(pattern, text))

    scores = {
        "confrontation_led": 0.0,
        "result_or_record_led": 0.0,
        "comparison_led": 0.0,
        "unexpected_person_led": 0.0,
        "why_it_matters_led": 0.0,
        "timeline_led": 0.0,
        "evidence_explainer": 0.5,
    }

    confrontation = count((
        r"\baccused\b", r"\baccusation\b", r"\bcriticiz(?:ed|es|ing)\b",
        r"\bcriticis(?:ed|es|ing)\b", r"\bslammed\b", r"\bblasted\b",
        r"\bcalled\b.{0,45}\barrogant\b", r"\bcontrovers(?:y|ial)\b",
        r"\bdispute\b", r"\bfeud\b", r"\bclash\b", r"\bresponded\b",
        r"\bhit(?:s|ting)? back\b", r"\bmocked\b", r"\binsulted\b",
        r"\bwarned\b",
    ))
    scores["confrontation_led"] += min(8.0, confrontation * 2.0)
    if {"comment", "respond", "criticise", "criticize"} & actions:
        scores["confrontation_led"] += 2.0

    result = count((
        r"\bwon\b", r"\bwins\b", r"\blost\b", r"\bloses\b",
        r"\bdefeated\b", r"\bbeat\b", r"\bbeats\b", r"\bclinched\b",
        r"\bqualified\b", r"\beliminated\b", r"\brecord\b", r"\bmilestone\b",
        r"\bfirst\b", r"\bfastest\b", r"\bhighest\b", r"\blowest\b",
        r"\b200th\b", r"\b100th\b", r"\b50th\b",
    ))
    scores["result_or_record_led"] += min(9.0, result * 1.65)
    if actions & {"win", "defeat", "beat", "qualify", "eliminate"}:
        scores["result_or_record_led"] += 2.0

    comparison = count((
        r"\bvs\.?\b", r"\bversus\b", r"\bcompared with\b",
        r"\bcompared to\b", r"\bovertook\b", r"\bsurpassed\b",
        r"\boutpaced\b", r"\bhigher than\b", r"\blower than\b",
    ))
    if comparison and len(entities) >= 2:
        scores["comparison_led"] += min(9.0, comparison * 2.75) + 1.5

    unexpected = count((
        r"\bunexpected\b", r"\bunheralded\b", r"\boutsider\b",
        r"\bunseeded\b", r"\bunlikely\b", r"\buncapped\b",
        r"\bdebut\b", r"\bcomeback\b", r"\byoungest\b", r"\boldest\b",
        r"\bfirst[- ]time\b", r"\breturn(?:s|ed)?\b",
    ))
    if unexpected and any(
        term in text
        for term in (
            "player", "star", "actor", "singer", "founder", "scientist",
            "coach", "captain", "batter", "bowler", "person",
        )
    ):
        scores["unexpected_person_led"] += min(8.0, unexpected * 1.9) + 1.0

    consequence = count((
        r"\bbanned\b", r"\bsuspended\b", r"\binjured\b", r"\bruled out\b",
        r"\bresigned\b", r"\bappointed\b", r"\bapproved\b", r"\bblocked\b",
        r"\bcancel(?:led|ed|s)?\b", r"\bdelayed\b", r"\blaunch(?:ed|es)?\b",
        r"\bacquired\b", r"\bsigned\b", r"\bdeal\b", r"\bdecision\b",
        r"\bchange\b", r"\bimpact\b", r"\baffect(?:s|ed|ing)?\b",
    ))
    if consequence or actions & {
        "ban", "appoint", "approve", "resign", "injure", "cancel",
        "delay", "launch", "acquire", "sign",
    }:
        scores["why_it_matters_led"] += min(8.0, consequence * 1.6) + 1.5

    timeline = count((
        r"\bhistory\b", r"\btimeline\b", r"\bsince\b", r"\bpreviously\b",
        r"\bearlier\b", r"\bbefore\b", r"\bover the past\b",
        r"\bin \d{4}\b", r"\byears? (?:later|ago)\b",
    ))
    if timeline >= 2 or re.search(r"\b(?:history|timeline)\b", title):
        scores["timeline_led"] += min(7.0, timeline * 1.9)

    priority = [
        "confrontation_led",
        "result_or_record_led",
        "comparison_led",
        "unexpected_person_led",
        "why_it_matters_led",
        "timeline_led",
        "evidence_explainer",
    ]
    chosen = max(priority, key=lambda name: (scores[name], -priority.index(name)))
    return {
        "type": chosen,
        "instruction": _EDITORIAL_ANGLE_STRATEGIES[chosen],
        "signal_score": round(scores[chosen], 2),
        "signals": {
            name: round(value, 2)
            for name, value in scores.items()
            if value > 0.5
        },
        "reason": (
            "Selected from concrete event, conflict, outcome, comparison, person-level, "
            "consequence and timeline signals; the writer must still follow the evidence pack."
        ),
    }


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


def _hook_quality_score(script_data, story_data=None):
    """Score the first spoken beat for immediate, factual scroll-stop value."""
    script_data = script_data if isinstance(script_data, dict) else {}
    scenes = script_data.get("script") or []
    if not scenes or not isinstance(scenes[0], dict):
        return {"score": 0.0, "reasons": ["missing opening scene"]}

    first = str(scenes[0].get("voiceover") or "").strip()
    story = story_data if isinstance(story_data, dict) else {}
    source = " ".join(
        str(story.get(key) or "").strip()
        for key in ("title", "topic", "summary", "description", "research_evidence_text")
    ).strip()
    first_tokens = set(_originality_words(first))
    source_tokens = set(_originality_words(source))
    score = 0.0
    reasons = []

    if not first_tokens:
        return {"score": 0.0, "reasons": ["empty opening"]}

    relevance = len(first_tokens & source_tokens) / max(1, len(first_tokens))
    if relevance >= 0.55:
        score += 2.5
        reasons.append("immediately story-relevant")
    elif relevance >= 0.35:
        score += 1.5
        reasons.append("partly story-relevant")

    lower = first.casefold()
    conflict_terms = {
        "arrogant", "accused", "accusation", "blasted", "criticized", "criticised",
        "controversy", "controversial", "debate", "dispute", "feud", "hits back",
        "insulted", "mocked", "rivalry", "ruin", "slammed", "warned", "scare",
        "upset", "shock", "shocks",
    }
    surprise_terms = {
        "record", "first", "fastest", "highest", "lowest", "historic", "surprise",
        "unexpected", "upset", "comeback", "debut", "youngest", "oldest", "rare",
    }
    quote_terms = {
        "said", "says", "called", "claimed", "claims", "declared", "praised",
        "hailed", "warned", "revealed", "admitted", "responded", "criticized",
        "criticised",
    }

    action_terms = {
        "won", "wins", "lost", "loses", "beat", "beats", "defeated", "named",
        "selected", "ruled out", "injured", "returns", "returned", "retired",
        "banned", "suspended", "appointed", "signed", "launched", "revealed",
        "announced", "reached", "missed", "failed", "secured", "clinched",
    }
    if any(
        re.search(r"(?<![a-z])" + re.escape(term) + r"(?![a-z])", lower)
        for term in action_terms
    ):
        score += 1.25
        reasons.append("concrete action stated immediately")
    if any(term in lower for term in conflict_terms):
        score += 2.0
        reasons.append("tension stated immediately")
    if any(term in lower for term in surprise_terms):
        score += 1.75
        reasons.append("surprise/novelty stated immediately")
    if any(
        re.search(r"(?<![a-z])" + re.escape(term) + r"(?![a-z])", lower)
        for term in quote_terms
    ):
        score += 1.5
        reasons.append("attribution/quote signal")
    if re.search(r"\b\d{2,}\b|%", first):
        score += 0.75
        reasons.append("specific detail")

    generic_openers = (
        "today we are going to", "in this video", "here is the latest",
        "here's the latest", "let's talk about", "here is an update",
        "here's an update", "the latest update", "big news today",
    )
    if first.casefold().startswith(generic_openers):
        score -= 2.5
        reasons.append("generic setup")

    setup_lead = re.match(
        r"^(?:in|on|at|during|before|after)\s+(?:the\s+)?"
        r"(?:first|opening|latest|2026|match|tournament|league|series|day)",
        first.casefold(),
    )
    concrete_hook = any(
        term in lower
        for term in (
            "accused","accusation","arrogant","controversy","dispute","feud",
            "clash","slammed","blasted","said","says","called","claimed",
            "praised","warned","revealed","record","first","fastest","historic",
            "won","wins","lost","beat","defeated","upset","comeback","debut",
        )
    )
    if setup_lead and not concrete_hook and "?" not in first:
        score -= 1.50
        reasons.append("delayed contextual setup")

    if contains_retention_bait(first):
        score -= 3.0
        reasons.append("retention bait")
    words = len(_originality_words(first))
    if 5 <= words <= 18:
        score += 0.75
        reasons.append("tight opening")
    elif words > 28:
        score -= 0.75
        reasons.append("opening needs compression")

    return {
        "score": round(max(0.0, min(10.0, score)), 2),
        "reasons": list(dict.fromkeys(reasons)),
    }


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


def rank_title_candidates(script_data, story_data=None):
    """Choose the strongest existing title candidate without changing the title set."""
    if not isinstance(script_data, dict):
        return {"recommended_title_index": 1, "scores": []}
    titles = script_data.get("titles")
    if not isinstance(titles, list) or not titles:
        return {"recommended_title_index": 1, "scores": []}

    story = story_data if isinstance(story_data, dict) else {}
    headline = " ".join(
        str(story.get(key) or "").strip()
        for key in ("title", "topic", "canonical_title")
        if str(story.get(key) or "").strip()
    )
    evidence = str(
        story.get("research_evidence_text")
        or story.get("summary")
        or story.get("description")
        or ""
    ).strip()[:4000]
    entity_text = " ".join(
        str(scene.get("primary_entity") or "")
        for scene in (script_data.get("script") or [])
        if isinstance(scene, dict)
    ).strip()
    source_terms = set(re.findall(r"[\w]+(?:['’.-][\w]+)*", f"{headline} {entity_text} {evidence}", flags=re.UNICODE))
    source_terms = {term.casefold() for term in source_terms if len(term) > 1}
    headline_terms = set(re.findall(r"[\w]+(?:['’.-][\w]+)*", headline, flags=re.UNICODE))
    headline_terms = {term.casefold() for term in headline_terms if len(term) > 1}
    has_number = bool(re.search(r"\d|%", headline))
    hook_family = classify_hook_style(script_data)
    combined_source = f"{headline} {entity_text} {evidence}".casefold()
    grounded_title_intensity = {
        "conflict": (
            "slams", "blasts", "fires back", "hits back", "under fire",
            "backlash", "clash", "feud", "accused", "controversy",
        ),
        "surprise": (
            "stuns", "shocks", "unexpected", "surprise", "dramatic",
            "huge", "snubbed", "dropped", "benched", "breaks silence",
            "major", "shock",
        ),
        "consequence": (
            "ruled out", "banned", "suspended", "set to miss", "forced out",
            "faces", "could miss", "returns", "comeback",
        ),
    }

    scores = []
    for index, raw_title in enumerate(titles, 1):
        title = re.sub(r"\s+", " ", str(raw_title or "")).strip()
        terms = re.findall(r"[\w]+(?:['’.-][\w]+)*", title, flags=re.UNICODE)
        lowered = [term.casefold() for term in terms if len(term) > 1]
        title_set = set(lowered)
        score = 0.0
        reasons = []

        title_lower = title.casefold()
        intensity_hits = 0
        for family, terms in grounded_title_intensity.items():
            matched = [term for term in terms if term in title_lower]
            grounded = [term for term in matched if term in combined_source]
            if grounded:
                intensity_hits += len(grounded)
                reasons.append(f"{family} tension packaging")
        if intensity_hits:
            score += min(2.8, intensity_hits * 1.0)

        # A title that merely restates the source headline is useful for search,
        # but should lose to a genuinely stronger truthful package.
        headline_overlap = (
            len(title_set & headline_terms) / max(1, len(title_set))
            if headline_terms else 0.0
        )
        if headline_overlap >= 0.85 and intensity_hits < 2:
            score -= 2.50
            reasons.append("plain headline restatement")

        char_count = len(title)
        if 20 <= char_count <= 55:
            score += 1.50
            reasons.append("compact package")
        elif char_count <= 60:
            score -= 0.75
            reasons.append("title above channel target")
        else:
            score -= 2.25
            reasons.append("title too long")
        if char_count > 70:
            score -= 0.75

        if "#" in title or "|" in title:
            score -= 0.75
            reasons.append("metadata/hashtag clutter")

        metadata_hits = sum(
            1
            for term in (
                "schedule", "timings", "fixture", "match", "league", "venue",
                "1st", "2nd", "3rd", "probable xi",
            )
            if re.search(r"(?<![a-z])" + re.escape(term) + r"(?![a-z])", title.casefold())
        )
        if metadata_hits >= 3:
            score -= 1.25
            reasons.append("match-metadata clutter")

        hook_title_bonus = {
            "Curiosity Question": "?" in title,
            "Conflict / Accusation": any(
                term in title.casefold()
                for term in ("accused", "critic", "slammed", "blasted", "arrogant", "controversy", "clash")
            ),
            "Bold Quote / Statement": bool(re.search(r'["“”]', title)) or any(
                term in title.casefold()
                for term in ("said", "says", "called", "claimed", "revealed", "warned")
            ),
            "Result / Record": bool(re.search(r"\d", title)) or any(
                term in title.casefold()
                for term in ("won", "wins", "defeated", "record", "first", "fastest", "milestone")
            ),
            "Surprise / Human Angle": any(
                term in title.casefold()
                for term in ("unexpected", "debut", "comeback", "youngest", "oldest", "rare")
            ),
        }
        if hook_title_bonus.get(hook_family):
            score += 0.75
            reasons.append("hook-aligned packaging")
        if 5 <= len(lowered) <= 14:
            score += 2.0
            reasons.append("concise")
        elif len(lowered) <= 18:
            score += 1.0
        elif len(lowered) > 22:
            score -= 1.5

        if lowered:
            relevance = len(title_set & source_terms) / max(1, len(title_set))
            score += min(3.0, relevance * 3.0)
            if relevance >= 0.60:
                reasons.append("story-relevant")

        entity_terms = set(
            term.casefold()
            for term in re.findall(r"[\w]+(?:['’.-][\w]+)*", entity_text, flags=re.UNICODE)
            if len(term) > 1
        )
        if entity_terms and title_set & entity_terms:
            score += 2.0
            reasons.append("names the subject")

        first_half = set(lowered[:max(1, len(lowered) // 2 + 1)])
        if first_half & headline_terms:
            score += 1.0
            reasons.append("key term early")

        if has_number and re.search(r"\d|%", title):
            score += 0.75
            reasons.append("specific detail")
        elif not has_number and re.search(r"\d|%", title):
            score += 0.25

        if any(re.search(pattern, title, flags=re.IGNORECASE) for pattern in (
            r"\byou (?:won['’]?t|will not) believe\b",
            r"\bwatch (?:this|what happens next)\b",
            r"\bshocking\b",
            r"\bunbelievable\b",
            r"\bcraziest\b",
            r"\binsane\b",
            r"\bmust[- ]see\b",
        )):
            score -= 4.0
            reasons.append("clickbait risk")

        alpha = [char for char in title if char.isalpha()]
        if alpha:
            upper_ratio = sum(1 for char in alpha if char.isupper()) / len(alpha)
            if upper_ratio > 0.70:
                score -= 1.0
                reasons.append("excessive capitals")

        emoji_count = sum(1 for char in title if ord(char) > 0x1F300)
        if emoji_count >= 3:
            score -= 0.75
            reasons.append("excessive emoji")

        scores.append({
            "index": index,
            "title": title,
            "score": round(score, 3),
            "reasons": reasons,
        })

    best = max(scores, key=lambda item: (item["score"], -item["index"]))
    script_data["recommended_title_index"] = int(best["index"])
    script_data["title_selection_diagnostics"] = scores
    return {
        "recommended_title_index": int(best["index"]),
        "scores": scores,
    }


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
    result["hook_quality"] = _hook_quality_score(result, story_data)
    result["hook_type"] = classify_hook_style(result)
    result["hook_style_used"] = result["hook_type"]
    return result, {
        "removed_cta": removed_cta,
        "removed_scenes": removed_scenes,
        "changed_scenes": changed_scenes,
        "visual_entity_grounding_changes": grounding_changed,
    }


def assess_release_structure(script_data, format_mode="regular"):
    """Check production-ready narrative structure without imposing scene-count quotas."""
    assessment = assess_narrative_completeness(script_data)
    if not assessment.get("passed"):
        return False, assessment.get("reason", "Narrative structure is incomplete."), assessment
    if str(format_mode or "").lower() == "top5":
        scenes = script_data.get("script", []) if isinstance(script_data, dict) else []
        if len(scenes) < 5:
            return False, "Top-5 script does not contain enough list entries.", assessment
    return True, "Narrative structure is production-ready.", assessment

def validate_content_density(script_data, story_data, format_mode, require_visual_metadata=False):
    """Minimal production narration gate: valid scenes plus a conservative initial word ceiling."""
    if not isinstance(script_data, dict):
        return False, "Script is missing."
    scenes = script_data.get("script")
    if not isinstance(scenes, list) or not scenes:
        return False, "Script contains no narration scenes."

    word_counts = []
    for index, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict):
            return False, f"Scene {index} is malformed."
        voiceover = str(scene.get("voiceover") or "").strip()
        if not voiceover:
            return False, f"Scene {index} is empty."
        word_counts.append(len(_originality_words(voiceover)))
        if contains_retention_bait(voiceover):
            return False, f"Scene {index} contains prohibited retention-bait phrasing."

        if require_visual_metadata:
            if not str(scene.get("primary_entity") or "").strip():
                return False, f"Scene {index} is missing a supported primary entity."
            if not str(scene.get("specific_search_prompt") or "").strip():
                return False, f"Scene {index} is missing a specific visual search prompt."

    total_words = sum(word_counts)
    if total_words > INITIAL_SCRIPT_MAX_WORDS:
        return False, (
            f"Initial narration is too long: {total_words} words; "
            f"maximum is {INITIAL_SCRIPT_MAX_WORDS}."
        )

    if word_counts:
        first_words = word_counts[0]
        if first_words > SCENE_1_MAX_WORDS:
            return False, (
                f"Scene 1 is too long: {first_words} words; "
                f"maximum is {SCENE_1_MAX_WORDS}."
            )
    hook_diagnostics = _hook_quality_score(script_data, story_data)
    script_data["hook_quality_score"] = hook_diagnostics["score"]
    script_data["hook_quality_reasons"] = hook_diagnostics["reasons"]
    if hook_diagnostics["score"] < 5.0:
        script_data["hook_quality_warning"] = "Opening is weak; human review should consider a sharper factual hook."

    return True, "Passed minimal narration, duration and opening-shape checks"


def validate_visual_metadata(script_data):
    """Validate visual fields separately from narration quality."""
    scenes = script_data.get("script", []) if isinstance(script_data, dict) else []
    if not isinstance(scenes, list) or not scenes:
        return False, "No scenes available for visual metadata."
    missing = []
    for index, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict) or not str(scene.get("primary_entity") or "").strip():
            missing.append(index)
    if missing:
        return False, "Visual metadata needs grounding for scene(s): " + ", ".join(map(str, missing))
    return True, "Visual metadata contains a grounded primary entity per scene."

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

    if str(format_mode or "").lower() != "top5" and len(scenes) < 3:
        raise ValueError(
            "Source-grounded fallback refused to invent narration: not enough distinct narrative beats "
            "for a hook, middle beat and payoff."
        )

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
