"""Additional low-cost image-source adapters for the Shorts visual pipeline.

These providers return downloaded image bytes so the existing quality and
semantic gates remain the single acceptance boundary. No provider is treated as
factual merely because it returned a result.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from visual_licensing_runtime import (
    LICENSE_URLS,
    is_allowed_license,
    licensed_candidate,
    normalize_license_code,
)


OPENVERSE_API = "https://api.openverse.org/v1/images/"
PIXABAY_API = "https://pixabay.com/api/"
SERPAPI_API = "https://serpapi.com/search"
SERPAPI_SAFE_SOURCE_HOSTS = {"pexels.com", "pixabay.com"}
CACHE_TTL_SECONDS = 24 * 60 * 60
DEFAULT_TIMEOUT = 10
MAX_PROVIDER_CANDIDATES = max(1, min(6, int(os.getenv("VISUAL_PROVIDER_CANDIDATES", "6"))))


def _clean_query(query: Any) -> str:
    return re.sub(r"\s+", " ", str(query or "")).strip()[:240]


def _cache_dir() -> Path:
    root = Path(os.getenv("ASSET_CACHE_DIR", "asset_cache")) / "provider_search_cache"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _cache_path(provider: str, query: str, page: int = 1) -> Path:
    digest = hashlib.sha256(
        f"{provider}:{query.casefold()}:page:{max(1, int(page))}".encode("utf-8")
    ).hexdigest()[:40]
    return _cache_dir() / f"{digest}.json"


def _read_cache(provider: str, query: str, page: int = 1) -> dict[str, Any] | None:
    path = _cache_path(provider, query, page)
    try:
        if not path.exists() or time.time() - path.stat().st_mtime > CACHE_TTL_SECONDS:
            return None
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _write_cache(provider: str, query: str, payload: dict[str, Any], page: int = 1) -> None:
    try:
        with _cache_path(provider, query, page).open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
    except Exception:
        pass


def _provider_page(args: tuple[Any, ...]) -> int:
    """Read an optional page number without changing existing provider call signatures."""
    if args and isinstance(args[-1], int) and not isinstance(args[-1], bool):
        return max(1, int(args[-1]))
    return 1


def _manual_mode(args: tuple[Any, ...]) -> bool:
    return bool(len(args) > 4 and isinstance(args[4], bool) and args[4])


def _remember_url(used_urls: set[str] | None, url: str) -> bool:
    if not url:
        return False
    if used_urls is None:
        return True
    if url in used_urls:
        return False
    used_urls.add(url)
    return True


def _download(url: str, used_urls: set[str] | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any] | None:
    parsed = urlparse(str(url or ""))
    if parsed.scheme not in {"http", "https"} or not _remember_url(used_urls, str(url)):
        return None
    try:
        response = requests.get(
            str(url),
            timeout=DEFAULT_TIMEOUT,
            headers={"User-Agent": "ViralShortsFactory/1.0 (+image-retrieval)"},
            allow_redirects=True,
        )
        response.raise_for_status()
        content_type = str(response.headers.get("content-type", "")).lower()
        data = response.content
        if not data or (content_type and "image" not in content_type and not content_type.startswith("application/octet-stream")):
            return None
        meta = dict(metadata or {})
        meta.setdefault("url", response.url or str(url))
        meta.setdefault("source_image_url", response.url or str(url))
        return licensed_candidate(data, meta)
    except Exception:
        return None


def fetch_openverse_candidates(query: str, used_urls: set[str] | None = None, *_args) -> list[dict[str, Any]]:
    """Search Openverse and return a bounded set of downloadable candidates."""
    q = _clean_query(query)
    if not q:
        return []
    page = _provider_page(_args)
    payload = _read_cache("openverse", q, page)
    if payload is None:
        try:
            response = requests.get(
                OPENVERSE_API,
                params={
                    "q": q,
                    "page": page,
                    "page_size": 15,
                    "mature": "false",
                    "license_type": "commercial",
                    "license": ["cc0", "pdm", "by", "by-sa"],
                },
                timeout=DEFAULT_TIMEOUT,
                headers={"User-Agent": "ViralShortsFactory/1.0 (+image-retrieval)"},
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                return []
            _write_cache("openverse", q, payload, page)
        except Exception as exc:
            print(f"   [Visual Source] Openverse | failed: {type(exc).__name__}: {exc} | query='{q}'", flush=True)
            return []

    candidates: list[dict[str, Any]] = []
    for position, item in enumerate(payload.get("results", []) if isinstance(payload, dict) else [], 1):
        if not isinstance(item, dict):
            continue
        license_code = normalize_license_code(item.get("license"))
        if not is_allowed_license(license_code):
            continue
        raw_tags = item.get("tags") or []
        if isinstance(raw_tags, str):
            search_tags = raw_tags
        else:
            search_tags = " ".join(
                str(tag.get("name") if isinstance(tag, dict) else tag)
                for tag in raw_tags
            )
        metadata = {
            "provider": "Openverse",
            "url": str(item.get("url") or ""),
            "author": str(item.get("creator") or ""),
            "license": license_code,
            "license_url": str(item.get("license_url") or LICENSE_URLS.get(license_code, "")),
            "search_title": str(item.get("title") or ""),
            "search_description": str(item.get("description") or ""),
            "search_tags": search_tags,
            "search_position": ((page - 1) * 15) + position,
        }
        for candidate in (item.get("url"), item.get("thumbnail")):
            data = _download(candidate, used_urls, metadata)
            if data:
                candidates.append(data)
                break
        if len(candidates) >= MAX_PROVIDER_CANDIDATES:
            break
    return candidates


def fetch_serpapi_candidates(query: str, used_urls: set[str] | None = None, *_args) -> list[dict[str, Any]]:
    """Find current-year images through Google Images with a conservative source allowlist.

    SerpApi's commercial-use flag is treated only as discovery metadata. Automated
    acceptance is restricted to Pexels/Pixabay source pages so the normal downstream
    provenance/license policy remains authoritative.
    """
    key = str(os.getenv("SERPAPI_API_KEY", "")).strip()
    q = _clean_query(query)
    if not key or not q:
        return []

    page = _provider_page(_args)
    payload = _read_cache("serpapi", q, page)
    if payload is None:
        try:
            today = datetime.now(timezone.utc)
            response = requests.get(
                SERPAPI_API,
                params={
                    "engine": "google_images",
                    "q": q,
                    "api_key": key,
                    "ijn": page - 1,
                    "safe": "active",
                    "image_type": "photo",
                    "licenses": "fmc",
                    "start_date": f"{today.year}0101",
                    "end_date": today.strftime("%Y%m%d"),
                },
                timeout=DEFAULT_TIMEOUT,
                headers={"User-Agent": "ViralShortsFactory/1.0 (+recent-visual-retrieval)"},
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                return []
            _write_cache("serpapi", q, payload, page)
        except Exception as exc:
            print(
                f"   [Visual Source] SerpApi | failed: {type(exc).__name__}: {exc} | query='{q}'",
                flush=True,
            )
            return []

    candidates = []
    for position, item in enumerate(
        payload.get("images_results", []) if isinstance(payload, dict) else [],
        1,
    ):
        if not isinstance(item, dict):
            continue
        if bool(item.get("unsafe")) or bool(item.get("is_product")):
            continue

        source_page = str(item.get("link") or "").strip()
        original = str(item.get("original") or "").strip()
        try:
            host = str(urlparse(source_page).hostname or "").casefold().removeprefix("www.")
        except Exception:
            host = ""

        matched_host = next(
            (
                allowed
                for allowed in SERPAPI_SAFE_SOURCE_HOSTS
                if host == allowed or host.endswith("." + allowed)
            ),
            "",
        )
        if not matched_host:
            continue
        if not source_page.startswith(("http://", "https://")):
            continue
        if not original.startswith(("http://", "https://")):
            continue

        if matched_host == "pexels.com":
            provider = "Pexels"
            license_name = "Pexels License"
            license_url = "https://www.pexels.com/license/"
        else:
            provider = "Pixabay"
            license_name = "Pixabay Content License"
            license_url = "https://pixabay.com/service/license-summary/"

        metadata = {
            "provider": provider,
            "url": source_page,
            "source_page_url": source_page,
            "author": "",
            "license": license_name,
            "license_url": license_url,
            "search_title": str(item.get("title") or ""),
            "search_description": str(item.get("source") or ""),
            "search_tags": str(item.get("tag") or ""),
            "search_position": ((page - 1) * 100) + position,
            "serpapi_recent_window": "current-year",
            "serpapi_commercial_filter": "fmc",
        }
        data = _download(original, used_urls, metadata)
        if data:
            candidates.append(data)
        if len(candidates) >= MAX_PROVIDER_CANDIDATES:
            break

    return candidates


def fetch_pixabay_candidates(query: str, used_urls: set[str] | None = None, *_args) -> list[dict[str, Any]]:
    """Search Pixabay and return a bounded set of downloadable candidates."""
    key = str(os.getenv("PIXABAY_API_KEY", "")).strip()
    q = _clean_query(query)
    page = _provider_page(_args)
    manual_mode = _manual_mode(_args)
    if not key or not q:
        if not key:
            print("   [Visual Source] Pixabay | API key not configured; skipped.", flush=True)
        return []

    payload = _read_cache("pixabay", q, page)
    if payload is None:
        try:
            response = requests.get(
                PIXABAY_API,
                params={
                    "key": key,
                    "q": q,
                    "image_type": "photo",
                    "safesearch": "true",
                    "order": "latest" if manual_mode else "popular",
                    "page": page,
                    "per_page": 20,
                },
                timeout=DEFAULT_TIMEOUT,
                headers={"User-Agent": "ViralShortsFactory/1.0 (+image-retrieval)"},
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                return []
            _write_cache("pixabay", q, payload, page)
        except Exception as exc:
            print(f"   [Visual Source] Pixabay | failed: {type(exc).__name__}: {exc} | query='{q}'", flush=True)
            return []

    candidates: list[bytes] = []
    for position, item in enumerate(payload.get("hits", []) if isinstance(payload, dict) else [], 1):
        if not isinstance(item, dict):
            continue
        metadata = {
            "provider": "Pixabay",
            "url": str(item.get("pageURL") or item.get("largeImageURL") or item.get("webformatURL") or ""),
            "author": str(item.get("user") or ""),
            "license": "Pixabay Content License",
            "license_url": "https://pixabay.com/service/license-summary/",
            "search_title": str(item.get("tags") or ""),
            "search_description": str(item.get("pageURL") or ""),
            "search_tags": str(item.get("tags") or ""),
            "search_position": ((page - 1) * 20) + position,
        }
        for candidate in (item.get("largeImageURL"), item.get("webformatURL")):
            data = _download(candidate, used_urls, metadata)
            if data:
                candidates.append(data)
                break
        if len(candidates) >= 4:
            break
    return candidates


