"""Channel-specific editorial strategy for FreshFeed Daily Shorts.

Deterministic, API-free rules translating the channel's observed YouTube
performance into reusable ranking and packaging signals.
"""
from __future__ import annotations
import re

CHANNEL_STRATEGY_VERSION = "freshfeed-daily-2026-09-v2"
CHANNEL_IDEAL_MIN_SECONDS = 20.0
CHANNEL_IDEAL_MAX_SECONDS = 30.0
CHANNEL_SOFT_MAX_SECONDS = 35.0
CHANNEL_TITLE_MAX_CHARS = 55

_CONFLICT_TERMS = {
    "accused","accuses","accusation","arrogant","blasted","blast","called","calls",
    "clash","clashes","controversy","controversial","criticized","criticised",
    "criticism","debate","dispute","feud","fight","hits back","insulted","mocked",
    "rivalry","row","ruin","slammed","slams","targeted","warned","warning","war of words",
}
_QUOTE_TERMS = {
    "said","says","called","described","declared","claimed","claims","criticized",
    "criticised","praised","hailed","warned","revealed","admitted","responded",
    "responds","hit back","hits back",
}
_SURPRISE_TERMS = {
    "record","first","fastest","highest","lowest","historic","unprecedented",
    "unexpected","surprise","stuns","stunned","upset","comeback","debut",
    "youngest","oldest","rare","never",
}
_RESULT_TERMS = {
    "won","wins","lost","loss","beat","beats","defeated","eliminated","eliminates",
    "qualified","qualifies","clinched","secured","record","milestone","first",
    "fastest","youngest","oldest",
}
_ROUTINE_TERMS = {
    "schedule","schedules","fixtures","fixture","timings","timing","where to watch",
    "live stream","live streaming","telecast","tv channel","playing xi","probable xi",
    "predicted xi","match preview","match prediction","prediction","fantasy","dream11",
    "tickets","scorecard","latest update","big update","squad list","full squad",
    "training update",
}
_ADMIN_TERMS = {
    "reconstitution","board appointments","board appointment","administrative",
    "municipal","engineer pension","pension","certificate","notification",
    "guideline","guidelines","policy update","routine policy","committee appointment",
}
_GENERIC_TERMS = {
    "latest news","big news","major update","big update","all you need to know",
    "here is what happened","what happened today","things to know",
}
_RIVALRY_TERMS = {
    "india pakistan","india vs pakistan","india v pakistan","pakistan india",
    "pakistan vs india","pakistan v india","india-pakistan","india–pakistan",
}

# Marquee names are intentionally sports-focused and deliberately small. They
# represent the type of recognisable person whose statements/results can create
# the immediate human hook seen in the channel's strongest Shorts.
_MARQUEE_PERSON_TERMS = {
    "virat kohli", "rohit sharma", "ms dhoni", "jasprit bumrah", "shubman gill",
    "rishabh pant", "hardik pandya", "ravindra jadeja", "suryakumar yadav",
    "yashasvi jaiswal", "kl rahul", "sanju samson", "mohammed siraj",
    "rashid khan", "babar azam", "pat cummins", "travis head", "gautam gambhir",
    "coco gauff", "carlos alcaraz", "novak djokovic", "lionel messi",
    "cristiano ronaldo", "lebron james",
}

_MAJOR_EVENT_TERMS = {
    "world cup", "us open", "wimbledon", "olympics", "grand slam",
    "champions trophy", "asia cup", "final", "semifinal", "semi-final",
}

_SCOPE_TERMS = {
    "history", "timeline", "background", "origins", "all you need to know",
    "everything you need to know", "explained in detail", "complete guide",
    "full breakdown",
}

def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()

def _text_blob(story: dict) -> str:
    return " ".join(
        str(story.get(key) or "")
        for key in ("title","description","summary","snippet","text","event_search_text")
    ).casefold()

def _hits(terms: set[str], text: str) -> int:
    return sum(
        1 for term in terms
        if re.search(r"(?<![a-z])" + re.escape(term) + r"(?![a-z])", text)
    )

def _headline_scope_score(story: dict) -> float:
    """Estimate whether the headline describes one clean 20–30s narrative."""
    story = story if isinstance(story, dict) else {}
    title = str(story.get("title") or "")
    words = len(re.findall(r"\\w+", title))
    actions = story.get("event_actions") or []
    action_count = len({str(item).strip().casefold() for item in actions if str(item).strip()})

    score = 5.0
    if 5 <= words <= 16:
        score += 2.0
    elif words <= 22:
        score += 1.0
    elif words > 28:
        score -= 2.0
    elif words > 22:
        score -= 1.0

    if action_count == 1:
        score += 1.5
    elif action_count == 2:
        score += 0.7
    elif action_count > 3:
        score -= min(1.5, (action_count - 3) * 0.5)

    title_lower = _clean(title)
    scope_hits = _hits(_SCOPE_TERMS, title_lower)
    if scope_hits:
        score -= min(2.0, scope_hits * 0.9)

    return round(max(0.0, min(10.0, score)), 2)


def _freshfeed_pattern_score(story: dict, conflict: int, quote: int, surprise: int, result: int,
                             routine: int, admin: int, generic: int, rivalry: bool,
                             marquee: int, question: bool, scope_score: float) -> tuple[float, list[str]]:
    """Translate the channel's observed winning patterns into one explicit signal."""
    title = str(story.get("title") or "")
    score = 0.0
    reasons: list[str] = []

    if conflict:
        score += min(2.75, conflict * 1.40)
        reasons.append("high-stakes conflict")
    if quote:
        score += min(2.10, quote * 0.85)
        reasons.append("bold quote/statement")
    if marquee:
        score += min(1.50, marquee * 0.90)
        reasons.append("marquee personality")
    if rivalry:
        score += 1.50
        reasons.append("India-Pakistan rivalry")
    if question and len(re.findall(r"\\w+", title)) >= 5:
        score += 1.25
        reasons.append("provocative question")
    if surprise:
        score += min(1.35, surprise * 0.68)
        reasons.append("surprise/novelty")
    if result:
        score += min(0.90, result * 0.35)
        reasons.append("result/record")

    # The strongest examples combine a human subject with tension or a quote.
    if marquee and (conflict or quote):
        score += 0.90
        reasons.append("marquee + tension synergy")
    if rivalry and (conflict or quote):
        score += 0.90
        reasons.append("rivalry + tension synergy")
    if conflict and quote:
        score += 0.75
        reasons.append("conflict + quote synergy")

    if 1 <= scope_score <= 0:
        pass
    elif scope_score >= 7.0:
        score += 0.80
        reasons.append("strong 20–30s scope")
    elif scope_score >= 5.5:
        score += 0.35
    elif scope_score < 4.5:
        score -= 0.80
        reasons.append("poor 20–30s scope")

    if routine and not (conflict or quote or question):
        score -= min(2.5, 1.65 + max(0, routine - 1) * 0.35)
        reasons.append("routine/service penalty")
    if admin and not (conflict or quote or question):
        score -= min(2.5, 1.75 + max(0, admin - 1) * 0.30)
        reasons.append("administrative-news penalty")
    if generic and not (conflict or quote or surprise or question):
        score -= min(1.75, generic * 0.75)
        reasons.append("generic-headline penalty")

    words = len(re.findall(r"\\w+", title))
    if 20 <= len(title) <= CHANNEL_TITLE_MAX_CHARS:
        score += 0.45
        reasons.append("compact title")
    elif len(title) > 70:
        score -= 1.0
        reasons.append("title too long")
    if words > 18:
        score -= 0.45
    return round(max(0.0, min(10.0, score)), 2), list(dict.fromkeys(reasons))


def score_story(story: dict) -> dict:
    story = story if isinstance(story, dict) else {}
    headline = _clean(
        " ".join(
            str(story.get(key) or "")
            for key in ("title", "source_headline", "canonical_title", "event_search_text")
        )
    )
    # Headline evidence is the key signal because 81%+ of this channel's Shorts
    # traffic comes from the Shorts Feed, where the opening promise matters most.
    conflict = _hits(_CONFLICT_TERMS, headline)
    quote = _hits(_QUOTE_TERMS, headline)
    surprise = _hits(_SURPRISE_TERMS, headline)
    result = _hits(_RESULT_TERMS, headline)
    routine = _hits(_ROUTINE_TERMS, headline)
    admin = _hits(_ADMIN_TERMS, headline)
    generic = _hits(_GENERIC_TERMS, headline)
    rivalry = any(term in headline for term in _RIVALRY_TERMS)

    marquee = sum(
        1 for term in _MARQUEE_PERSON_TERMS
        if re.search(r"(?<![a-z])" + re.escape(term) + r"(?![a-z])", headline)
    )

    title = str(story.get("title") or "")
    question = "?" in title
    combined = f"{headline} {_text_blob(story)}"
    scope_score = _headline_scope_score(story)

    score = 0.0
    reasons: list[str] = []
    if conflict:
        score += min(3.0, conflict * 1.45)
        reasons.append("conflict/controversy")
    if quote:
        score += min(2.25, quote * 0.80)
        reasons.append("bold quote/statement")
    if surprise:
        score += min(1.75, surprise * 0.65)
        reasons.append("surprise/novelty")
    if result:
        score += min(1.20, result * 0.45)
        reasons.append("result/record")
    if question:
        score += 1.10
        reasons.append("curiosity question")
    if re.search(r"\b\d{2,}\b|%", title):
        score += 0.75
        reasons.append("specific detail")

    entities = [str(x).strip() for x in (story.get("event_entities") or []) if str(x).strip()]
    if len(entities) >= 2:
        score += 0.65
        reasons.append("clear subjects")
    elif len(entities) == 1:
        score += 0.30
    if rivalry:
        score += 1.00
        reasons.append("India-Pakistan rivalry")
    if marquee:
        score += min(0.90, marquee * 0.55)
        reasons.append("marquee personality")

    strong_hook = bool(
        conflict or quote or surprise
        or (question and len(headline.split()) >= 5)
        or result
    )

    freshfeed_pattern_score, pattern_reasons = _freshfeed_pattern_score(
        story=story,
        conflict=conflict,
        quote=quote,
        surprise=surprise,
        result=result,
        routine=routine,
        admin=admin,
        generic=generic,
        rivalry=rivalry,
        marquee=marquee,
        question=question,
        scope_score=scope_score,
    )

    return {
        "score": round(max(0.0, min(10.0, score)), 2),
        "freshfeed_pattern_score": freshfeed_pattern_score,
        "freshfeed_scope_score": scope_score,
        "freshfeed_pattern_reasons": pattern_reasons,
        "marquee_person_hits": marquee,
        "rivalry_signal": rivalry,
        "strong_hook": strong_hook,
        "routine_or_admin": bool(routine or admin),
        "routine_hits": routine,
        "admin_hits": admin,
        "reasons": list(dict.fromkeys(reasons)),
        "version": CHANNEL_STRATEGY_VERSION,
    }

def candidate_gate(story_signal: dict, hook_potential: float, importance: float) -> tuple[bool, str]:
    if not isinstance(story_signal, dict):
        return True, ""
    signal = float(story_signal.get("score") or 0.0)
    pattern = float(story_signal.get("freshfeed_pattern_score") or 0.0)
    strong_hook = bool(story_signal.get("strong_hook"))
    routine_or_admin = bool(story_signal.get("routine_or_admin"))
    if routine_or_admin and not strong_hook and pattern < 4.5 and float(hook_potential or 0.0) < 5.5:
        return False, "Low-value routine/admin story for channel strategy"
    if pattern < 2.75 and signal < 2.25 and float(hook_potential or 0.0) < 4.5 and float(importance or 0.0) < 7.0:
        return False, "Weak channel-specific hook signal"
    return True, ""

def title_package_score(title: str) -> float:
    value = re.sub(r"\s+", " ", str(title or "")).strip()
    chars = len(value)
    words = len(re.findall(r"\w+", value))
    score = 0.0
    if 20 <= chars <= CHANNEL_TITLE_MAX_CHARS:
        score += 2.0
    elif chars <= 60:
        score -= 0.75
    else:
        score -= 2.0
    if chars > 70:
        score -= 1.0
    if "#" in value or "|" in value:
        score -= 0.9
    if words > 14:
        score -= 0.75
    if re.search(
        r"\b(schedule|timings?|fixtures?|venue|telecast|where to watch|playing xi|probable xi|scorecard|1st|2nd|3rd)\b",
        value, re.IGNORECASE
    ):
        score -= 0.9
    return round(score, 2)

def duration_policy() -> dict:
    return {
        "ideal_min_seconds": CHANNEL_IDEAL_MIN_SECONDS,
        "ideal_max_seconds": CHANNEL_IDEAL_MAX_SECONDS,
        "soft_max_seconds": CHANNEL_SOFT_MAX_SECONDS,
        "rewrite_trigger_seconds": CHANNEL_IDEAL_MAX_SECONDS,
        "version": CHANNEL_STRATEGY_VERSION,
    }

__all__ = [
    "CHANNEL_STRATEGY_VERSION","CHANNEL_IDEAL_MIN_SECONDS","CHANNEL_IDEAL_MAX_SECONDS",
    "CHANNEL_SOFT_MAX_SECONDS","CHANNEL_TITLE_MAX_CHARS","candidate_gate",
    "duration_policy","score_story","title_package_score",
]
