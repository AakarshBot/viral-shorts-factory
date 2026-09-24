"""Extract multiple static images directly from the selected news article.

This module is a dashboard visual-pool source, not a topic-discovery provider.
It fetches the selected article once, finds several likely article images using
page metadata, structured data, responsive/lazy image markup and article-body
images, then returns the strongest successfully downloaded static images with
exact page/image provenance. No visual AI or factory QC is applied here.
"""
from __future__ import annotations

import hashlib
import html
import io
import json
import os
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from PIL import Image, UnidentifiedImageError


DEFAULT_TIMEOUT = 12
MAX_HTML_BYTES = 4_000_000
MAX_IMAGE_BYTES = 12_000_000
MIN_IMAGE_SIDE = 500
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_ARTICLE_IMAGE_CANDIDATES = 8


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _cache_root() -> Path:
    root = Path(os.getenv("ASSET_CACHE_DIR", "asset_cache")) / "news_source_images"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _cache_key(url: str) -> str:
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()


def _cache_paths(url: str) -> tuple[Path, Path]:
    key = _cache_key(url)
    root = _cache_root()
    return root / f"{key}.json", root / f"{key}.img"


def _is_video_url(url: str) -> bool:
    path = urlparse(str(url or "")).path.casefold()
    return path.endswith((".mp4", ".webm", ".mov", ".m4v", ".m3u8", ".mpd", ".avi", ".mkv"))


def _srcset_urls(value: str) -> list[str]:
    urls: list[str] = []
    for entry in str(value or "").split(","):
        token = _clean(entry).split(" ", 1)[0].strip()
        if token and not _is_video_url(token):
            urls.append(token)
    return urls


def _image_score(attrs: dict[str, str], in_article: bool) -> float:
    score = 42.0 if in_article else 20.0
    blob = " ".join(
        _clean(attrs.get(key))
        for key in ("class", "id", "itemprop", "alt", "aria-label", "role")
    ).casefold()
    if "itemprop" in attrs and "image" in attrs.get("itemprop", "").casefold():
        score += 35.0
    if any(token in blob for token in ("hero", "featured", "lead-image", "article-image", "main-image", "story-image")):
        score += 22.0
    if any(token in blob for token in ("avatar", "favicon", "icon", "sprite", "tracking", "pixel", "advert", "banner")):
        score -= 30.0
    try:
        width = int(float(attrs.get("width") or 0))
        height = int(float(attrs.get("height") or 0))
        if width >= 800 and height >= 400:
            score += 12.0
        elif width >= 500 and height >= 300:
            score += 7.0
    except (TypeError, ValueError):
        pass
    return score


class _ArticleImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.link_images: list[str] = []
        self.image_candidates: list[tuple[float, str, str]] = []
        self.video_tags = 0
        self.video_sources = 0
        self._in_article = 0
        self._in_video = 0
        self._in_script = False
        self._script_type = ""
        self._script_parts: list[str] = []
        self.json_ld: list[Any] = []

    def _add_image(self, url: str, score: float, method: str) -> None:
        value = _clean(url)
        if not value or _is_video_url(value):
            return
        self.image_candidates.append((float(score), value, method))

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        data = {str(k).lower(): str(v) for k, v in attrs if k and v is not None}

        if tag == "meta":
            key = _clean(
                data.get("property") or data.get("name") or data.get("itemprop")
            ).lower()
            value = _clean(data.get("content"))
            if key and value:
                self.meta[key] = value
            return

        if tag == "article":
            self._in_article += 1
            return

        if tag == "video":
            self.video_tags += 1
            self._in_video += 1
            return

        if tag == "source":
            if self._in_video:
                self.video_sources += 1
                return
            for src in _srcset_urls(data.get("srcset") or data.get("data-srcset") or ""):
                self._add_image(src, 38.0, "responsive-source")
            src = data.get("src") or data.get("data-src")
            if src and "image" in data.get("type", "").casefold():
                self._add_image(src, 40.0, "picture-source")
            return

        if tag == "script":
            self._in_script = True
            self._script_type = data.get("type", "").lower()
            self._script_parts = []
            return

        if tag == "link":
            rel = _clean(data.get("rel")).lower()
            href = _clean(data.get("href"))
            if href and "image_src" in rel:
                self.link_images.append(href)
            elif href and "preload" in rel and data.get("as", "").lower() == "image":
                self.link_images.append(href)
            return

        if tag != "img":
            return

        score = _image_score(data, self._in_article > 0)
        candidates = (
            data.get("src"),
            data.get("data-src"),
            data.get("data-lazy-src"),
            data.get("data-original"),
            data.get("data-image"),
            data.get("data-image-url"),
            data.get("data-url"),
        )
        for src in candidates:
            if src:
                self._add_image(src, score, "article-img" if self._in_article else "page-img")

        for src in _srcset_urls(
            data.get("srcset")
            or data.get("data-srcset")
            or data.get("data-lazy-srcset")
            or ""
        ):
            self._add_image(src, score + 3.0, "responsive-img")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "article":
            self._in_article = max(0, self._in_article - 1)
        elif tag == "video":
            self._in_video = max(0, self._in_video - 1)
        elif tag == "script" and self._in_script:
            if "ld+json" in self._script_type:
                raw = "".join(self._script_parts).strip()
                if raw:
                    try:
                        self.json_ld.append(json.loads(html.unescape(raw)))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        pass
            self._in_script = False
            self._script_type = ""
            self._script_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_script:
            self._script_parts.append(data)


def _walk_json_images(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        image = value.get("image")
        if isinstance(image, str):
            found.append(image)
        elif isinstance(image, (dict, list)):
            found.extend(_walk_json_images(image))
        if str(value.get("@type") or "").casefold() == "imageobject":
            for key in ("contentUrl", "url", "thumbnailUrl"):
                item = value.get(key)
                if isinstance(item, str):
                    found.append(item)
        for child in value.values():
            if isinstance(child, (dict, list)):
                found.extend(_walk_json_images(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_walk_json_images(child))
    return found


def _absolute(url: str, page_url: str) -> str:
    value = _clean(url).replace("\\/", "/")
    if not value:
        return ""
    return urljoin(page_url, value)


def _ranked_candidate_urls(
    parser: _ArticleImageParser,
    page_url: str,
) -> list[tuple[str, str, float]]:
    meta_order = (
        ("og:image", 110.0, "og:image"),
        ("og:image:url", 108.0, "og:image:url"),
        ("og:image:secure_url", 106.0, "og:image:secure_url"),
        ("twitter:image", 104.0, "twitter:image"),
        ("twitter:image:src", 102.0, "twitter:image:src"),
        ("image", 88.0, "meta:image"),
    )
    raw: list[tuple[float, str, str]] = []
    for key, score, method in meta_order:
        if parser.meta.get(key):
            raw.append((score, parser.meta[key], method))
    for value in _walk_json_images(parser.json_ld):
        raw.append((96.0, value, "json-ld:image"))
    for value in parser.link_images:
        raw.append((84.0, value, "link:image"))
    raw.extend(parser.image_candidates)

    seen: set[str] = set()
    output: list[tuple[str, str, float]] = []
    for score, candidate, method in sorted(
        raw,
        key=lambda item: (-float(item[0]), len(str(item[1] or ""))),
    ):
        absolute = _absolute(candidate, page_url)
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"} or _is_video_url(absolute):
            continue
        lowered = absolute.casefold()
        if any(
            token in lowered
            for token in ("1x1", "pixel.gif", "spacer.gif", "transparent.gif", "favicon.ico")
        ):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        output.append((absolute, method, score))
    return output


def _candidate_urls(parser: _ArticleImageParser, page_url: str) -> list[str]:
    return [url for url, _method, _score in _ranked_candidate_urls(parser, page_url)]


def _publisher(parser: _ArticleImageParser, page_url: str, hint: str = "") -> str:
    for key in ("og:site_name", "application-name", "publisher"):
        value = _clean(parser.meta.get(key))
        if value:
            return value
    return _clean(hint) or (urlparse(page_url).netloc or "News source").removeprefix("www.")


def _download_image(
    session: requests.Session,
    url: str,
    referer: str,
) -> tuple[bytes | None, str]:
    if _is_video_url(url):
        return None, ""
    try:
        response = session.get(
            url,
            timeout=DEFAULT_TIMEOUT,
            headers={
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                "Referer": referer,
                "Sec-Fetch-Dest": "image",
            },
            allow_redirects=True,
            stream=True,
        )
        response.raise_for_status()
        content_type = str(response.headers.get("content-type", "")).lower()
        if content_type.startswith("video/"):
            return None, ""
        if content_type and "image" not in content_type and not content_type.startswith("application/octet-stream"):
            return None, ""

        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_IMAGE_BYTES:
                return None, ""
            chunks.append(chunk)
        data = b"".join(chunks)
        if not data:
            return None, ""
        return data, response.url or url
    except (requests.RequestException, OSError):
        return None, ""


def _valid_image(data: bytes) -> bool:
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            width, height = image.size
            return min(width, height) >= MIN_IMAGE_SIDE and width * height >= 300_000
    except (UnidentifiedImageError, OSError, ValueError):
        return False


def _cached(url: str) -> tuple[bytes | None, dict[str, str]]:
    meta_path, image_path = _cache_paths(url)
    try:
        if not meta_path.exists() or not image_path.exists():
            return None, {}
        if meta_path.stat().st_mtime + CACHE_TTL_SECONDS < time.time():
            return None, {}
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        data = image_path.read_bytes()
        return (
            data if _valid_image(data) else None,
            metadata if isinstance(metadata, dict) else {},
        )
    except (OSError, ValueError, json.JSONDecodeError):
        return None, {}


def _store(url: str, data: bytes, metadata: dict[str, str]) -> None:
    meta_path, image_path = _cache_paths(url)
    try:
        image_path.write_bytes(data)
        meta_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def extract_news_source_images(
    article_url: str,
    publisher_hint: str = "",
    max_images: int = MAX_ARTICLE_IMAGE_CANDIDATES,
) -> list[dict[str, Any]]:
    """Return several static images exposed by the selected article page."""
    page_url = _clean(article_url)
    parsed = urlparse(page_url)
    if parsed.scheme not in {"http", "https"}:
        return []

    try:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/151.0 Safari/537.36 "
                    "ViralShortsFactory/3.1 (+article-image-pool)"
                )
            }
        )
        response = session.get(
            page_url,
            timeout=DEFAULT_TIMEOUT,
            headers={"Accept": "text/html,application/xhtml+xml"},
            allow_redirects=True,
        )
        response.raise_for_status()
        raw_html = response.content[:MAX_HTML_BYTES]
        encoding = response.encoding or response.apparent_encoding or "utf-8"
        markup = raw_html.decode(encoding, errors="replace")
        final_url = response.url or page_url
    except (requests.RequestException, UnicodeError, OSError):
        return []

    parser = _ArticleImageParser()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:
        return []

    candidates = _ranked_candidate_urls(parser, final_url)
    if not candidates:
        return []

    publisher = _publisher(parser, final_url, publisher_hint)
    limit = max(1, min(MAX_ARTICLE_IMAGE_CANDIDATES, int(max_images or MAX_ARTICLE_IMAGE_CANDIDATES)))
    output: list[dict[str, Any]] = []
    seen_content: set[str] = set()

    for image_url, method, _score in candidates[: max(12, limit * 3)]:
        data, final_image_url = _download_image(session, image_url, final_url)
        metadata: dict[str, str] = {}
        if not data or not _valid_image(data):
            cached_data, cached_meta = _cached(image_url)
            if cached_data:
                data = cached_data
                metadata = cached_meta

        if not data or not _valid_image(data):
            continue

        content_key = hashlib.sha256(data).hexdigest()
        if content_key in seen_content:
            continue
        seen_content.add(content_key)

        actual_image_url = str(metadata.get("image_url") or final_image_url or image_url).strip()
        actual_page_url = str(metadata.get("page_url") or final_url).strip()
        actual_publisher = str(metadata.get("publisher") or publisher).strip() or "News source"

        record = {
            "bytes": data,
            "hash": content_key,
            "image_url": actual_image_url,
            "page_url": actual_page_url,
            "publisher": actual_publisher,
            "credit": f"Source: {actual_publisher}",
            "method": metadata.get("method") or method,
            "source": "news_source",
            "source_type": "news_source",
            "query": "article source",
            "status": "article-source",
            "used": False,
            "provenance": {
                "provider": actual_publisher,
                "url": actual_image_url or actual_page_url,
                "author": actual_publisher,
                "license": "Unverified article-source license",
                "license_url": actual_page_url,
            },
        }
        output.append(record)
        if len(output) >= limit:
            break

    return output


__all__ = ["extract_news_source_images", "_ArticleImageParser", "_candidate_urls", "_publisher"]
OS)



__all__ = ["extract_news_source_image", "compose_news_source_image"]
