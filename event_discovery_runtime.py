"""Broad event discovery primitives for the Viral Shorts Factory.

Phase 1 changes the discovery unit from individual articles to distinct news
events. This module is deliberately provider-agnostic above the GDELT adapter:
callers can feed GNews/RSS/Reddit/official-feed articles into the same
normalisation and clustering layer.
"""
from __future__ import annotations

import hashlib
import time
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
GDELT_TIMEOUT_SECONDS = 4.0
GDELT_FAILURE_COOLDOWN_SECONDS = 120.0
_GDELT_FAILURE_UNTIL = 0.0
_GDELT_FAILURE_LOGGED = False

EVENT_ACTION_FAMILIES = {
    "launch": {
        "launch", "launched", "launches",
        "lift", "lifts", "lifted", "lifting",
        "takeoff",
    },
    "unveil": {"unveil", "unveiled", "unveils"},
    "announce": {"announce", "announced", "announces"},
    "approve": {"approve", "approved", "approves"},
    "ban": {"ban", "banned", "bans"},
    "sign": {"sign", "signed", "signs", "signing"},
    "acquire": {"acquire", "acquired", "acquires"},
    "win": {"win", "won", "wins"},
    "defeat": {"defeat", "defeats"},
    "beat": {"beat", "beats"},
    "appoint": {"appoint", "appointed", "appoints"},
    "resign": {"resign", "resigned", "resigns"},
    "arrest": {"arrest", "arrested", "arrests"},
    "die": {"die", "dies", "died"},
    "injure": {"injure", "injured"},
    "qualify": {"qualify", "qualified", "qualifies"},
    "eliminate": {"eliminate", "eliminated"},
    "release": {"release", "released", "releases"},
    "delay": {"delay", "delayed", "delays"},
    "cancel": {"cancel", "cancelled", "cancels"},
    "join": {"join", "joins", "joined"},
    "open": {"open", "opened", "opens"},
    "close": {"close", "closed", "closes"},
}

EVENT_ACTION_LOOKUP = {
    form: family
    for family, forms in EVENT_ACTION_FAMILIES.items()
    for form in forms
}

GENERIC_ENTITY_TOKENS = {
    "india", "indian", "world", "global", "government", "minister", "president",
    "prime", "state", "city", "country", "company", "group", "team", "market",
    "court", "police", "officials", "people", "agency", "official",
}

ENTITY_NOISE = {
    "today", "latest", "breaking", "update", "news", "report", "reports",
    "says", "said", "after", "before", "new", "first", "major", "live",
    "watch", "here", "just", "now", "this", "that",
}


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


def _salient_entities(value: object) -> set[str]:
    """Extract cheap, deterministic named-entity proxies from headlines/text."""
    text = _clean(value)
    entities: set[str] = set()

    # Acronyms and proper-case words/phrases are useful without a heavyweight
    # NLP dependency. Keep these conservative to avoid treating every noun as
    # an entity.
    for match in re.findall(r"\b[A-Z]{2,}(?:[-&][A-Z]{2,})?\b", text):
        token = match.lower().strip()
        if token not in ENTITY_NOISE:
            entities.add(token)

    for match in re.findall(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,}){0,2}\b", text):
        phrase = re.sub(r"\s+", " ", match).strip().lower()
        words = phrase.split()
        if any(word not in ENTITY_NOISE for word in words):
            entities.add(phrase)

    # Mixed-case brand/product names such as OpenAI, ChatGPT and iPhone are
    # useful entity anchors even when they do not start with a capital letter.
    for match in re.findall(r"\b[A-Za-z]*[A-Z][A-Za-z0-9-]*\b", text):
        token = match.lower()
        if len(token) >= 4 and token not in ENTITY_NOISE:
            entities.add(token)

    return entities


def _event_actions(value: object) -> set[str]:
    tokens = _tokens(value)
    actions = {
        EVENT_ACTION_LOOKUP[token]
        for token in tokens
        if token in EVENT_ACTION_LOOKUP
    }
    # Multi-word variants such as “lifts off” / “takes off” are common in
    # news headlines and should resolve to the same launch event family.
    text = re.sub(r"[^a-z0-9]+", " ", _clean(value).lower())
    if re.search(r"\blifts? off\b|\btook off\b|\btakes? off\b", text):
        actions.add("launch")
    return actions


def _entity_context(story: dict) -> set[str]:
    return _salient_entities(
        " ".join(
            str(story.get(key) or "")
            for key in ("title", "description", "summary", "snippet", "text")
        )
    )


def _action_context(story: dict) -> set[str]:
    return _event_actions(
        " ".join(
            str(story.get(key) or "")
            for key in ("title", "description", "summary", "snippet", "text")
        )
    )


def _identity_features(story: dict) -> dict:
    return {
        "entities": sorted(_entity_context(story)),
        "actions": sorted(_action_context(story)),
        "tokens": sorted(_tokens(story.get("title"))),
    }


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
        for fmt in ("%Y%m%d%H%M%S", "%Y%m%dT%H%M%S", "%Y%m%dT%H%M%SZ"):
            try:
                return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def _cluster_compatible(left: dict, right: dict) -> bool:
    """Decide whether two articles likely describe the same real-world event."""
    overlap = _token_overlap(left.get("title"), right.get("title"))
    left_entities = set(left.get("identity_entities") or _entity_context(left))
    right_entities = set(right.get("identity_entities") or _entity_context(right))
    shared_entities = left_entities & right_entities

    left_actions = set(left.get("identity_actions") or _action_context(left))
    right_actions = set(right.get("identity_actions") or _action_context(right))
    shared_actions = left_actions & right_actions

    # Keep clustering deliberately small and evidence-based:
    # 1) very similar headlines must also agree on the event action;
    # 2) differently worded reports can merge when they share two entities
    #    and the same action;
    # 3) a single shared entity is enough only when there is also a shared
    #    action and a distinctive topical anchor.
    if overlap >= 0.62 and (
        not left_actions or not right_actions or shared_actions
    ):
        return True

    if len(shared_entities) >= 2 and shared_actions:
        return True

    shared_topical_tokens = (
        _tokens(left.get("title"))
        & _tokens(right.get("title"))
        - left_entities
        - right_entities
        - left_actions
        - right_actions
    )
    distinctive_topical_tokens = {
        token
        for token in shared_topical_tokens
        if token not in GENERIC_EVENT_TOPIC_TOKENS
    }
    return bool(
        len(shared_entities) >= 1
        and shared_actions
        and distinctive_topical_tokens
    )


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
        identity = _identity_features(item)
        item["identity_entities"] = identity["entities"]
        item["identity_actions"] = identity["actions"]
        item["identity_title_tokens"] = identity["tokens"]
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
        evidence_publishers = sorted({
            _clean(item.get("publisher_normalized"))
            for item in cluster
            if _clean(item.get("publisher_normalized"))
            and _clean(item.get("collection_source")).lower() not in {"reddit", "social"}
        })
        domains = sorted({
            urlparse(item["url"]).netloc.lower().removeprefix("www.")
            for item in cluster if item.get("url")
        })
        article_count = len(cluster)
        source_count = len(evidence_publishers or domains)

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

        event_genres = sorted({
            _clean(item.get("genre"))
            for item in cluster
            if _clean(item.get("genre"))
        })
        published_times = [
            item.get("published_dt")
            for item in cluster
            if item.get("published_dt") is not None
        ]
        first_seen = min(published_times) if published_times else None
        latest_seen = max(published_times) if published_times else None
        gdelt_count = sum(
            1 for item in cluster
            if _clean(item.get("collection_source")).lower() == "gdelt"
        )
        non_gdelt_count = article_count - gdelt_count
        if latest_seen and first_seen:
            span_hours = max(
                0.25,
                (latest_seen - first_seen).total_seconds() / 3600.0,
            )
            velocity_score = min(10.0, article_count / span_hours)
        else:
            velocity_score = 0.0
        action_count = len({
            action
            for item in cluster
            for action in (item.get("identity_actions") or [])
        })
        if article_count == 1:
            development_state = "single-source"
        elif action_count >= 2 or velocity_score >= 2.0:
            development_state = "developing"
        elif latest_seen and (datetime.now(timezone.utc) - latest_seen).total_seconds() <= 3 * 3600:
            development_state = "breaking"
        else:
            development_state = "established"
        representative.update({
            "event_id": _event_id(cluster),
            "event_genres": event_genres,
            "primary_genre": (
                event_genres[0]
                if len(event_genres) == 1
                else _clean(representative.get("genre"))
            ),
            "event_search_text": " ".join(
                _clean(item.get("title")) for item in cluster[:max_articles_per_event]
            ),
            "event_entities": sorted({
                entity
                for item in cluster
                for entity in (item.get("identity_entities") or [])
            }),
            "event_actions": sorted({
                action
                for item in cluster
                for action in (item.get("identity_actions") or [])
            }),
            "event_clustered": True,
            "event_article_count": article_count,
            "event_source_count": source_count,
            "event_total_publisher_count": len(publishers),
            "event_publishers": publishers,
            "event_evidence_publishers": evidence_publishers,
            "event_source_domains": domains,
            "event_evidence": evidence,
            "event_cluster_size": article_count,
            "event_corroboration_score": min(10.0, source_count * 2.0),
            "event_latest_published_at": (
                cluster[0]["published_dt"].isoformat()
                if cluster[0].get("published_dt")
                else _clean(cluster[0].get("publishedAt"))
            ),
            "event_first_seen_at": first_seen.isoformat() if first_seen else "",
            "event_latest_seen_at": latest_seen.isoformat() if latest_seen else "",
            "event_velocity_score": round(velocity_score, 3),
            "event_development_state": development_state,
            "event_gdelt_article_count": gdelt_count,
            "event_non_gdelt_article_count": non_gdelt_count,
            "event_discovery_gap": bool(gdelt_count and not non_gdelt_count),
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
    timeout: float = GDELT_TIMEOUT_SECONDS,
) -> list[dict]:
    """Fetch one bounded supplemental GDELT pool without blocking discovery."""
    global _GDELT_FAILURE_UNTIL, _GDELT_FAILURE_LOGGED

    query = _clean(query)
    if not query:
        return []

    now = time.time()
    if now < _GDELT_FAILURE_UNTIL:
        return []

    try:
        response = requests.get(
            GDELT_ENDPOINT,
            params={
                "query": query,
                "mode": "artlist",
                "maxrecords": max(1, min(100, int(max_records))),
                "timespan": timespan,
                "sort": "datedesc",
                "format": "json",
            },
            headers={"User-Agent": "ViralShortsFactory/2026 discovery/1.0"},
            timeout=max(1.0, min(GDELT_TIMEOUT_SECONDS, float(timeout))),
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.Timeout, requests.ConnectionError) as exc:
        _GDELT_FAILURE_UNTIL = time.time() + GDELT_FAILURE_COOLDOWN_SECONDS
        if not _GDELT_FAILURE_LOGGED:
            print(
                f"   [Discovery] GDELT unavailable ({type(exc).__name__}); skipping GDELT for the next {int(GDELT_FAILURE_COOLDOWN_SECONDS)}s.",
                flush=True,
            )
            _GDELT_FAILURE_LOGGED = True
        return []
    except requests.RequestException as exc:
        _GDELT_FAILURE_UNTIL = time.time() + GDELT_FAILURE_COOLDOWN_SECONDS
        if not _GDELT_FAILURE_LOGGED:
            print(
                f"   [Discovery] GDELT unavailable (HTTP/network {type(exc).__name__}); skipping GDELT for the next {int(GDELT_FAILURE_COOLDOWN_SECONDS)}s.",
                flush=True,
            )
            _GDELT_FAILURE_LOGGED = True
        return []
    except (ValueError, TypeError) as exc:
        _GDELT_FAILURE_UNTIL = time.time() + GDELT_FAILURE_COOLDOWN_SECONDS
        if not _GDELT_FAILURE_LOGGED:
            print(
                f"   [Discovery] GDELT returned an invalid response ({type(exc).__name__}); skipping GDELT for the next {int(GDELT_FAILURE_COOLDOWN_SECONDS)}s.",
                flush=True,
            )
            _GDELT_FAILURE_LOGGED = True
        return []
    except Exception as exc:
        _GDELT_FAILURE_UNTIL = time.time() + GDELT_FAILURE_COOLDOWN_SECONDS
        if not _GDELT_FAILURE_LOGGED:
            print(
                f"   [Discovery] GDELT failed ({type(exc).__name__}); skipping GDELT for the next {int(GDELT_FAILURE_COOLDOWN_SECONDS)}s.",
                flush=True,
            )
            _GDELT_FAILURE_LOGGED = True
        return []

    _GDELT_FAILURE_UNTIL = 0.0
    _GDELT_FAILURE_LOGGED = False

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
    "_cluster_compatible",
    "_identity_features",
]
