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
from concurrent.futures import ThreadPoolExecutor
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
CACHE_TTL_SECONDS = 24 * 60 * 60
API_TIMEOUT_SECONDS = max(2, min(5, int(os.getenv("VISUAL_PROVIDER_TIMEOUT_SECONDS", "4"))))
IMAGE_TIMEOUT_SECONDS = max(2, min(5, int(os.getenv("VISUAL_IMAGE_DOWNLOAD_TIMEOUT_SECONDS", "5"))))
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
            timeout=IMAGE_TIMEOUT_SECONDS,
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


def _bounded_downloads(
    urls: list[tuple[str, dict[str, Any]]],
    used_urls: set[str] | None = None,
    limit: int = MAX_PROVIDER_CANDIDATES,
) -> list[dict[str, Any]]:
    jobs: list[tuple[str, dict[str, Any]]] = []
    seen_urls: set[str] = set()
    for url, metadata in urls:
        value = str(url or "").strip()
        if not value or value in seen_urls:
            continue
        if used_urls is not None and value in used_urls:
            continue
        seen_urls.add(value)
        if used_urls is not None:
            used_urls.add(value)
        jobs.append((value, dict(metadata or {})))
        if len(jobs) >= max(1, int(limit)):
            break
    if not jobs:
        return []

    def _worker(job: tuple[str, dict[str, Any]]):
        url, metadata = job
        return _download(url, None, metadata)

    with ThreadPoolExecutor(
        max_workers=min(4, len(jobs)),
        thread_name_prefix="image-source-download",
    ) as executor:
        return [item for item in executor.map(_worker, jobs) if item]


def fetch_openverse_candidates(query: str, used_urls: set[str] | None = None, *_args) -> list[dict[str, Any]]:
    """Search Openverse and download a small bounded result set concurrently."""
    q = _clean_query(query)
    if not q:
        return []
    page = _provider_page(_args)
    manual_mode = _manual_mode(_args)
    cache_provider = "openverse-manual" if manual_mode else "openverse"
    payload = _read_cache(cache_provider, q, page)
    if payload is None:
        try:
            response = requests.get(
                OPENVERSE_API,
                params={
                    "q": q,
                    "page": page,
                    "page_size": 15,
                    "mature": "false",
                    **(
                        {}
                        if manual_mode
                        else {
                            "license_type": "commercial",
                            "license": ["cc0", "pdm", "by", "by-sa"],
                        }
                    ),
                },
                timeout=API_TIMEOUT_SECONDS,
                headers={"User-Agent": "ViralShortsFactory/1.0 (+image-retrieval)"},
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                return []
            _write_cache(cache_provider, q, payload, page)
        except Exception as exc:
            print(f"   [Visual Source] Openverse | failed: {type(exc).__name__}: {exc} | query='{q}'", flush=True)
            return []

    jobs = []
    for position, item in enumerate(payload.get("results", []) if isinstance(payload, dict) else [], 1):
        if not isinstance(item, dict):
            continue
        license_code = normalize_license_code(item.get("license"))
        if not manual_mode and not is_allowed_license(license_code):
            continue
        raw_tags = item.get("tags") or []
        search_tags = raw_tags if isinstance(raw_tags, str) else " ".join(
            str(tag.get("name") if isinstance(tag, dict) else tag) for tag in raw_tags
        )
        image_url = str(item.get("url") or item.get("thumbnail") or "").strip()
        if not image_url:
            continue
        metadata = {
            "provider": "Openverse",
            "url": image_url,
            "author": str(item.get("creator") or ""),
            "license": license_code,
            "license_url": str(item.get("license_url") or LICENSE_URLS.get(license_code, "")),
            "search_title": str(item.get("title") or ""),
            "search_description": str(item.get("description") or ""),
            "search_tags": search_tags,
            "search_position": ((page - 1) * 15) + position,
        }
        jobs.append((image_url, metadata))
    return _bounded_downloads(jobs, used_urls, limit=4 if manual_mode else MAX_PROVIDER_CANDIDATES)


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
                timeout=API_TIMEOUT_SECONDS,
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

    jobs = []
    for position, item in enumerate(payload.get("hits", []) if isinstance(payload, dict) else [], 1):
        if not isinstance(item, dict):
            continue
        image_url = str(item.get("largeImageURL") or item.get("webformatURL") or "").strip()
        if not image_url:
            continue
        metadata = {
            "provider": "Pixabay",
            "url": str(item.get("pageURL") or image_url),
            "author": str(item.get("user") or ""),
            "license": "Pixabay Content License",
            "license_url": "https://pixabay.com/service/license-summary/",
            "search_title": str(item.get("tags") or ""),
            "search_description": str(item.get("pageURL") or ""),
            "search_tags": str(item.get("tags") or ""),
            "search_position": ((page - 1) * 20) + position,
        }
        jobs.append((image_url, metadata))
    return _bounded_downloads(jobs, used_urls, limit=4 if manual_mode else MAX_PROVIDER_CANDIDATES)


