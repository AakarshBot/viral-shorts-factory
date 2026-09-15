"""Shorts story ranking based on live story signals and historical performance."""
import math
import re
import sqlite3


def _clean(value):
    return str(value or "").strip().lower()


def _safe_float(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _tokens(value):
    stop = {
        "the", "and", "for", "with", "from", "this", "that", "into", "after",
        "before", "over", "under", "what", "how", "why", "world", "news",
        "latest", "today", "just", "will", "says", "said", "new",
    }
    text = re.sub(r"[^a-z0-9 ]+", " ", _clean(value))
    return {x for x in text.split() if len(x) > 2 and x not in stop}


def _parse_combo(value):
    parts = [_clean(x) for x in str(value or "").split("|")]
    return (parts[0], parts[1], parts[2]) if len(parts) >= 3 else (None, None, None)


def _eligible(row):
    status = _clean(row.get("status"))
    video_id = _clean(row.get("video_id"))
    return (
        status not in {"pending_qc", "rejected", "failed"}
        and video_id not in {"", "pending_qc", "rejected", "failed"}
        and _safe_float(row.get("avg_view_percentage")) is not None
    )


def _historical_score(story, rows, target_category, target_format, target_language):
    """Find the strongest relevant historical retention signal for this topic."""
    current = _tokens(story.get("title", ""))
    if not current:
        return 0.0, 0

    best = 0.0
    matches = 0
    target_category = _clean(target_category)
    target_format = _clean(target_format)
    target_language = _clean(target_language)

    for row in rows:
        if not _eligible(row):
            continue
        old = _tokens(row.get("topic", ""))
        if not old:
            continue
        overlap = len(current & old) / max(1, len(current | old))
        if overlap < 0.22:
            continue

        value = _safe_float(row.get("avg_view_percentage"))
        fmt, category, language = _parse_combo(row.get("combo_key"))
        category = category or _clean(row.get("genre"))
        fmt = _clean(row.get("format_used")) or fmt
        language = _clean(row.get("language_used")) or language

        context = 1.0
        if target_category and category == target_category:
            context += 0.45
        if target_format and fmt == target_format:
            context += 0.25
        if target_language and language == target_language:
            context += 0.10

        signal = min(100.0, value) * overlap * context
        best = max(best, signal)
        matches += 1

    return best, matches


def _load_history(conn):
    if conn is None:
        return []
    try:
        conn.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in conn.execute(
                """SELECT status, video_id, avg_view_percentage, genre,
                          format_used, language_used, combo_key, topic
                   FROM vault
                   WHERE avg_view_percentage IS NOT NULL"""
            ).fetchall()
        ]
    except Exception as exc:
        print(f"   [Story Ranker] Historical data unavailable: {exc}")
        return []


def rank_story_candidates(
    stories,
    conn=None,
    target_category="",
    target_format="",
    target_language="",
):
    """Return stories ordered for the selected Shorts segment.

    Live signals remain the primary driver. Historical retention is deliberately
    capped so an old successful topic pattern cannot overpower a genuinely
    stronger current story.
    """
    if not stories:
        return stories

    rows = _load_history(conn)
    ranked = []

    for index, story in enumerate(stories):
        if not isinstance(story, dict):
            continue

        def number(name, default=0.0):
            value = _safe_float(story.get(name))
            return default if value is None else value

        live_score = (
            number("corroboration_bonus") * 2.0
            + number("velocity_score")
            + number("trend_bonus")
            - number("recency_penalty")
        )
        historical, matches = _historical_score(
            story, rows, target_category, target_format, target_language
        )

        # Historical evidence is a modest booster, capped at +8 points.
        history_boost = min(8.0, historical * 0.08)
        final_score = live_score + history_boost

        story["candidate_score"] = round(final_score, 3)
        story["historical_topic_signal"] = round(historical, 3)
        story["historical_topic_matches"] = matches
        ranked.append((final_score, index, story))

    ranked.sort(key=lambda item: (item[0], -item[1]), reverse=True)
    return [item[2] for item in ranked]


def patch_story_selection(bot):
    """Insert story ranking after collection/dedup and before editorial scoring."""
    original = bot.gather_and_filter_stories

    def gather(conn, genre_key, genre_cfg, trend_keyword=None,
               custom_gnews_q=None, custom_rss_url=None):
        stories = original(
            conn, genre_key, genre_cfg, trend_keyword,
            custom_gnews_q, custom_rss_url
        )
        config = getattr(bot, "_active_web_config", {}) or {}
        format_mode = config.get("format_mode", "regular")
        category = config.get("category", genre_key or "")
        language = config.get("language", "")
        ranked = rank_story_candidates(
            stories,
            conn=conn,
            target_category=category,
            target_format=format_mode,
            target_language=language,
        )
        if ranked:
            print(
                "   [Story Ranker] Ranked %d collected stories for "
                "%s | %s | %s. Top story: %s"
                % (
                    len(ranked), format_mode, category, language,
                    str(ranked[0].get("title", ""))[:100],
                )
            )
        return ranked

    bot.gather_and_filter_stories = gather
    return bot
