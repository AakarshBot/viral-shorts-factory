"""Raw visual-provider adapters used by the authoritative retrieval boundary.

Provider adapters only search/resolve/download candidate image bytes. The active
acceptance boundary remains ``visual_retrieval_runtime`` where decode,
resolution, deduplication and semantic verification are applied consistently.

The adapters expose both legacy single-candidate functions and bounded
multi-candidate functions. The active retrieval path uses the latter so a
poor first search result cannot hide a better second or third result.
"""
from __future__ import annotations

import os
import re
from typing import Any

import requests

DEFAULT_TIMEOUT = max(3, int(os.getenv("VISUAL_PROVIDER_TIMEOUT_SECONDS", "8")))
MAX_PROVIDER_CANDIDATES = max(1, min(6, int(os.getenv("VISUAL_PROVIDER_CANDIDATES", "4"))))


def _clean_query(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:240]


def _remember_success(used_urls: set[str] | None, url: str, data: bytes | None) -> bytes | None:
    if not data:
        return None
    if used_urls is not None and url in used_urls:
        return None
    if used_urls is not None:
        used_urls.add(url)
    return data


def _download_image(url: str, used_urls: set[str] | None = None) -> bytes | None:
    url = str(url or "").strip()
    if not url or not url.startswith(("http://", "https://")):
        return None
    if used_urls is not None and url in used_urls:
        return None
    try:
        response = requests.get(
            url,
            timeout=DEFAULT_TIMEOUT,
            headers={"User-Agent": "ViralShortsFactory/1.0 (+visual-retrieval)"},
            allow_redirects=True,
        )
        response.raise_for_status()
        data = response.content
        content_type = str(response.headers.get("content-type", "")).lower()
        if not data:
            return None
        if content_type and not ("image" in content_type or content_type.startswith("application/octet-stream")):
            return None
        return _remember_success(used_urls, response.url or url, data)
    except Exception:
        return None


def _api_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    try:
        response = requests.get(
            url,
            params=params or {},
            headers=headers or {"User-Agent": "ViralShortsFactory/1.0 (+visual-retrieval)"},
            timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else None
    except Exception as exc:
        print(f"   [Visual Source] raw provider request failed: {type(exc).__name__}: {exc}", flush=True)
        return None


def _bounded_downloads(urls: list[str], used_urls: set[str] | None, limit: int = MAX_PROVIDER_CANDIDATES) -> list[bytes]:
    candidates: list[bytes] = []
    seen_urls: set[str] = set()
    for url in urls:
        url = str(url or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        data = _download_image(url, used_urls)
        if data:
            candidates.append(data)
            if len(candidates) >= limit:
                break
    return candidates


def _title_is_entity(title: str, entity: str) -> bool:
    def norm(value: str) -> list[str]:
        return re.findall(r"[a-z0-9]+", str(value or "").casefold())

    wanted = norm(entity)
    actual = norm(title)
    if not wanted or not actual:
        return False
    return wanted == actual or all(token in actual for token in wanted)


def fetch_wikipedia_person_candidates(query: str, used_urls: set[str] | None = None, *_args) -> list[bytes]:
    """Resolve near-exact Wikipedia person pages in one API call."""
    entity = _clean_query(query)
    if not entity:
        return []
    payload = _api_json(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "query",
            "generator": "search",
            "gsrsearch": entity,
            "gsrnamespace": 0,
            "gsrlimit": MAX_PROVIDER_CANDIDATES,
            "prop": "pageimages",
            "piprop": "original|thumbnail",
            "pithumbsize": 1600,
            "format": "json",
        },
    )
    pages = payload.get("query", {}).get("pages", {}) if payload else {}
    urls: list[str] = []
    for page in pages.values() if isinstance(pages, dict) else []:
        if not isinstance(page, dict):
            continue
        title = str(page.get("title", "")).strip()
        if not _title_is_entity(title, entity):
            continue
        # Prefer Wikimedia's generated thumbnail. This is important for SVG
        # logos and other vector/page-image assets that Pillow cannot decode
        # directly. MediaWiki can return a raster thumbnail while preserving
        # the source image's visual content.
        thumbnail = ((page.get("thumbnail") or {}).get("source"))
        original = ((page.get("original") or {}).get("source"))
        source = thumbnail or original
        if source:
            urls.append(str(source))
    return _bounded_downloads(urls, used_urls)


def fetch_wikipedia_person(query: str, used_urls: set[str] | None = None, *_args) -> bytes | None:
    candidates = fetch_wikipedia_person_candidates(query, used_urls, *_args)
    return candidates[0] if candidates else None


def fetch_commons_candidates(query: str, used_urls: set[str] | None = None, *_args) -> list[bytes]:
    """Search Commons in one API request and return several image candidates."""
    q = _clean_query(query)
    if not q:
        return []
    payload = _api_json(
        "https://commons.wikimedia.org/w/api.php",
        params={
            "action": "query",
            "generator": "search",
            "gsrsearch": q,
            "gsrnamespace": 6,
            "gsrlimit": MAX_PROVIDER_CANDIDATES,
            "prop": "imageinfo",
            "iiprop": "url|mime",
            "iiurlwidth": 1600,
            "format": "json",
        },
    )
    pages = payload.get("query", {}).get("pages", {}) if payload else {}
    urls: list[str] = []
    for page in pages.values() if isinstance(pages, dict) else []:
        if not isinstance(page, dict):
            continue
        imageinfo = page.get("imageinfo") or []
        if imageinfo and isinstance(imageinfo[0], dict):
            info = imageinfo[0]
            # Prefer Wikimedia's server-rendered thumbnail. Besides keeping
            # downloads bounded, this transparently rasterizes SVG logos and
            # other formats that are not directly supported by Pillow.
            source = info.get("thumburl") or info.get("url")
            if source:
                urls.append(str(source))
    return _bounded_downloads(urls, used_urls)


def fetch_commons(query: str, used_urls: set[str] | None = None, *_args) -> bytes | None:
    candidates = fetch_commons_candidates(query, used_urls, *_args)
    return candidates[0] if candidates else None


def fetch_duckduckgo_candidates(query: str, used_urls: set[str] | None = None, *_args) -> list[bytes]:
    q = _clean_query(query)
    if not q:
        return []
    try:
        from ddgs import DDGS
    except Exception:
        return []
    try:
        results = DDGS().images(q, safesearch="moderate", max_results=max(8, MAX_PROVIDER_CANDIDATES * 2))
        urls: list[str] = []
        for result in results or []:
            if not isinstance(result, dict):
                continue
            image_url = result.get("image") or result.get("thumbnail") or result.get("url")
            if image_url:
                urls.append(str(image_url))
        return _bounded_downloads(urls, used_urls)
    except Exception as exc:
        print(f"   [Visual Source] DDG raw fetch failed: {type(exc).__name__}: {exc} | query='{q}'", flush=True)
        return []


def fetch_duckduckgo(query: str, used_urls: set[str] | None = None, *_args) -> bytes | None:
    candidates = fetch_duckduckgo_candidates(query, used_urls, *_args)
    return candidates[0] if candidates else None


def fetch_pexels_candidates(query: str, used_urls: set[str] | None = None, *_args) -> list[bytes]:
    key = str(os.getenv("PEXELS_API_KEY", "")).strip()
    q = _clean_query(query)
    if not key or not q:
        return []
    payload = _api_json(
        "https://api.pexels.com/v1/search",
        params={"query": q, "orientation": "portrait", "per_page": max(8, MAX_PROVIDER_CANDIDATES * 2)},
        headers={"Authorization": key, "User-Agent": "ViralShortsFactory/1.0 (+visual-retrieval)"},
    )
    urls: list[str] = []
    for photo in payload.get("photos", []) if payload else []:
        if not isinstance(photo, dict):
            continue
        src = photo.get("src") or {}
        if isinstance(src, dict):
            urls.extend(str(url) for url in (src.get("large2x"), src.get("large"), src.get("original")) if url)
    return _bounded_downloads(urls, used_urls)


def fetch_pexels(query: str, used_urls: set[str] | None = None, *_args) -> bytes | None:
    candidates = fetch_pexels_candidates(query, used_urls, *_args)
    return candidates[0] if candidates else None


def fetch_unsplash_candidates(query: str, used_urls: set[str] | None = None, *_args) -> list[bytes]:
    key = str(os.getenv("UNSPLASH_ACCESS_KEY", "")).strip()
    q = _clean_query(query)
    if not key or not q:
        return []
    payload = _api_json(
        "https://api.unsplash.com/search/photos",
        params={"query": q, "orientation": "portrait", "per_page": max(8, MAX_PROVIDER_CANDIDATES * 2), "client_id": key},
        headers={"User-Agent": "ViralShortsFactory/1.0 (+visual-retrieval)"},
    )
    urls: list[str] = []
    for item in payload.get("results", []) if payload else []:
        if not isinstance(item, dict):
            continue
        urls_meta = item.get("urls") or {}
        if isinstance(urls_meta, dict):
            urls.extend(str(url) for url in (urls_meta.get("regular"), urls_meta.get("full"), urls_meta.get("raw")) if url)
    return _bounded_downloads(urls, used_urls)


def fetch_unsplash(query: str, used_urls: set[str] | None = None, *_args) -> bytes | None:
    candidates = fetch_unsplash_candidates(query, used_urls, *_args)
    return candidates[0] if candidates else None


def build_raw_source_plan(visual_type: str):
    """Return multi-candidate raw providers with no semantic acceptance logic."""
    kind = str(visual_type or "GENERAL_CONTEXT").upper()
    plan = []
    if kind == "PERSON":
        plan.extend([("Wikipedia", fetch_wikipedia_person_candidates), ("Commons", fetch_commons_candidates)])
    elif kind in {"ORGANIZATION", "EVENT", "QUOTE", "DOCUMENT", "LOCATION"}:
        plan.append(("Commons", fetch_commons_candidates))

    # Openverse/Pixabay remain in image_sources_runtime until their multi-result
    # adapters are promoted. Their current wrappers still provide a useful
    # additional source without changing the active acceptance boundary.
    try:
        from image_sources_runtime import fetch_openverse, fetch_pixabay
    except Exception:
        fetch_openverse = fetch_pixabay = None

    plan.extend([
        ("Openverse", fetch_openverse),
        ("DDG", fetch_duckduckgo_candidates),
        ("Pixabay", fetch_pixabay),
        ("Pexels", fetch_pexels_candidates),
        ("Unsplash", fetch_unsplash_candidates),
    ])
    return [(name, fn) for name, fn in plan if callable(fn)]


__all__ = [
    "MAX_PROVIDER_CANDIDATES",
    "build_raw_source_plan",
    "fetch_commons",
    "fetch_commons_candidates",
    "fetch_duckduckgo",
    "fetch_duckduckgo_candidates",
    "fetch_pexels",
    "fetch_pexels_candidates",
    "fetch_unsplash",
    "fetch_unsplash_candidates",
    "fetch_wikipedia_person",
    "fetch_wikipedia_person_candidates",
]
