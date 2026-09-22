"""Channel-specific editorial strategy for FreshFeed Daily Shorts.

Deterministic, API-free rules translating the channel's observed YouTube
performance into reusable ranking and packaging signals.
"""
from __future__ import annotations
import re

CHANNEL_STRATEGY_VERSION = "freshfeed-daily-2026-09"
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

def score_story(story: dict) -> dict:
    story = story if isinstance(story, dict) else {}
    headline = _clean(
        " ".join(
            str(story.get(key) or "")
            for key in ("title", "source_headline", "canonical_title", "event_search_text")
        )
    )
    # Hook evidence is deliberately headline-first. Article body text is useful
    # for factual grounding, but body mentions such as "said" must not masquerade
    # as the kind of opening hook that actually drove the channel's retention.
    conflict = _hits(_CONFLICT_TERMS, headline)
    quote = _hits(_QUOTE_TERMS, headline)
    surprise = _hits(_SURPRISE_TERMS, headline)
    result = _hits(_RESULT_TERMS, headline)
    routine = _hits(_ROUTINE_TERMS, headline)
    admin = _hits(_ADMIN_TERMS, headline)
    generic = _hits(_GENERIC_TERMS, headline)
    combined = f"{headline} {_text_blob(story)}"

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
    if "?" in str(story.get("title") or ""):
        score += 1.10
        reasons.append("curiosity question")
    if re.search(r"\b\d{2,}\b|%", str(story.get("title") or "")):
        score += 0.75
        reasons.append("specific detail")

    entities = [str(x).strip() for x in (story.get("event_entities") or []) if str(x).strip()]
    if len(entities) >= 2:
        score += 0.65
        reasons.append("clear subjects")
    elif len(entities) == 1:
        score += 0.30
    if any(term in combined for term in _RIVALRY_TERMS):
        score += 1.00
        reasons.append("India-Pakistan rivalry")

    strong_hook = bool(
        conflict or quote or surprise
        or ("?" in title and len(title.split()) >= 5)
        or result
    )
    if routine and not strong_hook:
        score -= min(3.0, 1.65 + (routine - 1) * 0.35)
        reasons.append("routine/service penalty")
    if admin and not strong_hook:
        score -= min(2.5, 1.70 + (admin - 1) * 0.30)
        reasons.append("administrative-news penalty")
    if generic and not strong_hook:
        score -= min(1.75, generic * 0.75)
        reasons.append("generic-headline penalty")
    if len(re.findall(r"\w+", title)) > 24:
        score -= 0.75
        reasons.append("headline too broad")

    return {
        "score": round(max(0.0, min(10.0, score)), 2),
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
    strong_hook = bool(story_signal.get("strong_hook"))
    routine_or_admin = bool(story_signal.get("routine_or_admin"))
    if routine_or_admin and not strong_hook and float(hook_potential or 0.0) < 5.5:
        return False, "Low-value routine/admin story for channel strategy"
    if signal < 2.25 and float(hook_potential or 0.0) < 4.5 and float(importance or 0.0) < 7.0:
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
