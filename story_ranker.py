"""Shorts story ranking based on live signals and historical performance."""
import math
import re
from datetime import datetime, timezone
from urllib.parse import urlparse


SAFETY_BLOCKLIST = {
    "sexual assault", "child sexual", "child abuse", "sexual abuse", "explicit porn",
    "pornographic", "graphic sexual", "suicide method", "suicide instructions",
    "terrorist recruitment", "terrorist propaganda", "hate speech", "racial slur",
    "violent extremist propaganda", "gore", "graphic gore",
}

QUALITY_RISK_TERMS = {
    "death toll", "casualties", "killed", "murder", "shooting", "war crime",
    "terror attack", "bombing", "abuse", "assault", "fraud", "scam",
    "election fraud", "unverified claim", "conspiracy theory", "medical misinformation",
}

VISUAL_SIGNAL_TERMS = {
    "launch", "unveils", "reveals", "record", "sold", "wins", "won", "final",
    "goal", "trophy", "match", "film", "movie", "trailer", "celebration", "viral",
    "ai", "robot", "phone", "chip", "space", "rocket", "nasa", "earth", "scientist",
    "million", "billion", "%", "ranking", "chart", "result", "before", "after",
}


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
        "the", "and", "for", "with", "from", "this", "that", "into", "after", "before",
        "over", "under", "what", "how", "why", "world", "news", "latest", "today",
        "just", "will", "says", "said", "new", "has", "have", "its", "their", "more",
    }
    text = re.sub(r"[^a-z0-9%]+", " ", _clean(value))
    return {x for x in text.split() if len(x) > 2 and x not in stop}


def _parse_combo(value):
    parts = [_clean(x) for x in str(value or "").split("|")]
    return (parts[0], parts[1], parts[2]) if len(parts) >= 3 else (None, None, None)


def _text_blob(story):
    return " ".join(
        str(story.get(key) or "")
        for key in ("title", "description", "snippet", "summary", "content")
    ).strip().lower()


def _source_domain(story):
    raw = str(story.get("url") or story.get("link") or "").strip()
    if not raw:
        return ""
    try:
        return urlparse(raw).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def _published_datetime(story):
    for key in ("published_at", "publishedAt", "published", "pub_date", "date", "timestamp"):
        raw = story.get(key)
        if not raw:
            continue
        text = str(raw).strip()
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def _freshness_score(story):
    dt = _published_datetime(story)
    if dt is None:
        return 0.0
    age_hours = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0)
    if age_hours <= 1:
        return 10.0
    if age_hours <= 3:
        return 8.0
    if age_hours <= 6:
        return 6.0
    if age_hours <= 12:
        return 4.0
    if age_hours <= 24:
        return 2.0
    return 0.0


def _safety_gate(story):
    text = _text_blob(story)
    hits = sorted(term for term in SAFETY_BLOCKLIST if term in text)
    return len(hits) == 0, hits


def _risk_score(story):
    text = _text_blob(story)
    return sum(1 for term in QUALITY_RISK_TERMS if term in text)


def _visual_potential(story):
    tokens = _tokens(_text_blob(story))
    score = min(10.0, float(len(tokens & VISUAL_SIGNAL_TERMS)))
    if story.get("image_url") or story.get("thumbnail") or story.get("media_url"):
        score += 2.0
    return min(10.0, score)


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
        cursor = conn.execute(
            """SELECT status, video_id, avg_view_percentage, genre,
                      format_used, language_used, combo_key, topic
               FROM vault
               WHERE avg_view_percentage IS NOT NULL"""
        )
        columns = [item[0] for item in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    except Exception as exc:
        print(f"   [Story Ranker] Historical data unavailable: {exc}")
        return []


def _load_used_topics(conn):
    if conn is None:
        return []
    try:
        rows = conn.execute("SELECT topic FROM vault WHERE topic IS NOT NULL AND topic != ''").fetchall()
        return [str(row[0]) for row in rows if row and row[0]]
    except Exception as exc:
        print(f"   [Story Ranker] Used-topic history unavailable: {exc}")
        return []


def _eligible(row):
    status = _clean(row.get("status"))
    video_id = _clean(row.get("video_id"))
    return (
        status not in {"pending_qc", "rejected", "failed"}
        and video_id not in {"", "pending_qc", "rejected", "failed"}
        and _safe_float(row.get("avg_view_percentage")) is not None
    )


def _topic_overlap(a, b):
    aa, bb = _tokens(a), _tokens(b)
    if not aa or not bb:
        return 0.0
    return len(aa & bb) / max(1, len(aa | bb))


def _same_topic(story, used_topics):
    title = story.get("title", "")
    return max((_topic_overlap(title, old) for old in used_topics), default=0.0)


def _corroboration_scores(stories):
    """Measure how many distinct source domains independently cover the same story cluster."""
    values = [0] * len(stories)
    for i, story in enumerate(stories):
        domains = set()
        title = story.get("title", "")
        for j, other in enumerate(stories):
            if i == j:
                continue
            if _topic_overlap(title, other.get("title", "")) >= 0.34:
                domain = _source_domain(other)
                if domain:
                    domains.add(domain)
        own = _source_domain(story)
        if own:
            domains.add(own)
        values[i] = min(8, len(domains))
    return values


def _sports_niche_bonus(story, target_category):
    if _clean(target_category) != "sports":
        return 0.0
    text = _text_blob(story)
    non_cricket = {
        "tennis", "badminton", "athletics", "basketball", "formula 1", "f1", "motogp",
        "golf", "rugby", "volleyball", "hockey", "cycling", "boxing", "mma", "wrestling",
        "olympics", "olympic",
    }
    return 3.0 if any(term in text for term in non_cricket) else 0.0


def rank_story_candidates(stories, conn=None, target_category="", target_format="", target_language=""):
    """Apply cheap discovery gates, then rank survivors for the human-facing 3-story shortlist."""
    if not stories:
        return stories

    rows = _load_history(conn)
    used_topics = _load_used_topics(conn)
    corroboration = _corroboration_scores(stories)
    ranked = []
    rejected_safety = 0
    rejected_duplicate = 0
    rejected_weak = 0

    for index, story in enumerate(stories):
        if not isinstance(story, dict):
            continue

        safe, hits = _safety_gate(story)
        if not safe:
            story["discovery_rejection"] = "Safety/content policy gate"
            story["safety_hits"] = hits
            rejected_safety += 1
            continue

        same = _same_topic(story, used_topics)
        if same >= 0.78:
            story["discovery_rejection"] = "Previously covered topic"
            story["topic_overlap"] = round(same, 3)
            rejected_duplicate += 1
            continue

        def number(name, default=0.0):
            value = _safe_float(story.get(name))
            return default if value is None else value

        freshness = _freshness_score(story)
        velocity = number("velocity_score")
        trend = number("trend_bonus")
        history, history_matches = _historical_score(
            story, rows, target_category, target_format, target_language
        )
        corroboration_bonus = float(corroboration[index])
        visual = _visual_potential(story)
        risk = _risk_score(story)
        niche = _sports_niche_bonus(story, target_category)
        originality = max(0.0, 10.0 - (same * 10.0))

        # Current momentum leads; historical learning is useful but bounded.
        final_score = (
            velocity * 1.25
            + trend * 1.15
            + freshness * 1.20
            + corroboration_bonus * 1.10
            + visual * 0.55
            + originality * 0.80
            + niche
            + min(8.0, history * 0.08)
            - risk * 2.25
        )

        if final_score < -1.0:
            story["discovery_rejection"] = "Weak current opportunity signal"
            rejected_weak += 1
            continue

        story["candidate_score"] = round(final_score, 3)
        story["historical_topic_signal"] = round(history, 3)
        story["historical_topic_matches"] = history_matches
        story["freshness_score"] = round(freshness, 2)
        story["corroboration_bonus"] = round(corroboration_bonus, 2)
        story["visual_potential"] = round(visual, 2)
        story["risk_signal_count"] = risk
        story["originality_score"] = round(originality, 2)
        story["sports_niche_bonus"] = niche
        story["discovery_dimensions"] = {
            "momentum": round(velocity + trend, 2),
            "freshness": round(freshness, 2),
            "corroboration": round(corroboration_bonus, 2),
            "channel_history": round(min(10.0, history * 0.10), 2),
            "originality": round(originality, 2),
            "visual_potential": round(visual, 2),
            "safety_risk": risk,
        }
        ranked.append((final_score, index, story))

    ranked.sort(key=lambda item: (item[0], -item[1]), reverse=True)

    # Keep enough high-quality options for the newsroom's diversity selector.
    result = [item[2] for item in ranked[:15]]
    print(
        "   [Discovery Funnel] raw=%d -> safety/duplicate survivors=%d -> scored=%d -> shortlist_pool=%d "
        "(safety_rejected=%d, duplicate_rejected=%d, weak_rejected=%d)"
        % (len(stories), len(ranked), len(ranked), len(result), rejected_safety, rejected_duplicate, rejected_weak),
        flush=True,
    )
    return result


def patch_story_selection(bot):
    """Insert story ranking after collection/dedup and before the newsroom's 3-story selector."""
    if getattr(bot, "_story_selection_patch_installed", False):
        return bot

    original = bot.gather_and_filter_stories

    def gather(conn, genre_key, genre_cfg, trend_keyword=None, custom_gnews_q=None, custom_rss_url=None):
        stories = original(conn, genre_key, genre_cfg, trend_keyword, custom_gnews_q, custom_rss_url)
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
                "   [Story Ranker] Ranked %d discovery survivors for %s | %s | %s. Top story: %s"
                % (len(ranked), format_mode, category, language, str(ranked[0].get("title", ""))[:100]),
                flush=True,
            )
        return ranked

    gather._story_selection_patch = True
    bot.gather_and_filter_stories = gather
    bot._story_selection_patch_installed = True
    return bot
