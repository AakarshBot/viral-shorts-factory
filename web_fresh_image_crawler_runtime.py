"""Fresh web image crawler for current-story visual pools.

The crawler is deliberately independent of the factory's manual visual-search
queries. It discovers recent news coverage, scrapes the article pages for the
actual article images, keeps only coverage inside the configured freshness
window, and returns dashboard-ready candidates.

It is a discovery layer, not a rights-clearing layer. Web images retain source
and provenance metadata so the existing dashboard can show the publisher name.
Ambiguous candidates may be checked by the existing Gemini identity QA; strong
article-image matches bypass that AI call.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from PIL import Image

CRAWLER_MAX_AGE_HOURS = max(24, min(96, int(os.getenv("VISUAL_WEB_CRAWLER_MAX_AGE_HOURS", "72"))))
CRAWLER_TARGET = max(10, min(15, int(os.getenv("VISUAL_WEB_CRAWLER_TARGET", "15"))))
CRAWLER_SUCCESS = max(10, min(CRAWLER_TARGET, int(os.getenv("VISUAL_WEB_CRAWLER_SUCCESS", "10"))))
CRAWLER_NEWS_RESULTS_PER_QUERY = max(6, min(15, int(os.getenv("VISUAL_WEB_CRAWLER_NEWS_RESULTS", "12"))))
CRAWLER_MAX_ARTICLES = max(4, min(12, int(os.getenv("VISUAL_WEB_CRAWLER_ARTICLES", "10"))))
CRAWLER_ARTICLE_IMAGES = max(3, min(8, int(os.getenv("VISUAL_WEB_CRAWLER_IMAGES_PER_ARTICLE", "6"))))
CRAWLER_QUERY_COUNT = max(2, min(4, int(os.getenv("VISUAL_WEB_CRAWLER_QUERY_COUNT", "3"))))
CRAWLER_IMAGE_RESULTS_PER_QUERY = max(8, min(10, int(os.getenv("VISUAL_WEB_CRAWLER_IMAGE_RESULTS", "10"))))
CRAWLER_IMAGE_PAGE_SCRAPES = max(2, min(6, int(os.getenv("VISUAL_WEB_CRAWLER_IMAGE_PAGE_SCRAPES", "5"))))

_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "for", "from",
    "by", "with", "after", "before", "during", "over", "into", "about", "this",
    "that", "these", "those", "is", "are", "was", "were", "be", "been", "being",
    "has", "have", "had", "will", "would", "could", "should", "says", "said",
    "report", "reports", "latest", "news", "story", "update", "today",
}

_STRONG_IMAGE_METHODS = {
    "og:image",
    "og:image:url",
    "og:image:secure_url",
    "twitter:image",
    "twitter:image:src",
    "json-ld:image",
    "meta:image",
}


def _clean(value: Any, limit: int = 5000) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _tokens(value: str) -> set[str]:
    return {
        token.casefold()
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9'’.-]*", _clean(value))
        if len(token) > 2 and token.casefold() not in _STOPWORDS
    }


def _parse_datetime(value: Any) -> datetime | None:
    text = _clean(value, 200)
    if not text:
        return None
    candidates = [text, text.replace("Z", "+00:00")]
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
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
        return None


def _age_hours(published_at: datetime | None, now: datetime) -> float | None:
    if published_at is None:
        return None
    age = (now - published_at).total_seconds() / 3600.0
    if age < -0.25 or age > CRAWLER_MAX_AGE_HOURS:
        return None
    return max(0.0, age)


def _freshness_score(age_hours: float) -> float:
    return max(0.0, 30.0 * (1.0 - (age_hours / CRAWLER_MAX_AGE_HOURS)))


def _similarity(query_text: str, candidate_text: str, entity: str) -> float:
    q_tokens = _tokens(query_text)
    c_tokens = _tokens(candidate_text)
    if not q_tokens or not c_tokens:
        return 0.0
    overlap = len(q_tokens & c_tokens) / max(1, len(q_tokens))
    entity_tokens = _tokens(entity)
    entity_hit = (
        len(entity_tokens & c_tokens) / max(1, len(entity_tokens))
        if entity_tokens
        else 0.0
    )
    return min(1.0, (overlap * 0.65) + (entity_hit * 0.35))


def _entity_match(entity: str, text: str) -> bool:
    entity_tokens = _tokens(entity)
    if not entity_tokens:
        return False
    text_tokens = _tokens(text)
    return len(entity_tokens & text_tokens) >= max(1, int(round(len(entity_tokens) * 0.7)))


def _build_queries(
    story_title: str,
    story_text: str,
    entity: str,
    category: str,
) -> list[str]:
    title = _clean(story_title, 240)
    subject = _clean(entity, 120)
    title_tokens = [token for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9'’.-]*", title)
                    if len(token) > 2 and token.casefold() not in _STOPWORDS]
    variants: list[str] = []

    if title:
        variants.append(title)

    distinctive = []
    seen = set()
    for token in title_tokens + list(_tokens(story_text)):
        key = token.casefold()
        if key in seen or key in _STOPWORDS:
            continue
        seen.add(key)
        distinctive.append(token)
        if len(distinctive) >= 4:
            break
    if subject and distinctive:
        variants.append(f"{subject} {' '.join(distinctive[:4])}".strip())
    if subject:
        variants.append(f"{subject} {category}".strip() if category else subject)

    output: list[str] = []
    seen_queries = set()
    for query in variants:
        value = _clean(query, 240)
        key = value.casefold()
        if not value or key in seen_queries:
            continue
        seen_queries.add(key)
        output.append(value)
        if len(output) >= CRAWLER_QUERY_COUNT:
            break
    return output


def _news_search(query: str) -> list[dict[str, Any]]:
    """Search news with an explicit backend so one failing engine cannot erase results."""
    try:
        from ddgs import DDGS

        search = DDGS(timeout=8)
        try:
            results = search.news(
                query=query,
                region="us-en",
                safesearch="moderate",
                timelimit="w",
                max_results=CRAWLER_NEWS_RESULTS_PER_QUERY,
                backend="bing",
            )
        except Exception:
            results = []

        if results:
            return [dict(item) for item in results if isinstance(item, dict)]

        try:
            results = search.news(
                query=query,
                region="us-en",
                safesearch="moderate",
                timelimit="w",
                max_results=CRAWLER_NEWS_RESULTS_PER_QUERY,
                backend="yahoo",
            )
            return [dict(item) for item in (results or []) if isinstance(item, dict)]
        except Exception:
            return []
    except Exception as exc:
        print(
            f"   [Fresh Web Crawler] news search failed: {type(exc).__name__}: {exc} | query='{query}'",
            flush=True,
        )
        return []


def _image_search(query: str) -> list[dict[str, Any]]:
    """Search the current web image index without launching a browser or adding a dependency."""
    try:
        from ddgs import DDGS

        search = getattr(DDGS(timeout=8), "images", None)
        if not callable(search):
            return []

        request = {
            "query": query,
            "region": "us-en",
            "safesearch": "moderate",
            # Image-index results do not provide a trustworthy publication date.
            # Freshness is verified from the source page when available.
            "timelimit": None,
            "max_results": CRAWLER_IMAGE_RESULTS_PER_QUERY,
            "page": 1,
            "size": "Large",
            "type_image": "photo",
        }

        # Keep the primary image lane deterministic: Bing first, then a
        # separate DuckDuckGo fallback only when Bing returns nothing.
        try:
            results = search(backend="bing", **request)
        except Exception:
            results = []
        if results:
            return [dict(item) for item in results if isinstance(item, dict)]

        try:
            results = search(backend="duckduckgo", **request)
            return [dict(item) for item in (results or []) if isinstance(item, dict)]
        except Exception:
            return []
    except Exception as exc:
        print(
            f"   [Fresh Web Crawler] image search failed: {type(exc).__name__}: {exc} | query='{query}'",
            flush=True,
        )
        return []


def _image_search_publisher(result: dict[str, Any]) -> str:
    source = _clean(result.get("source"), 160)
    if source and source.casefold() not in {"bing", "duckduckgo"}:
        return source
    page_url = _clean(result.get("url") or result.get("image"), 2000)
    try:
        from urllib.parse import urlparse
        host = urlparse(page_url).netloc.removeprefix("www.")
        if host:
            return host
    except Exception:
        pass
    return source or "Web image source"


def _image_search_candidate(
    result: dict[str, Any],
    query: str,
    story_title: str,
    entity: str,
) -> dict[str, Any] | None:
    image_url = _clean(result.get("image"), 2000)
    page_url = _clean(result.get("url"), 2000)
    title = _clean(result.get("title"), 500)
    if not image_url:
        return None

    try:
        from news_source_image_runtime import fetch_direct_source_image
    except Exception:
        return None

    publisher = _image_search_publisher(result)
    fetched = fetch_direct_source_image(image_url, page_url, publisher)
    if not isinstance(fetched, dict) or not fetched.get("bytes"):
        return None

    data = fetched["bytes"]
    width, height = _image_dimensions(data)
    if min(width, height) < 500:
        return None

    search_text = _clean(
        " ".join(
            str(value)
            for value in (title, result.get("source"), publisher, page_url)
        ),
        3500,
    )
    relevance = _similarity(query, title or page_url, entity)
    entity_present = _entity_match(entity, f"{title} {page_url}") if entity else True
    if entity and not entity_present:
        return None
    if relevance < 0.10:
        return None

    # Image results expose a search freshness window, not a verified publication date.
    freshness_score = 18.0
    resolution_score = min(18.0, 18.0 * min(width, height) / 1800.0)
    source_match_bonus = 18.0 if entity and _entity_match(entity, title) else 0.0
    priority = round(
        freshness_score + relevance * 46.0 + resolution_score + source_match_bonus,
        3,
    )

    actual_image_url = _clean(fetched.get("image_url") or image_url, 2000)
    actual_page_url = _clean(fetched.get("page_url") or page_url or actual_image_url, 2000)
    actual_publisher = _clean(fetched.get("publisher") or publisher, 160) or publisher
    content_hash = hashlib.sha256(data).hexdigest()
    provenance = {
        "provider": actual_publisher,
        "url": actual_page_url or actual_image_url,
        "author": actual_publisher,
        "license": "Unverified web image license",
        "license_url": actual_page_url or actual_image_url,
    }

    return {
        "bytes": data,
        "hash": content_hash,
        "source": "web_image_search",
        "source_type": "web_image_search",
        "source_name": actual_publisher,
        "credit": f"Source: {actual_publisher}",
        "query": query,
        "source_page_url": actual_page_url,
        "source_image_url": actual_image_url,
        "publisher": actual_publisher,
        "image_search_title": title,
        "image_search_engine_source": _clean(result.get("source"), 120),
        "image_width": width,
        "image_height": height,
        "crawler_freshness_basis": "search-window:7d",
        "crawler_age_hours": None,
        "crawler_image_method": "ddgs-images",
        "crawler_confidence": "ambiguous",
        "crawler_relevance": round(relevance, 3),
        "visual_type": "PERSON" if entity else "GENERAL_CONTEXT",
        "visual_genre": "PERSON_ACTION" if entity else "GENERAL_PHOTO",
        "provenance": provenance,
        "provenance_status": "provenance-review",
        "priority": priority,
        "search_text": search_text,
        "status": "crawler-ai-pending",
        "used": False,
    }


def _collect_image_search_assets(
    queries: list[str],
    story_title: str,
    entity: str,
) -> tuple[list[dict[str, Any]], list[tuple[str, dict[str, Any]]]]:
    raw_results: list[tuple[str, dict[str, Any]]] = []
    if not queries:
        return [], []
    with ThreadPoolExecutor(max_workers=min(3, len(queries))) as executor:
        futures = {executor.submit(_image_search, query): query for query in queries}
        for future, query in futures.items():
            try:
                results = future.result()
            except Exception:
                results = []
            for result in results:
                raw_results.append((query, result))

    candidates: list[dict[str, Any]] = []
    if not raw_results:
        return candidates, raw_results
    with ThreadPoolExecutor(max_workers=min(8, len(raw_results))) as executor:
        futures = [
            executor.submit(_image_search_candidate, result, query, story_title, entity)
            for query, result in raw_results
        ]
        for future in futures:
            try:
                candidate = future.result()
            except Exception:
                candidate = None
            if candidate:
                candidates.append(candidate)
    return candidates, raw_results


def _web_search(query: str) -> list[dict[str, Any]]:
    """Recover publisher pages when image/news search returns nothing."""
    try:
        from ddgs import DDGS

        results = DDGS(timeout=8).text(
            query=query,
            region="us-en",
            safesearch="moderate",
            timelimit="w",
            max_results=CRAWLER_NEWS_RESULTS_PER_QUERY,
            page=1,
            backend="bing,brave,google,yahoo",
        )
        return [dict(item) for item in (results or []) if isinstance(item, dict)]
    except Exception as exc:
        print(
            f"   [Fresh Web Crawler] text search failed: {type(exc).__name__}: {exc} | query='{query}'",
            flush=True,
        )
        return []


def _collect_recent_web_pages(
    queries: list[str],
    story_title: str,
    entity: str,
    now: datetime,
) -> list[dict[str, Any]]:
    """Use general web search as the final discovery fallback before renderer rescue."""
    raw: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(3, len(queries))) as executor:
        futures = {executor.submit(_web_search, query): query for query in queries}
        for future, query in futures.items():
            try:
                results = future.result()
            except Exception:
                results = []
            for item in results:
                item["_crawler_query"] = query
                raw.append(item)

    ranked: list[tuple[float, dict[str, Any]]] = []
    seen_urls: set[str] = set()
    for item in raw:
        url = _clean(item.get("href") or item.get("url"), 2000)
        if not url or url in seen_urls:
            continue
        title = _clean(item.get("title"), 500)
        body = _clean(item.get("body") or item.get("description"), 1500)
        combined = f"{title} {body}"
        relevance = _similarity(story_title, combined, entity)
        entity_present = _entity_match(entity, combined) if entity else True
        if entity and not entity_present:
            continue
        if relevance < 0.08:
            continue
        published = _parse_datetime(
            item.get("date")
            or item.get("published")
            or item.get("published_at")
        )
        # General web search may omit a publication date. Keep it as a
        # discovery fallback rather than pretending it is verified fresh news.
        if published is not None and _age_hours(published, now) is None:
            continue
        seen_urls.add(url)
        ranked.append((relevance, {**item, "url": url, "date": published.isoformat() if published else ""}))

    ranked.sort(key=lambda pair: (-pair[0], _clean(pair[1].get("title")).casefold()))
    return [item for _score, item in ranked[:CRAWLER_MAX_ARTICLES]]
    

def _article_rank(article: dict[str, Any], story_title: str, entity: str, now: datetime) -> float:
    title = _clean(article.get("title"))
    body = _clean(article.get("body"))
    published = _parse_datetime(article.get("date"))
    age = _age_hours(published, now)
    if age is None:
        return -1000.0

    title_similarity = _similarity(story_title, title, entity)
    body_similarity = _similarity(story_title, body, entity)
    entity_bonus = 30.0 if _entity_match(entity, f"{title} {body}") else 0.0
    return (
        _freshness_score(age)
        + (title_similarity * 42.0)
        + (body_similarity * 12.0)
        + entity_bonus
    )


def _image_result_page_candidate(
    asset: dict[str, Any],
    result: dict[str, Any],
    query: str,
    entity: str,
) -> dict[str, Any] | None:
    """Turn a source-page scrape of an image result into a dashboard candidate."""
    data = asset.get("bytes")
    if not isinstance(data, (bytes, bytearray, memoryview)):
        return None
    data = bytes(data)
    width, height = _image_dimensions(data)
    if min(width, height) < 500:
        return None

    result_title = _clean(result.get("title"), 500)
    result_url = _clean(result.get("url"), 2000)
    page_url = _clean(asset.get("page_url") or result_url, 2000)
    image_url = _clean(asset.get("image_url") or asset.get("source_image_url"), 2000)
    publisher = (
        _clean(asset.get("publisher"), 160)
        or _clean(result.get("source"), 160)
        or _image_search_publisher(result)
        or "Web source"
    )
    relevance = _similarity(query, result_title or page_url, entity)
    entity_present = _entity_match(entity, f"{result_title} {page_url}") if entity else True
    if entity and not entity_present:
        return None
    if relevance < 0.05:
        return None

    method = _clean(asset.get("method"), 120) or "source-page-image"
    method_score = 24.0 if method in _STRONG_IMAGE_METHODS else 14.0
    resolution_score = min(14.0, 14.0 * min(width, height) / 1600.0)
    high_confidence = (
        method in _STRONG_IMAGE_METHODS
        and relevance >= 0.42
        and _entity_match(entity, result_title)
    )
    priority = round(18.0 + relevance * 46.0 + method_score + resolution_score, 3)

    return {
        "bytes": data,
        "hash": hashlib.sha256(data).hexdigest(),
        "source": "web_crawler",
        "source_type": "web_crawler",
        "source_name": publisher,
        "credit": f"Source: {publisher}",
        "query": query,
        "source_page_url": page_url,
        "source_image_url": image_url,
        "publisher": publisher,
        "image_search_title": result_title,
        "crawler_freshness_basis": "search-window:7d",
        "crawler_age_hours": None,
        "crawler_image_method": method,
        "crawler_confidence": "high" if high_confidence else "ambiguous",
        "crawler_relevance": round(relevance, 3),
        "visual_type": "PERSON" if entity else "GENERAL_CONTEXT",
        "visual_genre": "PERSON_ACTION" if entity else "GENERAL_PHOTO",
        "provenance": {
            "provider": publisher,
            "url": page_url or image_url,
            "author": publisher,
            "license": "Unverified web source",
            "license_url": page_url or image_url,
        },
        "provenance_status": "provenance-review",
        "priority": priority,
        "search_text": _clean(f"{result_title} {publisher} {page_url}", 3500),
        "status": "crawler-high-confidence" if high_confidence else "crawler-ai-pending",
        "used": False,
    }


def _collect_image_result_page_assets(
    raw_results: list[tuple[str, dict[str, Any]]],
    story_title: str,
    entity: str,
) -> tuple[list[dict[str, Any]], int]:
    """Scrape a small number of image-result source pages when direct URLs reject."""
    try:
        from news_source_image_runtime import extract_news_source_images
    except Exception:
        return [], 0

    unique_results: list[tuple[str, dict[str, Any]]] = []
    seen_pages: set[str] = set()
    for query, result in raw_results:
        page_url = _clean(result.get("url"), 2000)
        if not page_url or page_url in seen_pages:
            continue
        seen_pages.add(page_url)
        unique_results.append((query, result))
        if len(unique_results) >= CRAWLER_IMAGE_PAGE_SCRAPES:
            break

    def scrape_one(item: tuple[str, dict[str, Any]]) -> list[dict[str, Any]]:
        query, result = item
        page_url = _clean(result.get("url"), 2000)
        publisher = _image_search_publisher(result)
        if not page_url:
            return []
        try:
            assets = extract_news_source_images(
                page_url,
                publisher,
                max_images=CRAWLER_ARTICLE_IMAGES,
            )
        except Exception:
            return []
        candidates: list[dict[str, Any]] = []
        for asset in assets:
            if isinstance(asset, dict):
                candidate = _image_result_page_candidate(asset, result, query, entity)
                if candidate:
                    candidates.append(candidate)
        return candidates

    candidates: list[dict[str, Any]] = []
    if unique_results:
        with ThreadPoolExecutor(max_workers=min(4, len(unique_results))) as executor:
            futures = [executor.submit(scrape_one, item) for item in unique_results]
            for future in futures:
                try:
                    candidates.extend(future.result())
                except Exception:
                    continue
    return candidates, len(unique_results)


def _collect_recent_articles(
    queries: list[str],
    story_title: str,
    entity: str,
    now: datetime,
) -> list[dict[str, Any]]:
    raw: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(3, len(queries))) as executor:
        futures = {executor.submit(_news_search, query): query for query in queries}
        for future, query in futures.items():
            try:
                results = future.result()
            except Exception:
                results = []
            for item in results:
                item["_crawler_query"] = query
                raw.append(item)

    ranked: list[tuple[float, dict[str, Any]]] = []
    seen_urls: set[str] = set()
    for item in raw:
        url = _clean(item.get("url"), 2000)
        if not url or url in seen_urls:
            continue
        title = _clean(item.get("title"))
        body = _clean(item.get("body"))
        relevance = _similarity(story_title, title, entity)
        entity_present = _entity_match(entity, f"{title} {body}") if entity else True
        if not entity_present or relevance < 0.12:
            continue
        published = _parse_datetime(item.get("date"))
        age = _age_hours(published, now)
        if age is None:
            continue
        seen_urls.add(url)
        ranked.append((_article_rank(item, story_title, entity, now), item))

    ranked.sort(key=lambda pair: (-pair[0], _clean(pair[1].get("title")).casefold()))
    return [item for _score, item in ranked[:CRAWLER_MAX_ARTICLES]]


def _image_dimensions(data: bytes) -> tuple[int, int]:
    try:
        with Image.open(io.BytesIO(data)) as image:
            return image.size
    except Exception:
        return (0, 0)


def _candidate_from_asset(
    asset: dict[str, Any],
    article: dict[str, Any],
    story_title: str,
    entity: str,
    now: datetime,
    *,
    allow_search_freshness: bool = False,
) -> dict[str, Any] | None:
    data = asset.get("bytes")
    if not isinstance(data, (bytes, bytearray, memoryview)):
        return None
    data = bytes(data)
    width, height = _image_dimensions(data)
    if min(width, height) < 500:
        return None

    published = _parse_datetime(article.get("date"))
    age = _age_hours(published, now)
    if age is None and not allow_search_freshness:
        return None

    article_title = _clean(article.get("title"), 500)
    article_body = _clean(article.get("body"), 1500)
    image_method = _clean(asset.get("method"), 120)
    search_text = _clean(
        " ".join(
            str(value)
            for value in (
                article_title,
                article_body,
                asset.get("publisher"),
                asset.get("method"),
                asset.get("credit"),
            )
        ),
        4000,
    )
    relevance = _similarity(story_title, f"{article_title} {article_body}", entity)
    entity_present = _entity_match(entity, f"{article_title} {article_body}") if entity else True
    if not entity_present or relevance < 0.12:
        return None

    method_score = 24.0 if image_method in _STRONG_IMAGE_METHODS else 14.0
    resolution_score = min(14.0, 14.0 * min(width, height) / 1600.0)
    priority = round(
        (_freshness_score(age) if age is not None else 18.0)
        + relevance * 42.0
        + method_score
        + resolution_score,
        3,
    )

    high_confidence = (
        image_method in _STRONG_IMAGE_METHODS
        and relevance >= 0.42
        and _entity_match(entity, article_title or article_body)
    )

    publisher = (
        _clean(asset.get("publisher"), 160)
        or _clean(article.get("source"), 160)
        or _clean(article.get("source_url"), 160)
        or "Web source"
    )
    page_url = _clean(
        asset.get("page_url") or article.get("url") or "",
        2000,
    )
    image_url = _clean(
        asset.get("image_url") or asset.get("source_image_url") or article.get("image") or "",
        2000,
    )
    provenance = {
        "provider": publisher,
        "url": page_url or image_url,
        "author": publisher,
        "license": "Unverified web source",
        "license_url": page_url or image_url,
    }

    return {
        "bytes": data,
        "hash": hashlib.sha256(data).hexdigest(),
        "source": "web_crawler",
        "source_type": "web_crawler",
        "source_name": publisher,
        "credit": f"Source: {publisher}",
        "query": article_title or story_title,
        "source_page_url": page_url,
        "source_image_url": image_url,
        "publisher": publisher,
        "article_title": article_title,
        "published_at": published.isoformat() if published else "",
        "crawler_age_hours": round(age, 2) if age is not None else None,
        "crawler_freshness_basis": (
            "publication-date"
            if age is not None
            else "search-window:7d"
        ),
        "crawler_image_method": image_method,
        "crawler_confidence": "high" if high_confidence else "ambiguous",
        "crawler_relevance": round(relevance, 3),
        "visual_type": "PERSON" if entity else "GENERAL_CONTEXT",
        "visual_genre": "PERSON_ACTION" if entity else "GENERAL_PHOTO",
        "provenance": provenance,
        "provenance_status": "provenance-review",
        "priority": priority,
        "search_text": search_text,
        "status": "crawler-high-confidence" if high_confidence else "crawler-ai-pending",
        "used": False,
    }


def _scrape_article(article: dict[str, Any], story_title: str, entity: str, now: datetime) -> list[dict[str, Any]]:
    try:
        from news_source_image_runtime import (
            extract_news_source_images,
            fetch_direct_source_image,
        )
    except Exception as exc:
        print(
            f"   [Fresh Web Crawler] article-image extractor unavailable: {type(exc).__name__}: {exc}",
            flush=True,
        )
        return []

    article_url = _clean(article.get("url"), 2000)
    if not article_url:
        return []

    try:
        assets = extract_news_source_images(
            article_url,
            _clean(article.get("source"), 160),
            max_images=CRAWLER_ARTICLE_IMAGES,
        )
    except Exception as exc:
        print(
            f"   [Fresh Web Crawler] scrape failed: {type(exc).__name__}: {exc} | url='{article_url}'",
            flush=True,
        )
        assets = []

    if not assets:
        direct_url = _clean(article.get("image"), 2000)
        if direct_url:
            try:
                fallback = fetch_direct_source_image(
                    direct_url,
                    article_url,
                    _clean(article.get("source"), 160),
                )
            except Exception:
                fallback = None
            if fallback:
                assets = [fallback]

    candidates: list[dict[str, Any]] = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        candidate = _candidate_from_asset(asset, article, story_title, entity, now)
        if candidate:
            candidates.append(candidate)
    return candidates


def _scrape_web_search_page(
    page: dict[str, Any],
    story_title: str,
    entity: str,
    now: datetime,
) -> list[dict[str, Any]]:
    """Scrape a general web-search result without inventing a publication date."""
    try:
        from news_source_image_runtime import extract_news_source_images
    except Exception:
        return []

    page_url = _clean(page.get("url"), 2000)
    if not page_url:
        return []

    title = _clean(page.get("title"), 500)
    body = _clean(page.get("body"), 1500)
    relevance = _similarity(story_title, f"{title} {body}", entity)
    if entity and not _entity_match(entity, f"{title} {body} {page_url}"):
        return []
    if relevance < 0.08:
        return []

    publisher = _clean(page.get("source"), 160) or _image_search_publisher({"url": page_url})
    try:
        assets = extract_news_source_images(
            page_url,
            publisher,
            max_images=CRAWLER_ARTICLE_IMAGES,
        )
    except Exception:
        return []

    candidates: list[dict[str, Any]] = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        candidate = _candidate_from_asset(
            asset,
            {
                "url": page_url,
                "title": title,
                "body": body,
                "source": publisher,
                "date": "",
            },
            story_title,
            entity,
            now,
            allow_search_freshness=True,
        )
        if candidate:
            candidate["crawler_freshness_basis"] = "search-window:7d"
            candidate["crawler_age_hours"] = None
            candidate["crawler_image_method"] = (
                _clean(asset.get("method"), 120) or "web-search-page"
            )
            candidates.append(candidate)
    return candidates


def _dedupe_assets(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    seen_urls: set[str] = set()
    for asset in sorted(
        assets,
        key=lambda item: (
            -float(item.get("priority") or 0.0),
            str(item.get("published_at") or ""),
            str(item.get("source_page_url") or ""),
        ),
    ):
        image_hash = _clean(asset.get("hash"), 120)
        image_url = _clean(asset.get("source_image_url"), 2000).casefold().rstrip("/")
        if image_hash and image_hash in seen_hashes:
            continue
        if image_url and image_url in seen_urls:
            continue
        if image_hash:
            seen_hashes.add(image_hash)
        if image_url:
            seen_urls.add(image_url)
        output.append(asset)
        if len(output) >= CRAWLER_TARGET:
            break
    return output


def _verify_ambiguous(
    candidates: list[dict[str, Any]],
    entity: str,
) -> tuple[list[dict[str, Any]], int, int]:
    high = [item for item in candidates if item.get("crawler_confidence") == "high"]
    ambiguous = [item for item in candidates if item.get("crawler_confidence") != "high"]
    if len(high) >= CRAWLER_SUCCESS:
        return high[:CRAWLER_TARGET], 0, 0
    if not ambiguous:
        return high[:CRAWLER_TARGET], 0, 0

    if not entity:
        limited = candidates[:CRAWLER_TARGET]
        for item in limited:
            item["status"] = "crawler-review-unverified"
        return limited, 0, len(limited)

    try:
        from visual_qa_runtime import (
            GEMINI_VISUAL_BATCH_SIZE,
            get_last_visual_qa_failure,
            start_visual_qa_scene,
            strict_gemini_check_batch,
        )

        api_key = str(os.getenv("GEMINI_API_KEY") or "").strip()
        if not api_key:
            limited = ambiguous[: max(0, CRAWLER_TARGET - len(high))]
            for item in limited:
                item["status"] = "crawler-review-unverified"
            return (high + limited)[:CRAWLER_TARGET], 0, len(limited)

        start_visual_qa_scene()
        batch = ambiguous[: min(10, max(GEMINI_VISUAL_BATCH_SIZE, len(ambiguous)))]
        batch = batch[:10]
        verdicts = strict_gemini_check_batch(
            [item["bytes"] for item in batch],
            entity,
            api_key,
            tier="IDENTITY",
            visual_type=str(batch[0].get("visual_type") or "GENERAL_CONTEXT"),
            visual_genre=str(batch[0].get("visual_genre") or "GENERAL_CONTEXT"),
        )
        checked = len(batch) if get_last_visual_qa_failure() != "circuit_breaker" else 0
        accepted = list(high)
        ai_rejected = 0
        for index, item in enumerate(batch):
            verdict = verdicts.get(index)
            if verdict is True:
                item["status"] = "crawler-ai-verified"
                item["crawler_confidence"] = "ai-verified"
                accepted.append(item)
            elif verdict is None:
                item["status"] = "crawler-review-unverified"
                accepted.append(item)
            else:
                ai_rejected += 1
            if len(accepted) >= CRAWLER_TARGET:
                break
        return accepted[:CRAWLER_TARGET], checked, ai_rejected
    except Exception:
        limited = ambiguous[: max(0, CRAWLER_TARGET - len(high))]
        for item in limited:
            item["status"] = "crawler-review-unverified"
        return (high + limited)[:CRAWLER_TARGET], 0, len(limited)


def crawl_fresh_web_images(
    selected_story: dict[str, Any] | None,
    scenes: list[dict[str, Any]] | None = None,
    story_title: str = "",
    entity: str = "",
    category: str = "",
) -> dict[str, Any]:
    """Return a fresh web image pool for the selected story.

    The crawler is independent of dashboard manual image queries. It searches
    recent coverage and scrapes source articles for real images.
    """
    story = selected_story if isinstance(selected_story, dict) else {}
    title = _clean(
        story.get("title")
        or story.get("headline")
        or story_title
        or "",
        320,
    )
    body = _clean(
        story.get("story_text")
        or story.get("description")
        or story.get("summary")
        or "",
        5000,
    )

    if not entity and scenes:
        for scene in scenes:
            if isinstance(scene, dict):
                entity = _clean(
                    scene.get("factual_primary_entity")
                    or scene.get("primary_entity")
                    or scene.get("visual_search_subject")
                    or "",
                    180,
                )
                if entity:
                    break

    queries = _build_queries(title, body, entity, category)
    if not queries:
        return {
            "assets": [],
            "target": CRAWLER_TARGET,
            "success_threshold": CRAWLER_SUCCESS,
            "queries": [],
            "articles": 0,
            "high_confidence": 0,
            "ai_checked": 0,
            "rejection_counts": {},
        }

    now = datetime.now(timezone.utc)

    # Direct image search is the fast primary lane. Article-page scraping is
    # deferred until the indexed image pool is too small, reducing runtime and
    # avoiding a pool dominated by repeated article hero images.
    image_search_assets, image_search_results = _collect_image_search_assets(queries, title, entity)
    image_search_candidates = _dedupe_assets(image_search_assets)
    image_result_page_assets: list[dict[str, Any]] = []
    image_result_pages = 0
    article_candidates: list[dict[str, Any]] = []
    articles: list[dict[str, Any]] = []

    if len(image_search_candidates) < CRAWLER_SUCCESS and image_search_results:
        image_result_page_assets, image_result_pages = _collect_image_result_page_assets(
            image_search_results,
            title,
            entity,
        )

    if len(image_search_candidates) + len(_dedupe_assets(image_result_page_assets)) < CRAWLER_SUCCESS:
        articles = _collect_recent_articles(queries, title, entity, now)
        if articles:
            with ThreadPoolExecutor(max_workers=min(5, len(articles))) as executor:
                futures = [
                    executor.submit(_scrape_article, article, title, entity, now)
                    for article in articles
                ]
                for future in futures:
                    try:
                        article_candidates.extend(future.result())
                    except Exception:
                        continue

    web_page_candidates: list[dict[str, Any]] = []
    web_pages = 0
    current_visual_count = len(_dedupe_assets(
        image_search_assets + image_result_page_assets + article_candidates
    ))
    # General web-text recovery is an emergency discovery lane. Do not pay
    # for it on every merely-small pool; only invoke it when the normal
    # image/news discovery produced no usable visual candidates.
    if current_visual_count == 0:
        recent_web_pages = _collect_recent_web_pages(queries, title, entity, now)
        web_pages = len(recent_web_pages)
        if recent_web_pages:
            with ThreadPoolExecutor(max_workers=min(5, len(recent_web_pages))) as executor:
                futures = [
                    executor.submit(_scrape_web_search_page, page, title, entity, now)
                    for page in recent_web_pages
                ]
                for future in futures:
                    try:
                        web_page_candidates.extend(future.result())
                    except Exception:
                        continue

    raw_assets = (
        image_search_assets
        + image_result_page_assets
        + article_candidates
        + web_page_candidates
    )
    candidates = _dedupe_assets(raw_assets)
    if not candidates:
        print(
            f"   [Fresh Web Crawler] no relevant current image/article candidates found. "
            f"image_results={len(image_search_results)} direct_images={len(image_search_assets)} "
            f"source_page_images={len(image_result_page_assets)} news_articles={len(articles)} "
            f"web_search_pages={web_pages} web_search_images={len(web_page_candidates)}",
            flush=True,
        )
        return {
            "assets": [],
            "target": CRAWLER_TARGET,
            "success_threshold": CRAWLER_SUCCESS,
            "queries": queries,
            "articles": len(articles),
            "high_confidence": 0,
            "ai_checked": 0,
            "rejection_counts": {
                "image_search_raw": len(image_search_results),
                "image_search_downloaded": len(image_search_assets),
                "image_result_pages": image_result_pages,
                "image_result_page_images": len(image_result_page_assets),
                "news_articles": len(articles),
                "web_search_pages": web_pages,
                "web_search_images": len(web_page_candidates),
                "no_current_visual_candidates": 1,
            },
        }
    selected, ai_checked, ai_rejected = _verify_ambiguous(candidates, entity)
    selected = _dedupe_assets(selected)

    high_count = sum(1 for item in selected if item.get("crawler_confidence") in {"high", "ai-verified"})
    rejection_counts = {
        "image_search_raw": len(image_search_results),
        "image_search_downloaded": len(image_search_assets),
        "image_search_candidates": len(image_search_candidates),
        "image_result_pages": image_result_pages,
        "image_result_page_images": len(image_result_page_assets),
        "article_candidates": len(articles),
        "article_images": len(article_candidates),
        "web_search_pages": web_pages,
        "web_search_images": len(web_page_candidates),
        "raw_images": len(raw_assets),
        "dedupe_rejected": max(0, len(raw_assets) - len(candidates)),
        "ai_checked": ai_checked,
        "ai_rejected": ai_rejected,
        "final_images": len(selected),
    }

    print(
        f"   [Fresh Web Crawler] queries={len(queries)} image_search={len(image_search_assets)} "
        f"articles={len(articles)} article_images={len(article_candidates)} "
        f"raw_images={len(raw_assets)} final_pool={len(selected)}/{CRAWLER_TARGET} "
        f"verified_or_high={high_count} AI_checked={ai_checked}",
        flush=True,
    )

    return {
        "assets": selected,
        "target": CRAWLER_TARGET,
        "success_threshold": CRAWLER_SUCCESS,
        "queries": queries,
        "articles": len(articles),
        "high_confidence": high_count,
        "ai_checked": ai_checked,
        "rejection_counts": rejection_counts,
    }


__all__ = [
    "CRAWLER_MAX_AGE_HOURS",
    "CRAWLER_TARGET",
    "CRAWLER_SUCCESS",
    "crawl_fresh_web_images",
]