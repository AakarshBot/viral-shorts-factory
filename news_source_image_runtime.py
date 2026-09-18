"""Canonical news-source image extraction and attribution for the Shorts factory.

This module is intentionally isolated from the existing visual providers.  When a
story has a real article URL, it attempts to extract the article's own lead image
(OG image, Twitter image, JSON-LD image, or a real article image).  It never
treats a search-engine result as the article source.

Attribution is metadata, not a rights grant: callers must still respect the
source's licence/permission and YouTube's monetization/copyright rules.
"""
from __future__ import annotations

import hashlib
import html
import io
import json
import os
import re
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


class _ArticleImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.link_images: list[str] = []
        self.article_images: list[str] = []
        self._in_article = 0
        self._in_script = False
        self._script_type = ""
        self._script_parts: list[str] = []
        self.json_ld: list[Any] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        data = {str(k).lower(): str(v) for k, v in attrs if k and v is not None}

        if tag == "meta":
            key = _clean(data.get("property") or data.get("name") or data.get("itemprop")).lower()
            value = _clean(data.get("content"))
            if key and value:
                self.meta[key] = value
            return

        if tag == "article":
            self._in_article += 1
            return

        if tag == "script":
            self._in_script = True
            self._script_type = data.get("type", "").lower()
            self._script_parts = []
            return

        if tag == "link":
            rel = _clean(data.get("rel")).lower()
            href = _clean(data.get("href"))
            if href and ("image_src" in rel or "preload" in rel and data.get("as", "").lower() == "image"):
                self.link_images.append(href)
            return

        if tag == "img":
            src = _clean(
                data.get("src")
                or data.get("data-src")
                or data.get("data-lazy-src")
                or data.get("data-original")
            )
            if src:
                if self._in_article:
                    self.article_images.append(src)
                else:
                    self.article_images.append(src)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "article":
            self._in_article = max(0, self._in_article - 1)
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
        elif isinstance(image, dict):
            found.extend(_walk_json_images(image))
        elif isinstance(image, list):
            found.extend(_walk_json_images(image))
        for key in ("contentUrl", "url", "thumbnailUrl"):
            item = value.get(key)
            if isinstance(item, str) and key != "url":
                found.append(item)
        for child in value.values():
            if isinstance(child, (dict, list)):
                found.extend(_walk_json_images(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_walk_json_images(child))
    return found


def _absolute(url: str, page_url: str) -> str:
    value = _clean(url)
    if not value:
        return ""
    return urljoin(page_url, value)


def _candidate_urls(parser: _ArticleImageParser, page_url: str) -> list[str]:
    keys = (
        "og:image",
        "og:image:url",
        "og:image:secure_url",
        "twitter:image",
        "twitter:image:src",
        "image",
    )
    raw: list[str] = []
    for key in keys:
        if parser.meta.get(key):
            raw.append(parser.meta[key])
    raw.extend(_walk_json_images(parser.json_ld))
    raw.extend(parser.link_images)
    raw.extend(parser.article_images[:12])

    seen: set[str] = set()
    result: list[str] = []
    for candidate in raw:
        absolute = _absolute(candidate, page_url)
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"} or absolute in seen:
            continue
        # Skip obvious tracking/placeholder assets.
        lowered = absolute.lower()
        if any(token in lowered for token in ("1x1", "pixel.gif", "spacer.gif", "transparent.gif", "data:image")):
            continue
        seen.add(absolute)
        result.append(absolute)
    return result


def _publisher(parser: _ArticleImageParser, page_url: str, hint: str = "") -> str:
    for key in ("og:site_name", "application-name", "publisher"):
        value = _clean(parser.meta.get(key))
        if value:
            return value
    return _clean(hint) or (urlparse(page_url).netloc or "News source").removeprefix("www.")


def _download_image(url: str) -> tuple[bytes | None, str]:
    try:
        response = requests.get(
            url,
            timeout=DEFAULT_TIMEOUT,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/151.0 Safari/537.36 "
                "ViralShortsFactory/3.0 (+news-source-image)",
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            },
            allow_redirects=True,
            stream=True,
        )
        response.raise_for_status()
        content_type = str(response.headers.get("content-type", "")).lower()
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
        if os.path.getmtime(meta_path) + CACHE_TTL_SECONDS < __import__("time").time():
            return None, {}
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        data = image_path.read_bytes()
        return (data if _valid_image(data) else None), metadata if isinstance(metadata, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return None, {}


def _store(url: str, data: bytes, metadata: dict[str, str]) -> None:
    meta_path, image_path = _cache_paths(url)
    try:
        image_path.write_bytes(data)
        meta_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def extract_news_source_image(article_url: str, publisher_hint: str = "") -> dict[str, Any] | None:
    """Return the lead image actually exposed by the selected article page."""
    page_url = _clean(article_url)
    parsed = urlparse(page_url)
    if parsed.scheme not in {"http", "https"}:
        return None

    try:
        response = requests.get(
            page_url,
            timeout=DEFAULT_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0 ViralShortsFactory/3.0 (+article-image)"},
            allow_redirects=True,
        )
        response.raise_for_status()
        raw_html = response.content[:MAX_HTML_BYTES]
        encoding = response.encoding or response.apparent_encoding or "utf-8"
        markup = raw_html.decode(encoding, errors="replace")
        final_url = response.url or page_url
    except (requests.RequestException, UnicodeError, OSError):
        return None

    parser = _ArticleImageParser()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:
        return None

    publisher = _publisher(parser, final_url, publisher_hint)
    candidates = _candidate_urls(parser, final_url)
    for image_url in candidates:
        data, final_image_url = _download_image(image_url)
        if not data or not _valid_image(data):
            cached_data, cached_meta = _cached(image_url)
            if cached_data:
                return {
                    "bytes": cached_data,
                    "image_url": cached_meta.get("image_url") or image_url,
                    "page_url": cached_meta.get("page_url") or final_url,
                    "publisher": cached_meta.get("publisher") or publisher,
                    "credit": cached_meta.get("credit") or f"Source: {publisher}",
                    "method": cached_meta.get("method") or "article-metadata",
                }
            continue

        metadata = {
            "image_url": final_image_url or image_url,
            "page_url": final_url,
            "publisher": publisher,
            "credit": f"Source: {publisher}",
            "method": "article-metadata",
        }
        _store(image_url, data, metadata)
        return {"bytes": data, **metadata}

    return None




def compose_news_source_image(image: Image.Image, target_size: tuple[int, int] = (1080, 1920)) -> Image.Image:
    """Crop a source-news photo to 9:16 while preserving salient/face regions.

    Uses deterministic candidate-window scoring. Faces receive the strongest
    protection; otherwise high-contrast/edge-dense regions guide the crop.
    This is deliberately limited to source-news images and does not change the
    existing provider crop behavior.
    """
    import cv2
    import numpy as np

    src = image.convert("RGB")
    tw, th = target_size
    target_ratio = tw / th
    sw, sh = src.size
    if sw <= 0 or sh <= 0:
        return src.resize(target_size, Image.Resampling.LANCZOS)

    # If already close to target, only resize.
    if abs((sw / sh) - target_ratio) < 0.03:
        return src.resize(target_size, Image.Resampling.LANCZOS)

    # Work at a bounded resolution for predictable runtime.
    scale = min(1.0, 900.0 / max(sw, sh))
    probe = np.asarray(src.resize((max(1, int(sw * scale)), max(1, int(sh * scale))), Image.Resampling.BILINEAR))
    gray = cv2.cvtColor(probe, cv2.COLOR_RGB2GRAY)
    faces: list[tuple[float, float, float, float]] = []
    try:
        cascade = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        detector = cv2.CascadeClassifier(cascade)
        if not detector.empty():
            for x, y, w, h in detector.detectMultiScale(
                gray, scaleFactor=1.1, minNeighbors=5, minSize=(28, 28)
            ):
                faces.append((
                    (x + w / 2) / probe.shape[1],
                    (y + h / 2) / probe.shape[0],
                    w / probe.shape[1],
                    h / probe.shape[0],
                ))
    except Exception:
        faces = []

    # Edge density is a useful deterministic proxy for where the photograph
    # contains actual visual information, without requiring another API/model.
    edges = cv2.Canny(gray, 80, 160)
    integral = cv2.integral(edges.astype(np.float32) / 255.0)

    crop_w = min(sw, max(1, int(sh * target_ratio)))
    crop_h = min(sh, max(1, int(sw / target_ratio)))
    if crop_w == sw and crop_h == sh:
        return src.resize(target_size, Image.Resampling.LANCZOS)

    if crop_w < sw:
        max_left = sw - crop_w
        # Evaluate 31 horizontal candidates, with extra candidates around faces.
        positions = {int(round(max_left * i / 30)) for i in range(31)}
        for cx, _, fw, _ in faces:
            positions.add(int(round(cx * sw - crop_w / 2)))
            positions.add(int(round((cx - fw * 0.9) * sw)))
            positions.add(int(round((cx + fw * 0.9) * sw - crop_w)))
        positions = {max(0, min(max_left, p)) for p in positions}
        candidates = [(p, 0, p + crop_w, sh) for p in positions]
    else:
        max_top = sh - crop_h
        positions = {int(round(max_top * i / 30)) for i in range(31)}
        for _, cy, _, fh in faces:
            positions.add(int(round(cy * sh - crop_h / 2)))
            positions.add(int(round((cy - fh * 0.9) * sh)))
            positions.add(int(round((cy + fh * 0.9) * sh - crop_h)))
        positions = {max(0, min(max_top, p)) for p in positions}
        candidates = [(0, p, sw, p + crop_h) for p in positions]

    def rect_edge_score(left: int, top: int, right: int, bottom: int) -> float:
        px1 = int(left * scale)
        py1 = int(top * scale)
        px2 = max(px1 + 1, int(right * scale))
        py2 = max(py1 + 1, int(bottom * scale))
        px2 = min(probe.shape[1], px2)
        py2 = min(probe.shape[0], py2)
        area = integral[py2, px2] - integral[py1, px2] - integral[py2, px1] + integral[py1, px1]
        return float(area) / max(1, (px2 - px1) * (py2 - py1))

    def score(rect: tuple[int, int, int, int]) -> float:
        left, top, right, bottom = rect
        edge_score = rect_edge_score(left, top, right, bottom)
        face_score = 0.0
        for cx, cy, fw, fh in faces:
            fx, fy = cx * sw, cy * sh
            if left <= fx <= right and top <= fy <= bottom:
                # Penalize crops that put a face too close to an edge.
                dx = min(fx - left, right - fx) / max(1, crop_w)
                dy = min(fy - top, bottom - fy) / max(1, crop_h)
                face_score += 8.0 + 12.0 * min(dx, dy)
        # Mild center preference prevents pathological edge-only crops.
        center_x = (left + right) / 2 / sw
        center_y = (top + bottom) / 2 / sh
        center_penalty = 0.6 * ((center_x - 0.5) ** 2 + (center_y - 0.5) ** 2)
        return edge_score + face_score - center_penalty

    best = max(candidates, key=score)
    cropped = src.crop(best)
    return cropped.resize(target_size, Image.Resampling.LANCZOS)


def apply_source_credit(image: Image.Image, credit: str, *, font_size: int = 28) -> Image.Image:
    """Burn a compact, readable source credit into the bottom-right corner."""
    base = image.convert("RGBA")
    text = _clean(credit)[:120]
    if not text:
        return base
    draw = __import__("PIL.ImageDraw", fromlist=["ImageDraw"]).ImageDraw.Draw(base)
    try:
        from PIL import ImageFont
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font_size)
    except Exception:
        try:
            from PIL import ImageFont
            font = ImageFont.truetype(r"C:\\Windows\\Fonts\\arial.ttf", font_size)
        except Exception:
            from PIL import ImageFont
            font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    pad_x, pad_y = 14, 8
    margin = 24
    box_w = bbox[2] - bbox[0] + pad_x * 2
    box_h = bbox[3] - bbox[1] + pad_y * 2
    left = max(0, base.width - box_w - margin)
    top = max(0, base.height - box_h - margin)
    draw.rounded_rectangle(
        [left, top, base.width - margin, base.height - margin],
        radius=max(8, font_size // 3),
        fill=(0, 0, 0, 165),
    )
    draw.text(
        (left + pad_x, top + pad_y - bbox[1]),
        text,
        font=font,
        fill=(255, 255, 255, 235),
    )
    return base


__all__ = ["extract_news_source_image", "compose_news_source_image", "apply_source_credit"]
