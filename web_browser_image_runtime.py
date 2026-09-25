"""Browser-rendered image extraction for current news/profile pages.

This module is intentionally small and synchronous at its public boundary. It
uses one Chromium session per crawl, renders pages so JavaScript/lazy galleries
exist, captures DOM + network image URLs, and downloads candidates through the
same browser context so cookies/referers/CDN rules are preserved.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

from PIL import Image, UnidentifiedImageError

BROWSER_NAV_TIMEOUT_MS = max(6000, min(18000, int(os.getenv("VISUAL_BROWSER_NAV_TIMEOUT_MS", "12000"))))
BROWSER_SETTLE_MS = max(250, min(2500, int(os.getenv("VISUAL_BROWSER_SETTLE_MS", "900"))))
BROWSER_SCROLLS = max(1, min(5, int(os.getenv("VISUAL_BROWSER_SCROLLS", "4"))))
BROWSER_SCROLL_WAIT_MS = max(150, min(1200, int(os.getenv("VISUAL_BROWSER_SCROLL_WAIT_MS", "350"))))
BROWSER_MAX_PAGES = max(2, min(8, int(os.getenv("VISUAL_BROWSER_MAX_PAGES", "6"))))
BROWSER_IMAGES_PER_PAGE = max(2, min(8, int(os.getenv("VISUAL_BROWSER_IMAGES_PER_PAGE", "6"))))
BROWSER_MAX_CANDIDATE_URLS = max(12, min(40, int(os.getenv("VISUAL_BROWSER_MAX_CANDIDATE_URLS", "28"))))
MAX_IMAGE_BYTES = max(4_000_000, min(15_000_000, int(os.getenv("VISUAL_BROWSER_MAX_IMAGE_BYTES", "12_000_000"))))
MIN_IMAGE_SIDE = max(400, min(1000, int(os.getenv("VISUAL_BROWSER_MIN_IMAGE_SIDE", "500"))))
AUTO_INSTALL_BROWSER = str(os.getenv("VISUAL_BROWSER_AUTO_INSTALL", "1")).strip().lower() not in {"0", "false", "no", "off"}
_AUTO_INSTALL_ATTEMPTED = False

_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "for", "from",
    "by", "with", "after", "before", "during", "over", "into", "about", "this",
    "that", "these", "those", "is", "are", "was", "were", "be", "been", "being",
    "has", "have", "had", "will", "would", "could", "should", "says", "said",
    "report", "reports", "latest", "news", "story", "update", "today", "video",
    "photos", "photo", "images", "image", "pictures", "picture",
}
_ACTION_TERMS = {
    "action", "playing", "played", "match", "game", "batting", "batted", "bowling",
    "bowled", "fielder", "fielding", "wicket", "goal", "scored", "scores", "tackle",
    "tackling", "dribble", "dunk", "serve", "forehand", "backhand", "race", "running",
    "sprint", "training", "celebrate", "celebrates", "celebration", "shoot", "shot",
    "save", "header", "podium", "lap", "finish", "qualifying", "fight", "ring",
    "knockout", "victory", "lifting", "catch", "caught", "throw", "toss",
}
_BAD_IMAGE_TOKENS = {
    "logo", "icon", "favicon", "sprite", "tracking", "pixel", "avatar", "placeholder",
    "advert", "ads", "banner", "social-share", "share-image", "default-image",
}
_BLOCKED_HOSTS = {
    "facebook.com", "instagram.com", "x.com", "twitter.com", "youtube.com", "tiktok.com",
}


def _clean(value, limit=5000):
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _tokens(value):
    return {
        token.casefold()
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9'’.-]*", _clean(value))
        if len(token) > 2 and token.casefold() not in _STOPWORDS
    }


def _title_match(query, title, entity=""):
    q = _tokens(query)
    t = _tokens(title)
    if not q or not t:
        return 0.0
    overlap = len(q & t) / max(1, len(q))
    e = _tokens(entity)
    entity_overlap = len(e & t) / max(1, len(e)) if e else 1.0
    if e and entity_overlap < 0.75:
        return 0.0
    # A publisher headline may shorten a two-token person/entity query to
    # one token (for example, "Virat Kohli" -> "Kohli"). Treat that as a
    # valid entity-page title match; the crawler has already relevance-ranked
    # the page before it reaches this browser gate.
    if e and q == e and len(e) >= 2 and len(q & t) >= 1:
        return 0.75

    required = 0.82 if len(q) <= 4 else 0.58
    if overlap < required:
        return 0.0
    return min(1.0, overlap * 0.75 + entity_overlap * 0.25)


def _image_urls_from_srcset(value):
    output = []
    for entry in str(value or "").split(","):
        token = _clean(entry, 1600).split(" ", 1)[0].strip()
        if token:
            output.append(token)
    return output


def _walk_json_images(value):
    found = []
    if isinstance(value, str):
        if value.startswith(("http://", "https://", "/", "./", "../")):
            found.append(value)
    elif isinstance(value, dict):
        for key in ("image", "contentUrl", "url", "thumbnailUrl"):
            child = value.get(key)
            if isinstance(child, (str, dict, list)):
                found.extend(_walk_json_images(child))
        for child in value.values():
            if isinstance(child, (dict, list)):
                found.extend(_walk_json_images(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_walk_json_images(child))
    return found


def _absolute(value, base_url):
    value = _clean(value, 2500).replace("\\/", "/")
    if not value or value.startswith(("data:", "blob:", "javascript:")):
        return ""
    url = urljoin(base_url, value)
    parsed = urlparse(url)
    return url if parsed.scheme in {"http", "https"} else ""


def _extract_noscript_urls(markup, base_url):
    urls = []
    for match in re.finditer(r"""<(?:img|source)\b[^>]*(?:src|data-src|data-lazy-src|data-original|data-image|data-srcset)\s*=\s*["']([^"']+)["']""", markup or "", re.IGNORECASE):
        raw = match.group(1)
        values = _image_urls_from_srcset(raw) if "," in raw else [raw]
        for value in values:
            absolute = _absolute(value, base_url)
            if absolute:
                urls.append(absolute)
    return urls


def _candidate_score(candidate, query, entity, page_title):
    text = _clean(
        " ".join(
            str(candidate.get(key) or "")
            for key in ("alt", "title", "class_name", "caption", "context_text")
        ),
        5000,
    )
    q = _tokens(query)
    t = _tokens(text)
    query_overlap = len(q & t) / max(1, len(q)) if q else 0.0
    e = _tokens(entity)
    entity_overlap = len(e & t) / max(1, len(e)) if e else 0.0
    score = 0.0

    if candidate.get("in_article"):
        score += 45.0
    if candidate.get("in_figure"):
        score += 15.0
    if candidate.get("method") in {"og:image", "twitter:image", "json-ld:image"}:
        score += 18.0
    if candidate.get("method") in {"currentSrc", "srcset", "article-img"}:
        score += 14.0

    width = int(candidate.get("width") or 0)
    height = int(candidate.get("height") or 0)
    if min(width, height) >= 1400:
        score += 18.0
    elif min(width, height) >= 900:
        score += 10.0
    elif min(width, height) >= 500:
        score += 4.0

    if query_overlap:
        score += query_overlap * 22.0
    if entity_overlap:
        score += entity_overlap * 28.0

    action_hits = len(_tokens(text) & _ACTION_TERMS)
    score += min(24.0, action_hits * 8.0)

    bad_hits = len(_tokens(candidate.get("url") or "") & _BAD_IMAGE_TOKENS)
    if bad_hits:
        score -= 80.0
    lower_blob = (str(candidate.get("url") or "") + " " + text).casefold()
    if any(token in lower_blob for token in _BAD_IMAGE_TOKENS):
        score -= 45.0

    if not _title_match(query, page_title, entity):
        score -= 15.0

    return round(score, 3)


def _valid_image(data):
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            width, height = image.size
            return min(width, height) >= MIN_IMAGE_SIDE and width * height >= 300_000
    except (UnidentifiedImageError, OSError, ValueError):
        return False


def _image_dimensions(data):
    try:
        with Image.open(io.BytesIO(data)) as image:
            return image.size
    except Exception:
        return 0, 0


def _browser_user_agent():
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/151.0.0.0 Safari/537.36"
    )


def _install_browser_once():
    global _AUTO_INSTALL_ATTEMPTED
    if _AUTO_INSTALL_ATTEMPTED or not AUTO_INSTALL_BROWSER:
        return False
    _AUTO_INSTALL_ATTEMPTED = True
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=300,
            check=False,
        )
        if completed.returncode == 0:
            return True
    except Exception:
        pass
    return False


async def _launch_browser(playwright):
    try:
        return await playwright.chromium.launch(headless=True)
    except Exception as exc:
        message = str(exc).casefold()
        if AUTO_INSTALL_BROWSER and "executable doesn't exist" in message:
            if _install_browser_once():
                return await playwright.chromium.launch(headless=True)
        raise


_PAGE_SCRIPT = r"""
() => {
  const meta = {};
  document.querySelectorAll('meta[property], meta[name], meta[itemprop]').forEach(el => {
    const key = (el.getAttribute('property') || el.getAttribute('name') || el.getAttribute('itemprop') || '').toLowerCase();
    const value = el.getAttribute('content') || '';
    if (key && value && !meta[key]) meta[key] = value;
  });

  const imageData = Array.from(document.querySelectorAll('img')).map((img, index) => {
    const rect = img.getBoundingClientRect();
    const figure = img.closest('figure');
    const article = img.closest('article, main, [itemtype*="NewsArticle"], [itemtype*="Article"]');
    const caption = figure?.querySelector('figcaption')?.innerText || '';
    let parentText = '';
    let node = img.parentElement;
    for (let i = 0; i < 3 && node; i += 1, node = node.parentElement) {
      parentText += ' ' + (node.innerText || '').slice(0, 800);
    }
    return {
      index,
      currentSrc: img.currentSrc || '',
      src: img.getAttribute('src') || '',
      srcset: img.getAttribute('srcset') || '',
      dataSrc: img.getAttribute('data-src') || '',
      dataSrcset: img.getAttribute('data-srcset') || '',
      dataLazySrc: img.getAttribute('data-lazy-src') || '',
      dataOriginal: img.getAttribute('data-original') || '',
      alt: img.getAttribute('alt') || '',
      title: img.getAttribute('title') || '',
      className: img.className || '',
      width: img.naturalWidth || 0,
      height: img.naturalHeight || 0,
      inArticle: Boolean(article),
      inFigure: Boolean(figure),
      visible: rect.width > 40 && rect.height > 40 && rect.bottom > 0,
      rectTop: Math.round(rect.top),
      contextText: (caption + parentText).slice(0, 2200)
    };
  });

  const linkImages = Array.from(document.querySelectorAll(
    'link[rel~="image_src"], link[rel~="preload"][as="image"]'
  )).map(el => el.getAttribute('href') || '').filter(Boolean);

  const backgrounds = Array.from(document.querySelectorAll('article img, article figure, main img, main figure, article [style], main [style]'))
    .slice(0, 500)
    .map(el => {
      const image = getComputedStyle(el).backgroundImage || '';
      const match = image.match(/url\(["']?(.*?)["']?\)/);
      return match ? match[1] : '';
    }).filter(Boolean);

  const timeDates = Array.from(document.querySelectorAll('time[datetime], [itemprop="datePublished"][datetime]'))
    .map(el => el.getAttribute('datetime') || '').filter(Boolean);

  const jsonLd = Array.from(document.querySelectorAll('script[type="application/ld+json"]'))
    .map(el => el.textContent || '').filter(Boolean).slice(0, 20);

  const noscripts = Array.from(document.querySelectorAll('noscript'))
    .map(el => el.textContent || el.innerHTML || '').filter(Boolean).slice(0, 12);

  return {
    title: document.querySelector('meta[property="og:title"]')?.content
      || document.querySelector('meta[name="twitter:title"]')?.content
      || document.title
      || '',
    meta,
    imageData,
    linkImages,
    backgrounds,
    timeDates,
    jsonLd,
    noscripts,
    finalUrl: location.href
  };
}
"""


async def _download_image(page, url, page_url, network_responses):
    try:
        response = await page.request.get(
            url,
            timeout=min(9000, BROWSER_NAV_TIMEOUT_MS),
            headers={
                "Referer": page_url,
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            },
        )
        if not response.ok:
            return None
        content_type = str(response.headers.get("content-type") or "").lower()
        content_length = int(response.headers.get("content-length") or 0)
        if content_type and "image" not in content_type and not content_type.startswith("application/octet-stream"):
            return None
        if content_length > MAX_IMAGE_BYTES:
            return None
        data = await response.body()
        if len(data) > MAX_IMAGE_BYTES or not _valid_image(data):
            return None
        return data
    except Exception:
        fallback = network_responses.get(url)
        if fallback is not None:
            try:
                data = await fallback.body()
                if len(data) <= MAX_IMAGE_BYTES and _valid_image(data):
                    return data
            except Exception:
                pass
        return None


def _parse_published(data):
    candidates = []
    meta = data.get("meta") or {}
    for key in (
        "article:published_time", "datepublished", "datePublished", "pubdate",
        "publishdate", "parsely-pub-date", "dc.date", "dc.date.issued",
        "date", "date.created", "datecreated",
    ):
        if meta.get(key):
            candidates.append(meta[key])
    candidates.extend(data.get("timeDates") or [])

    for raw in data.get("jsonLd") or []:
        try:
            value = json.loads(raw)
        except Exception:
            continue
        stack = [value]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                for key in ("datePublished", "dateCreated"):
                    if item.get(key):
                        candidates.append(item[key])
                stack.extend(v for v in item.values() if isinstance(v, (dict, list)))
            elif isinstance(item, list):
                stack.extend(item)

    for raw in candidates:
        text = _clean(raw, 200)
        if not text:
            continue
        try:
            value = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def _build_candidates(data, page_url, query, entity, page_title, max_images):
    raw = []

    def add(url, method, payload=None):
        absolute = _absolute(url, page_url)
        if not absolute:
            return
        item = dict(payload or {})
        item["url"] = absolute
        item["method"] = method
        raw.append(item)

    meta = data.get("meta") or {}
    for key, method in (
        ("og:image", "og:image"),
        ("og:image:url", "og:image"),
        ("og:image:secure_url", "og:image"),
        ("twitter:image", "twitter:image"),
        ("twitter:image:src", "twitter:image"),
    ):
        if meta.get(key):
            add(meta[key], method)

    for raw_json in data.get("jsonLd") or []:
        try:
            value = json.loads(raw_json)
        except Exception:
            continue
        for image_url in _walk_json_images(value):
            add(image_url, "json-ld:image")

    for value in data.get("linkImages") or []:
        add(value, "link:image")

    for item in data.get("imageData") or []:
        values = (
            ("currentSrc", "currentSrc"),
            ("src", "article-img" if item.get("inArticle") else "page-img"),
            ("dataSrc", "lazy-src"),
            ("dataLazySrc", "lazy-src"),
            ("dataOriginal", "lazy-original"),
        )
        for key, method in values:
            if item.get(key):
                add(item[key], method, {
                    "alt": item.get("alt"),
                    "title": item.get("title"),
                    "class_name": item.get("className"),
                    "caption": "",
                    "context_text": item.get("contextText"),
                    "in_article": item.get("inArticle"),
                    "in_figure": item.get("inFigure"),
                    "width": item.get("width"),
                    "height": item.get("height"),
                })
        for value in _image_urls_from_srcset(item.get("srcset") or "") + _image_urls_from_srcset(item.get("dataSrcset") or ""):
            add(value, "srcset", {
                "alt": item.get("alt"),
                "title": item.get("title"),
                "class_name": item.get("className"),
                "context_text": item.get("contextText"),
                "in_article": item.get("inArticle"),
                "in_figure": item.get("inFigure"),
                "width": item.get("width"),
                "height": item.get("height"),
            })

    for value in data.get("backgrounds") or []:
        add(value, "background-image", {"in_article": True})

    for markup in data.get("noscripts") or []:
        for value in _extract_noscript_urls(markup, page_url):
            add(value, "noscript:image", {"in_article": True})

    deduped = []
    seen = set()
    for item in raw:
        url = item["url"]
        if url.casefold() in seen:
            continue
        seen.add(url.casefold())
        lowered = url.casefold()
        if any(token in lowered for token in _BAD_IMAGE_TOKENS):
            continue
        item["score"] = _candidate_score(item, query, entity, page_title)
        deduped.append(item)

    deduped.sort(key=lambda x: (-float(x.get("score") or 0), -int(x.get("height") or 0), x.get("url", "")))
    return deduped[:max(BROWSER_MAX_CANDIDATE_URLS, max_images * 4)]


async def _scrape_one(context, request):
    page = await context.new_page()
    network_responses = {}

    def remember_response(response):
        try:
            if response.request.resource_type != "image" or not response.ok:
                return
            if len(network_responses) < 40:
                network_responses.setdefault(response.url, response)
        except Exception:
            pass

    page.on("response", remember_response)
    try:
        try:
            await page.goto(
                request["url"],
                wait_until="domcontentloaded",
                timeout=BROWSER_NAV_TIMEOUT_MS,
            )
        except Exception:
            if not page.url:
                return {"assets": [], "error": "navigation-failed"}

        try:
            await page.wait_for_load_state("networkidle", timeout=2500)
        except Exception:
            pass
        await page.wait_for_timeout(BROWSER_SETTLE_MS)
        for _ in range(BROWSER_SCROLLS):
            try:
                await page.evaluate("window.scrollBy(0, Math.min(window.innerHeight * 1.35, 1600));")
            except Exception:
                break
            await page.wait_for_timeout(BROWSER_SCROLL_WAIT_MS)
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
            await page.wait_for_timeout(BROWSER_SCROLL_WAIT_MS)
            await page.evaluate("window.scrollTo(0, 0);")
            await page.wait_for_timeout(200)
        except Exception:
            pass

        try:
            data = await page.evaluate(_PAGE_SCRIPT)
        except Exception:
            return {"assets": [], "error": "page-evaluation-failed"}

        page_title = _clean(data.get("title"), 500)
        final_url = _clean(data.get("finalUrl") or page.url, 2500)
        query = _clean(request.get("query"), 500)
        entity = _clean(request.get("entity"), 180)
        title_match = _title_match(query, page_title, entity) if query else (
            1.0 if not entity or _title_match(entity, page_title, entity) else 0.0
        )
        if query and title_match <= 0:
            return {
                "assets": [],
                "page_title": page_title,
                "published_at": None,
                "final_url": final_url,
                "title_match": 0.0,
                "error": "page-title-mismatch",
            }

        published_at = _parse_published(data)
        candidates = _build_candidates(
            data,
            final_url,
            query,
            entity,
            page_title,
            int(request.get("max_images") or BROWSER_IMAGES_PER_PAGE),
        )

        assets = []
        seen_hashes = set()
        for candidate in candidates:
            if len(assets) >= int(request.get("max_images") or BROWSER_IMAGES_PER_PAGE):
                break
            data_bytes = await _download_image(page, candidate["url"], final_url, network_responses)
            if not data_bytes:
                continue
            width, height = _image_dimensions(data_bytes)
            if min(width, height) < MIN_IMAGE_SIDE:
                continue
            digest = __import__("hashlib").sha256(data_bytes).hexdigest()
            if digest in seen_hashes:
                continue
            seen_hashes.add(digest)
            assets.append({
                "bytes": data_bytes,
                "hash": digest,
                "image_url": candidate["url"],
                "page_url": final_url,
                "publisher": _clean(
                    (data.get("meta") or {}).get("og:site_name")
                    or request.get("publisher")
                    or (urlparse(final_url).netloc or "Web source").removeprefix("www."),
                    160,
                ),
                "method": candidate["method"],
                "image_alt": _clean(candidate.get("alt"), 800),
                "image_context": _clean(candidate.get("context_text"), 2200),
                "image_score": float(candidate.get("score") or 0),
                "width": width,
                "height": height,
                "page_title": page_title,
                "page_published_at": published_at.isoformat() if published_at else "",
                "title_match": round(title_match, 3),
            })

        return {
            "assets": assets,
            "page_title": page_title,
            "published_at": published_at,
            "final_url": final_url,
            "title_match": title_match,
            "error": "",
        }
    finally:
        try:
            await page.close()
        except Exception:
            pass


async def _scrape_pages_async(requests_list):
    from playwright.async_api import async_playwright

    requests_list = list(requests_list or [])[:BROWSER_MAX_PAGES]
    if not requests_list:
        return []
    async with async_playwright() as playwright:
        browser = await _launch_browser(playwright)
        try:
            context = await browser.new_context(
                viewport={"width": 1440, "height": 1400},
                locale="en-IN",
                user_agent=_browser_user_agent(),
                extra_http_headers={
                    "Accept-Language": "en-IN,en;q=0.9",
                    "Upgrade-Insecure-Requests": "1",
                },
            )
            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
            )
            results = await asyncio.gather(
                *(_scrape_one(context, request) for request in requests_list),
                return_exceptions=True,
            )
            output = []
            for result in results:
                if isinstance(result, Exception):
                    output.append({"assets": [], "error": f"{type(result).__name__}:{result}"})
                else:
                    output.append(result)
            await context.close()
            return output
        finally:
            await browser.close()


def scrape_web_pages(requests_list):
    """Render and scrape several current article/profile pages in one browser session."""
    try:
        return asyncio.run(_scrape_pages_async(requests_list))
    except Exception as exc:
        print(
            f"   [Browser Web Scraper] unavailable: {type(exc).__name__}: {exc}",
            flush=True,
        )
        return [{"assets": [], "error": f"{type(exc).__name__}:{exc}"} for _ in (requests_list or [])]


__all__ = [
    "BROWSER_MAX_PAGES",
    "BROWSER_IMAGES_PER_PAGE",
    "scrape_web_pages",
]
