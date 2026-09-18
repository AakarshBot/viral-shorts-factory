"""Broad event discovery primitives for the Viral Shorts Factory.

Phase 1 changes the discovery unit from individual articles to distinct news
events. This module is deliberately provider-agnostic above the GDELT adapter:
callers can feed GNews/RSS/Reddit/official-feed articles into the same
normalisation and clustering layer.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests


TRACKING_QUERY_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "mc_cid", "mc_eid",
}

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "by", "for", "from",
    "has", "have", "in", "into", "is", "it", "its", "of", "on", "or", "that",
    "the", "their", "this", "to", "was", "were", "will", "with", "after",
    "before", "over", "under", "new", "news", "latest", "today", "report",
    "reports", "says", "said", "update", "breaking", "official",
}

GDELT_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _tokens(value: object) -> set[str]:
    text = re.sub(r"[^a-z0-9%]+", " ", _clean(value).lower())
    return {token for token in text.split() if len(token) >= 3 and token not in STOPWORDS}


def _token_overlap(left: object, right: object) -> float:
    a = _tokens(left)
    b = _tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def normalize_url(value: object) -> str:
    """Canonicalise article URLs so tracking links do not split one article."""
    raw = _clean(value)
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
        query = [
            (key, val)
            for key, val in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() not in TRACKING_QUERY_KEYS
        ]
        netloc = parsed.netloc.lower().removeprefix("www.")
        path = parsed.path.rstrip("/") or "/"
        return urlunparse((
            parsed.scheme.lower() or "https",
            netloc,
            path,
            "",
            urlencode(query, doseq=True),
            "",
        ))
    except Exception:
        return raw.lower()


def normalize_publisher(story: dict) -> str:
    """Return a stable publisher/domain identifier for corroboration."""
    source = _clean(
        story.get("publisher")
        or story.get("source_name")
        or story.get("source")
        or story.get("domain")
    ).lower()
    source = re.sub(r"[^a-z0-9.\- ]+", " ", source)
    source = re.sub(r"\s+", " ", source).strip()

    url = normalize_url(story.get("url") or story.get("link"))
    domain = ""
    if url:
        try:
            domain = urlparse(url).netloc.lower().removeprefix("www.")
        except Exception:
            pass

    if source and source not in {"gnews", "rss", "google news", "news.google.com"}:
        return source
    return domain or source or "unknown"


def _published_datetime(story: dict) -> datetime | None:
    for key in (
        "published_at", "publishedAt", "published", "pub_date", "pubDate",
        "date", "timestamp",
    ):
        raw = story.get(key)
        if not raw:
            continue
        value = _clean(raw)
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except (TypeError, ValueError):
            pass
    return None


def _cluster_compatible(left: dict, right: dict) -> bool:
    """Conservative and explainable headline similarity rule."""
    overlap = _token_overlap(left.get("title"), right.get("title"))
    if overlap >= 0.62:
        return True

    left_tokens = _tokens(left.get("title"))
    right_tokens = _tokens(right.get("title"))
    shared = left_tokens & right_tokens
    if len(shared) < 4:
        return False

    smaller = min(len(left_tokens), len(right_tokens))
    if smaller and len(shared) / smaller >= 0.78:
        return True

    meaningful = {token for token in shared if len(token) >= 5 and not token.isdigit()}
    return len(meaningful) >= 4 and overlap >= 0.42


def _event_id(articles: list[dict]) -> str:
    tokens = set()
    for article in articles:
        tokens.update(_tokens(article.get("title")))
    fingerprint = " ".join(sorted(tokens))
    return hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:16]


def cluster_news_events(
    articles: list[dict] | None,
    *,
    max_articles_per_event: int = 12,
) -> list[dict]:
    """Cluster article records into distinct, evidence-backed news events."""
    normalised: list[dict] = []
    seen_urls: set[str] = set()

    for original in articles or []:
        if not isinstance(original, dict):
            continue
        title = _clean(original.get("title"))
        if len(title) < 8:
            continue

        item = dict(original)
        item["title"] = title
        item["url"] = normalize_url(item.get("url") or item.get("link"))
        item["publisher_normalized"] = normalize_publisher(item)
        if item["url"]:
            if item["url"] in seen_urls:
                continue
            seen_urls.add(item["url"])

        item["published_dt"] = _published_datetime(item)
        normalised.append(item)

    normalised.sort(
        key=lambda item: item.get("published_dt")
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    clusters: list[list[dict]] = []
    for article in normalised:
        matched = None
        for cluster in clusters:
            references = cluster[: min(4, len(cluster))]
            if any(_cluster_compatible(article, reference) for reference in references):
                matched = cluster
                break
        if matched is None:
            clusters.append([article])
        else:
            matched.append(article)

    events: list[dict] = []
    for cluster in clusters:
        cluster.sort(
            key=lambda item: item.get("published_dt")
            or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        representative = dict(cluster[0])
        publishers = sorted({
            _clean(item.get("publisher_normalized"))
            for item in cluster
            if _clean(item.get("publisher_normalized"))
        })
        domains = sorted({
            urlparse(item["url"]).netloc.lower().removeprefix("www.")
            for item in cluster if item.get("url")
        })
        article_count = len(cluster)
        source_count = len(publishers or domains)

        evidence = []
        for article in cluster[:max_articles_per_event]:
            evidence.append({
                "title": _clean(article.get("title")),
                "url": article.get("url", ""),
                "publisher": article.get("publisher_normalized", ""),
                "publishedAt": (
                    article["published_dt"].isoformat()
                    if article.get("published_dt")
                    else _clean(article.get("publishedAt"))
                ),
                "collection_source": _clean(article.get("collection_source")),
            })

        representative.update({
            "event_id": _event_id(cluster),
            "event_clustered": True,
            "event_article_count": article_count,
            "event_source_count": source_count,
            "event_publishers": publishers,
            "event_source_domains": domains,
            "event_evidence": evidence,
            "event_cluster_size": article_count,
            "event_corroboration_score": min(10.0, source_count * 2.0),
            "event_latest_published_at": (
                cluster[0]["published_dt"].isoformat()
                if cluster[0].get("published_dt")
                else _clean(cluster[0].get("publishedAt"))
            ),
        })
        events.append(representative)

    events.sort(
        key=lambda item: (
            item.get("event_source_count", 0),
            item.get("event_article_count", 0),
            item.get("published_dt")
            or datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )
    return events


def fetch_gdelt_articles(
    query: str,
    *,
    timespan: str = "48h",
    max_records: int = 75,
    timeout: float = 10.0,
) -> list[dict]:
    """Fetch a broad article pool from the free GDELT DOC 2.0 API."""
    query = _clean(query)
    if not query:
        return []

    try:
        response = requests.get(
            GDELT_ENDPOINT,
            params={
                "query": query,
                "mode": "artlist",
                "maxrecords": max(1, min(250, int(max_records))),
                "timespan": timespan,
                "sort": "datedesc",
                "format": "json",
            },
            headers={"User-Agent": "ViralShortsFactory/2026 discovery/1.0"},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError, TypeError) as exc:
        print(
            f"   [Discovery] GDELT intake unavailable ({type(exc).__name__}); continuing with existing sources.",
            flush=True,
        )
        return []
    except Exception as exc:
        print(
            f"   [Discovery] GDELT intake failed ({type(exc).__name__}); continuing with existing sources.",
            flush=True,
        )
        return []

    output = []
    articles = payload.get("articles", []) if isinstance(payload, dict) else []
    for article in articles:
        if not isinstance(article, dict):
            continue
        title = _clean(article.get("title"))
        url = _clean(article.get("url"))
        if not title or not url:
            continue
        domain = _clean(article.get("domain")).lower()
        output.append({
            "title": title,
            "text": "",
            "description": "",
            "source": domain or "GDELT",
            "source_name": domain or "GDELT",
            "publisher": domain or "GDELT",
            "url": url,
            "publishedAt": article.get("seendate") or article.get("datetime"),
            "language": article.get("language"),
            "genre": "",
            "collection_source": "gdelt",
            "gdelt_tone": article.get("tone"),
        })
    return output


def discover_event_pool(
    *,
    query: str,
    existing_articles: list[dict] | None = None,
    timespan: str = "48h",
    max_gdelt_records: int = 75,
) -> dict:
    """Merge existing intake with GDELT and return event-first output."""
    baseline = list(existing_articles or [])
    gdelt = fetch_gdelt_articles(
        query,
        timespan=timespan,
        max_records=max_gdelt_records,
    )
    merged = baseline + gdelt
    events = cluster_news_events(merged)
    return {
        "articles": merged,
        "events": events,
        "article_count": len(merged),
        "event_count": len(events),
        "gdelt_article_count": len(gdelt),
    }


__all__ = [
    "cluster_news_events",
    "discover_event_pool",
    "fetch_gdelt_articles",
    "normalize_publisher",
    "normalize_url",
]
