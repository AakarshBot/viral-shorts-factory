"""Raw visual-provider adapters used by the authoritative retrieval boundary.

Provider adapters in this module deliberately do *not* perform semantic image
QA. They only resolve/download candidate image bytes. The single active
acceptance boundary lives in ``visual_retrieval_runtime`` where decode,
resolution, deduplication and semantic verification are applied consistently
across every source.
"""
from __future__ import annotations

import os
import re
from typing import Any, Iterable

import requests

DEFAULT_TIMEOUT = max(3, int(os.getenv("VISUAL_PROVIDER_TIMEOUT_SECONDS", "8")))


def _clean_query(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:240]


def _remember_success(used_urls: set[str] | None, url: str, data: bytes | None) -> bytes | None:
    """Add a URL to the run-level dedupe set only after a successful download."""
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


def _wiki_titles(entity: str) -> list[str]:
    query = _clean_query(entity)
    if not query:
        return []
    payload = _api_json(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srnamespace": 0,
            "srlimit": 5,
            "format": "json",
        },
    )
    rows = payload.get("query", {}).get("search", []) if payload else []
    titles = [str(row.get("title", "")).strip() for row in rows if isinstance(row, dict)]
    return [title for title in titles if title]


def _title_is_entity(title: str, entity: str) -> bool:
    def norm(value: str) -> list[str]:
        return re.findall(r"[a-z0-9]+", str(value or "").casefold())

    wanted = norm(entity)
    actual = norm(title)
    if not wanted or not actual:
        return False
    return wanted == actual or all(token in actual for token in wanted)


def fetch_wikipedia_person(query: str, used_urls: set[str] | None = None, *_args) -> bytes | None:
    """Resolve an exact/near-exact Wikipedia person page and return its image bytes."""
    entity = _clean_query(query)
    for title in _wiki_titles(entity):
        if not _title_is_entity(title, entity):
            continue
        payload = _api_json(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "prop": "pageimages",
                "piprop": "original",
                "titles": title,
                "format": "json",
            },
        )
        pages = payload.get("query", {}).get("pages", {}) if payload else {}
        for page in pages.values() if isinstance(pages, dict) else []:
            if not isinstance(page, dict):
                continue
            source = ((page.get("original") or {}).get("source"))
            data = _download_image(source, used_urls)
            if data:
                return data
    return None


def fetch_commons(query: str, used_urls: set[str] | None = None, *_args) -> bytes | None:
    """Search Wikimedia Commons and return the first downloadable image candidate."""
    q = _clean_query(query)
    if not q:
        return None
    payload = _api_json(
        "https://commons.wikimedia.org/w/api.php",
        params={
            "action": "query",
            "list": "search",
            "srsearch": q,
            "srnamespace": 6,
            "srlimit": 8,
            "format": "json",
        },
    )
    rows = payload.get("query", {}).get("search", []) if payload else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title", "")).strip()
        if not title:
            continue
        info = _api_json(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query",
                "titles": title,
                "prop": "imageinfo",
                "iiprop": "url",
                "format": "json",
            },
        )
        pages = info.get("query", {}).get("pages", {}) if info else {}
        for page in pages.values() if isinstance(pages, dict) else []:
            if not isinstance(page, dict):
                continue
            imageinfo = page.get("imageinfo") or []
            if not imageinfo or not isinstance(imageinfo[0], dict):
                continue
            data = _download_image(imageinfo[0].get("url"), used_urls)
            if data:
                return data
    return None


def fetch_duckduckgo(query: str, used_urls: set[str] | None = None, *_args) -> bytes | None:
    q = _clean_query(query)
    if not q:
        return None
    try:
        from ddgs import DDGS
    except Exception:
        return None
    try:
        results = DDGS().images(q, safesearch="moderate", max_results=8)
        for result in results or []:
            if not isinstance(result, dict):
                continue
            image_url = result.get("image") or result.get("thumbnail") or result.get("url")
            data = _download_image(image_url, used_urls)
            if data:
                return data
    except Exception as exc:
        print(f"   [Visual Source] DDG raw fetch failed: {type(exc).__name__}: {exc} | query='{q}'", flush=True)
    return None


def fetch_pexels(query: str, used_urls: set[str] | None = None, *_args) -> bytes | None:
    key = str(os.getenv("PEXELS_API_KEY", "")).strip()
    q = _clean_query(query)
    if not key or not q:
        return None
    payload = _api_json(
        "https://api.pexels.com/v1/search",
        params={"query": q, "orientation": "portrait", "per_page": 8},
        headers={"Authorization": key, "User-Agent": "ViralShortsFactory/1.0 (+visual-retrieval)"},
    )
    photos = payload.get("photos", []) if payload else []
    for photo in photos if isinstance(photos, list) else []:
        if not isinstance(photo, dict):
            continue
        src = photo.get("src") or {}
        for url in (src.get("large2x"), src.get("large"), src.get("original")) if isinstance(src, dict) else ():
            data = _download_image(url, used_urls)
            if data:
                return data
    return None


def fetch_unsplash(query: str, used_urls: set[str] | None = None, *_args) -> bytes | None:
    key = str(os.getenv("UNSPLASH_ACCESS_KEY", "")).strip()
    q = _clean_query(query)
    if not key or not q:
        return None
    payload = _api_json(
        "https://api.unsplash.com/search/photos",
        params={"query": q, "orientation": "portrait", "per_page": 8, "client_id": key},
        headers={"User-Agent": "ViralShortsFactory/1.0 (+visual-retrieval)"},
    )
    results = payload.get("results", []) if payload else []
    for item in results if isinstance(results, list) else []:
        if not isinstance(item, dict):
            continue
        urls = item.get("urls") or {}
        for url in (urls.get("regular"), urls.get("full"), urls.get("raw")) if isinstance(urls, dict) else ():
            data = _download_image(url, used_urls)
            if data:
                return data
    return None


def build_raw_source_plan(visual_type: str):
    """Return providers that never perform semantic/blur/API-level acceptance QA."""
    try:
        from image_sources_runtime import fetch_openverse, fetch_pixabay
    except Exception:
        fetch_openverse = fetch_pixabay = None

    kind = str(visual_type or "GENERAL_CONTEXT").upper()
    plan = []
    if kind == "PERSON":
        plan.extend([("Wikipedia", fetch_wikipedia_person), ("Commons", fetch_commons)])
    elif kind in {"ORGANIZATION", "EVENT", "QUOTE", "DOCUMENT", "LOCATION"}:
        plan.append(("Commons", fetch_commons))

    plan.extend([
        ("Openverse", fetch_openverse),
        ("DDG", fetch_duckduckgo),
        ("Pixabay", fetch_pixabay),
        ("Pexels", fetch_pexels),
        ("Unsplash", fetch_unsplash),
    ])
    return [(name, fn) for name, fn in plan if callable(fn)]


__all__ = [
    "build_raw_source_plan",
    "fetch_commons",
    "fetch_duckduckgo",
    "fetch_pexels",
    "fetch_unsplash",
    "fetch_wikipedia_person",
]
