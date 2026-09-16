"""Authoritative person-image sourcing for the visual pipeline.

These fetchers deliberately use the person's entity name rather than narrowing
visual adjectives. They return raw image bytes and let visual_runtime perform
local sanity checks and cache/usage control.
"""

import io
import re

import requests
from PIL import Image

REQUEST_TIMEOUT_SECONDS = 15
USER_AGENT = "ViralShortsFactory/1.0 (person visual sourcing)"


def _clean(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _download(url, used_urls):
    if not url or url in used_urls:
        return None
    try:
        response = requests.get(
            url,
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        content = response.content
        if not content or len(content) < 10_000:
            return None
        content_type = str(response.headers.get("Content-Type", "")).lower()
        if content_type and not content_type.startswith("image/"):
            return None
        Image.open(io.BytesIO(content)).verify()
        used_urls.add(url)
        return content
    except Exception as exc:
        print(f"   [Person Source] image download failed: {type(exc).__name__}: {exc}", flush=True)
        return None


def fetch_wikipedia_person_image(entity, used_urls, query="", video_title=""):
    """Return the lead image from the exact Wikipedia page for the entity."""
    entity = _clean(entity)
    if not entity:
        return None
    try:
        response = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "format": "json",
                "formatversion": "2",
                "redirects": "1",
                "prop": "pageimages",
                "piprop": "original|thumbnail",
                "pithumbsize": "1600",
                "titles": entity,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        pages = response.json().get("query", {}).get("pages", [])
        for page in pages:
            if not page.get("pageid") or str(page.get("missing", "")):
                continue
            title = _clean(page.get("title", ""))
            if title.lower() != entity.lower():
                continue
            imageinfo = page.get("original") or page.get("thumbnail") or {}
            url = imageinfo.get("source")
            data = _download(url, used_urls)
            if data:
                print(f"   [Person Source] Wikipedia exact-entity image accepted for '{entity}'.", flush=True)
                return data
    except Exception as exc:
        print(f"   [Person Source] Wikipedia lookup failed for '{entity}': {type(exc).__name__}: {exc}", flush=True)
    return None


def fetch_wikimedia_commons_image(query, used_urls, original_query="", video_title=""):
    """Return a real image from Wikimedia Commons matching the person query."""
    raw_query = _clean(query)
    if not raw_query:
        return None
    # Strip our own routing hint; keep the person's complete name untouched.
    search_text = re.sub(r"\bWikimedia Commons\b", "", raw_query, flags=re.I).strip()
    search_text = search_text or raw_query
    try:
        response = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query",
                "format": "json",
                "formatversion": "2",
                "generator": "search",
                "gsrsearch": f'File:"{search_text}"',
                "gsrnamespace": "6",
                "gsrlimit": "12",
                "prop": "imageinfo",
                "iiprop": "url|mime|size",
                "iiurlwidth": "1800",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        pages = response.json().get("query", {}).get("pages", [])
        for page in pages:
            info = page.get("imageinfo") or []
            if not info:
                continue
            item = info[0]
            mime = str(item.get("mime", "")).lower()
            if mime not in {"image/jpeg", "image/png", "image/webp"}:
                continue
            url = item.get("thumburl") or item.get("url")
            data = _download(url, used_urls)
            if data:
                print(f"   [Person Source] Wikimedia Commons image accepted for query='{search_text}'.", flush=True)
                return data
    except Exception as exc:
        print(f"   [Person Source] Wikimedia Commons lookup failed for '{search_text}': {type(exc).__name__}: {exc}", flush=True)
    return None
