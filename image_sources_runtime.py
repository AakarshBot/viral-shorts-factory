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
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests


OPENVERSE_API = "https://api.openverse.org/v1/images/"
PIXABAY_API = "https://pixabay.com/api/"
CACHE_TTL_SECONDS = 24 * 60 * 60
DEFAULT_TIMEOUT = 10


def _clean_query(query: Any) -> str:
    return re.sub(r"\s+", " ", str(query or "")).strip()[:240]


def _cache_dir() -> Path:
    root = Path(os.getenv("ASSET_CACHE_DIR", "asset_cache")) / "provider_search_cache"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _cache_path(provider: str, query: str) -> Path:
    digest = hashlib.sha256(f"{provider}:{query.casefold()}".encode("utf-8")).hexdigest()[:40]
    return _cache_dir() / f"{digest}.json"


def _read_cache(provider: str, query: str) -> dict[str, Any] | None:
    path = _cache_path(provider, query)
    try:
        if not path.exists() or time.time() - path.stat().st_mtime > CACHE_TTL_SECONDS:
            return None
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _write_cache(provider: str, query: str, payload: dict[str, Any]) -> None:
    try:
        with _cache_path(provider, query).open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
    except Exception:
        pass


def _remember_url(used_urls: set[str] | None, url: str) -> bool:
    if not url:
        return False
    if used_urls is None:
        return True
    if url in used_urls:
        return False
    used_urls.add(url)
    return True


def _download(url: str, used_urls: set[str] | None = None) -> bytes | None:
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
        return data
    except Exception:
        return None


def fetch_openverse(query: str, used_urls: set[str] | None = None, *_args) -> bytes | None:
    """Search Openverse and return the first downloadable candidate.

    Openverse aggregates openly licensed/public-domain works, but the project
    itself warns that individual license metadata should still be checked before
    publishing.
    """
    q = _clean_query(query)
    if not q:
        return None
    payload = _read_cache("openverse", q)
    if payload is None:
        try:
            response = requests.get(
                OPENVERSE_API,
                params={"q": q, "page_size": 15, "mature": "false"},
                timeout=DEFAULT_TIMEOUT,
                headers={"User-Agent": "ViralShortsFactory/1.0 (+image-retrieval)"},
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                return None
            _write_cache("openverse", q, payload)
        except Exception as exc:
            print(f"   [Visual Source] Openverse | failed: {type(exc).__name__}: {exc} | query='{q}'", flush=True)
            return None

    for item in payload.get("results", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        license_code = str(item.get("license") or "").lower()
        if license_code in {"", "all-rights-reserved", "arr"}:
            continue
        for candidate in (item.get("url"), item.get("thumbnail")):
            data = _download(candidate, used_urls)
            if data:
                return data
    return None


def fetch_pixabay(query: str, used_urls: set[str] | None = None, *_args) -> bytes | None:
    """Search Pixabay using its free API key, when configured."""
    key = str(os.getenv("PIXABAY_API_KEY", "")).strip()
    q = _clean_query(query)
    if not key or not q:
        if not key:
            print("   [Visual Source] Pixabay | API key not configured; skipped.", flush=True)
        return None

    payload = _read_cache("pixabay", q)
    if payload is None:
        try:
            response = requests.get(
                PIXABAY_API,
                params={
                    "key": key,
                    "q": q,
                    "image_type": "photo",
                    "safesearch": "true",
                    "per_page": 20,
                },
                timeout=DEFAULT_TIMEOUT,
                headers={"User-Agent": "ViralShortsFactory/1.0 (+image-retrieval)"},
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                return None
            _write_cache("pixabay", q, payload)
        except Exception as exc:
            print(f"   [Visual Source] Pixabay | failed: {type(exc).__name__}: {exc} | query='{q}'", flush=True)
            return None

    for item in payload.get("hits", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        for candidate in (item.get("largeImageURL"), item.get("webformatURL")):
            data = _download(candidate, used_urls)
            if data:
                return data
    return None
