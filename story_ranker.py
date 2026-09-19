"""High-recall discovery funnel and historical story ranking for the Shorts newsroom."""
from __future__ import annotations

import math
import os
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from urllib.parse import urlparse

import requests

from event_discovery_runtime import discover_event_pool, cluster_news_events, _event_actions


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

# A temporary network problem should not make every GNews query wait for the
# full timeout. After one connection-level failure, discovery skips additional
# GNews attempts for a short cooldown and relies on RSS/social intake instead.
_GNEWS_COOLDOWN_SECONDS = 90.0
_GNEWS_FAILURE_UNTIL = 0.0
_GNEWS_FAILURE_LOGGED = False


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
    return max(candidates) if candidates else None


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
    source = _clean(story.get("source") or story.get("publisher") or story.get("source_name"))
    domain = _source_domain(story)
    blob = f"{source} {domain} {_text_blob(story)}"
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
            "WHERE topic IS NOT NULL AND topic != ''"
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


@lru_cache(maxsize=1)
def _india_trend_terms():
    try:
        from pytrends.request import TrendReq
        pytrends = TrendReq(hl="en-IN", tz=330)
        values = pytrends.trending_searches(pn="india")
        return tuple(_clean(item) for item in values[0].tolist()[:30] if item)
    except Exception:
        return tuple()


def _trend_signal(title):
    title_text = _clean(title)
    if not title_text:
        return 0.0
    terms = _india_trend_terms()
    return min(4.0, sum(1.0 for term in terms if term and term in title_text))


def _discovery_query_lanes(base_query, genre_key="", ai_cricket=False):
    """Create a small set of intentional discovery lenses without query fanout."""
    base = str(base_query or "").strip()
    if not base:
        return []
    key = _clean(genre_key)
    if ai_cricket or key == "sports_stories_of_day":
        lenses = [
            f"({base}) AND (latest OR today OR breaking)",
            f"({base}) AND (record OR result OR squad OR selection OR injury OR announcement)",
            f"({base}) AND (match OR series OR tournament OR player)",
        ]
    elif key in {"technology", "tech_reviews"}:
        lenses = [
            f"({base}) AND (latest OR today OR breaking)",
            f"({base}) AND (launch OR release OR update OR reveal)",
            f"({base}) AND (AI OR chip OR smartphone OR startup OR gadget)",
        ]
    elif key == "business_finance":
        lenses = [
            f"({base}) AND (latest OR today OR breaking)",
            f"({base}) AND (earnings OR deal OR funding OR market OR acquisition)",
            f"({base}) AND (India OR global)",
        ]
    elif key == "entertainment":
        lenses = [
            f"({base}) AND (latest OR today OR breaking)",
            f"({base}) AND (release OR trailer OR box office OR casting OR announcement)",
            f"({base}) AND (film OR series OR celebrity OR music)",
        ]
    elif key == "health_lifestyle":
        lenses = [
            f"({base}) AND (latest OR today OR breaking)",
            f"({base}) AND (study OR research OR approval OR warning OR discovery)",
            f"({base}) AND (health OR fitness OR nutrition OR wellness)",
        ]
    elif key == "viral_phenomenon":
        lenses = [
            f"({base}) AND (latest OR today OR trending)",
            f"({base}) AND (viral OR internet OR social media OR video)",
            f"({base}) AND (explained OR reaction OR controversy)",
        ]
    else:
        lenses = [
            f"({base}) AND (latest OR today OR breaking)",
            f"({base}) AND (announcement OR decision OR result OR update)",
            f"({base}) AND (India OR world OR global)",
        ]
    output = [base]
    for query in lenses:
        if query not in output:
            output.append(query)
    return output[:4]


def _adaptive_discovery_query(base_query, social_titles):
    """Build at most one supplemental query from public-interest novelty."""
    base_tokens = _tokens(base_query)
    if not social_titles:
        return ""

    frequency = {}
    for title in social_titles[:50]:
        for token in _tokens(title):
            if token in base_tokens or token in {"india", "indian", "world", "news", "reddit"}:
                continue
            frequency[token] = frequency.get(token, 0) + 1

    candidates = sorted(
        frequency.items(),
        key=lambda item: (-item[1], -len(item[0]), item[0]),
    )
    selected = [token for token, count in candidates if count >= 2][:3]
    if not selected:
        return ""

    query = " ".join(selected)
    if _topic_overlap(query, base_query) >= 0.50:
        return ""
    return query


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


def _gnews_items(query, api_key, genre_key):
    global _GNEWS_FAILURE_UNTIL, _GNEWS_FAILURE_LOGGED

    if not api_key:
        return []
    now = time.time()
    if now < _GNEWS_FAILURE_UNTIL:
        return []

    try:
        response = requests.get(
            "https://api.gnews.io/api/v4/search",
            params={
                "q": query,
                "lang": "en",
                "country": "in",
                "max": 20,
                "apikey": api_key,
            },
            timeout=8,
        )
        if response.status_code != 200:
            if response.status_code in {401, 403, 429, 500, 502, 503, 504}:
                _GNEWS_FAILURE_UNTIL = time.time() + _GNEWS_COOLDOWN_SECONDS
                if not _GNEWS_FAILURE_LOGGED:
                    print(
                        f"   [Discovery] GNews intake unavailable (HTTP {response.status_code}); using RSS/social fallback for this run.",
                        flush=True,
                    )
                    _GNEWS_FAILURE_LOGGED = True
            return []
        _GNEWS_FAILURE_UNTIL = 0.0
        _GNEWS_FAILURE_LOGGED = False
        output = []
        for article in response.json().get("articles", []):
            title = str(article.get("title") or "").strip()
            url = str(article.get("url") or "").strip()
            if not title or not url:
                continue
            source = article.get("source") or {}
            output.append({
                "title": title,
                "text": article.get("description") or article.get("content") or "",
                "description": article.get("description") or "",
                "source": source.get("name") or "GNews",
                "source_name": source.get("name") or "GNews",
                "url": url,
                "publishedAt": article.get("publishedAt"),
                "genre": genre_key,
                "collection_source": "gnews",
            })
        return output
    except (requests.ConnectionError, requests.Timeout) as exc:
        _GNEWS_FAILURE_UNTIL = time.time() + _GNEWS_COOLDOWN_SECONDS
        if not _GNEWS_FAILURE_LOGGED:
            print(
                f"   [Discovery] GNews network intake unavailable ({type(exc).__name__}); using RSS/social fallback for this run.",
                flush=True,
            )
            _GNEWS_FAILURE_LOGGED = True
        return []
    except Exception as exc:
        print(f"   [Discovery] GNews intake failed for query '{query[:60]}': {type(exc).__name__}", flush=True)
        return []


def _rss_items(url, genre_key, collection_source="rss"):
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
        for item in root.findall(".//item"):
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
    items = []
    for url in _official_feed_urls(genre_key, genre_cfg):
        items.extend(_rss_items(url, genre_key, collection_source="official"))
    return items


def _reddit_items(genre_key):
    subreddits = {
        "sports": "sports",
        "sports_stories_of_day": "sports",
        "technology": "technology",
        "business_finance": "business",
        "entertainment": "movies",
        "viral_phenomenon": "popular",
        "national_global_affairs": "worldnews",
    }
    subreddit = subreddits.get(genre_key, "popular")
    url = f"https://www.reddit.com/r/{subreddit}/hot.json?limit=50"
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
        if story.get("event_clustered"):
            selected.append(story)
            story["dedupe_pass"] = True
        else:
            title = story.get("title", "")
            if any(_topic_overlap(title, old.get("title", "")) >= 0.82 for old in selected):
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
    source_quality = _safe_float(story.get("source_quality_score")) or 0.0
    event_text = story.get("event_search_text") or story.get("title", "")
    social = _social_signal(event_text, social_titles)
    google_trend = _trend_signal(event_text)
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

    momentum = min(10.0, velocity + trend)
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
    momentum_weight = 1.08 if ai_cricket else 1.0

    # candidate_score remains a single ranking score, but its components are
    # now explicitly separated so audience interest does not masquerade as
    # factual importance and correlated coverage signals are capped.
    final_score = (
        importance * 1.55
        + audience * 1.35 * momentum_weight
        + shorts_viability * 1.20
        + originality * 0.45
        + visual * 0.20
        + channel_history * 0.70
        + niche * 0.20
        - risk * 0.55
    )
    story["candidate_score"] = round(final_score, 3)
    story["event_momentum_score"] = event_momentum
    story["independent_corroboration_score"] = independent_corroboration
    story["historical_topic_signal"] = round(history, 3)
    story["historical_topic_matches"] = history_matches
    story["freshness_score"] = round(freshness, 2)
    story["visual_potential"] = round(visual, 2)
    story["shorts_viability_score"] = round(shorts_viability, 2)
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
        "originality": round(originality, 2),
        "visual_potential": round(visual, 2),
        "safety_risk": risk,
    }
    return story


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



def collect_high_recall_stories(bot, genre_key, genre_cfg, trend_keyword=None, custom_gnews_q=None, custom_rss_url=None, ai_cricket=False, discover_lanes=None):
    """Collect a broad article pool, then collapse it into distinct event candidates.\n\n    ``discover_lanes`` is a legacy compatibility keyword. The current\n    collector uses one canonical intake path, so the value is intentionally\n    ignored; accepting it prevents stale dashboard runtimes from crashing\n    during rolling deployments.\n    """
    api_key = str(os.getenv("GNEWS_API_KEY") or getattr(bot, "GNEWS_API_KEY", "") or "").strip()
    base_query = trend_keyword or custom_gnews_q or genre_cfg.get("gnews_q", "")
    raw = []

    # These providers are independent. Fetch them concurrently so one slow
    # source does not make the entire intake run serially.
    rss_url = custom_rss_url or genre_cfg.get("rss_url", "")
    query_lanes = _discovery_query_lanes(base_query, genre_key=genre_key, ai_cricket=ai_cricket)
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="discovery-intake") as pool:
        gnews_futures = [
            pool.submit(_gnews_items, query, api_key, genre_key)
            for query in query_lanes
        ]
        rss_future = pool.submit(_rss_items, rss_url, genre_key)
        official_future = pool.submit(_official_feed_items, genre_key, genre_cfg)
        reddit_future = pool.submit(_reddit_items, genre_key)

        for future in gnews_futures:
            raw.extend(future.result())
        raw.extend(rss_future.result())
        raw.extend(official_future.result())
        social_rows = reddit_future.result()

    social_titles = [row.get("title", "") for row in social_rows]
    raw.extend(social_rows)

    # One bounded adaptive lane: if public-interest signals surface a
    # genuinely new vocabulary not covered by the base query, let GNews
    # explore that vocabulary once. This improves recall without restoring
    # blind multi-query fanout.
    adaptive_query = _adaptive_discovery_query(base_query, social_titles)
    if adaptive_query and api_key and adaptive_query != _clean(base_query):
        adaptive_rows = _gnews_items(adaptive_query, api_key, genre_key)
        if adaptive_rows:
            raw.extend(adaptive_rows)
            social_titles.extend(
                row.get("title", "")
                for row in adaptive_rows[:8]
                if row.get("title")
            )

    for story in raw:
        story["social_signal_raw"] = _social_signal(story.get("title", ""), social_titles)

    def compact_items(items):
        compacted = []
        seen = set()
        for story in items:
            if not isinstance(story, dict):
                continue
            key = _canonical_url(_source_url_from_item(story)) or "title:" + " ".join(sorted(_tokens(story.get("title", ""))))
            if not key or key in seen:
                continue
            seen.add(key)
            compacted.append(story)
        return compacted

    compact = compact_items(raw)
    event_pool = discover_event_pool(
        query=base_query,
        existing_articles=compact,
        timespan="48h",
        max_gdelt_records=75,
    )
    events = event_pool["events"]

    # GDELT is one bounded supplemental source. Do not fan out into
    # multiple query lanes: the base intake already combines GNews, RSS,
    # official feeds and public social signals before event clustering.
    raw = list(event_pool.get("articles") or raw)
    initial_gdelt_count = int(event_pool.get("gdelt_article_count") or 0)
    print(
        f"   [Discovery Funnel] article intake={event_pool['article_count']} "
        f"(GDELT={initial_gdelt_count}) -> "
        f"distinct events={event_pool['event_count']}; "
        f"bounded adaptive intake complete.",
        flush=True,
    )
    return events, social_titles


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
    stage100 = _recent_topic_cooldown(conn, stage120, hours=72)
    stage80 = _deduplicate_stage(stage100, max_items=80)
    stage60 = _fact_source_stage(stage80, max_items=60)
    stage50 = _originality_stage(stage60, used_topics, max_items=50)

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
        for item in stage40
    ]
    ranked.sort(
        key=lambda item: _safe_float(item.get("candidate_score")) or -9999.0,
        reverse=True,
    )

    selected = diversity_rerank(ranked, max_items=max_candidates)
    for story in selected:
        story["discovery_reason"] = _candidate_reason(story)

    print(
        "   [Discovery Portfolio] %d -> %d -> %d -> %d -> %d scored -> %d diverse dashboard stories"
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
            ai_cricket=ai_cricket,
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
