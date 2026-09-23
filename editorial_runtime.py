"""Authoritative editorial scoring for the production Shorts factory."""

import os
import re
import sqlite3


_EDITORIAL_SAFETY_TERMS = {
    "sexual assault", "child sexual", "child abuse", "sexual abuse", "explicit porn",
    "pornographic", "graphic sexual", "suicide method", "suicide instructions",
    "terrorist recruitment", "terrorist propaganda", "hate speech", "racial slur",
    "violent extremist propaganda", "gore", "graphic gore",
}


def _contains_editorial_safety_block(story):
    if not isinstance(story, dict):
        return False
    text = " ".join(
        str(story.get(key) or "")
        for key in ("title", "source_headline", "canonical_title", "text")
    ).lower()
    return any(term in text for term in _EDITORIAL_SAFETY_TERMS)


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
            rows = conn.execute(
                "SELECT topic FROM vault WHERE topic IS NOT NULL AND topic != '' "
                "AND video_id IS NOT NULL AND video_id NOT IN ('', 'PENDING_QC', 'READY_FOR_UPLOAD', 'REJECTED', 'FAILED') "
                "AND status NOT IN ('PENDING_QC', 'READY_FOR_UPLOAD', 'REJECTED', 'FAILED')"
            ).fetchall()
            return [row[0] for row in rows if row and row[0]]
        finally:
            conn.close()
    except Exception:
        return []


def _coerce_bool(value, default=False):
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


def _fallback_editorial_scores(story):
    text_len = len(str((story or {}).get("text", "") or "").split())
    narrative = 6.5 if text_len >= 45 else 6.0 if text_len >= 20 else 5.5
    velocity = _num((story or {}).get("velocity_score"), 0.0)
    hook = min(8.0, 5.5 + velocity * 0.35)
    shelf_life = min(7.0, 5.0 + _num((story or {}).get("trend_bonus"), 0.0) * 0.25)
    return {
        "hook_strength": hook,
        "narrative_completeness": narrative,
        "audience_fit": 6.0,
        "monetization_risk": 5.0,
        "shelf_life": shelf_life,
        "hard_reject": False,
    }


def _normalise_editorial_records(scored_data, batch_stories):
    records = list(scored_data) if isinstance(scored_data, list) else []
    normalised = []
    for index, story in enumerate(batch_stories or []):
        raw = records[index] if index < len(records) and isinstance(records[index], dict) else None
        clean = dict(raw) if raw is not None else _fallback_editorial_scores(story)
        clean["hard_reject"] = _coerce_bool(clean.get("hard_reject"), False)
        for field, default in (
            ("hook_strength", 5.0),
            ("narrative_completeness", 5.0),
            ("audience_fit", 5.0),
            ("monetization_risk", 5.0),
            ("shelf_life", 5.0),
        ):
            clean[field] = _num(clean.get(field), default)
        normalised.append(clean)
    return normalised


def score_candidates(scored_data, batch_stories, bonuses, last_genre, format_mode, prior_topics=None):
    """Authoritative editorial scoring. Risk is a penalty, not a hard reject."""
    batch_stories = list(batch_stories or [])
    scored_data = _normalise_editorial_records(scored_data, batch_stories)
    scored_candidates = []
    prior_topics = prior_topics or []
    for idx, scores in enumerate(scored_data or []):
        if idx >= len(batch_stories) or not isinstance(scores, dict):
            break
        story = batch_stories[idx]
        if not isinstance(story, dict):
            continue

        model_hard_reject = _coerce_bool(scores.get("hard_reject"), False)
        hs = max(0.0, min(10.0, _num(scores.get("hook_strength"), 5.0)))
        nc = max(0.0, min(10.0, _num(scores.get("narrative_completeness"), 5.0)))
        af = max(0.0, min(10.0, _num(scores.get("audience_fit"), 5.0)))
        mr = max(0.0, min(10.0, _num(scores.get("monetization_risk"), 5.0)))
        sl = max(0.0, min(10.0, _num(scores.get("shelf_life"), 5.0)))

        # Provider hard_reject is advisory. Deterministic safety remains a hard
        # stop, while a high monetization-risk score must not itself kill a
        # substantively strong story. This prevents false stops on normal sports
        # stories involving younger athletes or other brand-safety-sensitive context.
        safety_blocked = _contains_editorial_safety_block(story)
        if safety_blocked:
            continue
        channel_fit = max(
            0.0, min(10.0, _num(story.get("freshfeed_channel_fit_score"), 0.0))
        )
        # The channel report shows the first-second promise is more predictive
        # than generic audience fit. Keep safety/completeness/risk in the gate,
        # but give the model's hook judgment + deterministic channel fit the
        # largest share of the final editorial composite.
        quality_score = (
            hs * 0.35
            + nc * 0.15
            + af * 0.15
            + (10.0 - mr) * 0.15
            + sl * 0.10
            + channel_fit * 0.10
        )
        if model_hard_reject:
            # Model hard-reject is advisory only. Deterministic safety remains
            # authoritative; subjective provider judgements become a soft penalty.
            quality_score -= 2.0

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
            "freshfeed_channel_fit_score": round(channel_fit, 2),
            "repetition_penalty": round(repetition, 3),
            "composite_score": round(composite, 2),
        })
        scored_candidates.append(story)

    scored_candidates.sort(key=lambda x: x.get("composite_score", 0.0), reverse=True)
    return scored_candidates


def patch_editorial_scoring(bot):
    """Bind the authoritative scorer once; repeated dashboard reruns only reassert the live callable."""
    existing = getattr(bot, "_editorial_scoring_corrected_process", None)
    run_robot = getattr(bot, "run_robot", None)
    namespace = getattr(run_robot, "__globals__", None)
    if callable(existing) and getattr(existing, "_editorial_scoring_corrected", False):
        bot.process_scored_candidates = existing
        if isinstance(namespace, dict):
            namespace["process_scored_candidates"] = existing
        bot._editorial_scoring_patch_installed = True
        bot._editorial_scoring_patch_version = "authoritative-v2"
        return bot

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
