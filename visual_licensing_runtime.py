"""Central visual licensing, provenance and attribution policy.

The production visual boundary is deliberately conservative: monetized videos
accept only assets with an explicit commercial-use-compatible license. Search
providers may still exist for compatibility, but unlicensed providers are
disabled unless ALLOW_UNLICENSED_VISUALS is explicitly enabled.
"""
from __future__ import annotations

import html
import json
import os
import re
from typing import Any

ALLOW_UNLICENSED_ENV = "ALLOW_UNLICENSED_VISUALS"
UNLICENSED_PROVIDERS = {"ddg", "duckduckgo", "news_source", "article_source"}
ALLOW_LISTED_OPEN_LICENSES = {"cc0", "pdm", "by", "by-sa", "godl-india"}
LICENSE_URLS = {
    "cc0": "https://creativecommons.org/publicdomain/zero/1.0/",
    "pdm": "https://creativecommons.org/publicdomain/mark/1.0/",
    "by": "https://creativecommons.org/licenses/by/4.0/",
    "by-sa": "https://creativecommons.org/licenses/by-sa/4.0/",
    "godl-india": "https://data.gov.in/sites/default/files/Gazette_Notification_OGDL.pdf",
}
PROVIDER_DEFAULTS = {
    "pexels": ("Pexels", "Pexels License", "https://www.pexels.com/license/"),
    "unsplash": ("Unsplash", "Unsplash License", "https://unsplash.com/license"),
    "pixabay": ("Pixabay", "Pixabay Content License", "https://pixabay.com/service/license-summary/"),
}


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def allow_unlicensed_visuals() -> bool:
    return _truthy(os.getenv(ALLOW_UNLICENSED_ENV, "false"))


def provider_allowed(provider: str) -> bool:
    return allow_unlicensed_visuals() or str(provider or "").strip().casefold() not in UNLICENSED_PROVIDERS


def normalize_license_code(value: Any) -> str:
    text = re.sub(r"\s+", "-", str(value or "").strip().casefold())
    text = re.sub(r"[^a-z0-9-]+", "", text)
    text = re.sub(r"-+", "-", text).strip("-")
    text = re.sub(r"-universal$", "", text)
    if text.startswith("cc-"):
        text = text[3:]
    text = re.sub(r"-[0-9]+(?:-[0-9]+)?$", "", text)
    if text in {"cc0", "zero", "cczero"} or text.startswith(
        ("cc0-", "zero-", "cczero-", "public-domain-dedication-", "public-domain-")
    ):
        return "cc0"
    if text in {"godl", "government-open-data-license-india"}:
        return "godl-india"
    return text


def is_allowed_license(value: Any) -> bool:
    code = normalize_license_code(value)
    if not code or "-nc" in code or "-nd" in code:
        return False
    return code in ALLOW_LISTED_OPEN_LICENSES


def _strip_markup(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def provenance(
    provider: str,
    url: str = "",
    author: str = "",
    license: str = "",
    license_url: str = "",
) -> dict[str, str]:
    provider_name = str(provider or "").strip()
    default = PROVIDER_DEFAULTS.get(provider_name.casefold())
    if default:
        provider_name = default[0]
        license = license or default[1]
        license_url = license_url or default[2]
    return {
        "provider": provider_name,
        "url": str(url or "").strip(),
        "author": _strip_markup(author),
        "license": _strip_markup(license),
        "license_url": str(license_url or "").strip(),
    }


_PROVENANCE_FIELDS = {"provider", "url", "author", "license", "license_url"}


def licensed_candidate(data: bytes, metadata: dict[str, Any]) -> dict[str, Any]:
    """Attach provenance while preserving provider search metadata for candidate ranking."""
    raw = dict(metadata or {})
    provenance_fields = {key: raw.get(key, "") for key in _PROVENANCE_FIELDS}
    candidate = {
        "bytes": bytes(data),
        "provenance": provenance(**provenance_fields),
    }
    for key, value in raw.items():
        if key not in _PROVENANCE_FIELDS:
            candidate[str(key)] = value
    return candidate


def candidate_bytes(value: Any) -> bytes | None:
    if isinstance(value, dict) and "bytes" in value:
        value = value["bytes"]
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    return None


def candidate_provenance(value: Any) -> dict[str, str]:
    if isinstance(value, dict) and isinstance(value.get("provenance"), dict):
        return provenance(**value["provenance"])
    return provenance("unknown")


def provenance_is_usable(record: dict[str, Any]) -> bool:
    """Return True only when commercial-use provenance is explicitly established."""
    provider = str(record.get("provider") or "").strip().casefold()
    license_name = str(record.get("license") or "").strip().casefold()
    if provider in {"pexels", "unsplash", "pixabay"}:
        return bool(license_name) and "-nc" not in license_name and "-nd" not in license_name
    return is_allowed_license(record.get("license"))


def provenance_is_retainable(record: dict[str, Any]) -> bool:
    """Keep uncertain provenance for human review unless metadata is explicitly restrictive.

    This is not an approval. Unknown provenance stays out of automatic selection.
    """
    license_name = str(record.get("license") or "").strip().casefold()
    code = normalize_license_code(record.get("license"))
    if "-nc" in license_name or "-nd" in license_name:
        return False
    if code in {"by-nc", "by-nc-sa", "by-nd", "by-sa-nd"}:
        return False
    return True


def provenance_status(record: dict[str, Any]) -> str:
    if provenance_is_usable(record):
        return "commercial-verified"
    if provenance_is_retainable(record):
        return "provenance-review"
    return "rights-restricted"


def attribution_required(record: dict[str, Any]) -> bool:
    code = normalize_license_code(record.get("license"))
    return code in {"by", "by-sa", "godl-india"}


def append_image_credits(description: str, records: list[dict[str, Any]], max_bytes: int = 5000) -> str:
    """Append required CC attribution while preserving YouTube's byte limit."""
    base = str(description or "").strip()
    credits = build_image_credits(records)
    if not credits:
        return base
    separator = "\n\n"
    suffix = separator + credits
    available = max(0, int(max_bytes) - len(suffix.encode("utf-8")))
    base_bytes = base.encode("utf-8")
    if len(base_bytes) > available:
        base = base_bytes[:available].decode("utf-8", errors="ignore").rstrip()
    return base + suffix


def build_image_credits(records: list[dict[str, Any]]) -> str:
    lines = []
    seen = set()
    for raw in records or []:
        record = provenance(
            raw.get("provider", ""),
            raw.get("url", ""),
            raw.get("author", ""),
            raw.get("license", ""),
            raw.get("license_url", ""),
        )
        if not attribution_required(record):
            continue
        key = tuple(record.values())
        if key in seen:
            continue
        seen.add(key)
        author = record["author"] or "Unknown author"
        license_name = record["license"] or "CC license"
        url = record["url"] or record["license_url"]
        lines.append(f"- {author} — {license_name} — {url}")
    if not lines:
        return ""
    return "Image credits\n" + "\n".join(lines)


def ai_provenance() -> dict[str, str]:
    return provenance(
        "AI-generated",
        url="",
        author="",
        license="AI-generated",
        license_url="",
    )


def rescue_provenance() -> dict[str, str]:
    return provenance(
        "Factory visual",
        url="",
        author="",
        license="Original factory graphic",
        license_url="",
    )


__all__ = [
    "ALLOW_LISTED_OPEN_LICENSES",
    "ALLOW_UNLICENSED_ENV",
    "allow_unlicensed_visuals",
    "append_image_credits",
    "attribution_required",
    "build_image_credits",
    "candidate_bytes",
    "candidate_provenance",
    "is_allowed_license",
    "licensed_candidate",
    "normalize_license_code",
    "provider_allowed",
    "provenance_is_usable",
    "provenance_is_retainable",
    "provenance_status",
    "provenance",
    "rescue_provenance",
    "ai_provenance",
]