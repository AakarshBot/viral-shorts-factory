"""Sports-first cricket topic desk with deliberate news/viral/social buckets."""

from __future__ import annotations

import math
import re
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from urllib.parse import urlparse

import requests

import story_ranker as sr
from event_discovery_runtime import fetch_gdelt_articles

SPORTS_DESK_VERSION = "cricket-desk-v2-2026-09-23"
LOOKBACK_HOURS = 72
DESK_TIMEOUT = 7.0
REQUEST_TIMEOUT = 2.5
PER_BUCKET = 10
SOURCE_TIMEOUT = 2.2

DIRECT_CRICKET_SOURCES = (
    ("ICC", "https://www.icc-cricket.com/index?feed=rss2", "rss"),
    ("BCCI", "https://www.bcci.tv/news", "listing"),
    ("Cricbuzz", "https://www.cricbuzz.com/cricket-news/latest-news", "listing"),
    ("Wisden", "https://www.wisden.com/cricket-news", "listing"),
    ("ESPNcricinfo", "https://www.espncricinfo.com/cricket-news", "listing"),
)

NEWS_QUERIES = (
    'cricket (India OR BCCI OR ICC) (selection OR injury OR retirement OR appointment OR ban OR investigation OR contract OR debut)',
    'cricket (India OR Pakistan OR Sri Lanka OR Bangladesh) (record OR milestone OR comeback OR upset OR breakthrough OR controversy)',
    'cricket (Australia OR England OR South Africa OR New Zealand OR West Indies) (record OR selection OR injury OR statement OR controversy)',
    'cricket (women OR "India Women" OR WPL OR U19 OR U23 OR domestic) (record OR debut OR selection OR upset OR comeback OR milestone)',
    'cricket (umpire OR law OR rule OR decision OR sanction OR fine OR controversy OR investigation)',
    'cricket (coach OR captain OR selector OR board) (said OR says OR warned OR responds OR reveals OR comments)',
)

VIRAL_QUERIES = (
    'cricket (viral OR trending OR bizarre OR unusual OR shocking OR unexpected)',
    'cricket (fans react OR reaction OR backlash OR debate OR praise)',
    'cricket (social media OR meme OR celebration OR moment)',
    'cricket (controversy OR row OR feud OR argument OR clash)',
)

SOCIAL_QUERIES = ("cricket reaction", "cricket fans", "cricket debate")
REDDIT_SUBREDDITS = ("Cricket", "IndiaCricket", "CricketShitpost")
BLUESKY_QUERIES = ("cricket", '"India cricket"', '"Team India"', "cricket reaction", "cricket controversy", "cricket fans")
MASTODON_QUERIES = ("cricket", "India cricket")
TREND_GEOS = ("IN", "GB", "AU", "US")

CRICKET_TERMS = {
    "cricket", "icc", "bcci", "pcb", "wpl", "ipl", "t20", "test", "odi",
    "batter", "bowler", "wicket", "runs", "innings", "overs", "spin", "pace",
    "allrounder", "all-rounder", "keeper", "captain", "selector", "coach",
}
REACTION_TERMS = (
    "said", "says", "called", "responded", "reaction", "reacts", "comment",
    "comments", "praised", "criticised", "criticized", "slammed", "warned",
    "debate", "backlash", "fans", "meme", "viral", "social media", "post",
)
HOOK_TERMS = (
    "record", "first", "fastest", "youngest", "oldest", "debut", "breakthrough",
    "comeback", "upset", "survived", "scare", "controversy", "investigation",
    "sanctioned", "fined", "retired", "ruled out", "recalled", "dropped",
    "statement", "revealed", "reveals", "banned",
)
SATURATION_TERMS = ("world cup", "final", "asia cup", "ashes", "india vs", "india v", "championship", "major final")


def _clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _age_hours(value):
    try:
        parsed = sr._parse_dt(value)
        if parsed is None:
            return 9999.0
        return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds() / 3600.0)
    except Exception:
        return 9999.0


def _is_cricket(item):
    text = _clean(" ".join(str(item.get(k) or "") for k in ("title", "text", "description", "summary", "snippet", "trend_query"))).casefold()
    source_hint = _clean(" ".join(
        str(item.get(k) or "")
        for k in ("source", "source_name", "publisher", "url")
    )).casefold()
    if any(term in text for term in CRICKET_TERMS):
        return True
    if bool(item.get("social_post")) and any(
        marker in source_hint
        for marker in ("r/cricket", "r/indiacricket", "cricketshitpost", "cricket", "bsky")
    ):
        return True
    return False


def _domain(item):
    try:
        return urlparse(_clean(item.get("url") or item.get("link"))).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def _tokens(value):
    return set(sr._tokens(value))


def _similarity(left, right):
    a, b = _tokens(left.get("title")), _tokens(right.get("title"))
    if not a or not b:
        return 0.0
    token_sim = len(a & b) / max(1, len(a | b))
    title_sim = SequenceMatcher(None, _clean(left.get("title")).casefold(), _clean(right.get("title")).casefold()).ratio()
    ea, eb = set(sr._topic_entities(left)), set(sr._topic_entities(right))
    entity_sim = len(ea & eb) / max(1, len(ea | eb))
    return min(1.0, token_sim * 0.45 + title_sim * 0.35 + entity_sim * 0.20)


def _bluesky(query):
    try:
        r = requests.get(
            "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts",
            params={"q": query, "limit": 40, "sort": "latest"},
            headers={"User-Agent": "ViralShortsFactory/2026 cricket-desk"},
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code != 200:
            return []
        posts = r.json().get("posts") or []
    except Exception:
        return []
    out = []
    for post in posts:
        record = post.get("record") or {}
        author = post.get("author") or {}
        text = _clean(record.get("text"))
        if not text:
            continue
        handle = _clean(author.get("handle"))
        uri = _clean(post.get("uri"))
        rkey = uri.rsplit("/", 1)[-1] if uri else ""
        url = f"https://bsky.app/profile/{handle}/post/{rkey}" if handle and rkey else ""
        out.append({
            "title": text[:220], "text": text, "description": text,
            "source": f"Bluesky @{handle}" if handle else "Bluesky",
            "source_name": f"Bluesky @{handle}" if handle else "Bluesky",
            "url": url, "publishedAt": record.get("createdAt") or post.get("indexedAt") or "",
            "collection_source": "bluesky", "social_post": True,
            "social_like": float(post.get("likeCount") or 0),
            "social_reply": float(post.get("replyCount") or 0),
            "social_repost": float(post.get("repostCount") or 0),
            "social_quote": float(post.get("quoteCount") or 0),
        })
    return out


def _reddit_search(subreddit, query):
    try:
        r = requests.get(
            f"https://www.reddit.com/r/{subreddit}/search.json",
            params={"q": query, "restrict_sr": "on", "sort": "new", "t": "day", "limit": 35},
            headers={"User-Agent": "ViralShortsFactory/2026 cricket-desk"},
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code != 200:
            return []
        children = r.json().get("data", {}).get("children", [])
    except Exception:
        return []
    out = []
    for node in children:
        data = node.get("data") if isinstance(node, dict) else {}
        if not isinstance(data, dict):
            continue
        title = _clean(data.get("title"))
        if not title:
            continue
        try:
            published = datetime.fromtimestamp(float(data.get("created_utc") or 0), tz=timezone.utc).isoformat()
        except Exception:
            published = ""
        permalink = _clean(data.get("permalink"))
        out.append({
            "title": title, "text": _clean(data.get("selftext")) or title,
            "description": _clean(data.get("selftext")) or title,
            "source": f"Reddit r/{subreddit}", "source_name": f"Reddit r/{subreddit}",
            "url": f"https://www.reddit.com{permalink}" if permalink else "",
            "publishedAt": published, "collection_source": "reddit", "social_post": True,
            "social_like": float(data.get("score") or 0),
            "social_reply": float(data.get("num_comments") or 0),
        })
    return out


def _mastodon(query):
    try:
        r = requests.get(
            "https://mastodon.social/api/v2/search",
            params={"q": query, "type": "statuses", "limit": 30},
            headers={"User-Agent": "ViralShortsFactory/2026 cricket-desk"},
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code != 200:
            return []
        statuses = r.json().get("statuses") or []
    except Exception:
        return []
    out = []
    for status in statuses:
        text = _clean(re.sub(r"<[^>]+>", " ", _clean(status.get("content"))))
        if not text:
            continue
        out.append({
            "title": text[:220], "text": text, "description": text,
            "source": "Mastodon", "source_name": "Mastodon", "url": _clean(status.get("url")),
            "publishedAt": _clean(status.get("created_at")), "collection_source": "mastodon",
            "social_post": True,
            "social_like": float(status.get("favourites_count") or 0),
            "social_reply": float(status.get("replies_count") or 0),
            "social_repost": float(status.get("reblogs_count") or 0),
        })
    return out


def _relative_age_hours(text):
    value = _clean(text).casefold()
    match = re.search(r"(?<!\d)(\d{1,3})\s*(minute|minutes|min|hour|hours|hr|hrs|day|days|d|h)\s+ago\b", value)
    if not match:
        return None
    amount = float(match.group(1))
    unit = match.group(2)
    if unit.startswith("min"):
        return amount / 60.0
    if unit.startswith("h"):
        return amount
    return amount * 24.0


def _direct_listing_source(name, url):
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 ViralShortsFactory/2026 cricket-desk"},
            timeout=SOURCE_TIMEOUT,
        )
        if response.status_code != 200:
            return []
        page = response.text or ""
    except Exception:
        return []

    allowed_hosts = {
        "ICC": ("icc-cricket.com",),
        "BCCI": ("bcci.tv",),
        "Cricbuzz": ("cricbuzz.com",),
        "Wisden": ("wisden.com",),
        "ESPNcricinfo": ("espncricinfo.com",),
    }.get(name, ())
    out = []
    seen = set()

    for match in re.finditer(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', page, re.IGNORECASE | re.DOTALL):
        href = _clean(match.group(1))
        anchor = _clean(re.sub(r"<[^>]+>", " ", match.group(2)))
        if not href or not anchor or len(anchor) < 12:
            continue
        if href.startswith("/"):
            href = f"https://www.{allowed_hosts[0]}{href}" if allowed_hosts else href
        if not href.startswith(("http://", "https://")):
            continue

        parsed = urlparse(href)
        host = parsed.netloc.lower().removeprefix("www.")
        if allowed_hosts and not any(host == allowed or host.endswith("." + allowed) for allowed in allowed_hosts):
            continue

        path = parsed.path.casefold()
        if name == "BCCI" and "/news/article/" not in path:
            continue
        if name in {"Cricbuzz", "Wisden", "ESPNcricinfo"} and "/cricket-news/" not in path:
            continue
        if name == "ICC" and "/news/" not in path:
            continue

        key = sr._canonical_url(href)
        if not key or key in seen:
            continue

        nearby = _clean(re.sub(
            r"<[^>]+>",
            " ",
            page[max(0, match.start() - 1400):min(len(page), match.end() + 1800)],
        ))
        age = _relative_age_hours(nearby)
        if age is None:
            iso = re.search(r"20\d{2}-\d{2}-\d{2}(?:T[0-9:.+\-Z]+)?", nearby)
            if iso:
                age = _age_hours(iso.group(0))
        if age is None or age > LOOKBACK_HOURS:
            continue

        seen.add(key)
        published = datetime.now(timezone.utc) - timedelta(hours=age)
        out.append({
            "title": anchor[:220],
            "text": nearby[:1000],
            "description": nearby[:1000],
            "source": name,
            "source_name": name,
            "publisher": name,
            "url": href,
            "publishedAt": published.isoformat(),
            "collection_source": "official" if name in {"ICC", "BCCI"} else "specialist_direct",
            "direct_source": name,
        })
        if len(out) >= 35:
            break
    return out


def _collect():
    jobs = {}
    pool = ThreadPoolExecutor(max_workers=20, thread_name_prefix="cricket-topic-desk")
    try:
        for query in NEWS_QUERIES + VIRAL_QUERIES + SOCIAL_QUERIES:
            future = pool.submit(sr._google_news_search_items, query, "sports_stories_of_day", 25)
            jobs[future] = "Google News"

        for name, url, kind in DIRECT_CRICKET_SOURCES:
            if kind == "rss":
                future = pool.submit(sr._rss_items, url, "sports_stories_of_day", "official", 35)
            else:
                future = pool.submit(_direct_listing_source, name, url)
            jobs[future] = name

        for subreddit in REDDIT_SUBREDDITS:
            future = pool.submit(_reddit_search, subreddit, "cricket")
            jobs[future] = f"Reddit r/{subreddit}"

        for query in BLUESKY_QUERIES[:4]:
            future = pool.submit(_bluesky, query)
            jobs[future] = "Bluesky"

        for query in MASTODON_QUERIES:
            future = pool.submit(_mastodon, query)
            jobs[future] = "Mastodon"

        for geo in TREND_GEOS:
            future = pool.submit(sr._google_trends_items, geo, 20)
            jobs[future] = f"Google Trends {geo}"

        jobs[pool.submit(
            fetch_gdelt_articles,
            "cricket India selection injury record controversy quotes",
            timespan="72h",
            max_records=60,
            timeout=1.5,
        )] = "GDELT India"
        jobs[pool.submit(
            fetch_gdelt_articles,
            "cricket women domestic associate social reaction",
            timespan="72h",
            max_records=60,
            timeout=1.5,
        )] = "GDELT wider"

        done, pending = wait(list(jobs), timeout=DESK_TIMEOUT)
        rows = []
        counts = {}
        failures = []

        for future in done:
            label = jobs[future]
            try:
                values = future.result() or []
                rows.extend(values)
                counts[label] = counts.get(label, 0) + len(values)
            except Exception as exc:
                failures.append(f"{label}:{type(exc).__name__}")

        for future in pending:
            label = jobs[future]
            future.cancel()
            failures.append(f"{label}:timeout")
            counts.setdefault(label, 0)

        compact_counts = ", ".join(
            f"{label}={count}" for label, count in sorted(counts.items())
        )
        print(
            f"   [Cricket Desk] Raw source intake: {compact_counts or 'none'}",
            flush=True,
        )
        if failures:
            print(
                f"   [Cricket Desk] Source issues: {', '.join(failures[:12])}",
                flush=True,
            )
        return rows
    finally:
        for future in jobs:
            future.cancel()
        pool.shutdown(wait=False, cancel_futures=True)


def _normalise_rows(rows):
    output, seen = [], set()
    for raw in rows:
        if not isinstance(raw, dict) or not _is_cricket(raw):
            continue
        item = dict(raw)
        age = _age_hours(item.get("publishedAt") or item.get("created_at"))
        if age == 9999.0 or age > LOOKBACK_HOURS:
            continue
        safe, _ = sr._safety_gate(item)
        if not safe:
            continue
        if not item.get("social_post"):
            if not sr._source_page_pass(item) or not sr._headline_noise_pass(item):
                continue
            if not sr._cricket_service_title_pass(item):
                continue
            if item.get("collection_source") not in {"official", "specialist_direct"}:
                if not sr._story_substance_pass(item, minimum_body_chars=90):
                    continue
        url = _clean(item.get("url") or item.get("link"))
        canonical = sr._canonical_url(url)
        key = canonical or ("title:" + _clean(item.get("title")).casefold())
        if key in seen:
            continue
        seen.add(key)
        item["title"] = _clean(item.get("title"))
        item["source_domain"] = _domain(item)
        item["age_hours"] = round(age, 2)
        item["social_engagement"] = math.log1p(sum(float(item.get(k) or 0) for k in ("social_like", "social_reply", "social_repost", "social_quote")))
        output.append(item)
    return output


def _cluster(rows):
    clusters = []
    for row in sorted(rows, key=lambda item: float(item.get("age_hours") or 9999)):
        match = None
        best = 0.0
        for cluster in clusters:
            candidate = cluster["representative"]
            if abs(float(row.get("age_hours") or 9999) - float(candidate.get("age_hours") or 9999)) > 30:
                continue
            sim = _similarity(row, candidate)
            if sim > best:
                best, match = sim, cluster
        if match is not None and best >= 0.60:
            match["rows"].append(row)
        else:
            clusters.append({"representative": row, "rows": [row]})

    concepts = []
    for n, cluster in enumerate(clusters, 1):
        rows = cluster["rows"]
        articles = [x for x in rows if not x.get("social_post")]
        social = [x for x in rows if x.get("social_post")]
        representative = max(
            rows,
            key=lambda x: (0 if x.get("social_post") else 1, -float(x.get("age_hours") or 9999), float(x.get("social_engagement") or 0)),
        )
        domains = {_clean(x.get("source_domain") or x.get("source_name")) for x in articles if _clean(x.get("source_domain") or x.get("source_name"))}
        concepts.append({
            **representative,
            "cluster_id": f"cricket-{n}",
            "article_count": len(articles),
            "social_items": social,
            "article_items": articles,
            "article_count": len(articles),
            "social_post_count": len(social),
            "independent_source_count": len(domains),
            "social_engagement_total": round(sum(float(x.get("social_engagement") or 0) for x in social), 3),
        })
    return concepts


def _trend_signal(item, trend_rows):
    title_tokens = _tokens(item.get("title"))
    best = 0.0
    for trend in trend_rows:
        trend_text = _clean(f"{trend.get('trend_query', '')} {trend.get('title', '')}")
        if not _is_cricket({"title": trend_text}):
            continue
        t = _tokens(trend_text)
        if not t:
            continue
        overlap = len(title_tokens & t) / max(1, len(title_tokens | t))
        if overlap >= 0.30:
            best = max(best, float(trend.get("trend_bonus") or 0))
    return min(6.0, best * 1.25)



def _history_titles(conn, limit=500) -> list[str]:
    """Read previously used cricket topics/titles so novelty includes factory history."""
    if conn is None:
        return []
    try:
        rows = conn.execute(
            """
            SELECT topic, title_used
            FROM vault
            WHERE status NOT IN ('FAILED', 'REJECTED', 'PENDING_QC', 'RUNNING',
                                 'WAITING_SCRIPT_REVIEW', 'WAITING_VISUAL_REVIEW', 'READY_FOR_UPLOAD')
            ORDER BY COALESCE(updated_at, created_at) DESC, id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    except Exception:
        return []
    titles = []
    for topic, title in rows:
        for value in (title, topic):
            clean = _clean(value)
            if clean and clean.casefold() not in {x.casefold() for x in titles}:
                titles.append(clean)
    return titles


def _history_penalty(item, history_titles, retained_candidates):
    current = _clean(item.get("title")).casefold()
    if not current:
        return 0.0
    comparisons = []
    comparisons.extend(history_titles or [])
    comparisons.extend(
        _clean(row.get("title"))
        for row in (retained_candidates or [])
        if isinstance(row, dict)
    )
    best = max(
        (
            SequenceMatcher(None, current, value.casefold()).ratio()
            for value in comparisons
            if value
        ),
        default=0.0,
    )
    return min(6.0, best * 6.0)


def _score(item, trends, history_titles=None, retained=None):
    text = _clean(" ".join(str(item.get(k) or "") for k in ("title", "text", "description"))).casefold()
    reaction_hits = sum(1 for term in REACTION_TERMS if term in text)
    hook_hits = sum(1 for term in HOOK_TERMS if term in text)
    major_hits = sum(1 for term in SATURATION_TERMS if term in text)
    coverage = min(10.0, int(item.get("independent_source_count") or 0) * 1.6 + min(4.0, int(item.get("article_count") or 0) * 0.3))
    social = min(10.0, float(item.get("social_engagement_total") or 0) * 1.4 + min(5.0, int(item.get("social_post_count") or 0) * 0.7))
    trend = _trend_signal(item, trends)
    undercovered = max(0.0, min(10.0, 10.0 - coverage + social * 0.5 + (3.0 if int(item.get("independent_source_count") or 0) <= 2 else 0)))
    freshness = max(0.0, min(10.0, 10.0 - float(item.get("age_hours") or 72) / 7.2))
    saturation = min(8.0, major_hits + max(0, int(item.get("independent_source_count") or 0) - 3) * 0.8)
    history_penalty = _history_penalty(item, history_titles or [], retained or [])
    news = freshness * 0.35 + coverage * 1.0 + hook_hits * 0.6 + reaction_hits * 0.2 - saturation * 0.6 - history_penalty
    viral = trend * 1.6 + social * 1.3 + undercovered * 1.35 + hook_hits * 0.6 + freshness * 0.5 - saturation * 0.9 - history_penalty
    social_score = social * 1.8 + reaction_hits * 1.4 + trend * 0.8 + undercovered * 1.2 + freshness * 0.4 - saturation * 0.5 - history_penalty
    item.update({
        "coverage_score": round(coverage, 2),
        "undercovered_score": round(undercovered, 2),
        "trend_signal_score": round(trend, 2),
        "social_signal_score": round(social, 2),
        "viral_signal_score": round(max(0.0, viral / 2.0), 2),
        "news_score": round(news, 2),
        "viral_score": round(viral, 2),
        "social_score": round(social_score, 2),
    })
    return item


def _select(pool, count, score_name, chosen):
    selected = []
    remaining = list(pool)
    while remaining and len(selected) < count:
        best = max(
            remaining,
            key=lambda x: float(x.get(score_name) or 0)
              - max((_similarity(x, old) for old in chosen + selected), default=0) * 8.0
              + (1.5 if float(x.get("undercovered_score") or 0) >= 7 else 0),
        )
        selected.append(best)
        remaining.remove(best)
    return selected


def _bucketize(concepts):
    remaining = list(concepts)
    result = []
    for bucket, score_name in (("news","news_score"),("viral","viral_score"),("social","social_score")):
        picked = _select(remaining, PER_BUCKET, score_name, [])
        for item in picked:
            remaining.remove(item)
            row = dict(item)
            if bucket == "social" and row.get("social_post_count"):
                social_titles = [
                    _clean(part.get("title"))
                    for part in row.get("social_items", [])
                    if isinstance(part, dict) and _clean(part.get("title"))
                ]
                if social_titles:
                    row["event_anchor_title"] = row.get("title")
                    row["title"] = max(
                        social_titles,
                        key=lambda value: len(value),
                    )
            row["discovery_bucket"] = bucket
            row["bucket_score"] = float(row.get(score_name) or 0)
            result.append(row)
    for bucket, score_name in (("news","news_score"),("viral","viral_score"),("social","social_score")):
        bucket_rows = [x for x in result if x.get("discovery_bucket")==bucket]
        need = PER_BUCKET - len(bucket_rows)
        if need:
            for item in sorted(remaining, key=lambda x: float(x.get(score_name) or 0), reverse=True)[:need]:
                remaining.remove(item)
                row = dict(item)
                row["discovery_bucket"] = bucket
                row["cross_bucket_backfill"] = True
                row["bucket_score"] = float(row.get(score_name) or 0)
                result.append(row)
    return result


def discover_cricket_topics(bot, conn=None, scope="India / Asia", requested_topic="", max_candidates=30, retained_candidates=None):
    raw = _collect()
    if isinstance(raw, dict):
        raw = raw.get("rows") or []
    trend_rows = [row for row in raw if isinstance(row, dict) and row.get("collection_source") == "google_trends"]
    rows = _normalise_rows([row for row in raw if not (isinstance(row, dict) and row.get("collection_source") == "google_trends")])
    scope_key = _clean(scope).casefold()
    if scope_key == "india / asia":
        rows = [
            x for x in rows
            if x.get("social_post")
            or any(term in _clean(" ".join(str(x.get(k) or "") for k in ("title","text","description"))).casefold() for term in ("india","bcci","pakistan","sri lanka","bangladesh","asia"))
        ]
    concepts = _cluster(rows)
    articles = []
    social = []
    for item in concepts:
        if item.get("social_post_count"):
            social.append(item)
        else:
            articles.append(item)
    history_titles = _history_titles(conn)
    scored = [_score(dict(x), trend_rows, history_titles, retained_candidates or []) for x in concepts]
    if requested_topic:
        terms = _tokens(requested_topic)
        scored = [x for x in scored if len(terms & _tokens(x.get("title"))) >= 1]
    buckets = _bucketize(scored)
    output = []
    for item in buckets:
        item["dashboard_discovery_version"] = SPORTS_DESK_VERSION
        item["story_key"] = sr._story_key(item)
        item["story_url"] = item.get("url") or sr._story_url(item)
        item["source_label"] = sr._source_label(item) or _clean(item.get("source")) or "Cricket source"
        item["recommended_category"] = "sports_stories_of_day"
        item["recommended_format"] = "cricket"
        item["cricket_pipeline"] = True
        item["primary_genre"] = "cricket"
        if item.get("social_post_count") and not item.get("article_count"):
            item["verification_level"] = "social lead"
        elif int(item.get("independent_source_count") or 0) >= 2:
            item["verification_level"] = "corroborated"
        else:
            item["verification_level"] = "single-source lead"
        item["discovery_reason"] = (
            f"Undercovered {item['discovery_bucket']} lead with "
            f"{int(item.get('independent_source_count') or 0)} independent publisher(s), "
            f"{int(item.get('social_post_count') or 0)} social lead(s), and "
            f"novelty {float(item.get('undercovered_score') or 0):.1f}/10."
        )
        output.append(item)
    return output[:30]


def render_cricket_topic_desk(candidates, ui_text, ui_html, remember_callback):
    import streamlit as st

    specs = (
        ("news", "NEWS", "Concrete current developments."),
        ("viral", "VIRAL / EMERGING", "Undercovered stories with momentum, novelty or reaction."),
        ("social", "SOCIAL & REACTIONS", "Player comments, fan debate and social-first leads."),
    )
    grouped = {key: [] for key, _, _ in specs}
    for candidate in candidates or []:
        bucket = str(candidate.get("discovery_bucket") or "news").strip().lower()
        grouped.get(bucket, grouped["news"]).append(candidate)

    st.markdown(
        "<div class='live-bar'><div class='live-bar-copy'><b>Cricket story desk</b> · 30 deliberately different ideas.</div></div>",
        unsafe_allow_html=True,
    )

    for bucket, label, description in specs:
        stories = grouped[bucket][:10]
        with st.expander(f"{label} · {len(stories)}", expanded=(bucket == "news")):
            st.caption(description)
            for start in range(0, len(stories), 2):
                row = stories[start:start + 2]
                cols = st.columns(len(row), gap="medium")
                for offset, candidate in enumerate(row):
                    index = start + offset
                    with cols[offset]:
                        title = ui_text(candidate.get("title"), "Untitled story")
                        source = ui_text(candidate.get("source_label"), "Cricket source")
                        reason = ui_text(candidate.get("discovery_reason"))
                        verification = ui_text(candidate.get("verification_level"), "lead")
                        url = str(candidate.get("story_url") or "").strip()

                        chips = (
                            f"<span class='topic-chip strong'>Novel {float(candidate.get('undercovered_score') or 0):.1f}</span>"
                            f"<span class='topic-chip'>Coverage {float(candidate.get('coverage_score') or 0):.1f}</span>"
                            f"<span class='topic-chip'>Viral {float(candidate.get('viral_signal_score') or 0):.1f}</span>"
                        )
                        if float(candidate.get("trend_signal_score") or 0) > 0:
                            chips += f"<span class='topic-chip'>Trend {float(candidate.get('trend_signal_score') or 0):.1f}</span>"
                        if int(candidate.get("social_post_count") or 0):
                            chips += f"<span class='topic-chip'>{int(candidate.get('social_post_count') or 0)} social lead(s)</span>"
                        if int(candidate.get("independent_source_count") or 0):
                            chips += f"<span class='topic-chip'>{int(candidate.get('independent_source_count') or 0)} publisher(s)</span>"

                        st.markdown(
                            "<div class='topic-card'>"
                            f"<div class='topic-kicker'><span>{ui_html(bucket.upper())} · {index + 1:02d}</span>"
                            f"<span>{ui_html(verification.upper())}</span></div>"
                            f"<div class='topic-title'>{ui_html(title)}</div>"
                            f"<div class='topic-subtitle'>{ui_html(source)}</div>"
                            f"<div class='topic-chips'>{chips}</div></div>",
                            unsafe_allow_html=True,
                        )
                        if reason:
                            st.caption(reason)
                        action_cols = st.columns(2)
                        with action_cols[0]:
                            if url.startswith(("http://", "https://")):
                                st.link_button("Open", url, width="stretch")
                        with action_cols[1]:
                            if st.button(
                                "Use story",
                                type="primary",
                                width="stretch",
                                key=f"cricket_desk_{bucket}_{index}_{candidate.get('story_key', index)}",
                            ):
                                remember_callback(candidate)
                                st.session_state.pending_candidate = dict(candidate)
                                st.session_state.visual_search_queries = ""
                                st.session_state.visual_query_story_key = ""
                                st.session_state.visual_query_suggestions = []
                                st.session_state.visual_query_field_count = 0
                                st.rerun()
