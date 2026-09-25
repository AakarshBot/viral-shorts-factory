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
    try:
        from ddgs import DDGS

        results = DDGS(timeout=8).news(
            query=query,
            region="us-en",
            safesearch="moderate",
            timelimit="w",
            max_results=CRAWLER_NEWS_RESULTS_PER_QUERY,
            backend="auto",
        )
        return [dict(item) for item in (results or []) if isinstance(item, dict)]
    except Exception as exc:
        print(
            f"   [Fresh Web Crawler] news search failed: {type(exc).__name__}: {exc} | query='{query}'",
            flush=True,
        )
        return []


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
    if age is None:
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
        _freshness_score(age)
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
        "crawler_age_hours": round(age, 2),
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
    articles = _collect_recent_articles(queries, title, entity, now)
    if not articles:
        print("   [Fresh Web Crawler] no recent relevant articles found.", flush=True)
        return {
            "assets": [],
            "target": CRAWLER_TARGET,
            "success_threshold": CRAWLER_SUCCESS,
            "queries": queries,
            "articles": 0,
            "high_confidence": 0,
            "ai_checked": 0,
            "rejection_counts": {"no_recent_articles": 1},
        }

    raw_assets: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(5, len(articles))) as executor:
        futures = [
            executor.submit(_scrape_article, article, title, entity, now)
            for article in articles
        ]
        for future in futures:
            try:
                raw_assets.extend(future.result())
            except Exception:
                continue

    candidates = _dedupe_assets(raw_assets)
    selected, ai_checked, ai_rejected = _verify_ambiguous(candidates, entity)
    selected = _dedupe_assets(selected)

    high_count = sum(1 for item in selected if item.get("crawler_confidence") in {"high", "ai-verified"})
    rejection_counts = {
        "article_candidates": len(articles),
        "raw_images": len(raw_assets),
        "dedupe_rejected": max(0, len(raw_assets) - len(candidates)),
        "ai_checked": ai_checked,
        "ai_rejected": ai_rejected,
        "final_images": len(selected),
    }

    print(
        f"   [Fresh Web Crawler] queries={len(queries)} articles={len(articles)} "
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
