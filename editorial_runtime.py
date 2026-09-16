"""Authoritative editorial scoring for the production Shorts factory."""

import os
import re
import sqlite3


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y", "on"}:
        return True
    if text in {"false", "0", "no", "n", "off", ""}:
        return False
    return default


def _tokens(value):
    stop = {
        "the", "and", "for", "with", "from", "this", "that", "into",
        "after", "before", "over", "under", "what", "how", "why", "world",
        "news", "latest", "today", "just", "new", "says", "said", "will",
    }
    return {
        token for token in re.sub(r"[^a-z0-9 ]+", " ", str(value or "").lower()).split()
        if len(token) > 2 and token not in stop
    }


def _similarity(a, b):
    aa, bb = _tokens(a), _tokens(b)
    if not aa or not bb:
        return 0.0
    return len(aa & bb) / max(1, len(aa | bb))


def _repetition_penalty(story, prior_topics):
    title = story.get("title", "")
    if not title or not prior_topics:
        return 0.0
    strongest = max((_similarity(title, topic) for topic in prior_topics), default=0.0)
    if strongest < 0.25:
        return 0.0
    return min(3.0, strongest * 3.0)


def _load_prior_topics(db_path):
    if not db_path or not os.path.exists(db_path):
        return []
    try:
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute("SELECT topic FROM vault WHERE topic IS NOT NULL").fetchall()
            return [row[0] for row in rows if row and row[0]]
        finally:
            conn.close()
    except Exception:
        return []


def score_candidates(scored_data, batch_stories, bonuses, last_genre, format_mode, prior_topics=None):
    """Authoritative editorial scoring. Risk is a penalty, not a hard reject."""
    scored_candidates = []
    prior_topics = prior_topics or []
    for idx, scores in enumerate(scored_data or []):
        if idx >= len(batch_stories) or not isinstance(scores, dict):
            break
        story = batch_stories[idx]
        if not isinstance(story, dict) or _bool(scores.get("hard_reject"), False):
            continue

        hs = max(0.0, min(10.0, _num(scores.get("hook_strength"), 5.0)))
        nc = max(0.0, min(10.0, _num(scores.get("narrative_completeness"), 5.0)))
        af = max(0.0, min(10.0, _num(scores.get("audience_fit"), 5.0)))
        mr = max(0.0, min(10.0, _num(scores.get("monetization_risk"), 5.0)))
        sl = max(0.0, min(10.0, _num(scores.get("shelf_life"), 5.0)))
        quality_score = hs * 0.25 + nc * 0.20 + af * 0.20 + (10.0 - mr) * 0.20 + sl * 0.15

        trend_bonus = _num(story.get("trend_bonus"), 0.0)
        velocity_boost = _num(story.get("velocity_score"), 0.0)
        corroboration = _num(story.get("corroboration_bonus"), 0.0)
        recency_penalty = _num(story.get("recency_penalty"), 0.0)
        genre_bonus = _num(bonuses.get(story.get("genre"), 0.0), 0.0) if format_mode == "regular" else 0.0
        repetition = _repetition_penalty(story, prior_topics)
        composite = quality_score + genre_bonus + corroboration + trend_bonus + velocity_boost - recency_penalty - repetition

        story.update({
            "hook_strength": round(hs, 2),
            "narrative_completeness": round(nc, 2),
            "audience_fit": round(af, 2),
            "monetization_risk": round(mr, 2),
            "shelf_life": round(sl, 2),
            "repetition_penalty": round(repetition, 3),
            "composite_score": round(composite, 2),
        })
        scored_candidates.append(story)

    scored_candidates.sort(key=lambda x: x.get("composite_score", 0.0), reverse=True)
    return scored_candidates


def patch_editorial_scoring(bot):
    """Reassert the authoritative scorer on every runtime binding pass."""
    def process(scored_data, batch_stories, bonuses, last_genre, format_mode):
        db_path = getattr(bot, "DB_PATH", "")
        prior_topics = _load_prior_topics(db_path)
        return score_candidates(scored_data, batch_stories, bonuses or {}, last_genre, format_mode, prior_topics=prior_topics)

    process._editorial_scoring_corrected = True
    process._authoritative_runtime_binding = True
    bot._editorial_scoring_corrected_process = process
    bot.process_scored_candidates = process

    run_robot = getattr(bot, "run_robot", None)
    namespace = getattr(run_robot, "__globals__", None)
    if isinstance(namespace, dict):
        namespace["process_scored_candidates"] = process

    try:
        from runtime_hardener import reassert_live_bindings
        reassert_live_bindings(bot)
    except Exception as exc:
        print(f"   [Editorial] Runtime hardener unavailable: {type(exc).__name__}: {exc}", flush=True)

    bot._editorial_scoring_patch_installed = True
    bot._editorial_scoring_patch_version = "authoritative-v2"
    return bot
