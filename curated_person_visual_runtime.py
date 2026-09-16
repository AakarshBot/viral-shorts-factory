"""Reliable, licence-aware person-image sourcing for the Shorts visual pipeline.

Uses Wikimedia Commons' public MediaWiki API directly instead of depending on
legacy bot fetcher signatures. Person images from Commons are treated as
curated PERSON visuals by visual_runtime, so they do not consume Gemini QA
attempts.
"""

import io
import re

import requests
from PIL import Image

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "ViralShortsFactory/2.0 (visual sourcing; contact via GitHub)"
TIMEOUT_SECONDS = 12


def _sanity(data):
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
        if min(img.size) < 300:
            return False
        ratio = img.width / max(1, img.height)
        return 0.4 <= ratio <= 2.5
    except Exception:
        return False


def _clean_name(name):
    return re.sub(r"\s+", " ", str(name or "")).strip()


def fetch_person_from_commons(entity, query="", used_urls=None):
    """Return image bytes from Wikimedia Commons for a named person.

    The search is deliberately bounded. It prefers files whose Commons
    metadata explicitly identifies the person, then falls back to the Commons
    category/search results for that person.
    """
    entity = _clean_name(entity)
    if not entity:
        return None, None
    used_urls = used_urls if used_urls is not None else set()

    searches = [
        f'"{entity}"',
        f"{entity} portrait",
        f"{entity} photo",
    ]

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    for search in searches:
        try:
            params = {
                "action": "query",
                "format": "json",
                "generator": "search",
                "gsrsearch": search,
                "gsrnamespace": 6,
                "gsrlimit": 8,
                "prop": "imageinfo|categories",
                "iiprop": "url|mime|size",
                "iiurlwidth": 1200,
                "cllimit": 20,
            }
            response = session.get(COMMONS_API, params=params, timeout=TIMEOUT_SECONDS)
            response.raise_for_status()
            pages = response.json().get("query", {}).get("pages", {})
        except Exception as exc:
            print(f"   [Curated Person] Commons search failed: {type(exc).__name__}: {exc}", flush=True)
            continue

        ranked = []
        entity_tokens = {x.lower() for x in re.findall(r"[a-z0-9]+", entity.lower()) if len(x) > 1}
        for page in pages.values():
            title = str(page.get("title", ""))
            info = (page.get("imageinfo") or [{}])[0]
            url = info.get("thumburl") or info.get("url")
            mime = str(info.get("mime", "")).lower()
            width = int(info.get("thumbwidth") or info.get("width") or 0)
            height = int(info.get("thumbheight") or info.get("height") or 0)
            if not url or url in used_urls or not mime.startswith("image/"):
                continue
            if min(width, height) < 300:
                continue
            title_tokens = {x.lower() for x in re.findall(r"[a-z0-9]+", title)}
            overlap = len(entity_tokens & title_tokens)
            ranked.append((overlap, width * height, title, url))

        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        for _, _, title, url in ranked:
            try:
                image_response = session.get(url, timeout=TIMEOUT_SECONDS)
                image_response.raise_for_status()
                data = image_response.content
                if not _sanity(data):
                    continue
                used_urls.add(url)
                print(f"   [Curated Person] Wikimedia Commons verified candidate: '{title}'.", flush=True)
                return data, url
            except Exception as exc:
                print(f"   [Curated Person] Image download failed: {type(exc).__name__}: {exc}", flush=True)

    return None, None
