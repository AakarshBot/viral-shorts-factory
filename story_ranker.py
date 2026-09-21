"""High-recall discovery funnel and historical story ranking for the Shorts newsroom."""
from __future__ import annotations

import math
import os
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests

from event_discovery_runtime import fetch_gdelt_articles, cluster_news_events, _event_actions


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

SOURCE_QUALITY_TERMS = {
    "reuters", "associated press", "ap news", "bbc", "cnn", "guardian", "new york times",
    "washington post", "economist", "bloomberg", "financial times", "espn", "espncricinfo",
    "icc", "bcci", "fifa", "nba", "formula1", "nasa", "who", "united nations",
    "the hindu", "indian express", "hindustan times", "times of india", "ndtv", "aaj tak",
    "official", "gov", "government", "ministry",
}

SPORTS_NICHE_TERMS = {
    "tennis", "football", "soccer", "badminton", "athletics", "basketball", "formula 1", "f1",
    "motogp", "golf", "rugby", "volleyball", "hockey", "cycling", "boxing", "mma", "wrestling",
    "olympics", "olympic",
}

CRICKET_TERMS = {"cricket", "icc", "bcci", "pcb", "test cricket", "t20", "odi", "ipl", "psl"}



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
        "breaking", "report", "reports", "official", "update",
    }
    text = re.sub(r"[^a-z0-9%]+", " ", _clean(value))
    return {x for x in text.split() if len(x) > 2 and x not in stop}


def _text_blob(story):
    return " ".join(
        str(story.get(key) or "")
        for key in (
            "title", "description", "snippet", "summary", "content", "text",
            "event_search_text",
        )
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
    """Return the newest trustworthy publication/update timestamp available."""
    candidates = []
    for key in (
        "updated_at", "updatedAt", "modified_at", "modifiedAt", "last_updated",
        "published_at", "publishedAt", "published", "pub_date", "date", "timestamp",
    ):
        raw = story.get(key)
        if raw in (None, ""):
            continue
        if isinstance(raw, (int, float)):
            try:
                candidates.append(datetime.fromtimestamp(float(raw), tz=timezone.utc))
            except (TypeError, ValueError, OSError, OverflowError):
                continue
            continue
        text = str(raw).strip()
        if not text:
            continue
        parsed = None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(text)
            except (TypeError, ValueError, OverflowError):
                parsed = None
        if parsed is None:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        candidates.append(parsed.astimezone(timezone.utc))

    now = datetime.now(timezone.utc)
    future_cutoff = now.timestamp() + (15 * 60)
    trustworthy = [
        value for value in candidates
        if value.timestamp() <= future_cutoff
    ]
    return max(trustworthy) if trustworthy else None


def _age_hours(story):
    dt = _published_datetime(story)
    if dt is None:
        return 9999.0
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0)


def _freshness_score(story):
    age_hours = _age_hours(story)
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


def _event_momentum_score(story):
    """Score how quickly an event is accumulating recent coverage."""
    evidence = story.get("event_evidence") or []
    if not evidence:
        return 0.0

    now = datetime.now(timezone.utc)
    score = 0.0
    for item in evidence:
        raw = item.get("publishedAt") if isinstance(item, dict) else None
        if not raw:
            continue
        published = _published_datetime({"publishedAt": raw})
        if published is None:
            continue

        age_hours = max(0.0, (now - published).total_seconds() / 3600.0)
        if age_hours <= 1:
            score += 2.5
        elif age_hours <= 3:
            score += 2.0
        elif age_hours <= 6:
            score += 1.25
        elif age_hours <= 12:
            score += 0.5

    # A single stale article should never masquerade as a fast-moving event.
    return round(min(10.0, score), 2)


def _independent_corroboration_score(story):
    """Reward independent publishers, not raw duplicate article volume."""
    source_count = _safe_float(story.get("event_source_count")) or 0.0
    domains = len(set(story.get("event_source_domains") or []))
    publishers = len(set(story.get("event_evidence_publishers") or []))
    independent = max(source_count, float(domains), float(publishers))
    return round(min(10.0, independent * 2.5), 2)


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


SHORTS_STAKES_TERMS = {
    "win", "won", "wins", "record", "first", "final", "launch", "launched",
    "unveils", "reveals", "ban", "banned", "approve", "approved", "acquire",
    "acquired", "qualify", "qualified", "eliminate", "eliminated", "release",
    "released", "price", "deal", "breakthrough", "historic", "surprise",
    "controversy", "viral", "million", "billion", "%",
}


def _clamp_score(value, maximum=10.0):
    return round(max(0.0, min(float(maximum), float(value))), 2)


def _shorts_viability(story, visual=None):
    """Estimate whether an event can compress cleanly into a compelling Short."""
    text = _text_blob(story)
    title = _clean(story.get("title") or story.get("event_search_text") or "")
    tokens = _tokens(title)
    actions = set(story.get("event_actions") or _event_actions(title))
    visual = _visual_potential(story) if visual is None else float(visual)

    clarity = 2.0 if 1 <= len(actions) <= 2 else (1.0 if actions else 0.0)
    stakes = min(2.0, float(len(_tokens(text) & SHORTS_STAKES_TERMS)) * 0.5)
    compression = 2.0 if len(tokens) <= 14 else (1.0 if len(tokens) <= 22 else 0.0)

    article_count = int(story.get("event_article_count") or 1)
    update_density = 2.0 if 2 <= article_count <= 8 else (1.0 if article_count > 1 else 0.0)
    visual_component = min(2.0, visual * 0.20)

    return _clamp_score(
        clarity + stakes + compression + update_density + visual_component
    )


def _audience_potential(story, social, google_trend, event_momentum, originality):
    """Score audience-interest signals separately from factual importance."""
    velocity = _safe_float(story.get("velocity_score")) or 0.0
    trend = _safe_float(story.get("trend_bonus")) or 0.0
    social = float(social or 0.0)
    google_trend = float(google_trend or 0.0)

    return _clamp_score(
        social * 0.32
        + google_trend * 0.32
        + min(10.0, velocity) * 0.16
        + min(10.0, trend) * 0.10
        + min(10.0, event_momentum) * 0.06
        + min(10.0, originality) * 0.04
    )


def _channel_performance_prior(rows, target_category="", target_format="", target_language=""):
    """Learn a small channel-fit prior from the factory's own completed uploads.

    The prior is intentionally conservative: it shrinks sparse category/format/language
    results toward the overall channel baseline so a single unusually good or bad Short
    cannot dominate current-event ranking.
    """
    eligible = [row for row in (rows or []) if _eligible(row)]
    if not eligible:
        return 5.0, 0

    def _value(row):
        return _safe_float(row.get("avg_view_percentage"))

    values = [value for value in (_value(row) for row in eligible) if value is not None]
    if not values:
        return 5.0, 0

    global_mean = sum(values) / len(values)

    def _context_value(value):
        cleaned = _clean(value)
        if "sports" in cleaned or "cricket" in cleaned:
            return "sports"
        return cleaned

    category = _context_value(target_category)
    fmt = _clean(target_format)
    language = _clean(target_language)

    def matches(row, require_all=True):
        row_category = _context_value(row.get("genre"))
        row_format = _clean(row.get("format_used"))
        row_language = _clean(row.get("language_used"))
        checks = []
        if category:
            checks.append(row_category == category)
        if fmt:
            checks.append(row_format == fmt)
        if language:
            checks.append(row_language == language)
        return all(checks) if require_all else any(checks)

    context_rows = [row for row in eligible if matches(row, require_all=True)]
    if not context_rows and category:
        context_rows = [row for row in eligible if _context_value(row.get("genre")) == category]
    if not context_rows and fmt:
        context_rows = [row for row in eligible if _clean(row.get("format_used")) == fmt]
    if not context_rows and language:
        context_rows = [row for row in eligible if _clean(row.get("language_used")) == language]

    context_values = [value for value in (_value(row) for row in context_rows) if value is not None]
    if not context_values:
        return 5.0, 0

    # Hierarchical shrinkage: context mean gets only as much influence as the
    # observed sample supports, with five virtual observations at the channel mean.
    prior_weight = 5.0
    context_mean = (
        sum(context_values) + global_mean * prior_weight
    ) / (len(context_values) + prior_weight)

    # Convert the percentage-point lift/loss against the channel baseline into
    # a neutral 0-10 signal. A 10pp difference changes the score by about 2 points.
    score = 5.0 + max(-4.0, min(4.0, (context_mean - global_mean) / 5.0))
    return round(score, 2), len(context_values)


def _historical_context_score(story, rows, target_category, target_format, target_language):
    """Keep history useful without letting token overlap dominate ranking."""
    history, matches = _historical_score(
        story, rows, target_category, target_format, target_language
    )
    if not matches:
        return 0.0, 0

    # The raw historical signal is a percentage-like value. Compress it to a
    # stable 0-10 channel so one unusually high-performing old video cannot
    # overwhelm current-event evidence.
    return _clamp_score(history * 0.10), matches


def _topic_overlap(a, b):
    aa, bb = _tokens(a), _tokens(b)
    if not aa or not bb:
        return 0.0
    return len(aa & bb) / max(1, len(aa | bb))


def _same_topic(story, used_topics):
    title = story.get("title", "")
    return max((_topic_overlap(title, old) for old in used_topics), default=0.0)


def _canonical_url(value):
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
        return f"{parsed.netloc.removeprefix('www.')}" + parsed.path.rstrip("/")
    except Exception:
        return raw


def _source_quality(story):
    """Score the publisher itself, never reputation words found inside article text."""
    source = _clean(story.get("source") or story.get("publisher") or story.get("source_name"))
    domain = _source_domain(story)
    blob = f"{source} {domain}"
    score = min(5.0, sum(1 for term in SOURCE_QUALITY_TERMS if term in blob))
    if _clean(story.get("collection_source")) == "official":
        score = min(5.0, score + 2.0)
    return score


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


def _recent_topic_cooldown(conn, stories, hours=72):
    """Remove recently covered topics while allowing genuinely new developments."""
    if conn is None:
        return list(stories or [])
    try:
        rows = conn.execute(
            "SELECT topic, COALESCE(date_used, created_at) FROM vault "
            "WHERE topic IS NOT NULL AND topic != '' "
            "AND video_id IS NOT NULL AND video_id NOT IN ('', 'PENDING_QC', 'READY_FOR_UPLOAD', 'REJECTED', 'FAILED') "
            "AND status NOT IN ('PENDING_QC', 'READY_FOR_UPLOAD', 'REJECTED', 'FAILED')"
        ).fetchall()
    except Exception:
        return list(stories or [])
    now = datetime.now(timezone.utc)
    recent = []
    for topic, raw_date in rows:
        if not topic or not raw_date:
            continue
        try:
            when = raw_date if isinstance(raw_date, datetime) else datetime.fromisoformat(
                str(raw_date).replace("Z", "+00:00")
            )
        except (TypeError, ValueError):
            try:
                when = datetime.strptime(str(raw_date)[:10], "%Y-%m-%d")
            except ValueError:
                continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        age = (now - when.astimezone(timezone.utc)).total_seconds() / 3600.0
        if 0 <= age <= hours:
            recent.append(str(topic))
    if not recent:
        return list(stories or [])
    kept = []
    for story in stories or []:
        title = str(story.get("title") or "")
        current_tokens = _tokens(title)
        candidate_actions = set(story.get("event_actions") or _event_actions(title))
        repeated = False
        for old_topic in recent:
            overlap = _topic_overlap(title, old_topic)
            shared = len(current_tokens & _tokens(old_topic))
            if overlap < 0.55 and not (shared >= 3 and overlap >= 0.32):
                continue
            old_actions = set(_event_actions(old_topic))
            if candidate_actions and old_actions and candidate_actions - old_actions:
                continue
            repeated = True
            break
        if repeated:
            story["discovery_rejection"] = "Recent topic cooldown"
            continue
        kept.append(story)
    return kept


def _eligible(row):
    status = _clean(row.get("status"))
    video_id = _clean(row.get("video_id"))
    return (
        status not in {"pending_qc", "rejected", "failed"}
        and video_id not in {"", "pending_qc", "rejected", "failed"}
        and _safe_float(row.get("avg_view_percentage")) is not None
    )


def _historical_score(story, rows, target_category, target_format, target_language):
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

        value = _safe_float(row.get("avg_view_percentage")) or 0.0
        combo = str(row.get("combo_key") or "").split("|")
        fmt = _clean(row.get("format_used") or (combo[0] if len(combo) > 0 else ""))
        category = _clean(row.get("genre") or (combo[1] if len(combo) > 1 else ""))
        language = _clean(row.get("language_used") or (combo[2] if len(combo) > 2 else ""))

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


def _apply_sports_niche_bonus(story, target_category):
    if _clean(target_category) != "sports":
        return 0.0
    return 3.0 if any(term in _text_blob(story) for term in SPORTS_NICHE_TERMS) else 0.0


def _requested_topic_pass(story, requested_topic):
    topic_terms = _tokens(requested_topic)
    if not topic_terms:
        return True
    story_terms = _tokens(_text_blob(story))
    overlap = len(topic_terms & story_terms)
    required = 2 if len(topic_terms) >= 3 else 1
    if overlap >= required:
        story["requested_topic_overlap"] = overlap
        return True
    story["discovery_rejection"] = "Does not match requested topic"
    story["requested_topic_overlap"] = overlap
    return False


def _cricket_relevance_pass(story, genre_key):
    if _clean(genre_key) != "sports_stories_of_day":
        return True
    story_terms = _tokens(_text_blob(story))
    if story_terms & {term for term in CRICKET_TERMS if " " not in term}:
        return True
    if "test cricket" in _text_blob(story) or "formula 1" in _text_blob(story):
        return False
    story["discovery_rejection"] = "Not cricket-relevant"
    return False


GOOGLE_NEWS_RADAR_QUERIES = (
    "(India OR Indian OR world OR global) (news OR announced OR decision OR deal OR launch)",
    "(technology OR AI OR science) (news OR launch OR research OR breakthrough)",
    "(business OR economy OR markets OR company) (news OR deal OR earnings OR investment)",
    "(sports OR cricket OR football OR tennis) (news OR match OR tournament OR record)",
    "(entertainment OR movies OR music OR celebrity) (news OR release OR announcement)",
    "(health OR medicine OR wellness) (news OR study OR approval)",
)
GOOGLE_TRENDS_GEOS = ("IN", "US", "GB")
REDDIT_RADAR_SUBREDDITS = ("news", "worldnews", "india", "technology", "sports", "movies")

DISCOVERY_SOURCE_WAIT_SECONDS = 10.0
DISCOVERY_SIGNAL_WAIT_SECONDS = 8.0
DISCOVERY_MIN_CORE_ARTICLES_FOR_GDELT = 60
DISCOVERY_MAX_GOOGLE_QUERIES_BROAD = 7
DISCOVERY_MAX_GOOGLE_QUERIES_STANDARD = 4
DISCOVERY_MAX_REDDIT_SUBREDDITS_BROAD = 6


def _trend_traffic_score(value, rank=0):
    text = str(value or "").strip().upper().replace(",", "")
    match = re.search(r"(\d+(?:\.\d+)?)\s*([KMB])?", text)
    if match:
        number = float(match.group(1))
        multiplier = {"K": 1e3, "M": 1e6, "B": 1e9}.get(match.group(2) or "", 1.0)
        traffic = number * multiplier
        if traffic >= 500_000:
            return 4.0
        if traffic >= 200_000:
            return 3.5
        if traffic >= 100_000:
            return 3.0
        if traffic >= 50_000:
            return 2.5
        if traffic >= 20_000:
            return 2.0
        if traffic > 0:
            return 1.5
    return max(1.0, min(3.0, 3.0 - max(0, int(rank) - 1) * 0.2))


def _google_trends_root(geo="IN"):
    geo = str(geo or "").strip().upper()
    if not geo:
        return None
    try:
        response = requests.get(
            "https://trends.google.com/trending/rss",
            params={"geo": geo},
            headers={"User-Agent": "ViralShortsFactory/2026 discovery/1.0"},
            timeout=8,
        )
        response.raise_for_status()
        return ET.fromstring(response.content)
    except Exception as exc:
        print(f"   [Discovery] Google Trends RSS unavailable for {geo}: {type(exc).__name__}", flush=True)
        return None


def _google_trends_items(geo="IN", max_items=10):
    geo = str(geo or "").strip().upper()
    root = _google_trends_root(geo)
    if root is None:
        return []

    output = []
    for rank, item in enumerate(root.findall("./channel/item")[:max_items], 1):
        trend_query = str(item.findtext("title") or "").strip()
        if not trend_query:
            continue
        traffic = str(item.findtext("{*}approx_traffic") or "").strip()
        trend_score = _trend_traffic_score(traffic, rank)
        for news_item in item.findall("{*}news_item"):
            title = str(news_item.findtext("{*}news_item_title") or "").strip()
            url_value = str(news_item.findtext("{*}news_item_url") or "").strip()
            publisher = str(news_item.findtext("{*}news_item_source") or "").strip()
            snippet = str(news_item.findtext("{*}news_item_snippet") or "").strip()
            if not title or not url_value:
                continue
            output.append({
                "title": title,
                "text": snippet or title,
                "description": snippet,
                "source": publisher or "Google Trends",
                "source_name": publisher or "Google Trends",
                "publisher": publisher or "Google Trends",
                "url": url_value,
                "publishedAt": str(item.findtext("pubDate") or "").strip(),
                "genre": "",
                "collection_source": "google_trends",
                "trend_query": trend_query,
                "trend_traffic": traffic,
                "trend_geo": geo,
                "trend_rank": rank,
                "trend_bonus": trend_score,
            })
    return output


def fetch_google_trending_topics(geos=("IN",), max_terms=15):
    terms = []
    seen = set()
    for geo in geos or ("IN",):
        root = _google_trends_root(geo)
        if root is None:
            continue
        for item in root.findall("./channel/item")[:max(1, int(max_terms))]:
            trend = str(item.findtext("title") or "").strip()
            if trend and trend.casefold() not in seen:
                seen.add(trend.casefold())
                terms.append(trend)
                if len(terms) >= max(1, int(max_terms)):
                    return terms
    return terms


def _google_news_search_items(query, genre_key="", max_items=60):
    query = str(query or "").strip()
    if not query:
        return []
    url = (
        "https://news.google.com/rss/search"
        f"?q={quote_plus(query)}&hl=en-IN&gl=IN&ceid=IN:en"
    )
    return _rss_items(url, genre_key, collection_source="google_news_rss", max_items=max_items)


def _infer_discovery_category(story):
    text = _clean(" ".join(
        str(story.get(key, "") or "")
        for key in ("title", "text", "description", "event_search_text", "trend_query")
    ))
    weighted = {
        "sports": {"cricket": 5, "icc": 5, "ipl": 5, "football": 4, "soccer": 4, "tennis": 4, "match": 2, "tournament": 3, "athlete": 3, "olympic": 4},
        "technology": {"artificial intelligence": 5, "ai": 4, "chip": 3, "software": 3, "robot": 3, "smartphone": 4, "gadget": 3, "startup": 3, "nvidia": 3},
        "business_finance": {"stock": 4, "market": 3, "economy": 4, "earnings": 4, "funding": 3, "acquisition": 4, "ipo": 4, "bank": 2, "finance": 3},
        "entertainment": {"movie": 4, "film": 4, "bollywood": 5, "tollywood": 5, "celebrity": 4, "trailer": 3, "actor": 3, "music": 3, "album": 3},
        "health_lifestyle": {"health": 3, "medical": 4, "medicine": 4, "disease": 4, "study": 2, "nutrition": 3, "fitness": 3, "wellness": 3},
        "regional_state_news": {"telangana": 5, "hyderabad": 5, "andhra pradesh": 5, "amaravati": 5},
        "viral_phenomenon": {"viral": 4, "trending": 3, "internet": 3, "social media": 3, "tiktok": 3, "instagram": 3, "youtube": 2},
    }
    scores = {key: 0 for key in weighted}
    for category, terms in weighted.items():
        for term, points in terms.items():
            matched = bool(re.search(r"\bai\b", text)) if term == "ai" else term in text
            if matched:
                scores[category] += points
    winner = max(scores.items(), key=lambda item: item[1])
    return winner[0] if winner[1] > 0 else "national_global_affairs"


def _social_signal(title, social_titles):
    tokens = _tokens(title)
    if not tokens or not social_titles:
        return 0.0
    best = 0.0
    for other in social_titles:
        other_tokens = _tokens(other)
        if not other_tokens:
            continue
        overlap = len(tokens & other_tokens) / max(1, len(tokens | other_tokens))
        best = max(best, overlap)
    return min(4.0, best * 12.0)


def _source_url_from_item(item):
    return str(item.get("url") or item.get("link") or "").strip()


def _rss_items(url, genre_key, collection_source="rss", max_items=60):
    if not url:
        return []
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 ViralShortsFactory/2026"},
            timeout=8,
        )
        if response.status_code != 200:
            return []
        root = ET.fromstring(response.content)
        items = []
        for item in root.findall(".//item")[:max_items]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            description = (item.findtext("description") or "").strip()
            published = (item.findtext("pubDate") or "").strip()
            source_node = item.find("source")
            publisher = (
                (source_node.text or "").strip()
                if source_node is not None and source_node.text
                else _source_domain({"url": link}) or "RSS"
            )
            if title and link:
                items.append({
                    "title": title,
                    "text": description,
                    "description": description,
                    "source": publisher,
                    "source_name": publisher,
                    "url": link,
                    "publishedAt": published,
                    "genre": genre_key,
                    "collection_source": collection_source,
                })
        return items
    except Exception:
        return []


def _official_feed_urls(genre_key, genre_cfg):
    urls = []
    configured = genre_cfg.get("official_rss_urls") or []
    if isinstance(configured, str):
        configured = re.split(r"[;,]", configured)
    urls.extend(str(item).strip() for item in configured if str(item).strip())
    urls.extend(
        item.strip()
        for item in os.getenv(f"OFFICIAL_FEEDS_{str(genre_key).upper()}", "").split(";")
        if item.strip()
    )
    urls.extend(
        item.strip()
        for item in os.getenv("FACTORY_OFFICIAL_FEEDS", "").split(";")
        if item.strip()
    )
    return list(dict.fromkeys(urls))


def _official_feed_items(genre_key, genre_cfg):
    """Fetch configured official feeds in parallel without serializing the lane."""
    urls = _official_feed_urls(genre_key, genre_cfg)
    if not urls:
        return []

    pool = ThreadPoolExecutor(
        max_workers=min(4, len(urls)),
        thread_name_prefix="discovery-official",
    )
    futures = {
        pool.submit(_rss_items, url, genre_key, collection_source="official"): url
        for url in urls
    }
    try:
        done, pending = wait(futures, timeout=8.0)
        items = []
        for future in done:
            try:
                items.extend(future.result() or [])
            except Exception:
                continue
        for future in pending:
            future.cancel()
        return items
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _reddit_items(genre_key="", subreddit="", limit=50):
    subreddit = str(subreddit or "").strip() or {
        "sports": "sports",
        "sports_stories_of_day": "sports",
        "technology": "technology",
        "business_finance": "business",
        "entertainment": "movies",
        "viral_phenomenon": "popular",
        "national_global_affairs": "worldnews",
    }.get(genre_key, "popular")
    url = f"https://www.reddit.com/r/{subreddit}/hot.json?limit={max(10, min(50, int(limit)))}"
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "ViralShortsFactory/2026 discovery/1.0"},
            timeout=8,
        )
        if response.status_code != 200:
            return []
        children = response.json().get("data", {}).get("children", [])
        output = []
        for node in children:
            data = node.get("data", {}) if isinstance(node, dict) else {}
            title = str(data.get("title") or "").strip()
            permalink = str(data.get("permalink") or "").strip()
            if not title:
                continue
            created_utc = _safe_float(data.get("created_utc"))
            published = ""
            if created_utc:
                try:
                    published = datetime.fromtimestamp(created_utc, tz=timezone.utc).isoformat()
                except (TypeError, ValueError, OSError):
                    published = ""
            output.append({
                "title": title,
                "text": title,
                "description": title,
                "source": f"Reddit r/{subreddit}",
                "source_name": f"Reddit r/{subreddit}",
                "url": f"https://www.reddit.com{permalink}" if permalink else "",
                "publishedAt": published,
                "genre": genre_key,
                "collection_source": "reddit",
                "reddit_score": _safe_float(data.get("score")) or 0.0,
            })
        return output
    except Exception:
        return []


def _cheap_filter(stories, max_items=30, max_age_hours=72):
    """Apply freshness/safety eligibility before truncating the intake."""
    survivors = []
    seen_urls = set()
    for story in stories:
        if not isinstance(story, dict):
            continue
        title = str(story.get("title") or "").strip()
        if len(title) < 12:
            continue
        safe, hits = _safety_gate(story)
        if not safe:
            story["discovery_rejection"] = "Safety/content policy gate"
            story["safety_hits"] = hits
            continue
        age = _age_hours(story)
        if age == 9999.0 or age > max_age_hours:
            story["discovery_rejection"] = "Missing or stale publication date"
            continue
        if age > 48.0 and _event_momentum_score(story) < 0.5:
            story["discovery_rejection"] = "Stale event without recent development"
            continue
        url = _canonical_url(_source_url_from_item(story))
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        story["cheap_filter_pass"] = True
        story["age_hours"] = round(age, 2)
        survivors.append(story)

    survivors.sort(
        key=lambda item: (
            _freshness_score(item),
            -max(0.0, _safe_float(item.get("age_hours")) or 9999.0),
            _safe_float(item.get("event_corroboration_score")) or 0.0,
            _source_quality(item),
        ),
        reverse=True,
    )
    return survivors[:max_items]


def _deduplicate_stage(stories, max_items=15):
    """Remove residual duplicate articles without collapsing clustered events."""
    selected = []
    for story in sorted(
        stories,
        key=lambda item: (
            _safe_float(item.get("event_corroboration_score")) or 0.0,
            _freshness_score(item),
            _source_quality(item),
        ),
        reverse=True,
    ):
        duplicate = False
        for old in selected:
            if (
                story.get("event_id")
                and old.get("event_id")
                and str(story.get("event_id")) == str(old.get("event_id"))
            ):
                duplicate = True
                break
            similarity = _story_theme_similarity(story, old)
            if similarity >= 0.74 and _topic_dedupe_compatible(story, old):
                duplicate = True
                break
        if duplicate:
            story["discovery_rejection"] = "Duplicate event/topic"
            continue
        story["dedupe_pass"] = True
        selected.append(story)
        if len(selected) >= max_items:
            break
    return selected


def _fact_source_stage(stories, max_items=8):
    for story in stories:
        publishers = set()
        if story.get("event_clustered"):
            domains = set(story.get("event_source_domains") or [])
            publishers = set(story.get("event_publishers") or [])
            if story.get("event_article_count", 0) == 1 and not domains:
                domain = _source_domain(story)
                if domain:
                    domains.add(domain)
            corroboration = max(
                len(domains),
                len(publishers),
                int(story.get("event_source_count") or 0),
            )
            article_count = int(story.get("event_article_count") or 1)
        else:
            title = story.get("title", "")
            domains = {_source_domain(story)} if _source_domain(story) else set()
            for other in stories:
                if other is story:
                    continue
                if _topic_overlap(title, other.get("title", "")) >= 0.30:
                    domain = _source_domain(other)
                    if domain:
                        domains.add(domain)
            corroboration = len(domains)
            article_count = 1

        event_bonus = _safe_float(story.get("event_corroboration_score")) or 0.0
        article_bonus = min(4.0, max(0.0, article_count - 1) * 0.5)
        story["corroboration_bonus"] = min(10.0, max(float(corroboration) * 2.0, event_bonus))
        story["article_support_bonus"] = article_bonus
        story["source_domains"] = sorted(domains)
        story["source_quality_score"] = _source_quality(story)
        story["fact_source_score"] = min(
            16.0,
            float(corroboration) * 2.5 + _source_quality(story) + article_bonus,
        )
        story["fact_source_pass"] = bool(domains or publishers) and (
            _source_quality(story) >= 1.0 or corroboration >= 2
        )

    passed = [story for story in stories if story.get("fact_source_pass")]
    for story in stories:
        if not story.get("fact_source_pass"):
            story["discovery_rejection"] = "Insufficient source support"
    passed.sort(
        key=lambda item: (
            _safe_float(item.get("fact_source_score")) or 0.0,
            _freshness_score(item),
        ),
        reverse=True,
    )
    return passed[:max_items]


def _originality_stage(stories, used_topics, max_items=5):
    selected = []
    for story in stories:
        overlap = _same_topic(story, used_topics)

        # Historical cooldown is deliberately conservative: high lexical
        # overlap indicates the same covered topic, while moderate overlap can
        # simply mean the same entity has a genuinely new development.
        if overlap >= 0.72:
            story["discovery_rejection"] = "Previously covered topic/angle"
            continue

        title_overlap = max(
            (
                _topic_overlap(story.get("title", ""), old.get("title", ""))
                for old in selected
            ),
            default=0.0,
        )
        if title_overlap >= 0.58:
            continue

        story["originality_score"] = round(
            max(0.0, 10.0 - overlap * 9.0 - title_overlap * 6.0),
            2,
        )
        story["originality_pass"] = True
        selected.append(story)
        if len(selected) >= max_items:
            break
    return selected


def _editorial_score(story, rows, target_category, target_format, target_language, social_titles, ai_cricket=False):
    velocity = _safe_float(story.get("velocity_score")) or 0.0
    trend = _safe_float(story.get("trend_bonus")) or 0.0
    freshness = _freshness_score(story)
    corroboration = _safe_float(story.get("corroboration_bonus")) or 0.0
    article_support = _safe_float(story.get("article_support_bonus")) or 0.0
    visual = _visual_potential(story)
    risk = _risk_score(story)
    india_relevance = _india_relevance_score(story)
    topic_actionability = _topic_actionability(story)
    source_quality = _safe_float(story.get("source_quality_score")) or 0.0
    event_text = story.get("event_search_text") or story.get("title", "")
    social = _social_signal(event_text, social_titles)
    google_trend = trend
    history, history_matches = _historical_context_score(
        story,
        rows,
        target_category,
        target_format,
        target_language,
    )
    niche = _apply_sports_niche_bonus(story, target_category)
    originality = _safe_float(story.get("originality_score")) or 5.0
    event_momentum = _event_momentum_score(story)
    independent_corroboration = _independent_corroboration_score(story)
    event_velocity = _safe_float(story.get("event_velocity_score")) or 0.0
    discovery_gap = bool(story.get("event_discovery_gap"))
    development_state = _clean(story.get("event_development_state"))

    momentum = min(
        10.0,
        velocity
        + trend
        + min(2.0, event_velocity * 0.35)
        + (0.75 if development_state == "developing" else 0.0)
    )
    importance = _clamp_score(
        momentum * 0.28
        + min(10.0, event_momentum) * 0.18
        + min(10.0, freshness) * 0.16
        + min(10.0, corroboration) * 0.16
        + min(10.0, independent_corroboration) * 0.10
        + min(10.0, article_support) * 0.04
        + min(10.0, source_quality) * 0.08
        - min(10.0, risk * 2.0) * 0.18
    )

    audience = _audience_potential(
        story,
        social,
        google_trend,
        event_momentum,
        originality,
    )
    shorts_viability = _shorts_viability(story, visual)
    channel_history = history
    channel_fit, channel_fit_samples = _channel_performance_prior(
        rows,
        target_category,
        target_format,
        target_language,
    )
    momentum_weight = 1.08 if ai_cricket else 1.0

    # candidate_score remains a single ranking score, but its components are
    # now explicitly separated so audience interest does not masquerade as
    # factual importance and correlated coverage signals are capped.
    final_score = (
        importance * 1.55
        + audience * 1.35 * momentum_weight
        + shorts_viability * 1.20
        + topic_actionability * 0.65
        + originality * 0.45
        + visual * 0.20
        + india_relevance * 0.55
        + channel_history * 0.70
        + channel_fit * 0.45
        + niche * 0.20
        + (0.60 if discovery_gap else 0.0)
        - risk * 0.55
    )
    story["candidate_score"] = round(final_score, 3)
    story["event_momentum_score"] = event_momentum
    story["independent_corroboration_score"] = independent_corroboration
    story["historical_topic_signal"] = round(history, 3)
    story["historical_topic_matches"] = history_matches
    story["channel_fit_score"] = channel_fit
    story["channel_fit_samples"] = channel_fit_samples
    story["freshness_score"] = round(freshness, 2)
    story["visual_potential"] = round(visual, 2)
    story["shorts_viability_score"] = round(shorts_viability, 2)
    story["topic_actionability_score"] = round(topic_actionability, 2)
    story["india_relevance_score"] = round(india_relevance, 2)
    story["importance_score"] = round(importance, 2)
    story["audience_potential_score"] = round(audience, 2)
    story["risk_signal_count"] = risk
    story["social_signal"] = round(social, 2)
    story["google_trends_signal"] = round(google_trend, 2)
    story["sports_niche_bonus"] = niche
    story["discovery_dimensions"] = {
        "importance": round(importance, 2),
        "audience_potential": round(audience, 2),
        "shorts_viability": round(shorts_viability, 2),
        "topic_actionability": round(topic_actionability, 2),
        "india_relevance": round(india_relevance, 2),
        "momentum": round(momentum, 2),
        "event_momentum": round(event_momentum, 2),
        "freshness": round(freshness, 2),
        "corroboration": round(corroboration, 2),
        "independent_corroboration": round(independent_corroboration, 2),
        "article_support": round(article_support, 2),
        "source_quality": round(source_quality, 2),
        "social_signal": round(social, 2),
        "google_trends": round(google_trend, 2),
        "channel_history": round(history, 2),
        "channel_fit": round(channel_fit, 2),
        "channel_fit_samples": channel_fit_samples,
        "originality": round(originality, 2),
        "visual_potential": round(visual, 2),
        "safety_risk": risk,
        "event_velocity": round(event_velocity, 2),
        "development_state": development_state,
        "discovery_gap": discovery_gap,
    }
    return story


def _discovery_source_pass(story):
    """Require minimum provenance for dashboard discovery without requiring corroboration."""
    url = _source_url_from_item(story)
    evidence = [
        item for item in (story.get("event_evidence") or [])
        if isinstance(item, dict)
    ]
    has_evidence_url = any(str(item.get("url") or "").strip() for item in evidence)
    publisher = _clean(
        story.get("publisher")
        or story.get("source")
        or story.get("source_name")
        or story.get("domain")
    )
    has_evidence_publisher = any(
        _clean(item.get("publisher"))
        for item in evidence
        if isinstance(item, dict)
    )
    if not (url or has_evidence_url):
        story["discovery_rejection"] = "No source URL/evidence"
        return False
    if not (publisher or has_evidence_publisher):
        story["discovery_rejection"] = "No identifiable publisher"
        return False
    story["discovery_source_backed"] = True
    return True


def _candidate_quality_pass(story):
    """Keep weak candidates out of the dashboard instead of padding the list."""
    dimensions = story.get("discovery_dimensions") or {}
    freshness = _safe_float(dimensions.get("freshness")) or 0.0
    momentum = _safe_float(dimensions.get("event_momentum")) or 0.0
    importance = _safe_float(dimensions.get("importance")) or 0.0
    shorts = _safe_float(dimensions.get("shorts_viability")) or 0.0
    corroboration = _safe_float(dimensions.get("corroboration")) or 0.0
    source_quality = _safe_float(dimensions.get("source_quality")) or 0.0
    score = _safe_float(story.get("candidate_score")) or 0.0

    if freshness < 2.0 and momentum < 2.0:
        story["discovery_rejection"] = "Insufficient current-event signal"
        return False
    if importance < 3.5:
        story["discovery_rejection"] = "Insufficient editorial importance"
        return False
    if shorts < 3.0:
        story["discovery_rejection"] = "Weak Shorts viability"
        return False
    if source_quality < 1.0 and corroboration < 2.0:
        story["discovery_rejection"] = "Insufficient source support"
        return False
    if score < 14.0:
        story["discovery_rejection"] = "Below discovery quality floor"
        return False
    return True



def _discovery_portfolio_pass(story):
    """Keep the dashboard broad without weakening the production selection gate.

    The dashboard is a human exploration surface, so niche but current,
    source-supported stories should remain visible even when they are not
    strong enough for automatic production selection.
    """
    dimensions = story.get("discovery_dimensions") or {}
    freshness = _safe_float(dimensions.get("freshness")) or 0.0
    momentum = _safe_float(dimensions.get("event_momentum")) or 0.0
    score = _safe_float(story.get("candidate_score")) or 0.0
    actionability = _safe_float(story.get("topic_actionability_score")) or 0.0
    event_sources = int(story.get("event_source_count") or 0)
    body = " ".join(
        str(story.get(key) or "")
        for key in ("description", "summary", "snippet", "text")
    ).strip()

    if freshness < 1.0 and momentum < 1.0:
        story["discovery_rejection"] = "Insufficient current-event signal"
        return False
    if actionability < 3.0:
        story["discovery_rejection"] = "Headline lacks enough story substance for a Short"
        return False
    if event_sources <= 1 and len(body) < 50 and not (story.get("event_actions") or _event_actions(story.get("title") or "")):
        story["discovery_rejection"] = "Insufficient story detail behind headline"
        return False
    if score < 6.0:
        story["discovery_rejection"] = "Below exploration quality floor"
        return False

    story["discovery_tier"] = (
        "production-ready" if _candidate_quality_pass(story) else "exploratory"
    )
    return True


def _candidate_reason(story):
    dimensions = story.get("discovery_dimensions") or {}
    parts = []
    if _safe_float(dimensions.get("importance")) >= 7:
        parts.append("strong editorial importance")
    if _safe_float(dimensions.get("audience_potential")) >= 7:
        parts.append("strong audience-interest signal")
    if _safe_float(dimensions.get("shorts_viability")) >= 7:
        parts.append("strong Shorts potential")
    if _safe_float(dimensions.get("momentum")) >= 5:
        parts.append("strong current momentum")
    if _safe_float(dimensions.get("event_momentum")) >= 4:
        parts.append("coverage accelerating")
    if _safe_float(dimensions.get("event_velocity")) >= 2:
        parts.append("high reporting velocity")
    if dimensions.get("development_state") == "developing":
        parts.append("new event development detected")
    if dimensions.get("discovery_gap"):
        parts.append("independent discovery-gap signal")
    if _safe_float(dimensions.get("freshness")) >= 6:
        parts.append("very fresh")
    if _safe_float(dimensions.get("corroboration")) >= 2:
        parts.append("multi-source coverage")
    article_count = int(story.get("event_article_count") or 1)
    source_count = int(story.get("event_source_count") or 0)
    if article_count >= 3:
        parts.append(f"{article_count} articles clustered")
    elif source_count >= 2:
        parts.append(f"{source_count} publishers covering the event")
    if _safe_float(dimensions.get("social_signal")) >= 2:
        parts.append("social-interest signal")
    if _safe_float(dimensions.get("google_trends")) >= 1:
        parts.append("Google Trends signal")
    if _safe_float(dimensions.get("channel_history")) >= 2:
        parts.append("relevant channel history")
    if _safe_float(dimensions.get("originality")) >= 7:
        parts.append("strong originality")
    if not parts:
        parts.append("strong editorial score after staged discovery checks")
    return ", ".join(parts) + "."



def _topic_entities(story):
    """Return lightweight subject/entity tokens for diversity-aware selection."""
    generic = {
        "india", "indian", "cricket", "icc", "bcci", "pcb", "t20", "odi", "test",
        "ipl", "psl", "team", "teams", "player", "players", "match", "matches",
        "series", "tournament", "league", "sports", "sport", "news", "latest",
        "today", "world", "global", "official",
    }
    entities = set()
    for value in story.get("event_entities") or []:
        entities.update(token for token in _tokens(value) if token not in generic)
    if not entities:
        entities = {
            token for token in _tokens(story.get("title", ""))
            if token not in generic
        }
    return entities


def _story_theme_similarity(left, right):
    """Estimate whether two candidates are materially the same editorial subject."""
    title_similarity = _topic_overlap(
        left.get("event_search_text") or left.get("title", ""),
        right.get("event_search_text") or right.get("title", ""),
    )
    left_entities = _topic_entities(left)
    right_entities = _topic_entities(right)
    if left_entities and right_entities:
        entity_similarity = len(left_entities & right_entities) / max(
            1, len(left_entities | right_entities)
        )
    else:
        entity_similarity = 0.0

    left_actions = set(left.get("event_actions") or [])
    right_actions = set(right.get("event_actions") or [])
    action_similarity = 1.0 if left_actions and right_actions and left_actions & right_actions else 0.0

    return round(
        min(1.0, title_similarity * 0.60 + entity_similarity * 0.30 + action_similarity * 0.10),
        4,
    )


def diversity_rerank(stories, max_items=28):
    """Select a high-quality but materially diverse dashboard portfolio."""
    candidates = [item for item in (stories or []) if isinstance(item, dict)]
    candidates.sort(
        key=lambda item: (
            _safe_float(item.get("candidate_score")) or -9999.0,
            _safe_float(item.get("freshness_score")) or 0.0,
        ),
        reverse=True,
    )

    selected = []
    remaining = list(candidates)
    while remaining and len(selected) < max(0, int(max_items or 0)):
        best_index = 0
        best_adjusted = -999999.0

        for index, candidate in enumerate(remaining):
            base_score = _safe_float(candidate.get("candidate_score")) or -9999.0
            max_similarity = max(
                (_story_theme_similarity(candidate, old) for old in selected),
                default=0.0,
            )
            novelty_bonus = 1.5 if selected and max_similarity < 0.20 else 0.0
            repetition_penalty = max_similarity * 10.0

            candidate_entities = _topic_entities(candidate)
            repeated_entity_penalty = 0.0
            if candidate_entities:
                entity_repeats = sum(
                    1
                    for old in selected
                    if candidate_entities & _topic_entities(old)
                )
                repeated_entity_penalty = min(4.0, entity_repeats * 1.25)

            candidate_genre = _clean(candidate.get("primary_genre") or candidate.get("genre"))
            same_genre_repeats = sum(
                1
                for old in selected
                if candidate_genre
                and candidate_genre == _clean(old.get("primary_genre") or old.get("genre"))
            )
            portfolio_penalty = min(3.0, max(0, same_genre_repeats - 2) * 0.75)

            adjusted = (
                base_score
                + novelty_bonus
                - repetition_penalty
                - repeated_entity_penalty
                - portfolio_penalty
            )

            if adjusted > best_adjusted:
                best_adjusted = adjusted
                best_index = index

        winner = remaining.pop(best_index)
        winner["diversity_max_similarity"] = round(
            max(
                (_story_theme_similarity(winner, old) for old in selected),
                default=0.0,
            ),
            3,
        )
        winner["discovery_adjusted_score"] = round(best_adjusted, 3)
        selected.append(winner)

    return selected



def _google_news_query_from_url(url):
    """Extract a Google News RSS search query so it is not fetched twice."""
    try:
        parsed = urlparse(str(url or "").strip())
        if parsed.netloc.lower().removeprefix("www.") != "news.google.com":
            return ""
        if not parsed.path.rstrip("/").endswith("/rss/search"):
            return ""
        query = parse_qs(parsed.query).get("q", [""])[0]
        return unquote(str(query or "")).strip()
    except Exception:
        return ""


def _is_reddit_json_url(url):
    """Identify Reddit JSON endpoints that belong in the social lane, not RSS."""
    try:
        parsed = urlparse(str(url or "").strip())
        host = parsed.netloc.lower().removeprefix("www.")
        return host.endswith("reddit.com") and parsed.path.lower().endswith(".json")
    except Exception:
        return False


def _reddit_subreddit_from_url(url):
    """Recover the configured subreddit when a category points at a Reddit JSON feed."""
    try:
        parsed = urlparse(str(url or "").strip())
        host = parsed.netloc.lower().removeprefix("www.")
        if not host.endswith("reddit.com"):
            return ""
        match = re.search(r"/r/([^/]+)/", parsed.path, re.IGNORECASE)
        return match.group(1).strip() if match else ""
    except Exception:
        return ""


def _dedupe_discovery_queries(queries, max_items):
    seen = set()
    output = []
    for query in queries:
        text_value = re.sub(r"\s+", " ", str(query or "").strip())
        key = text_value.casefold()
        if not text_value or key in seen:
            continue
        seen.add(key)
        output.append(text_value)
        if len(output) >= max(1, int(max_items)):
            break
    return output


INDIA_SIGNAL_TERMS = {
    "india", "indian", "delhi", "mumbai", "hyderabad", "bengaluru", "bangalore",
    "chennai", "kolkata", "pune", "ahmedabad", "telangana", "andhra", "amaravati",
    "bcci", "ipl", "wpl", "rbi", "isro", "drdo", "supreme court", "parliament",
    "modi", "government of india",
}

CLICKBAIT_TITLE_TERMS = {
    "you won't believe", "you will not believe", "what happens next", "watch this",
    "shocking", "craziest", "insane", "unbelievable", "must see", "viral video",
}

ACTIONABILITY_TERMS = {
    "announce", "announced", "launch", "launched", "unveil", "unveiled", "approve",
    "approved", "ban", "banned", "sign", "signed", "acquire", "acquired", "win",
    "won", "wins", "defeat", "beat", "appoint", "appointed", "resign", "resigned",
    "arrest", "arrested", "qualify", "qualified", "eliminate", "eliminated",
    "release", "released", "delay", "delayed", "cancel", "cancelled", "join", "joined",
    "open", "opened", "close", "closed", "surge", "surges", "rise", "rises", "fall",
    "falls", "drop", "drops", "approve", "decision", "decides", "set to", "faces",
    "hit", "record", "records", "breakthrough", "deal", "agreement", "investigation",
    "study", "finds", "found", "result", "results", "election", "elected", "court",
}

def _india_relevance_score(story):
    text = _text_blob(story)
    title = _clean(story.get("title") or "")
    score = min(10.0, float(len(_tokens(text) & INDIA_SIGNAL_TERMS)))
    if re.search(r"\b\.in\b", _source_domain(story)):
        score += 2.0
    if any(term in title for term in ("india", "indian")):
        score += 2.0
    if _clean(story.get("collection_source")) == "official" and (
        "india" in text or "indian" in text
    ):
        score += 1.0
    return _clamp_score(score)

def _topic_actionability(story):
    text = _text_blob(story)
    title = _clean(story.get("title") or "")
    actions = set(story.get("event_actions") or _event_actions(text))
    evidence = [
        item for item in (story.get("event_evidence") or [])
        if isinstance(item, dict)
    ]
    body = " ".join(
        str(story.get(key) or "")
        for key in ("description", "summary", "snippet", "text")
    ).strip()
    score = 0.0
    if actions:
        score += 3.0
    if len(body) >= 240:
        score += 3.0
    elif len(body) >= 100:
        score += 2.0
    elif len(body) >= 50:
        score += 1.0
    if len(evidence) >= 2 or int(story.get("event_source_count") or 0) >= 2:
        score += 2.0
    if story.get("event_entities") or _topic_entities(story):
        score += 1.0
    if re.search(r"\d|%", title):
        score += 1.0
    if any(term in title for term in CLICKBAIT_TITLE_TERMS):
        score -= 3.0
    return _clamp_score(score)

def _topic_dedupe_compatible(left, right):
    left_actions = set(left.get("event_actions") or _event_actions(left.get("title") or ""))
    right_actions = set(right.get("event_actions") or _event_actions(right.get("title") or ""))
    if left_actions and right_actions and not (left_actions & right_actions):
        return False
    return True

def _build_discovery_google_queries(
    genre_key,
    genre_cfg,
    trend_keyword=None,
    custom_gnews_q=None,
    selected_rss="",
    broad_discovery=False,
):
    """Build a bounded Google News query budget around requested/category context."""
    genre_cfg = genre_cfg if isinstance(genre_cfg, dict) else {}
    candidates = []
    rss_query = _google_news_query_from_url(selected_rss)
    india_query = str(genre_cfg.get("india_gnews_q") or "").strip()
    global_query = str(genre_cfg.get("global_gnews_q") or "").strip()

    for value in (
        india_query,
        trend_keyword,
        custom_gnews_q,
        rss_query,
        genre_cfg.get("gnews_q"),
        global_query,
    ):
        text_value = str(value or "").strip()
        if text_value:
            candidates.append(text_value)

    targeted = bool(
        str(trend_keyword or "").strip()
        or str(custom_gnews_q or "").strip()
        or str(rss_query or "").strip()
    )
    has_category_query = bool(str(genre_cfg.get("gnews_q") or "").strip())

    if broad_discovery:
        radar_budget = 2 if targeted or has_category_query else len(GOOGLE_NEWS_RADAR_QUERIES)
        candidates.extend(GOOGLE_NEWS_RADAR_QUERIES[:radar_budget])
        return _dedupe_discovery_queries(candidates, DISCOVERY_MAX_GOOGLE_QUERIES_BROAD)

    candidates.append(GOOGLE_NEWS_RADAR_QUERIES[0])
    return _dedupe_discovery_queries(candidates, DISCOVERY_MAX_GOOGLE_QUERIES_STANDARD)


def _resolve_discovery_futures(future_sources, timeout):
    """Resolve completed discovery workers without allowing one source to block its lane."""
    if not future_sources:
        return {}

    futures = list(future_sources)
    done, pending = wait(futures, timeout=max(1.0, float(timeout)))

    resolved = {}
    for future in done:
        label = future_sources.get(future, "discovery source")
        try:
            resolved[future] = future.result()
        except Exception as exc:
            print(
                f"   [Discovery] {label} failed ({type(exc).__name__}); continuing with the other sources.",
                flush=True,
            )

    for future in pending:
        label = future_sources.get(future, "discovery source")
        if future.cancel():
            print(
                f"   [Discovery] {label} exceeded the {float(timeout):g}s lane budget; continuing without it.",
                flush=True,
            )
        else:
            print(
                f"   [Discovery] {label} still running at the {float(timeout):g}s lane boundary; continuing without it.",
                flush=True,
            )

    return resolved


def collect_high_recall_stories(
    bot,
    genre_key,
    genre_cfg,
    trend_keyword=None,
    custom_gnews_q=None,
    custom_rss_url=None,
    broad_discovery=False,
):
    """Collect bounded factual discovery plus separate trend/social signals."""
    genre_cfg = genre_cfg if isinstance(genre_cfg, dict) else {}

    configured_rss = str(custom_rss_url or genre_cfg.get("rss_url") or "").strip()
    configured_google_query = _google_news_query_from_url(configured_rss)
    configured_reddit_subreddit = _reddit_subreddit_from_url(configured_rss)
    selected_rss = configured_rss
    if configured_google_query or _is_reddit_json_url(selected_rss):
        # Google News search URLs become query lanes; Reddit JSON belongs to the
        # dedicated social lane. Neither should also be fetched as generic RSS.
        selected_rss = ""

    google_queries = _build_discovery_google_queries(
        genre_key,
        genre_cfg,
        trend_keyword=trend_keyword,
        custom_gnews_q=custom_gnews_q,
        selected_rss=configured_rss,
        broad_discovery=broad_discovery,
    )

    trend_geos = GOOGLE_TRENDS_GEOS if broad_discovery else ("IN",)
    if broad_discovery:
        reddit_subreddits = REDDIT_RADAR_SUBREDDITS[:DISCOVERY_MAX_REDDIT_SUBREDDITS_BROAD]
    elif configured_reddit_subreddit:
        reddit_subreddits = (configured_reddit_subreddit,)
    else:
        reddit_subreddits = ("",)

    official_urls = _official_feed_urls(genre_key, genre_cfg)
    core_job_count = len(google_queries) + bool(selected_rss) + bool(official_urls)
    core_pool = ThreadPoolExecutor(
        max_workers=max(1, core_job_count),
        thread_name_prefix="discovery-core",
    )
    signal_job_count = len(trend_geos) + len(reddit_subreddits)
    signal_pool = ThreadPoolExecutor(
        max_workers=max(1, signal_job_count),
        thread_name_prefix="discovery-signals",
    )

    google_futures = []
    rss_futures = []
    official_futures = []
    trend_futures = []
    reddit_futures = []

    try:
        google_futures = [
            core_pool.submit(_google_news_search_items, query, genre_key, 60)
            for query in google_queries
        ]
        if selected_rss:
            rss_futures = [
                core_pool.submit(_rss_items, selected_rss, genre_key, "rss", 60)
            ]
        if official_urls:
            official_futures = [
                core_pool.submit(_official_feed_items, genre_key, genre_cfg)
            ]

        trend_futures = [
            signal_pool.submit(_google_trends_items, geo, 10)
            for geo in trend_geos
        ]
        reddit_futures = [
            signal_pool.submit(_reddit_items, genre_key, subreddit, 40)
            for subreddit in reddit_subreddits
        ]

        core_sources = {future: "Google News" for future in google_futures}
        core_sources.update({future: "RSS" for future in rss_futures})
        core_sources.update({future: "official feeds" for future in official_futures})

        print(
            f"   [Discovery] Factual intake: {len(core_sources)} bounded source job(s).",
            flush=True,
        )
        resolved_core = _resolve_discovery_futures(
            core_sources,
            DISCOVERY_SOURCE_WAIT_SECONDS,
        )

        signal_sources = {future: "Google Trends" for future in trend_futures}
        signal_sources.update({future: "Reddit" for future in reddit_futures})
        resolved_signals = _resolve_discovery_futures(
            signal_sources,
            DISCOVERY_SIGNAL_WAIT_SECONDS,
        )

        raw = []
        for future in (*google_futures, *rss_futures, *official_futures):
            raw.extend(resolved_core.get(future) or [])

        social_rows = []
        for future in reddit_futures:
            social_rows.extend(resolved_signals.get(future) or [])
        social_titles = [
            str(row.get("title") or "")
            for row in social_rows
            if isinstance(row, dict) and str(row.get("title") or "").strip()
        ]

        # Google Trends rows contain linked news articles and can be used as factual
        # source records; Reddit remains an audience-interest signal only.
        for future in trend_futures:
            raw.extend(resolved_signals.get(future) or [])

        compacted = []
        seen = set()
        for story in raw:
            if not isinstance(story, dict):
                continue
            key = _canonical_url(_source_url_from_item(story))
            if not key:
                key = "title:" + " ".join(sorted(_tokens(story.get("title", ""))))
            if key in seen:
                continue
            seen.add(key)
            compacted.append(story)

        if len(compacted) < DISCOVERY_MIN_CORE_ARTICLES_FOR_GDELT:
            gdelt_query = (
                str(trend_keyword or "").strip()
                or str(custom_gnews_q or "").strip()
                or str(genre_cfg.get("gnews_q") or "").strip()
                or GOOGLE_NEWS_RADAR_QUERIES[0]
            )
            print(
                f"   [Discovery] Core factual intake is light ({len(compacted)}); using one bounded GDELT fallback.",
                flush=True,
            )
            gdelt_rows = fetch_gdelt_articles(
                gdelt_query,
                timespan="48h",
                max_records=75,
                timeout=3.0,
            )
            for row in gdelt_rows:
                key = _canonical_url(_source_url_from_item(row))
                if not key:
                    key = "title:" + " ".join(sorted(_tokens(row.get("title", ""))))
                if key not in seen:
                    seen.add(key)
                    compacted.append(row)

        for story in compacted:
            story["social_signal_raw"] = _social_signal(story.get("title", ""), social_titles)
            story["trend_bonus"] = min(
                4.0,
                _safe_float(story.get("trend_bonus")) or 0.0,
            )

        events = cluster_news_events(compacted)
        for event in events:
            event["recommended_category"] = _infer_discovery_category(event)

        print(
            f"   [Discovery Funnel] factual intake={len(compacted)} -> distinct events={len(events)}; signals handled separately.",
            flush=True,
        )
        return events, social_titles
    finally:
        core_pool.shutdown(wait=False, cancel_futures=True)
        signal_pool.shutdown(wait=False, cancel_futures=True)


def rank_story_candidates(stories, conn=None, target_category="", target_format="", target_language="", social_titles=None, ai_cricket=False):
    """Rank an event-first discovery pool through the existing editorial funnel."""
    stories = list(stories or [])
    rows = _load_history(conn)
    used_topics = _load_used_topics(conn)
    social_titles = social_titles or []

    stage60 = _cheap_filter(stories, max_items=60, max_age_hours=72)
    stage50 = _recent_topic_cooldown(conn, stage60, hours=72)
    stage30 = _deduplicate_stage(stage50, max_items=30)
    stage15 = _fact_source_stage(stage30, max_items=15)
    stage8 = _originality_stage(stage15, used_topics, max_items=8)
    ranked = [_editorial_score(item, rows, target_category, target_format, target_language, social_titles, ai_cricket) for item in stage8]
    ranked.sort(key=lambda item: _safe_float(item.get("candidate_score")) or -9999.0, reverse=True)
    ranked = [item for item in ranked if _candidate_quality_pass(item)]

    for story in ranked:
        story["discovery_reason"] = _candidate_reason(story)

    print(
        "   [Discovery Funnel] %d -> %d -> %d -> %d -> %d -> ranked top %d"
        % (len(stories), len(stage60), len(stage50), len(stage30), len(stage15), min(3, len(ranked))),
        flush=True,
    )
    return ranked[:3]


def rank_discovery_candidates(
    stories,
    conn=None,
    target_category="",
    target_format="",
    target_language="",
    social_titles=None,
    ai_cricket=False,
    max_candidates=28,
):
    """Build the dashboard pool: score broadly, then rerank for diversity."""
    stories = list(stories or [])
    rows = _load_history(conn)
    used_topics = _load_used_topics(conn)
    social_titles = social_titles or []

    stage120 = _cheap_filter(stories, max_items=120, max_age_hours=72)
    stage100 = _recent_topic_cooldown(conn, stage120, hours=36)
    stage80 = _deduplicate_stage(stage100, max_items=80)
    stage60 = [
        item for item in stage80
        if _discovery_source_pass(item)
    ]
    stage50 = _originality_stage(stage60, used_topics, max_items=60)

    ranked = [
        _editorial_score(
            item,
            rows,
            target_category,
            target_format,
            target_language,
            social_titles,
            ai_cricket,
        )
        for item in stage50
    ]
    ranked.sort(
        key=lambda item: _safe_float(item.get("candidate_score")) or -9999.0,
        reverse=True,
    )
    ranked = [item for item in ranked if _discovery_portfolio_pass(item)]

    selected = diversity_rerank(ranked, max_items=max_candidates)
    for story in selected:
        story["discovery_reason"] = _candidate_reason(story)

    print(
        "   [Discovery Portfolio] %d -> %d -> %d -> %d -> %d -> %d scored -> %d diverse dashboard stories"
        % (
            len(stories),
            len(stage120),
            len(stage100),
            len(stage80),
            len(stage60),
            len(stage50),
            len(selected),
        ),
        flush=True,
    )
    return selected


def patch_story_selection(bot):
    """Bind production story selection to the canonical event-first discovery funnel."""
    if getattr(bot, "_story_selection_patch_installed", False):
        return bot

    def gather(conn, genre_key, genre_cfg, trend_keyword=None, custom_gnews_q=None, custom_rss_url=None):
        config = getattr(bot, "_active_web_config", {}) or {}
        requested_topic = str(config.get("requested_topic", "") or "").strip()
        ai_cricket = (
            genre_key == "sports_stories_of_day"
            and str(config.get("cricket_category", "")) == "AI-assisted top story in cricket"
        )

        events, social_titles = collect_high_recall_stories(
            bot,
            genre_key,
            genre_cfg,
            trend_keyword,
            custom_gnews_q,
            custom_rss_url,
        )

        relevant = []
        for candidate in events:
            if not _cricket_relevance_pass(candidate, genre_key):
                continue
            if not _requested_topic_pass(candidate, requested_topic):
                continue
            relevant.append(candidate)

        if requested_topic or genre_key == "sports_stories_of_day":
            print(
                f"   [Discovery Relevance] {len(events)} event candidates -> {len(relevant)} topic/category-relevant candidates",
                flush=True,
            )

        return rank_story_candidates(
            relevant,
            conn=conn,
            target_category=config.get("category", genre_key or ""),
            target_format=config.get("format_mode", "regular"),
            target_language=config.get("language", ""),
            social_titles=social_titles,
            ai_cricket=ai_cricket,
        )

    gather._story_selection_patch = True
    bot.gather_and_filter_stories = gather
    bot._story_selection_patch_installed = True
    return bot
