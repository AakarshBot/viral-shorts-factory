"""Fresh web image retrieval: current article discovery + browser-rendered scraping.

The active web lane deliberately does not use the factory's image search index.
It first discovers the newest relevant news articles whose significant query
terms appear in the article title, then renders those publisher pages in
Chromium and extracts the actual article images. If current articles do not
supply ten usable images, a small second web lane searches entity/profile pages.
Commons/DDG and the factory's other visual providers remain the downstream
fallback outside this module.
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
from urllib.parse import urlparse
from xml.etree import ElementTree

from PIL import Image

CRAWLER_MAX_AGE_HOURS = max(24, min(96, int(os.getenv("VISUAL_WEB_CRAWLER_MAX_AGE_HOURS", "72"))))
CRAWLER_TARGET = max(10, min(15, int(os.getenv("VISUAL_WEB_CRAWLER_TARGET", "15"))))
CRAWLER_SUCCESS = max(10, min(CRAWLER_TARGET, int(os.getenv("VISUAL_WEB_CRAWLER_SUCCESS", "10"))))
CRAWLER_NEWS_RESULTS_PER_QUERY = max(8, min(20, int(os.getenv("VISUAL_WEB_CRAWLER_NEWS_RESULTS", "15"))))
CRAWLER_MAX_ARTICLES = max(6, min(12, int(os.getenv("VISUAL_WEB_CRAWLER_ARTICLES", "10"))))
CRAWLER_ARTICLE_IMAGES = max(3, min(8, int(os.getenv("VISUAL_WEB_CRAWLER_IMAGES_PER_ARTICLE", "6"))))
CRAWLER_QUERY_COUNT = max(2, min(4, int(os.getenv("VISUAL_WEB_CRAWLER_QUERY_COUNT", "3"))))
CRAWLER_PROFILE_PAGES = max(2, min(5, int(os.getenv("VISUAL_WEB_CRAWLER_PROFILE_PAGES", "4"))))
CRAWLER_PROFILE_IMAGES = max(2, min(6, int(os.getenv("VISUAL_WEB_CRAWLER_PROFILE_IMAGES", "5"))))

_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "for", "from",
    "by", "with", "after", "before", "during", "over", "into", "about", "this",
    "that", "these", "those", "is", "are", "was", "were", "be", "been", "being",
    "has", "have", "had", "will", "would", "could", "should", "says", "said",
    "report", "reports", "latest", "news", "story", "update", "today", "video",
    "photos", "photo", "images", "image", "pictures", "picture",
}
_BLOCKED_PROFILE_HOSTS = {
    "facebook.com", "instagram.com", "x.com", "twitter.com", "youtube.com", "tiktok.com",
}
_BAD_PATH_PARTS = {
    "/search", "/tag/", "/tags/", "/category/", "/categories/", "/author/", "/authors/",
    "/topic/", "/topics/", "/feed", "/rss", "/sitemap",
}
_STRONG_METHODS = {"og:image", "twitter:image", "json-ld:image"}
_ACTION_TERMS = {
    "action", "playing", "played", "match", "game", "batting", "batted", "bowling",
    "bowled", "fielding", "fielder", "wicket", "goal", "scored", "scores", "tackle",
    "dribble", "dunk", "serve", "forehand", "backhand", "race", "running", "sprint",
    "training", "celebrate", "celebrates", "celebration", "shoot", "shot", "save",
    "header", "podium", "lap", "finish", "qualifying", "fight", "victory", "catch",
    "caught", "throw", "toss", "lifting",
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
    for candidate in (text, text.replace("Z", "+00:00")):
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
    if age < -0.5 or age > CRAWLER_MAX_AGE_HOURS:
        return None
    return max(0.0, age)


def _freshness_score(age_hours: float) -> float:
    return max(0.0, 36.0 * (1.0 - age_hours / CRAWLER_MAX_AGE_HOURS))


def _title_match(query: str, title: str, entity: str = "") -> float:
    q_tokens = _tokens(query)
    title_tokens = _tokens(title)
    if not q_tokens or not title_tokens:
        return 0.0
    overlap = len(q_tokens & title_tokens) / max(1, len(q_tokens))
    entity_tokens = _tokens(entity)
    entity_overlap = (
        len(entity_tokens & title_tokens) / max(1, len(entity_tokens))
        if entity_tokens else 1.0
    )
    if entity_tokens and entity_overlap < 0.75:
        return 0.0
    threshold = 0.82 if len(q_tokens) <= 4 else 0.58
    if overlap < threshold:
        return 0.0
    return min(1.0, overlap * 0.75 + entity_overlap * 0.25)


def _similarity(query_text: str, candidate_text: str, entity: str) -> float:
    q = _tokens(query_text)
    c = _tokens(candidate_text)
    if not q or not c:
        return 0.0
    overlap = len(q & c) / max(1, len(q))
    e = _tokens(entity)
    entity_hit = len(e & c) / max(1, len(e)) if e else 0.0
    return min(1.0, overlap * 0.65 + entity_hit * 0.35)


def _entity_match(entity: str, text: str) -> bool:
    e = _tokens(entity)
    t = _tokens(text)
    return bool(e and len(e & t) >= max(1, int(round(len(e) * 0.75))))


def _build_queries(story_title: str, story_text: str, entity: str, category: str) -> list[str]:
    title = _clean(story_title, 260)
    subject = _clean(entity, 140)
    title_tokens = [
        token for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9'’.-]*", title)
        if len(token) > 2 and token.casefold() not in _STOPWORDS
    ]
    variants = []
    if title:
        variants.append(title)
    distinctive = []
    seen = set()
    for token in title_tokens + list(_tokens(story_text)):
        key = token.casefold()
        if key in seen:
            continue
        seen.add(key)
        distinctive.append(token)
        if len(distinctive) >= 5:
            break
    if subject and distinctive:
        variants.append(f"{subject} {' '.join(distinctive[:5])}")
    if subject and category:
        variants.append(f"{subject} {category} {' '.join(distinctive[:2])}".strip())
    output = []
    seen_queries = set()
    for query in variants:
        value = _clean(query, 260)
        key = value.casefold()
        if not value or key in seen_queries:
            continue
        seen_queries.add(key)
        output.append(value)
        if len(output) >= CRAWLER_QUERY_COUNT:
            break
    return output


def _ddgs_news(query: str, timelimit: str) -> list[dict[str, Any]]:
    try:
        from ddgs import DDGS
        search = DDGS(timeout=8)
        for backend in ("bing", "yahoo"):
            try:
                results = search.news(
                    query=query,
                    region="us-en",
                    safesearch="moderate",
                    timelimit=timelimit,
                    max_results=CRAWLER_NEWS_RESULTS_PER_QUERY,
                    backend=backend,
                )
                if results:
                    return [dict(item) for item in results if isinstance(item, dict)]
            except Exception:
                continue
    except Exception as exc:
        print(
            f"   [Fresh Web Crawler] news discovery failed: {type(exc).__name__}: {exc}",
            flush=True,
        )
    return []


def _google_news_rss(query: str) -> list[dict[str, Any]]:
    try:
        import requests
        from urllib.parse import quote_plus
        url = (
            "https://news.google.com/rss/search?q="
            + quote_plus(query)
            + "&hl=en-IN&gl=IN&ceid=IN:en"
        )
        response = requests.get(
            url,
            timeout=8,
            headers={"User-Agent": "ViralShortsFactory/4.0 (+current-web-image-crawler)"},
        )
        response.raise_for_status()
        root = ElementTree.fromstring(response.content)
        output = []
        for item in root.findall(".//item"):
            title = _clean(item.findtext("title"), 500)
            link = _clean(item.findtext("link"), 2500)
            pub_date = _clean(item.findtext("pubDate"), 200)
            source = _clean(item.findtext("source"), 160)
            if title and link:
                output.append({
                    "title": title,
                    "url": link,
                    "date": pub_date,
                    "published": pub_date,
                    "body": "",
                    "source": source,
                })
        return output[:CRAWLER_NEWS_RESULTS_PER_QUERY]
    except Exception:
        return []


def _news_search(query: str) -> list[dict[str, Any]]:
    fresh = _ddgs_news(query, "d")
    if fresh:
        return fresh
    wider = _ddgs_news(query, "w")
    return wider or _google_news_rss(query)


def _article_url_is_usable(url: str) -> bool:
    parsed = urlparse(str(url or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    host = parsed.netloc.casefold().split(":")[0]
    if host in _BLOCKED_PROFILE_HOSTS:
        return False
    lowered_path = parsed.path.casefold()
    if any(part in lowered_path for part in _BAD_PATH_PARTS):
        return False
    return True


def _collect_recent_articles(queries: list[str], story_title: str, entity: str, now: datetime):
    raw = []
    with ThreadPoolExecutor(max_workers=min(3, len(queries))) as executor:
        futures = {executor.submit(_news_search, query): query for query in queries}
        for future, query in futures.items():
            try:
                results = future.result()
            except Exception:
                results = []
            for item in results:
                if not isinstance(item, dict):
                    continue
                item["_crawler_query"] = query
                raw.append(item)

    ranked = []
    seen_urls = set()
    for item in raw:
        url = _clean(item.get("url") or item.get("href"), 2500)
        article_title = _clean(item.get("title"), 600)
        body = _clean(item.get("body") or item.get("description"), 1800)
        if not url or not article_title or not _article_url_is_usable(url):
            continue
        if url.casefold() in seen_urls:
            continue
        match = _title_match(str(item.get("_crawler_query") or ""), article_title, entity)
        if match <= 0:
            continue
        published = _parse_datetime(
            item.get("date") or item.get("published") or item.get("published_at")
        )
        if published is not None and _age_hours(published, now) is None:
            continue
        seen_urls.add(url.casefold())
        ranked.append((
            1 if published is None else 0,
            -(published.timestamp() if published else 0.0),
            -match,
            {
                **item,
                "url": url,
                "date": published.isoformat() if published else "",
                "body": body,
                "title_match": match,
            },
        ))

    ranked.sort(key=lambda item: (item[0], item[1], item[2], _clean(item[3].get("title")).casefold()))
    return [item for *_meta, item in ranked[:CRAWLER_MAX_ARTICLES]]


def _collect_profile_pages(entity: str) -> list[dict[str, Any]]:
    if not entity:
        return []
    queries = [f"{entity} player profile", f"{entity} profile", f"{entity} official"]
    raw = []
    for query in queries:
        results = []
        try:
            from ddgs import DDGS
            search = DDGS(timeout=8)
            for backend in ("bing", "yahoo"):
                try:
                    results = search.text(
                        query=query,
                        region="us-en",
                        safesearch="moderate",
                        max_results=8,
                        backend=backend,
                    )
                    if results:
                        break
                except Exception:
                    continue
        except Exception:
            results = []
        for item in results or []:
            if isinstance(item, dict):
                raw.append(item)

    ranked = []
    seen = set()
    for item in raw:
        url = _clean(item.get("href") or item.get("url"), 2500)
        title = _clean(item.get("title"), 500)
        if not url or not title or url.casefold() in seen:
            continue
        if not _article_url_is_usable(url):
            continue
        if not _title_match(entity, title, entity):
            continue
        seen.add(url.casefold())
        ranked.append({
            **item,
            "url": url,
            "title_match": _title_match(entity, title, entity),
        })
    return ranked[:CRAWLER_PROFILE_PAGES]


def _image_dimensions(data: bytes) -> tuple[int, int]:
    try:
        with Image.open(io.BytesIO(data)) as image:
            return image.size
    except Exception:
        return 0, 0


def _candidate_from_browser_asset(
    asset: dict[str, Any],
    page: dict[str, Any],
    query: str,
    story_title: str,
    entity: str,
    now: datetime,
    *,
    profile_page: bool = False,
) -> dict[str, Any] | None:
    data = asset.get("bytes")
    if not isinstance(data, (bytes, bytearray, memoryview)):
        return None
    data = bytes(data)
    width, height = _image_dimensions(data)
    if min(width, height) < 500 or width * height < 300_000:
        return None

    page_title = _clean(asset.get("page_title") or page.get("title"), 600)
    page_title_match = _title_match(query or entity, page_title, entity)
    if query and page_title_match <= 0:
        return None
    if profile_page and entity and not _entity_match(entity, page_title):
        return None

    published = _parse_datetime(asset.get("page_published_at")) or _parse_datetime(page.get("date"))
    age = _age_hours(published, now) if not profile_page else None
    if not profile_page and age is None:
        return None

    context = _clean(
        " ".join(
            str(asset.get(field) or "")
            for field in ("image_alt", "image_context", "method", "page_title")
        ),
        5000,
    )
    relevance = _similarity(story_title or query, f"{page_title} {context}", entity)
    action_hits = len(_tokens(context) & _ACTION_TERMS)
    image_score = float(asset.get("image_score") or 0.0)
    method = _clean(asset.get("method"), 120)
    freshness = _freshness_score(age) if age is not None else 5.0
    priority = round(
        freshness
        + page_title_match * 42.0
        + relevance * 36.0
        + min(24.0, action_hits * 6.0)
        + min(30.0, max(0.0, image_score) * 0.35),
        3,
    )
    publisher = _clean(
        asset.get("publisher")
        or page.get("source")
        or (urlparse(asset.get("page_url") or page.get("url") or "").netloc or "Web source").removeprefix("www."),
        160,
    )
    page_url = _clean(asset.get("page_url") or page.get("url"), 2500)
    image_url = _clean(asset.get("image_url"), 2500)
    high_confidence = bool(
        not profile_page
        and method in _STRONG_METHODS
        and page_title_match >= 0.72
        and relevance >= 0.38
    )
    return {
        "bytes": data,
        "hash": hashlib.sha256(data).hexdigest(),
        "source": "web_crawler",
        "source_type": "web_crawler",
        "source_name": publisher,
        "credit": f"Source: {publisher}",
        "query": query or entity,
        "source_page_url": page_url,
        "source_image_url": image_url,
        "publisher": publisher,
        "article_title": page_title,
        "published_at": published.isoformat() if published else "",
        "crawler_age_hours": round(age, 2) if age is not None else None,
        "crawler_freshness_basis": "publication-date" if published else "profile-page",
        "crawler_image_method": method,
        "crawler_confidence": "high" if high_confidence else "ambiguous",
        "crawler_relevance": round(relevance, 3),
        "crawler_action_score": action_hits,
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
        "search_text": _clean(f"{page_title} {context} {publisher} {page_url}", 5000),
        "status": "crawler-high-confidence" if high_confidence else "crawler-ai-pending",
        "used": False,
        "crawler_page_kind": "profile" if profile_page else "news-article",
    }


def _scrape_browser_pages(
    pages: list[dict[str, Any]],
    story_title: str,
    entity: str,
    now: datetime,
    *,
    profile_page: bool = False,
    max_images_per_page: int = CRAWLER_ARTICLE_IMAGES,
):
    if not pages:
        return [], 0, 0
    try:
        from web_browser_image_runtime import scrape_web_pages
    except Exception as exc:
        print(
            f"   [Fresh Web Crawler] browser scraper unavailable: {type(exc).__name__}: {exc}",
            flush=True,
        )
        return [], 0, 0

    requests = [
        {
            "url": _clean(page.get("url"), 2500),
            "query": "" if profile_page else _clean(page.get("_crawler_query"), 500),
            "entity": entity,
            "publisher": _clean(page.get("source"), 160),
            "max_images": max_images_per_page,
        }
        for page in pages
        if _clean(page.get("url"), 2500)
    ]
    browser_results = scrape_web_pages(requests)
    candidates = []
    image_count = 0
    title_mismatch = 0
    for page, result in zip(pages, browser_results):
        if not isinstance(result, dict):
            continue
        if result.get("error") == "page-title-mismatch":
            title_mismatch += 1
            continue
        page_assets = list(result.get("assets") or [])
        image_count += len(page_assets)
        enriched_page = {
            **page,
            "title": result.get("page_title") or page.get("title"),
            "date": (
                result.get("published_at").isoformat()
                if isinstance(result.get("published_at"), datetime)
                else page.get("date")
            ),
            "url": result.get("final_url") or page.get("url"),
        }
        query = "" if profile_page else _clean(page.get("_crawler_query"), 500)
        for asset in page_assets:
            candidate = _candidate_from_browser_asset(
                asset,
                enriched_page,
                query,
                story_title,
                entity,
                now,
                profile_page=profile_page,
            )
            if candidate:
                candidates.append(candidate)
    return candidates, image_count, title_mismatch


def _dedupe_assets(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    seen_hashes = set()
    seen_urls = set()
    for asset in sorted(
        assets,
        key=lambda item: (
            -float(item.get("priority") or 0.0),
            -float(item.get("crawler_relevance") or 0.0),
            str(item.get("source_page_url") or ""),
        ),
    ):
        digest = _clean(asset.get("hash"), 120)
        url = _clean(asset.get("source_image_url"), 2500).casefold().rstrip("/")
        if digest and digest in seen_hashes:
            continue
        if url and url in seen_urls:
            continue
        if digest:
            seen_hashes.add(digest)
        if url:
            seen_urls.add(url)
        output.append(asset)
        if len(output) >= CRAWLER_TARGET:
            break
    return output


def _verify_ambiguous(candidates: list[dict[str, Any]], entity: str):
    high = [item for item in candidates if item.get("crawler_confidence") == "high"]
    ambiguous = [item for item in candidates if item.get("crawler_confidence") != "high"]
    if len(high) >= CRAWLER_SUCCESS or not ambiguous:
        return high[:CRAWLER_TARGET], 0, 0
    if not entity:
        limited = ambiguous[: max(0, CRAWLER_TARGET - len(high))]
        for item in limited:
            item["status"] = "crawler-review-unverified"
        return (high + limited)[:CRAWLER_TARGET], 0, len(limited)

    try:
        from visual_qa_runtime import (
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
        batch = ambiguous[:10]
        result_map = strict_gemini_check_batch(
            [item["bytes"] for item in batch],
            entity,
            api_key,
            tier="IDENTITY",
            visual_type="PERSON",
            visual_genre="PERSON_ACTION",
        )
        checked = len(batch) if get_last_visual_qa_failure() != "circuit_breaker" else 0
        accepted = list(high)
        rejected = 0
        for index, item in enumerate(batch):
            verdict = result_map.get(index)
            if verdict is True:
                item["status"] = "crawler-ai-verified"
                item["crawler_confidence"] = "ai-verified"
                accepted.append(item)
            elif verdict is None:
                item["status"] = "crawler-review-unverified"
                accepted.append(item)
            else:
                rejected += 1
            if len(accepted) >= CRAWLER_TARGET:
                break
        return accepted[:CRAWLER_TARGET], checked, rejected
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
    story = selected_story if isinstance(selected_story, dict) else {}
    title = _clean(story.get("title") or story.get("headline") or story_title, 320)
    body = _clean(story.get("story_text") or story.get("description") or story.get("summary"), 5000)

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
            "profile_pages": 0,
            "high_confidence": 0,
            "ai_checked": 0,
            "rejection_counts": {"no_search_queries": 1},
        }

    now = datetime.now(timezone.utc)
    articles = _collect_recent_articles(queries, title, entity, now)
    article_candidates, browser_image_count, article_title_mismatches = _scrape_browser_pages(
        articles,
        title,
        entity,
        now,
        profile_page=False,
        max_images_per_page=CRAWLER_ARTICLE_IMAGES,
    )
    candidates = _dedupe_assets(article_candidates)

    profile_pages_used = []
    profile_candidates = []
    profile_image_count = 0
    profile_title_mismatches = 0
    if len(candidates) < CRAWLER_SUCCESS and entity:
        profile_pages_used = _collect_profile_pages(entity)
        profile_candidates, profile_image_count, profile_title_mismatches = _scrape_browser_pages(
            profile_pages_used,
            title,
            entity,
            now,
            profile_page=True,
            max_images_per_page=CRAWLER_PROFILE_IMAGES,
        )
        candidates = _dedupe_assets(candidates + profile_candidates)

    selected, ai_checked, ai_rejected = _verify_ambiguous(candidates, entity)
    selected = _dedupe_assets(selected)
    high_count = sum(
        1 for item in selected
        if item.get("crawler_confidence") in {"high", "ai-verified"}
    )

    rejection_counts = {
        "news_search_queries": len(queries),
        "news_search_articles": len(articles),
        "article_title_mismatches": article_title_mismatches,
        "article_browser_images": browser_image_count,
        "article_candidates": len(article_candidates),
        "profile_pages": len(profile_pages_used),
        "profile_title_mismatches": profile_title_mismatches,
        "profile_browser_images": profile_image_count,
        "profile_candidates": len(profile_candidates),
        "raw_images": len(article_candidates) + len(profile_candidates),
        "dedupe_rejected": max(
            0,
            len(article_candidates) + len(profile_candidates) - len(candidates),
        ),
        "ai_checked": ai_checked,
        "ai_rejected": ai_rejected,
        "final_images": len(selected),
    }
    if len(selected) < CRAWLER_SUCCESS:
        rejection_counts["web_pool_underfilled"] = 1

    print(
        f"   [Fresh Web Crawler] articles={len(articles)} profile_pages={len(profile_pages_used)} "
        f"browser_images={browser_image_count + profile_image_count} "
        f"final_pool={len(selected)}/{CRAWLER_TARGET} "
        f"| fallback_providers={'allowed' if len(selected) < CRAWLER_SUCCESS else 'skipped'}",
        flush=True,
    )

    return {
        "assets": selected,
        "target": CRAWLER_TARGET,
        "success_threshold": CRAWLER_SUCCESS,
        "queries": queries,
        "articles": len(articles),
        "profile_pages": len(profile_pages_used),
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
