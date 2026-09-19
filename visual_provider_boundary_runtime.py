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

from visual_taxonomy_runtime import preferred_sources
from visual_licensing_runtime import (
    LICENSE_URLS,
    allow_unlicensed_visuals,
    is_allowed_license,
    licensed_candidate,
    normalize_license_code,
)

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


def _download_image(url: str, used_urls: set[str] | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any] | None:
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
        accepted = _remember_success(used_urls, response.url or url, data)
        if not accepted:
            return None
        meta = dict(metadata or {})
        meta.setdefault("url", response.url or url)
        return licensed_candidate(accepted, meta)
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


def _bounded_downloads(urls: list[Any], used_urls: set[str] | None, limit: int = MAX_PROVIDER_CANDIDATES) -> list[dict[str, Any]]:
    candidates: list[bytes] = []
    seen_urls: set[str] = set()
    for item in urls:
        metadata = {}
        if isinstance(item, (tuple, list)) and len(item) == 2:
            url, metadata = item[0], item[1] if isinstance(item[1], dict) else {}
        else:
            url = item
        url = str(url or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        data = _download_image(url, used_urls, metadata)
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
            "piprop": "name|original|thumbnail",
            "pilicense": "free",
            "pithumbsize": 1600,
            "format": "json",
        },
    )
    pages = payload.get("query", {}).get("pages", {}) if payload else {}
    urls: list[Any] = []
    for page in pages.values() if isinstance(pages, dict) else []:
        if not isinstance(page, dict):
            continue
        title = str(page.get("title", "")).strip()
        if not _title_is_entity(title, entity):
            continue
        file_name = str(page.get("pageimage") or "").strip()
        if not file_name:
            continue
        info_payload = _api_json(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "prop": "imageinfo",
                "titles": "File:" + file_name,
                "iiprop": "url|mime|extmetadata",
                "iiurlwidth": 1600,
                "iiextmetadatafilter": "LicenseShortName|Artist|LicenseUrl",
                "format": "json",
            },
        )
        file_pages = info_payload.get("query", {}).get("pages", {}) if info_payload else {}
        info = None
        for file_page in file_pages.values() if isinstance(file_pages, dict) else []:
            imageinfo = file_page.get("imageinfo") or [] if isinstance(file_page, dict) else []
            if imageinfo and isinstance(imageinfo[0], dict):
                info = imageinfo[0]
                break
        if not info:
            continue
        ext = info.get("extmetadata") or {}
        def _meta_value(key):
            value = ext.get(key)
            return value.get("value", "") if isinstance(value, dict) else str(value or "")
        license_code = normalize_license_code(_meta_value("LicenseShortName"))
        if not is_allowed_license(license_code):
            continue
        source = info.get("thumburl") or info.get("url") or ((page.get("thumbnail") or {}).get("source"))
        if source:
            urls.append((
                str(source),
                {
                    "provider": "Wikipedia",
                    "url": str(info.get("descriptionurl") or ("https://en.wikipedia.org/wiki/File:" + file_name)),
                    "author": _meta_value("Artist"),
                    "license": license_code,
                    "license_url": _meta_value("LicenseUrl") or LICENSE_URLS.get(license_code, ""),
                },
            ))
    return _bounded_downloads(urls, used_urls)


def fetch_wikipedia_person(query: str, used_urls: set[str] | None = None, *_args) -> bytes | None:
    candidates = fetch_wikipedia_person_candidates(query, used_urls, *_args)
    return candidates[0] if candidates else None


def _commons_search_query(query: str) -> str:
    """Use the vocabulary Commons actually uses for match/event media."""
    q = _clean_query(query)
    q = re.sub(r"\bversus\b", "v", q, flags=re.IGNORECASE)
    q = re.sub(r"\bvs\.?\b", "v", q, flags=re.IGNORECASE)
    return q


def fetch_commons_candidates(query: str, used_urls: set[str] | None = None, *_args) -> list[bytes]:
    """Search Commons in one API request and return several image candidates."""
    q = _commons_search_query(query)
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
            "iiprop": "url|mime|extmetadata",
            "iiurlwidth": 1600,
            "iiextmetadatafilter": "LicenseShortName|Artist|LicenseUrl",
            "format": "json",
        },
    )
    pages = payload.get("query", {}).get("pages", {}) if payload else {}
    urls: list[Any] = []
    for page in pages.values() if isinstance(pages, dict) else []:
        if not isinstance(page, dict):
            continue
        imageinfo = page.get("imageinfo") or []
        if imageinfo and isinstance(imageinfo[0], dict):
            info = imageinfo[0]
            ext = info.get("extmetadata") or {}
            def _meta_value(key):
                value = ext.get(key)
                return value.get("value", "") if isinstance(value, dict) else str(value or "")
            license_code = normalize_license_code(_meta_value("LicenseShortName"))
            if not is_allowed_license(license_code):
                continue
            source = info.get("thumburl") or info.get("url")
            if source:
                urls.append((
                    str(source),
                    {
                        "provider": "Commons",
                        "url": str(info.get("descriptionurl") or ("https://commons.wikimedia.org/wiki/" + str(page.get("title", "")))),
                        "author": _meta_value("Artist"),
                        "license": license_code,
                        "license_url": _meta_value("LicenseUrl") or LICENSE_URLS.get(license_code, ""),
                    },
                ))
    return _bounded_downloads(urls, used_urls)


def fetch_commons(query: str, used_urls: set[str] | None = None, *_args) -> bytes | None:
    candidates = fetch_commons_candidates(query, used_urls, *_args)
    return candidates[0] if candidates else None


def fetch_duckduckgo_candidates(query: str, used_urls: set[str] | None = None, *_args) -> list[dict[str, Any]]:
    if not allow_unlicensed_visuals():
        return []
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


def fetch_duckduckgo(query: str, used_urls: set[str] | None = None, *_args) -> dict[str, Any] | None:
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
            author = str(photo.get("photographer") or "")
            for url in (src.get("large2x"), src.get("large"), src.get("original")):
                if url:
                    urls.append((str(url), {
                        "provider": "Pexels",
                        "url": str(url),
                        "author": author,
                        "license": "Pexels License",
                        "license_url": "https://www.pexels.com/license/",
                    }))
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
            user = item.get("user") or {}
            author = str(user.get("name") or user.get("username") or "") if isinstance(user, dict) else ""
            for url in (urls_meta.get("regular"), urls_meta.get("full"), urls_meta.get("raw")):
                if url:
                    urls.append((str(url), {
                        "provider": "Unsplash",
                        "url": str(url),
                        "author": author,
                        "license": "Unsplash License",
                        "license_url": "https://unsplash.com/license",
                    }))
    return _bounded_downloads(urls, used_urls)


def fetch_unsplash(query: str, used_urls: set[str] | None = None, *_args) -> bytes | None:
    candidates = fetch_unsplash_candidates(query, used_urls, *_args)
    return candidates[0] if candidates else None


def build_raw_source_plan(visual_type: str, visual_genre: str = ""):
    """Return raw providers ordered for the visual genre.

    The provider layer never decides whether an image is correct. It only
    changes the order in which low-cost sources are searched so obvious
    category mismatches do not consume the verification budget first.
    """
    kind = str(visual_type or "GENERAL_CONTEXT").upper()
    genre = str(visual_genre or "").strip().upper()
    if not genre:
        genre = "PERSON_PORTRAIT" if kind == "PERSON" else "GENERAL_CONTEXT"

    plan = []
    if kind == "PERSON":
        plan.append(("Wikipedia", fetch_wikipedia_person_candidates))

    commons_kinds = {
        "PERSON", "ORGANIZATION", "EVENT", "PRODUCT", "LOCATION", "DOCUMENT",
        "QUOTE", "PROCESS", "CONCEPT",
    }
    commons_genres = {
        "ORG_BRANDING", "ORG_HEADQUARTERS", "TEAM_BRANDING", "TEAM_ACTION",
        "PRODUCT_PHOTO", "PRODUCT_LAUNCH", "LANDMARK", "ARCHITECTURE",
        "PLACE_SCENE", "EVENT_SCENE", "SPORTS_ACTION", "SPORTS_MATCH",
        "TROPHY_AWARD", "DOCUMENT", "SCREENSHOT_UI", "CHART_GRAPH", "MAP",
        "DIAGRAM", "PROCESS", "SCIENCE_VISUAL", "SPACE_VISUAL",
        "HISTORICAL_ARTIFACT", "HISTORICAL_PHOTO", "MEDIA_ARTWORK",
        "MONEY_CURRENCY", "FLAG_SYMBOL",
    }
    if kind in commons_kinds or genre in commons_genres:
        plan.append(("Commons", fetch_commons_candidates))

    try:
        from image_sources_runtime import fetch_openverse_candidates, fetch_pixabay_candidates
    except Exception:
        fetch_openverse = fetch_pixabay = None

    plan.extend([
        ("Openverse", fetch_openverse_candidates),
        ("Pixabay", fetch_pixabay_candidates),
        ("Pexels", fetch_pexels_candidates),
        ("Unsplash", fetch_unsplash_candidates),
    ])
    if allow_unlicensed_visuals():
        plan.append(("DDG", fetch_duckduckgo_candidates))

    plan = [(name, fn) for name, fn in plan if callable(fn)]
    preferred = preferred_sources(genre)
    rank = {name.casefold(): index for index, name in enumerate(preferred)}
    plan.sort(key=lambda item: (rank.get(item[0].casefold(), 999), item[0]))

    deduped = []
    seen = set()
    for item in plan:
        key = item[0].casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


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
