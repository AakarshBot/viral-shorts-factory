"""High-recall discovery funnel and historical story ranking for the Shorts newsroom."""
from __future__ import annotations

import math
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from urllib.parse import urlparse

import requests

from event_discovery_runtime import discover_event_pool, cluster_news_events


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
    """Parse the date formats used by GNews, RSS, Reddit and event evidence."""
    for key in ("published_at", "publishedAt", "published", "pub_date", "date", "timestamp"):
        raw = story.get(key)
        if raw in (None, ""):
            continue
        if isinstance(raw, (int, float)):
            try:
                return datetime.fromtimestamp(float(raw), tz=timezone.utc)
            except (TypeError, ValueError, OSError, OverflowError):
                continue
        text = str(raw).strip()
        if not text:
            continue
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass
        try:
            parsed = parsedate_to_datetime(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError):
            continue
    return None


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


def _cheap_filter(stories, max_items=30, max_age_hours=48):
    """Apply cheap eligibility checks to the full intake before truncating."""
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
            _safe_float(item.get("event_corroboration_score")) or 0.0,
            _freshness_score(item),
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
        if overlap >= 0.55:
            story["discovery_rejection"] = "Previously covered topic/angle"
            continue
        title_overlap = max((_topic_overlap(story.get("title", ""), old.get("title", "")) for old in selected), default=0.0)
        if title_overlap >= 0.58:
            continue
        story["originality_score"] = round(max(0.0, 10.0 - overlap * 12.0 - title_overlap * 6.0), 2)
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
    history, history_matches = _historical_score(story, rows, target_category, target_format, target_language)
    niche = _apply_sports_niche_bonus(story, target_category)
    originality = _safe_float(story.get("originality_score")) or 5.0
    event_momentum = _event_momentum_score(story)
    independent_corroboration = _independent_corroboration_score(story)

    momentum = velocity + trend
    momentum_weight = 1.45 if ai_cricket else 1.25
    final_score = (
        momentum * momentum_weight
        + event_momentum * 0.85
        + freshness * 1.35
        + corroboration * 1.15
        + independent_corroboration * 0.55
        + article_support * 0.80
        + source_quality * 0.85
        + visual * 0.60
        + originality * 1.00
        + social * 1.15
        + google_trend * 1.20
        + niche
        + min(8.0, history * 0.08)
        - risk * 2.25
    )
    story["candidate_score"] = round(final_score, 3)
    story["event_momentum_score"] = event_momentum
    story["independent_corroboration_score"] = independent_corroboration
    story["historical_topic_signal"] = round(history, 3)
    story["historical_topic_matches"] = history_matches
    story["freshness_score"] = round(freshness, 2)
    story["visual_potential"] = round(visual, 2)
    story["risk_signal_count"] = risk
    story["social_signal"] = round(social, 2)
    story["google_trends_signal"] = round(google_trend, 2)
    story["sports_niche_bonus"] = niche
    story["discovery_dimensions"] = {
        "momentum": round(momentum, 2),
        "event_momentum": round(event_momentum, 2),
        "freshness": round(freshness, 2),
        "corroboration": round(corroboration, 2),
        "independent_corroboration": round(independent_corroboration, 2),
        "article_support": round(article_support, 2),
        "source_quality": round(source_quality, 2),
        "social_signal": round(social, 2),
        "google_trends": round(google_trend, 2),
        "channel_history": round(min(10.0, history * 0.10), 2),
        "originality": round(originality, 2),
        "visual_potential": round(visual, 2),
        "safety_risk": risk,
    }
    return story


def _candidate_reason(story):
    dimensions = story.get("discovery_dimensions") or {}
    parts = []
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


def collect_high_recall_stories(bot, genre_key, genre_cfg, trend_keyword=None, custom_gnews_q=None, custom_rss_url=None, ai_cricket=False):
    """Collect a broad article pool, then collapse it into distinct event candidates."""
    api_key = str(os.getenv("GNEWS_API_KEY") or getattr(bot, "GNEWS_API_KEY", "") or "").strip()
    base_query = trend_keyword or custom_gnews_q or genre_cfg.get("gnews_q", "")
    raw = []

    # Dashboard discovery is the authoritative intake path. The patched
    # bot.gather_and_filter_stories performs its own intake, so calling it
    # here would repeat the same network work a second time.
    if base_query:
        raw.extend(_gnews_items(base_query, api_key, genre_key))

    raw.extend(_rss_items(custom_rss_url or genre_cfg.get("rss_url", ""), genre_key))
    raw.extend(_official_feed_items(genre_key, genre_cfg))
    social_rows = _reddit_items(genre_key)
    social_titles = [row.get("title", "") for row in social_rows]
    raw.extend(social_rows)

    for story in raw:
        story["social_signal_raw"] = _social_signal(story.get("title", ""), social_titles)

    compact = []
    seen = set()
    for story in raw:
        if not isinstance(story, dict):
            continue
        key = _canonical_url(_source_url_from_item(story)) or "title:" + " ".join(sorted(_tokens(story.get("title", ""))))
        if not key or key in seen:
            continue
        seen.add(key)
        compact.append(story)

    event_pool = discover_event_pool(
        query=base_query,
        existing_articles=compact,
        timespan="48h",
        max_gdelt_records=75,
    )
    events = event_pool["events"]
    print(
        f"   [Discovery Funnel] article intake={event_pool['article_count']} "
        f"(GDELT={event_pool['gdelt_article_count']}) -> "
        f"distinct events={event_pool['event_count']}; "
        f"news + RSS + public social signals retained.",
        flush=True,
    )
    return events, social_titles


def rank_story_candidates(stories, conn=None, target_category="", target_format="", target_language="", social_titles=None, ai_cricket=False):
    """Rank an event-first discovery pool through the existing editorial funnel."""
    stories = list(stories or [])
    rows = _load_history(conn)
    used_topics = _load_used_topics(conn)
    social_titles = social_titles or []

    stage30 = _cheap_filter(stories, max_items=30, max_age_hours=24 if ai_cricket else 48)
    stage15 = _deduplicate_stage(stage30, max_items=15)
    stage8 = _fact_source_stage(stage15, max_items=8)
    stage5 = _originality_stage(stage8, used_topics, max_items=5)
    ranked = [_editorial_score(item, rows, target_category, target_format, target_language, social_titles, ai_cricket) for item in stage5]
    ranked.sort(key=lambda item: _safe_float(item.get("candidate_score")) or -9999.0, reverse=True)

    for story in ranked:
        story["discovery_reason"] = _candidate_reason(story)

    print(
        "   [Discovery Funnel] %d -> %d -> %d -> %d -> %d -> ranked top %d"
        % (len(stories), len(stage30), len(stage15), len(stage8), len(stage5), min(3, len(ranked))),
        flush=True,
    )
    return ranked[:3]


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
