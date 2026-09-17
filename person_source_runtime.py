"""Authoritative Wikimedia image sourcing for the visual pipeline.

The module keeps the existing PERSON fetcher API for compatibility, while the
Commons fetcher is generic enough for organizations, events, documents,
locations and other named visual subjects. It returns raw image bytes and lets
visual_runtime perform local sanity checks and semantic verification.
"""

import io
import re

import requests
from PIL import Image

REQUEST_TIMEOUT_SECONDS = 15
USER_AGENT = "ViralShortsFactory/1.0 (visual sourcing)"


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
        if not content or len(content) < 2_000:
            return None
        content_type = str(response.headers.get("Content-Type", "")).lower()
        if content_type and not content_type.startswith("image/"):
            return None
        Image.open(io.BytesIO(content)).verify()
        used_urls.add(url)
        return content
    except Exception as exc:
        print(f"   [Visual Source] image download failed: {type(exc).__name__}: {exc}", flush=True)
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
                print(f"   [Visual Source] Wikipedia exact-entity image accepted for '{entity}'.", flush=True)
                return data
    except Exception as exc:
        print(f"   [Visual Source] Wikipedia lookup failed for '{entity}': {type(exc).__name__}: {exc}", flush=True)
    return None


def fetch_wikimedia_commons_image(query, used_urls, original_query="", video_title=""):
    """Return a real Commons image for a bounded visual search phrase.

    SVG source files are intentionally accepted because logos, seals, emblems,
    diagrams and other exact visual assets are frequently stored as SVG on
    Wikimedia Commons. The API's thumbnail URL is preferred so Pillow receives
    a raster image suitable for the existing quality gate.
    """
    raw_query = _clean(query)
    if not raw_query:
        return None
    search_text = re.sub(r"\bWikimedia Commons\b", "", raw_query, flags=re.I).strip() or raw_query
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
                "gsrlimit": "20",
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
            if not mime.startswith("image/"):
                continue
            url = item.get("thumburl") or item.get("url")
            data = _download(url, used_urls)
            if data:
                print(f"   [Visual Source] Wikimedia Commons image accepted for query='{search_text}' mime={mime}.", flush=True)
                return data
    except Exception as exc:
        print(f"   [Visual Source] Wikimedia Commons lookup failed for '{search_text}': {type(exc).__name__}: {exc}", flush=True)
    return None
