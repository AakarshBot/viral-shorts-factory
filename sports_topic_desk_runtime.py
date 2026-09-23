"""Sports-first cricket topic desk with maximum recall and event-level uniqueness."""

from __future__ import annotations

import html
import math
import os
import re
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

import story_ranker as sr
from event_discovery_runtime import cluster_news_events, fetch_gdelt_articles

SPORTS_DESK_VERSION = "cricket-desk-v7-2026-09-23"
LOOKBACK_HOURS = 72
MAX_DASHBOARD_HEADLINES = 60

# All core requests are allowed to finish inside the shared wall-clock budget.
# No task is intentionally abandoned while its own HTTP timeout is still live.
CORE_DISCOVERY_TIMEOUT = 8.0
GOOGLE_REQUEST_TIMEOUT = 6.0
SOURCE_TIMEOUT = 6.0
SECONDARY_REQUEST_TIMEOUT = 3.0
GDELT_REQUEST_TIMEOUT = 4.0

PER_BUCKET = 20
GOOGLE_QUERY_LIMIT = 10
GOOGLE_RESULT_LIMIT = 30
DIRECT_RESULT_LIMIT = 45

# Reddit/Mastodon are secondary-only. They are disabled by default because the
# public unauthenticated endpoints are not dependable enough to be discovery
# dependencies. Bluesky's public search API is safe to use as a secondary signal.
ENABLE_REDDIT_DISCOVERY = os.getenv("REDDIT_DISCOVERY_ENABLED", "0").strip().lower() in {"1", "true", "yes"}
ENABLE_MASTODON_DISCOVERY = os.getenv("MASTODON_DISCOVERY_ENABLED", "0").strip().lower() in {"1", "true", "yes"}

DIRECT_CRICKET_SOURCES = (
    ("ICC", "https://www.icc-cricket.com/news", "listing"),
    ("BCCI", "https://www.bcci.tv/news", "listing"),
    ("Cricbuzz", "https://www.cricbuzz.com/cricket-news/latest-news", "listing"),
    ("Wisden", "https://www.wisden.com/cricket-news", "listing"),
    ("ESPNcricinfo", "https://www.espncricinfo.com/cricket-news", "listing"),
)

INDIA_ASIA_GOOGLE_QUERIES = (
    '"India cricket" OR BCCI OR "India women cricket" when:3d',
    'India cricket players selection injury retirement appointment coach captain when:3d',
    'India cricket statement reaction controversy dispute row debate when:3d',
    'India cricket record milestone first fastest historic upset comeback thriller scare when:3d',
    'India women cricket WPL domestic Ranji Duleep "India A" U19 U23 when:7d',
    'Pakistan Sri Lanka Bangladesh Afghanistan Nepal Japan UAE "India cricket" when:3d',
    'India cricket uncapped emerging debut breakthrough academy grassroots when:7d',
    'IPL India cricket auction transfer trade coach franchise squad when:7d',
    'India cricket bizarre unusual viral fans reaction social media when:3d',
    'cricket India announced confirmed revealed latest development when:3d',
)

GLOBAL_GOOGLE_QUERIES = (
    'international cricket latest when:3d',
    'Australia England South Africa New Zealand West Indies cricket when:3d',
    'cricket selection injury retirement appointment coach captain when:3d',
    'cricket statement reaction controversy dispute row debate when:3d',
    'cricket record milestone first fastest historic upset comeback thriller scare when:3d',
    'women cricket domestic associate emerging player when:7d',
    'cricket uncapped debut breakthrough academy comeback when:7d',
    'T20 Test franchise league auction coaching cricket when:7d',
    'cricket bizarre unusual viral fans reaction social media when:3d',
    'ICC cricket latest development when:3d',
)

REDDIT_SUBREDDITS = ("Cricket", "IndiaCricket", "CricketShitpost")
BLUESKY_QUERIES = ("cricket", '"India cricket"', "cricket reaction")
MASTODON_QUERIES = ("cricket", "India cricket")
TREND_GEOS = ("IN", "GB", "AU", "US")

CRICKET_TERMS = (
    "cricket", "icc", "bcci", "pcb", "wpl", "ipl", "psl", "t20", "odi", "test",
    "batter", "batting", "bowler", "bowling", "wicket", "wickets", "innings",
    "over", "overs", "allrounder", "all-rounder", "keeper", "wicketkeeper",
    "captain", "selector", "coach", "run", "runs", "century", "fifty", "spin",
    "pace",
)

INDIA_ASIA_ANCHORS = (
    "india", "indian", "bcci", "india a", "rest of india", "ranji", "duleep",
    "dpl", "wpl", "ipl", "u19", "u-19", "u23", "u-23", "pakistan", "pcb",
    "sri lanka", "slc", "bangladesh", "bcb", "afghanistan", "acb", "nepal",
    "uae", "hong kong", "japan", "asia", "asian games", "asia cup", "acc",
)

REACTION_TERMS = (
    "said", "says", "called", "responded", "reaction", "reacts", "comment",
    "comments", "praised", "criticised", "criticized", "slammed", "warned",
    "debate", "backlash", "fans", "meme", "viral", "social media", "post",
)
HOOK_TERMS = (
    "record", "first", "fastest", "youngest", "oldest", "debut", "breakthrough",
    "comeback", "upset", "survived", "scare", "controversy", "investigation",
    "sanctioned", "fined", "retired", "ruled out", "recalled", "dropped",
    "statement", "revealed", "reveals", "banned", "appointed", "returns",
)
SATURATION_TERMS = (
    "world cup", "final", "asia cup", "asian games", "ashes",
    "india vs", "india v", "championship", "major final",
)


def _clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _word_match(text, term):
    text = _clean(text).casefold()
    term = _clean(term).casefold()
    if not text or not term:
        return False
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text))


def _age_hours(value):
    raw = _clean(value)
    if not raw:
        return 9999.0
    parsed = None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        pass
    if parsed is None:
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
            pass
    if parsed is None:
        for fmt in ("%Y%m%d%H%M%S", "%Y%m%dT%H%M%S", "%Y%m%dT%H%M%SZ"):
            try:
                parsed = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return 9999.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(
        0.0,
        (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() / 3600.0,
    )


def _domain(item):
    try:
        return urlparse(_clean(item.get("url") or item.get("link"))).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def _tokens(value):
    return set(sr._tokens(value))


def _similarity(left, right):
    left = left if isinstance(left, dict) else {}
    right = right if isinstance(right, dict) else {}
    a = _tokens(left.get("title"))
    b = _tokens(right.get("title"))
    if not a or not b:
        return 0.0
    token_sim = len(a & b) / max(1, len(a | b))
    title_sim = SequenceMatcher(
        None,
        _clean(left.get("title")).casefold(),
        _clean(right.get("title")).casefold(),
    ).ratio()
    ea, eb = set(sr._topic_entities(left)), set(sr._topic_entities(right))
    entity_sim = len(ea & eb) / max(1, len(ea | eb))
    return min(1.0, token_sim * 0.45 + title_sim * 0.35 + entity_sim * 0.20)


def _is_cricket(item):
    item = item if isinstance(item, dict) else {}
    text = _clean(" ".join(
        str(item.get(k) or "")
        for k in ("title", "text", "description", "summary", "snippet", "trend_query")
    ))
    source_hint = _clean(" ".join(
        str(item.get(k) or "")
        for k in ("source", "source_name", "publisher", "url")
    )).casefold()
    cricket_term_hits = sum(1 for term in CRICKET_TERMS if _word_match(text, term))
    if cricket_term_hits >= 1:
        # Generic words such as "coach", "captain", "pace" and "test" are not
        # enough on their own. Require an unambiguous cricket anchor.
        specific_hits = sum(
            1 for term in (
                "cricket", "icc", "bcci", "pcb", "wpl", "ipl", "psl",
                "t20", "odi", "wicket", "wickets", "innings", "batter",
                "bowler", "batting", "bowling",
            )
            if _word_match(text, term)
        )
        entity_hits = sum(
            1 for term in (
                "virat kohli", "rohit sharma", "jasprit bumrah", "shubman gill",
                "rishabh pant", "hardik pandya", "ravindra jadeja",
                "suryakumar yadav", "yashasvi jaiswal", "kl rahul",
                "sanju samson", "smriti mandhana", "harmapreet kaur",
                "harmanpreet kaur", "rashid khan", "babar azam", "pat cummins",
                "travis head", "ben stokes", "joe root", "steve smith",
            )
            if _word_match(text, term)
        )
        if specific_hits or entity_hits:
            return True
    if any(marker in source_hint for marker in (
        "icc-cricket.com", "bcci.tv", "cricbuzz", "wisden", "espncricinfo", "cricinfo.com",
    )):
        return True
    if bool(item.get("social_post")) and any(marker in source_hint for marker in (
        "r/cricket", "r/indiacricket", "cricketshitpost", "bsky",
    )):
        return True
    return False


def _scope_pass(item, scope):
    if _clean(scope).casefold() != "india / asia":
        return True
    text = _clean(" ".join(
        str(item.get(k) or "")
        for k in ("title", "text", "description", "summary", "snippet", "event_search_text")
    ))
    entity_anchors = tuple(dict.fromkeys(
        (*INDIA_ASIA_ANCHORS, *tuple(getattr(sr, "CRICKET_MARQUEE_NAMES", ()))),
    ))
    anchors = [term for term in entity_anchors if _word_match(text, term)]
    if anchors:
        return True
    entities = {
        str(entity).strip().casefold()
        for entity in (item.get("event_entities") or [])
        if str(entity).strip()
    }
    return bool(
        entities
        & {
            "india", "indian", "bcci", "india a", "ranji", "duleep", "wpl", "ipl",
            "pakistan", "pcb", "sri lanka", "bangladesh", "afghanistan",
            "nepal", "uae", "hong kong", "japan", "asian games", "asia cup", "acc",
        }
    )


def _source_local_date(text, now=None):
    value = html.unescape(_clean(text))
    current = now or datetime.now(timezone.utc)
    candidates = []

    for raw in re.findall(
        r"""(?:datetime|datePublished|dateModified|data-date|data-published|data-published-at)\s*[:=]\s*["']([^"']+)["']""",
        value,
        re.IGNORECASE,
    ):
        age = _age_hours(raw)
        if age != 9999.0:
            candidates.append(age)

    for raw in re.findall(r"\b20\d{2}-\d{2}-\d{2}(?:T[0-9:.+\-Z]+)?\b", value):
        age = _age_hours(raw)
        if age != 9999.0:
            candidates.append(age)

    month_names = (
        "Jan|January|Feb|February|Mar|March|Apr|April|May|Jun|June|"
        "Jul|July|Aug|August|Sep|September|Oct|October|Nov|November|Dec|December"
    )
    patterns = (
        rf"\b(?:{month_names})\s+\d{{1,2}}(?:,\s*|\s+)\d{{4}}\b",
        rf"\b\d{{1,2}}\s+(?:{month_names})\s+\d{{4}}\b",
        rf"\b(?:{month_names})\s+\d{{1,2}}\b",
    )
    for pattern in patterns:
        for raw in re.findall(pattern, value, re.IGNORECASE):
            clean = raw.replace(",", "").strip()
            parsed = None
            for fmt in ("%b %d %Y", "%B %d %Y", "%d %b %Y", "%d %B %Y"):
                try:
                    parsed = datetime.strptime(clean, fmt).replace(tzinfo=timezone.utc)
                    break
                except ValueError:
                    continue
            if parsed is None:
                for fmt in ("%b %d", "%B %d"):
                    try:
                        parsed = datetime.strptime(clean, fmt).replace(year=current.year, tzinfo=timezone.utc)
                        if parsed > current:
                            parsed = parsed.replace(year=current.year - 1)
                        break
                    except ValueError:
                        continue
            if parsed is not None:
                candidates.append(max(0.0, (current - parsed).total_seconds() / 3600.0))

    relative = re.search(
        r"(?<!\d)(\d{1,3})\s*(minute|minutes|min|hour|hours|hr|hrs|day|days|d|h)\s+ago\b",
        value.casefold(),
    )
    if relative:
        amount = float(relative.group(1))
        unit = relative.group(2)
        candidates.append(amount / 60.0 if unit.startswith("min") else amount if unit.startswith("h") else amount * 24.0)

    valid = [age for age in candidates if age <= LOOKBACK_HOURS]
    return min(valid) if valid else None


def _card_container(anchor):
    for parent in anchor.parents:
        classes = " ".join(parent.get("class") or []).casefold()
        role = str(parent.get("role") or "").casefold()
        tag = str(parent.name or "").casefold()
        if tag in {"article", "li"}:
            return parent
        if any(marker in classes or marker in role for marker in (
            "card", "story", "article", "news-item", "listing", "content-item", "media-object",
        )):
            return parent
        if parent is anchor.parent and tag in {"div", "section"}:
            return parent
    return anchor.parent


def _direct_listing_source(name, url):
    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 ViralShortsFactory/2026 cricket-desk",
                "Accept": "text/html,application/xhtml+xml",
            },
            timeout=SOURCE_TIMEOUT,
        )
        if response.status_code != 200:
            return []
        soup = BeautifulSoup(response.text or "", "html.parser")
    except Exception:
        return []

    allowed_hosts = {
        "ICC": ("icc-cricket.com",),
        "BCCI": ("bcci.tv",),
        "Cricbuzz": ("cricbuzz.com",),
        "Wisden": ("wisden.com",),
        "ESPNcricinfo": ("espncricinfo.com", "cricinfo.com"),
    }.get(name, ())

    out, seen = [], set()
    for anchor in soup.find_all("a", href=True):
        href = _clean(anchor.get("href"))
        title = _clean(anchor.get_text(" ", strip=True))
        if not href or len(title) < 12:
            continue
        if not href.startswith(("http://", "https://")):
            href = urljoin(url, href)
        parsed = urlparse(href)
        host = parsed.netloc.lower().removeprefix("www.")
        path = parsed.path.casefold()
        if allowed_hosts and not any(host == allowed or host.endswith("." + allowed) for allowed in allowed_hosts):
            continue
        if name == "BCCI" and "/news/article/" not in path:
            continue
        if name == "Cricbuzz" and "/cricket-news/" not in path:
            continue
        if name == "Wisden" and "/cricket-news/" not in path:
            continue
        if name == "ESPNcricinfo" and not any(marker in path for marker in ("/story/", "/cricket-news/")):
            continue
        if name == "ICC" and "/news/" not in path:
            continue

        card = _card_container(anchor)
        card_text = _clean(card.get_text(" ", strip=True) if card else "")
        age = _source_local_date(str(card) if card else card_text)
        if age is None:
            age = _source_local_date(str(anchor.parent) if anchor.parent else "")
        if age is None or age > LOOKBACK_HOURS:
            continue

        key = sr._canonical_url(href)
        if not key or key in seen:
            continue
        seen.add(key)
        published = datetime.now(timezone.utc) - timedelta(hours=age)

        # Keep only card-local context; never use a giant neighboring-page window.
        out.append({
            "title": title[:220],
            "text": card_text[:1200] or title,
            "description": card_text[:1200] or title,
            "source": name,
            "source_name": name,
            "publisher": name,
            "url": href,
            "publishedAt": published.isoformat(),
            "collection_source": "official" if name in {"ICC", "BCCI"} else "specialist_direct",
            "direct_source": name,
        })
        if len(out) >= DIRECT_RESULT_LIMIT:
            break
    return out


def _bluesky(query):
    try:
        r = requests.get(
            "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts",
            params={"q": query, "limit": 40, "sort": "latest"},
            headers={"User-Agent": "ViralShortsFactory/2026 cricket-desk"},
            timeout=SECONDARY_REQUEST_TIMEOUT,
        )
        if r.status_code != 200:
            return []
        posts = r.json().get("posts") or []
    except Exception:
        return []
    output = []
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
        output.append({
            "title": text[:220],
            "text": text,
            "description": text,
            "source": f"Bluesky @{handle}" if handle else "Bluesky",
            "source_name": f"Bluesky @{handle}" if handle else "Bluesky",
            "url": url,
            "publishedAt": record.get("createdAt") or post.get("indexedAt") or "",
            "collection_source": "bluesky",
            "social_post": True,
            "social_like": float(post.get("likeCount") or 0),
            "social_reply": float(post.get("replyCount") or 0),
            "social_repost": float(post.get("repostCount") or 0),
            "social_quote": float(post.get("quoteCount") or 0),
        })
    return output


def _reddit_search(subreddit, query):
    try:
        return sr._reddit_items("sports_stories_of_day", subreddit, 35)
    except Exception:
        return []


def _mastodon(query):
    try:
        r = requests.get(
            "https://mastodon.social/api/v2/search",
            params={"q": query, "type": "statuses", "limit": 30},
            headers={"User-Agent": "ViralShortsFactory/2026 cricket-desk"},
            timeout=SECONDARY_REQUEST_TIMEOUT,
        )
        if r.status_code != 200:
            return []
        statuses = r.json().get("statuses") or []
    except Exception:
        return []
    output = []
    for status in statuses:
        text = _clean(re.sub(r"<[^>]+>", " ", _clean(status.get("content"))))
        if not text:
            continue
        output.append({
            "title": text[:220],
            "text": text,
            "description": text,
            "source": "Mastodon",
            "source_name": "Mastodon",
            "url": _clean(status.get("url")),
            "publishedAt": _clean(status.get("created_at")),
            "collection_source": "mastodon",
            "social_post": True,
            "social_like": float(status.get("favourites_count") or 0),
            "social_reply": float(status.get("replies_count") or 0),
            "social_repost": float(status.get("reblogs_count") or 0),
        })
    return output


def _google_queries_for_scope(scope):
    scope_key = _clean(scope).casefold()
    if scope_key == "india / asia":
        return INDIA_ASIA_GOOGLE_QUERIES[:GOOGLE_QUERY_LIMIT]
    if scope_key == "global":
        return GLOBAL_GOOGLE_QUERIES[:GOOGLE_QUERY_LIMIT]
    merged = tuple(dict.fromkeys((*INDIA_ASIA_GOOGLE_QUERIES, *GLOBAL_GOOGLE_QUERIES)))
    return merged[:GOOGLE_QUERY_LIMIT]


def _normalise_rows(rows):
    output, seen = [], set()
    for raw in rows or []:
        if not isinstance(raw, dict) or not _is_cricket(raw):
            continue
        item = dict(raw)
        age = _age_hours(item.get("publishedAt") or item.get("published_at") or item.get("created_at"))
        if age == 9999.0 or age > LOOKBACK_HOURS:
            continue
        safe, _ = sr._safety_gate(item)
        if not safe:
            continue
        if not item.get("social_post"):
            if not sr._source_page_pass(item):
                continue
            if not sr._cricket_service_title_pass(item):
                continue
        url = _clean(item.get("url") or item.get("link"))
        canonical = sr._canonical_url(url)
        title_key = " ".join(sorted(_tokens(item.get("title"))))
        key = canonical or ("title:" + title_key)
        if key in seen:
            continue
        seen.add(key)
        item["title"] = _clean(item.get("title"))
        item["source_domain"] = _domain(item)
        item["age_hours"] = round(age, 2)
        item["social_engagement"] = math.log1p(
            sum(float(item.get(k) or 0) for k in (
                "social_like", "social_reply", "social_repost", "social_quote"
            ))
        )
        output.append(item)
    return output


def _social_stats_by_url(rows):
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not row.get("social_post"):
            continue
        url = sr._canonical_url(row.get("url") or row.get("link"))
        if url:
            result[url] = result.get(url, 0.0) + float(row.get("social_engagement") or 0.0)
    return result


def _enrich_events(events, rows):
    social_by_url = _social_stats_by_url(rows)
    for event in events:
        evidence = event.get("event_evidence") or []
        social_count = 0
        social_engagement = 0.0
        social_titles = []
        for item in evidence:
            collection = _clean(item.get("collection_source")).casefold()
            if collection in {"bluesky", "reddit", "mastodon", "social"}:
                social_count += 1
                social_titles.append(_clean(item.get("title")))
                social_engagement += social_by_url.get(sr._canonical_url(item.get("url")), 0.0)
        event["social_post_count"] = social_count
        event["social_engagement_total"] = round(social_engagement, 3)
        event["social_titles"] = social_titles[:12]
        event["event_article_count"] = max(
            0,
            int(event.get("event_article_count") or 0) - social_count,
        )
        event["event_identity_key"] = str(event.get("event_identity_key") or event.get("event_id") or "").strip()
    return events


def _score(item, trends, history_titles=None, retained=None):
    text = _clean(" ".join(
        str(item.get(k) or "")
        for k in ("title", "text", "description", "event_search_text")
    )).casefold()
    reaction_hits = sum(1 for term in REACTION_TERMS if _word_match(text, term))
    hook_hits = sum(1 for term in HOOK_TERMS if _word_match(text, term))
    major_hits = sum(1 for term in SATURATION_TERMS if _word_match(text, term))
    source_count = int(item.get("event_source_count") or 0)
    article_count = int(item.get("event_article_count") or 0)
    coverage = min(10.0, source_count * 1.8 + min(4.0, article_count * 0.35))
    social = min(10.0, float(item.get("social_engagement_total") or 0) * 1.2 + min(5.0, int(item.get("social_post_count") or 0) * 0.8))
    trend = 0.0
    title_tokens = _tokens(item.get("title"))
    for trend_row in trends or []:
        trend_tokens = _tokens(f"{trend_row.get('trend_query', '')} {trend_row.get('title', '')}")
        if not trend_tokens:
            continue
        overlap = len(title_tokens & trend_tokens) / max(1, len(title_tokens | trend_tokens))
        if overlap >= 0.30:
            trend = max(trend, float(trend_row.get("trend_bonus") or 0))
    undercovered = max(0.0, min(10.0, 10.0 - coverage + social * 0.5 + (2.5 if source_count <= 2 else 0.0)))
    age = float(item.get("age_hours") or _age_hours(item))
    freshness = max(0.0, min(10.0, 10.0 - age / 7.2))
    saturation = min(8.0, major_hits + max(0, source_count - 3) * 0.6)
    history_penalty = 0.0
    current_title = _clean(item.get("title")).casefold()
    history_values = list(history_titles or []) + [
        _clean(row.get("title")) for row in (retained or []) if isinstance(row, dict)
    ]
    if current_title and history_values:
        history_penalty = min(
            6.0,
            max(
                (
                    SequenceMatcher(None, current_title, value.casefold()).ratio()
                    for value in history_values if value
                ),
                default=0.0,
            ) * 6.0,
        )
    news = freshness * 0.45 + coverage * 1.2 + hook_hits * 0.55 + reaction_hits * 0.2 - saturation * 0.45 - history_penalty
    viral = trend * 1.8 + social * 1.25 + undercovered * 1.45 + hook_hits * 0.7 + freshness * 0.55 - saturation * 0.8 - history_penalty
    social_score = social * 1.7 + reaction_hits * 1.2 + trend * 0.9 + undercovered * 1.15 + freshness * 0.45 - saturation * 0.45 - history_penalty
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


def _bucketize(concepts):
    # One event can appear in only one dashboard bucket. Bucketization is a
    # presentation layer; it must never duplicate the underlying event.
    candidates = []
    seen = set()
    for item in concepts or []:
        if not isinstance(item, dict):
            continue
        key = str(
            item.get("event_identity_key")
            or item.get("event_id")
            or sr._canonical_url(item.get("url") or item.get("link"))
            or sr._story_key(item)
        ).strip().casefold()
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        candidates.append(item)

    result = []
    chosen = set()

    def choose_bucket(bucket, score_name, count):
        remaining = [item for item in candidates if id(item) not in chosen]
        picked = []
        while remaining and len(picked) < count:
            best = max(
                remaining,
                key=lambda item: (
                    float(item.get(score_name) or 0.0)
                    - max(
                        (
                            sr._story_theme_similarity(item, old)
                            for old in result
                        ),
                        default=0.0,
                    ) * 8.0
                    + (1.2 if float(item.get("undercovered_score") or 0) >= 7 else 0.0)
                ),
            )
            picked.append(best)
            chosen.add(id(best))
            remaining.remove(best)
            row = dict(best)
            row["discovery_bucket"] = bucket
            row["bucket_score"] = float(row.get(score_name) or 0.0)
            row["cricket_event_family"] = sr._cricket_event_family(best)
            result.append(row)

    for bucket, score_name in (
        ("news", "news_score"),
        ("viral", "viral_score"),
        ("social", "social_score"),
    ):
        choose_bucket(bucket, score_name, PER_BUCKET)

    # Sparse-source backfill: never invent stories, and never re-use an event.
    for bucket, score_name in (
        ("news", "news_score"),
        ("viral", "viral_score"),
        ("social", "social_score"),
    ):
        need = PER_BUCKET - sum(1 for item in result if item.get("discovery_bucket") == bucket)
        while need:
            remaining = [item for item in candidates if id(item) not in chosen]
            if not remaining:
                break
            winner = max(
                remaining,
                key=lambda item: float(item.get(score_name) or 0.0)
                - max((sr._story_theme_similarity(item, old) for old in result), default=0.0) * 8.0,
            )
            chosen.add(id(winner))
            row = dict(winner)
            row["discovery_bucket"] = bucket
            row["cross_bucket_backfill"] = True
            row["bucket_score"] = float(row.get(score_name) or 0.0)
            row["cricket_event_family"] = sr._cricket_event_family(winner)
            result.append(row)
            need -= 1

    return result


def _collect(scope="India / Asia"):
    google_queries = _google_queries_for_scope(scope)
    primary_jobs = []
    secondary_jobs = []

    # Core factual intake: every Google lens and every direct cricket newsroom
    # runs once. This is deliberately bounded but broad enough to avoid a single
    # provider determining the entire dashboard.
    scope_key = _clean(scope).casefold()
    news_hl, news_gl, news_ceid = (
        ("en-IN", "IN", "IN:en")
        if scope_key == "india / asia"
        else ("en-GB", "GB", "GB:en")
    )
    for query in google_queries:
        primary_jobs.append((
            f"Google News:{query}",
            sr._google_news_search_items,
            (query, "sports_stories_of_day", GOOGLE_RESULT_LIMIT),
            {
                "timeout": GOOGLE_REQUEST_TIMEOUT,
                "hl": news_hl,
                "gl": news_gl,
                "ceid": news_ceid,
            },
        ))
    for name, url, _kind in DIRECT_CRICKET_SOURCES:
        primary_jobs.append((
            name,
            _direct_listing_source,
            (name, url),
            {},
        ))

    # Secondary signal sources never determine factual discovery success.
    secondary_jobs.append(("Bluesky", _bluesky, (BLUESKY_QUERIES[1] if _clean(scope).casefold() == "india / asia" else "cricket",), {}))
    trend_geos = ("IN",) if _clean(scope).casefold() == "india / asia" else ("GB", "AU")
    for geo in trend_geos:
        secondary_jobs.append((
            f"Google Trends {geo}",
            sr._google_trends_items,
            (geo, 20),
            {"timeout": SECONDARY_REQUEST_TIMEOUT},
        ))
    if ENABLE_REDDIT_DISCOVERY:
        for subreddit in REDDIT_SUBREDDITS:
            secondary_jobs.append((f"Reddit r/{subreddit}", _reddit_search, (subreddit, "cricket"), {}))
    if ENABLE_MASTODON_DISCOVERY:
        secondary_jobs.append(("Mastodon", _mastodon, (MASTODON_QUERIES[1] if _clean(scope).casefold() == "india / asia" else MASTODON_QUERIES[0],), {}))

    rows = []
    source_counts = {}
    failures = []

    pool = ThreadPoolExecutor(
        max_workers=max(1, len(primary_jobs) + len(secondary_jobs)),
        thread_name_prefix="cricket-discovery",
    )
    futures = {}
    try:
        for label, fn, args, kwargs in (*primary_jobs, *secondary_jobs):
            future = pool.submit(fn, *args, **kwargs)
            futures[future] = label
        done, pending = wait(tuple(futures), timeout=CORE_DISCOVERY_TIMEOUT)

        for future in done:
            label = futures[future]
            try:
                values = future.result() or []
                rows.extend(values)
                source_counts[label] = len(values)
            except Exception as exc:
                failures.append(f"{label}:{type(exc).__name__}")

        if pending:
            # Requests in this collector all have bounded HTTP timeouts shorter
            # than the shared budget. Anything still pending is therefore a
            # provider implementation anomaly; don't conceal it as success.
            for future in pending:
                label = futures[future]
                failures.append(f"{label}:timeout")
                future.cancel()

        compact_counts = ", ".join(f"{label}={count}" for label, count in sorted(source_counts.items()))
        print(
            f"   [Cricket Desk] Raw source intake: {compact_counts or 'none'} "
            f"| Google lanes {sum(1 for label in source_counts if label.startswith('Google News:'))}/{len(google_queries)}",
            flush=True,
        )
        if failures:
            print(f"   [Cricket Desk] Source issues: {', '.join(failures[:20])}", flush=True)

    finally:
        for future in futures:
            future.cancel()
        pool.shutdown(wait=False, cancel_futures=True)

    # Conditional factual fallback: only pay for GDELT if the primary pool is
    # actually sparse. Never let it slow a healthy discovery run.
    core_rows = [row for row in rows if isinstance(row, dict) and not row.get("social_post")]
    deduped_core = []
    seen = set()
    for row in core_rows:
        key = sr._canonical_url(row.get("url") or row.get("link")) or (
            "title:" + " ".join(sorted(_tokens(row.get("title"))))
        )
        if key in seen:
            continue
        seen.add(key)
        deduped_core.append(row)

    if len(deduped_core) < 45:
        gdelt_query = (
            "India cricket selection injury controversy records women domestic"
            if _clean(scope).casefold() == "india / asia"
            else "international cricket selection injury controversy records women"
        )
        try:
            gdelt_rows = fetch_gdelt_articles(
                gdelt_query,
                timespan="72h",
                max_records=75,
                timeout=GDELT_REQUEST_TIMEOUT,
            )
            rows.extend(gdelt_rows)
            print(
                f"   [Cricket Desk] Conditional GDELT fallback added {len(gdelt_rows)} row(s) because core factual intake was {len(deduped_core)}.",
                flush=True,
            )
        except Exception as exc:
            print(f"   [Cricket Desk] GDELT fallback failed: {type(exc).__name__}", flush=True)

    return rows


def discover_cricket_topics(bot, conn=None, scope="India / Asia", requested_topic="", max_candidates=60, retained_candidates=None):
    raw = _collect(scope)
    all_rows = _normalise_rows(raw)
    trend_rows = [
        row for row in all_rows
        if _clean(row.get("collection_source")).casefold() == "google_trends"
    ]
    rows = [
        row for row in all_rows
        if _clean(row.get("collection_source")).casefold() != "google_trends"
        and _scope_pass(row, scope)
    ]

    events = cluster_news_events(rows)
    events = _enrich_events(events, rows)

    # Trend items are signals only; they never become independent factual events.
    for event in events:
        title_tokens = _tokens(event.get("title"))
        best = 0.0
        for trend in trend_rows:
            trend_tokens = _tokens(f"{trend.get('trend_query', '')} {trend.get('title', '')}")
            if not title_tokens or not trend_tokens:
                continue
            overlap = len(title_tokens & trend_tokens) / max(1, len(title_tokens | trend_tokens))
            if overlap >= 0.30:
                best = max(best, float(trend.get("trend_bonus") or 0.0))
        event["trend_signal_score"] = round(min(6.0, best * 1.25), 2)

    # Prevent already-uploaded events from ever returning to the dashboard.
    # Unpublished selections are not consulted here; they are merged back by
    # dashboard_runtime and remain visible until an upload is recorded.
    uploaded = sr._load_uploaded_story_identities(conn)
    events = [event for event in events if not sr._uploaded_story_match(event, uploaded)]

    for event in events:
        event["recommended_category"] = "sports_stories_of_day"
        event["primary_genre"] = "cricket"

    history_titles = []
    if conn is not None:
        try:
            history_rows = conn.execute(
                """
                SELECT topic, title_used
                FROM vault
                WHERE status IN ('UPLOADED', 'UPLOADED_PRIVATE', 'COMPLETED')
                  AND video_id IS NOT NULL
                  AND TRIM(video_id) <> ''
                  AND video_id NOT IN ('PENDING_QC', 'REJECTED', 'READY_FOR_UPLOAD')
                ORDER BY COALESCE(updated_at, created_at) DESC, id DESC
                LIMIT 500
                """
            ).fetchall()
            history_titles = [
                _clean(value)
                for row in history_rows
                for value in row[:2]
                if _clean(value)
            ]
        except Exception:
            history_titles = []

    trends = trend_rows
    retained = retained_candidates or []
    scored = []
    for event in events:
        if requested_topic and not sr._requested_topic_pass(event, requested_topic):
            continue
        scored.append(_score(event, trends, history_titles, retained))

    buckets = _bucketize(scored)
    output = []
    for item in buckets[:MAX_DASHBOARD_HEADLINES]:
        item = dict(item)
        item["dashboard_discovery_version"] = SPORTS_DESK_VERSION
        item["story_key"] = sr._story_key(item)
        item["story_url"] = item.get("url") or sr._story_url(item)
        item["source_label"] = sr._source_label(item) or _clean(item.get("source")) or "Cricket source"
        item["recommended_category"] = "sports_stories_of_day"
        item["recommended_format"] = "cricket"
        item["cricket_pipeline"] = True
        item["primary_genre"] = "cricket"
        item["verification_level"] = (
            "social lead" if int(item.get("social_post_count") or 0) and int(item.get("event_article_count") or 0) == 0
            else "corroborated" if int(item.get("event_source_count") or 0) >= 2
            else "single-source lead"
        )
        item["discovery_reason"] = (
            f"{item.get('discovery_bucket', 'news').title()} lead: "
            f"{int(item.get('event_source_count') or 0)} independent publisher(s), "
            f"{int(item.get('social_post_count') or 0)} social signal(s), "
            f"freshness {float(item.get('age_hours') or 0):.1f}h."
        )
        output.append(item)

    # Final hard uniqueness assertion at the boundary; this is intentionally
    # fail-safe and deterministic.
    unique = []
    seen = set()
    for item in output:
        key = str(
            item.get("event_identity_key")
            or item.get("event_id")
            or item.get("story_key")
        ).strip().casefold()
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        unique.append(item)
    return unique[:max(1, min(MAX_DASHBOARD_HEADLINES, int(max_candidates or MAX_DASHBOARD_HEADLINES)))]


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
        "<div class='live-bar'><div class='live-bar-copy'><b>Cricket story desk</b> · 60 deliberately different ideas.</div></div>",
        unsafe_allow_html=True,
    )

    for bucket, label, description in specs:
        stories = grouped[bucket][:20]
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
