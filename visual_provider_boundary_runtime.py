"""Raw visual-provider adapters used by the authoritative retrieval boundary.

Provider adapters only search/resolve/download candidate image bytes. The active
acceptance boundary remains ``visual_retrieval_runtime`` where decode,
resolution, deduplication and semantic verification are applied consistently.

The adapters expose bounded multi-candidate functions. The active retrieval
path can therefore reject a poor first result and evaluate better alternatives.
"""
from __future__ import annotations

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse
from typing import Any

import requests

from visual_taxonomy_runtime import preferred_sources
from visual_licensing_runtime import (
    LICENSE_URLS,
    is_allowed_license,
    licensed_candidate,
    normalize_license_code,
)

API_TIMEOUT_SECONDS = max(2, min(5, int(os.getenv("VISUAL_PROVIDER_TIMEOUT_SECONDS", "4"))))
IMAGE_TIMEOUT_SECONDS = max(2, min(5, int(os.getenv("VISUAL_IMAGE_DOWNLOAD_TIMEOUT_SECONDS", "5"))))
MAX_PROVIDER_CANDIDATES = max(1, min(10, int(os.getenv("VISUAL_PROVIDER_CANDIDATES", "10"))))
_PROVIDER_429_COOLDOWN_SECONDS = max(10, min(120, int(os.getenv("VISUAL_PROVIDER_429_COOLDOWN_SECONDS", "45"))))
_PROVIDER_429_UNTIL: dict[str, float] = {}


def _clean_query(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:240]


def _provider_page(args: tuple[Any, ...] | list[Any] | None) -> int:
    """Read the optional trailing provider page without changing legacy call signatures."""
    try:
        values = list(args or ())
        if values:
            candidate = values[-1]
            if isinstance(candidate, bool):
                return 1
            page = int(candidate)
            return max(1, min(25, page))
    except (TypeError, ValueError):
        pass
    return 1



def _provider_host(url: str) -> str:
    try:
        return str(urlparse(str(url or "")).netloc or "").casefold()
    except Exception:
        return ""


def _provider_429_available(url: str) -> bool:
    host = _provider_host(url)
    if not host:
        return True
    until = float(_PROVIDER_429_UNTIL.get(host, 0.0) or 0.0)
    if until <= time.monotonic():
        _PROVIDER_429_UNTIL.pop(host, None)
        return True
    return False


def _mark_provider_429(url: str, retry_after: str = "") -> None:
    host = _provider_host(url)
    if not host:
        return
    delay = float(_PROVIDER_429_COOLDOWN_SECONDS)
    try:
        if retry_after:
            delay = max(delay, min(120.0, float(retry_after)))
    except (TypeError, ValueError):
        pass
    _PROVIDER_429_UNTIL[host] = time.monotonic() + delay


def _remember_success(used_urls: set[str] | None, url: str, data: bytes | None) -> bytes | None:
    if not data:
        return None
    if used_urls is not None and url in used_urls:
        return None
    if used_urls is not None:
        used_urls.add(url)
    return data


def _download_image(url: str, used_urls: set[str] | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any] | None:
    url = str(url or "").strip()
    if not url or not url.startswith(("http://", "https://")):
        return None
    if used_urls is not None and url in used_urls:
        return None
    if not _provider_429_available(url):
        return None
    try:
        response = requests.get(
            url,
            timeout=IMAGE_TIMEOUT_SECONDS,
            headers={"User-Agent": "ViralShortsFactory/1.0 (+visual-retrieval)"},
            allow_redirects=True,
        )
        if response.status_code == 429:
            _mark_provider_429(url, response.headers.get("Retry-After", ""))
            return None
        response.raise_for_status()
        data = response.content
        content_type = str(response.headers.get("content-type", "")).lower()
        if not data:
            return None
        if content_type and not ("image" in content_type or content_type.startswith("application/octet-stream")):
            return None
        accepted = _remember_success(used_urls, response.url or url, data)
        if not accepted:
            return None
        meta = dict(metadata or {})
        meta.setdefault("url", response.url or url)
        meta.setdefault("source_image_url", response.url or url)
        return licensed_candidate(accepted, meta)
    except Exception:
        return None


def _api_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    if not _provider_429_available(url):
        return None
    try:
        response = requests.get(
            url,
            params=params or {},
            headers=headers or {"User-Agent": "ViralShortsFactory/1.0 (+visual-retrieval)"},
            timeout=API_TIMEOUT_SECONDS,
        )
        if response.status_code == 429:
            _mark_provider_429(url, response.headers.get("Retry-After", ""))
            print(
                f"   [Visual Source] 429 rate limit; cooling down host={_provider_host(url)}.",
                flush=True,
            )
            return None
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else None
    except Exception as exc:
        print(f"   [Visual Source] raw provider request failed: {type(exc).__name__}: {exc}", flush=True)
        return None


_PERSON_IDENTITY_CACHE: dict[str, dict[str, str]] = {}
_PERSON_IDENTITY_CACHE_MAX = 128
_WIKIDATA_ENTITY_CACHE: dict[str, dict[str, str]] = {}
_WIKIDATA_ENTITY_CACHE_MAX = 256


def _verify_wikidata_human(candidate_ids: list[str], labels: dict[str, str]) -> tuple[str, str, bool]:
    """Return the first verified human QID, its label, and whether detail lookup succeeded."""
    ids = [str(qid or "").strip() for qid in candidate_ids if re.fullmatch(r"Q\d+", str(qid or "").strip())]
    if not ids:
        return "", "", False

    detail_payload = _api_json(
        "https://www.wikidata.org/w/api.php",
        params={
            "action": "wbgetentities",
            "ids": "|".join(ids[:5]),
            "props": "claims|labels",
            "languages": "en",
            "format": "json",
        },
    )
    entities = detail_payload.get("entities", {}) if detail_payload else {}
    detail_succeeded = isinstance(entities, dict) and bool(entities)
    if not detail_succeeded:
        return "", "", False

    for qid in ids[:5]:
        entity = entities.get(qid)
        claims = entity.get("claims", {}) if isinstance(entity, dict) else {}
        p31 = claims.get("P31", []) if isinstance(claims, dict) else []
        for claim in p31 if isinstance(p31, list) else []:
            main_snak = claim.get("mainsnak", {}) if isinstance(claim, dict) else {}
            value = main_snak.get("datavalue", {}).get("value", {}) if isinstance(main_snak, dict) else {}
            if isinstance(value, dict) and str(value.get("id") or "").strip() == "Q5":
                label = labels.get(qid, "")
                label_data = entity.get("labels", {}).get("en", {}) if isinstance(entity, dict) else {}
                if isinstance(label_data, dict):
                    label = str(label_data.get("value") or label).strip()
                return qid, label, True
    return "", "", True


def _wikipedia_identity_candidates(query: str) -> tuple[list[str], dict[str, str]]:
    """Use Wikipedia's fuzzy search as a bounded spelling/alias fallback."""
    payload = _api_json(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "query",
            "generator": "search",
            "gsrsearch": _clean_query(query),
            "redirects": 1,
            "gsrnamespace": 0,
            "gsrlimit": 5,
            "prop": "pageprops",
            "ppprop": "wikibase_item",
            "format": "json",
        },
    )
    pages = payload.get("query", {}).get("pages", {}) if payload else {}
    if not isinstance(pages, dict):
        return [], {}

    ordered_pages = sorted(
        (page for page in pages.values() if isinstance(page, dict)),
        key=lambda page: int(page.get("index") or 10**9),
    )
    candidate_ids: list[str] = []
    labels: dict[str, str] = {}
    for page in ordered_pages:
        qid = str((page.get("pageprops") or {}).get("wikibase_item") or "").strip()
        title = str(page.get("title") or "").strip()
        if not re.fullmatch(r"Q\d+", qid):
            continue
        if qid not in candidate_ids:
            candidate_ids.append(qid)
        if title:
            labels[qid] = title
    return candidate_ids[:5], labels


def _cache_person_identity(cache_key: str, resolved: dict[str, str]) -> None:
    key = str(cache_key or "").strip().casefold()
    if not key or not resolved:
        return
    if key in _PERSON_IDENTITY_CACHE:
        _PERSON_IDENTITY_CACHE[key] = dict(resolved)
        return
    if len(_PERSON_IDENTITY_CACHE) >= _PERSON_IDENTITY_CACHE_MAX:
        oldest_key = next(iter(_PERSON_IDENTITY_CACHE), "")
        if oldest_key:
            _PERSON_IDENTITY_CACHE.pop(oldest_key, None)
    _PERSON_IDENTITY_CACHE[key] = dict(resolved)



def resolve_person_identity(entity: str) -> dict[str, str]:
    """Resolve a person name through Wikidata, with Wikipedia spelling/alias fallback."""
    normalized = _clean_query(entity)
    if not normalized:
        return {}
    cache_key = normalized.casefold()
    cached = _PERSON_IDENTITY_CACHE.get(cache_key)
    if cached:
        return dict(cached)

    payload = _api_json(
        "https://www.wikidata.org/w/api.php",
        params={
            "action": "wbsearchentities",
            "search": normalized,
            "language": "en",
            "uselang": "en",
            "type": "item",
            "limit": 5,
            "format": "json",
        },
    )
    results = payload.get("search", []) if payload else []
    if not isinstance(results, list):
        results = []

    candidate_ids: list[str] = []
    labels: dict[str, str] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("id") or "").strip()
        label = str(item.get("label") or "").strip()
        if not re.fullmatch(r"Q\d+", qid):
            continue
        candidate_ids.append(qid)
        if label:
            labels[qid] = label

    verified_qid, verified_label, detail_succeeded = _verify_wikidata_human(candidate_ids, labels)
    if verified_qid:
        resolved = {"qid": verified_qid, "label": verified_label}
    else:
        # Wikipedia search is deliberately a fallback rather than an acceptance
        # boundary: its job is to recover canonical spellings/aliases. The QID is
        # still type-checked through Wikidata when structured data is available.
        fallback_ids, fallback_labels = _wikipedia_identity_candidates(normalized)
        fallback_qid, fallback_label, fallback_detail_succeeded = _verify_wikidata_human(
            fallback_ids,
            fallback_labels,
        )
        if fallback_qid:
            resolved = {"qid": fallback_qid, "label": fallback_label}
        elif not fallback_detail_succeeded and fallback_ids:
            # Bounded compatibility fallback when the verification lookup is
            # unavailable; semantic visual QA remains mandatory downstream.
            fallback_qid = fallback_ids[0]
            resolved = {"qid": fallback_qid, "label": fallback_labels.get(fallback_qid, "")}
        elif not detail_succeeded and candidate_ids:
            # Preserve the previous bounded fallback when Wikidata itself is
            # reachable only through search results.
            qid = candidate_ids[0]
            resolved = {"qid": qid, "label": labels.get(qid, "")}
        else:
            return {}

    _cache_person_identity(cache_key, resolved)
    canonical_label = str(resolved.get("label") or "").strip()
    if canonical_label:
        _cache_person_identity(canonical_label, resolved)
    return dict(resolved)


def _normalize_identity_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _clean_query(value).casefold()).strip()


def resolve_wikidata_entity(entity: str) -> dict[str, str]:
    """Resolve a named Wikidata item for structured Commons discovery."""
    normalized = _clean_query(entity)
    if not normalized:
        return {}
    cache_key = normalized.casefold()
    cached = _WIKIDATA_ENTITY_CACHE.get(cache_key)
    if cached:
        return dict(cached)

    payload = _api_json(
        "https://www.wikidata.org/w/api.php",
        params={
            "action": "wbsearchentities",
            "search": normalized,
            "language": "en",
            "uselang": "en",
            "type": "item",
            "limit": 5,
            "format": "json",
        },
    )
    results = payload.get("search", []) if payload else []
    if not isinstance(results, list):
        return {}

    query_norm = _normalize_identity_text(normalized)
    query_tokens = set(query_norm.split())
    ranked: list[tuple[float, dict[str, str]]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("id") or "").strip()
        label = str(item.get("label") or "").strip()
        description = str(item.get("description") or "").strip()
        aliases = item.get("aliases") or []
        if not isinstance(aliases, list):
            aliases = []
        alias_values = [
            str(alias.get("value") or "").strip()
            for alias in aliases
            if isinstance(alias, dict) and str(alias.get("value") or "").strip()
        ]
        if not re.fullmatch(r"Q\d+", qid) or not label:
            continue
        label_norm = _normalize_identity_text(label)
        label_tokens = set(label_norm.split())
        score = 0.0
        if label_norm == query_norm:
            score += 100.0
        if any(_normalize_identity_text(alias) == query_norm for alias in alias_values):
            score += 95.0
        if query_norm and query_norm in label_norm:
            score += 35.0
        score += 20.0 * len(query_tokens & label_tokens) / max(1, len(query_tokens))
        ranked.append(
            (
                score,
                {
                    "qid": qid,
                    "label": label,
                    "description": description,
                },
            )
        )

    if not ranked:
        return {}
    ranked.sort(key=lambda item: (-item[0], item[1]["label"].casefold()))
    if ranked[0][0] < 20.0:
        return {}
    resolved = dict(ranked[0][1])
    if len(_WIKIDATA_ENTITY_CACHE) >= _WIKIDATA_ENTITY_CACHE_MAX:
        oldest_key = next(iter(_WIKIDATA_ENTITY_CACHE), "")
        if oldest_key:
            _WIKIDATA_ENTITY_CACHE.pop(oldest_key, None)
    _WIKIDATA_ENTITY_CACHE[cache_key] = resolved
    _WIKIDATA_ENTITY_CACHE[resolved["label"].casefold()] = resolved
    return dict(resolved)


def _bounded_downloads(
    urls: list[Any],
    used_urls: set[str] | None,
    limit: int = MAX_PROVIDER_CANDIDATES,
) -> list[dict[str, Any]]:
    """Download only a small reserved URL set concurrently so one provider stays within its deadline."""
    jobs: list[tuple[str, dict[str, Any]]] = []
    seen_urls: set[str] = set()
    for item in urls:
        metadata = {}
        if isinstance(item, (tuple, list)) and len(item) == 2:
            url, metadata = item[0], item[1] if isinstance(item[1], dict) else {}
        else:
            url = item
        url = str(url or "").strip()
        if not url or url in seen_urls:
            continue
        if used_urls is not None and url in used_urls:
            continue
        seen_urls.add(url)
        if used_urls is not None:
            used_urls.add(url)
        jobs.append((url, dict(metadata)))
        if len(jobs) >= max(1, int(limit)):
            break

    if not jobs:
        return []

    def _download(job: tuple[str, dict[str, Any]]):
        url, metadata = job
        return _download_image(url, None, metadata)

    with ThreadPoolExecutor(
        max_workers=min(4, len(jobs)),
        thread_name_prefix="visual-provider-download",
    ) as executor:
        results = list(executor.map(_download, jobs))

    return [item for item in results if item]


def fetch_wikipedia_person_candidates(query: str, used_urls: set[str] | None = None, *_args) -> list[dict[str, Any]]:
    """Resolve near-exact Wikipedia person pages with one search + one batched metadata call."""
    entity = _clean_query(query)
    provider_page = _provider_page(_args)
    manual_mode = bool(len(_args) > 4 and isinstance(_args[4], bool) and _args[4])
    if not entity:
        return []
    payload = _api_json(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "query", "generator": "search", "gsrsearch": entity,
            "redirects": 1, "gsrnamespace": 0, "gsrlimit": MAX_PROVIDER_CANDIDATES,
            "gsroffset": (provider_page - 1) * MAX_PROVIDER_CANDIDATES,
            "prop": "pageimages|pageprops", "piprop": "name|original|thumbnail",
            "ppprop": "wikibase_item",
            **({"pilicense": "free"} if not manual_mode else {}),
            "pithumbsize": 1600,
            "format": "json",
        },
    )
    pages = payload.get("query", {}).get("pages", {}) if payload else {}
    ordered = sorted(
        [p for p in (pages.values() if isinstance(pages, dict) else []) if isinstance(p, dict)],
        key=lambda p: int(p.get("index") or 10**9),
    )
    file_names = []
    for page in ordered:
        name = str(page.get("pageimage") or "").strip()
        if name and name not in file_names:
            file_names.append(name)
    if not file_names:
        return []

    info_payload = _api_json(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "query",
            "titles": "|".join("File:" + name for name in file_names),
            "prop": "imageinfo",
            "iiprop": "url|mime|extmetadata",
            "iiurlwidth": 1600,
            "iiextmetadatafilter": "LicenseShortName|Artist|LicenseUrl|ImageDescription",
            "format": "json",
        },
    )
    info_pages = info_payload.get("query", {}).get("pages", {}) if info_payload else {}
    info_by_title = {}
    for page in info_pages.values() if isinstance(info_pages, dict) else []:
        if not isinstance(page, dict):
            continue
        imageinfo = page.get("imageinfo") or []
        title = str(page.get("title") or "").strip()
        if title and imageinfo and isinstance(imageinfo[0], dict):
            info_by_title[title.casefold()] = imageinfo[0]

    urls = []
    for position, page in enumerate(ordered, 1):
        title = str(page.get("title") or "").strip()
        file_name = str(page.get("pageimage") or "").strip()
        info = info_by_title.get(("File:" + file_name).casefold())
        if not info:
            continue
        ext = info.get("extmetadata") or {}
        def _meta_value(name):
            value = ext.get(name)
            return value.get("value", "") if isinstance(value, dict) else str(value or "")
        license_code = normalize_license_code(_meta_value("LicenseShortName"))
        if not manual_mode and not is_allowed_license(license_code):
            continue
        source = info.get("thumburl") or info.get("url") or ((page.get("thumbnail") or {}).get("source"))
        if source:
            urls.append((
                str(source),
                {
                    "provider": "Wikipedia",
                    "url": str(info.get("descriptionurl") or ("https://en.wikipedia.org/wiki/File:" + file_name)),
                    "source_page_url": str(info.get("descriptionurl") or ("https://en.wikipedia.org/wiki/File:" + file_name)),
                    "author": _meta_value("Artist"),
                    "license": license_code,
                    "license_url": _meta_value("LicenseUrl") or LICENSE_URLS.get(license_code, ""),
                    "search_title": title,
                    "search_description": _meta_value("ImageDescription"),
                    "search_position": position,
                },
            ))
    return _bounded_downloads(urls, used_urls, limit=4 if manual_mode else MAX_PROVIDER_CANDIDATES)

def _commons_search_query(query: str) -> str:
    """Use the vocabulary Commons actually uses for match/event media."""
    q = _clean_query(query)
    q = re.sub(r"\bversus\b", "v", q, flags=re.IGNORECASE)
    q = re.sub(r"\bvs\.?\b", "v", q, flags=re.IGNORECASE)
    return q


def _commons_person_seed(query: str) -> str:
    """Extract a compact person-name seed for generic Commons fallback search."""
    tokens = re.findall(r"[A-Za-z][A-Za-z'’.-]*", _clean_query(query))
    if len(tokens) < 2:
        return ""
    return " ".join(tokens[:2]).strip()


def _commons_search_queries(
    query: str,
    visual_type: str = "",
    visual_genre: str = "",
) -> list[tuple[str, str, str]]:
    """Return a small, topic-aware Commons search ladder without weakening QC."""
    exact = _commons_search_query(query)
    if not exact:
        return []

    searches: list[tuple[str, str, str]] = []
    visual_l = str(visual_type or "").strip().upper()
    genre_l = str(visual_genre or "").strip().upper()

    team_variants = _commons_team_search_variants(exact, visual_l, genre_l)
    team_core = team_variants[-1] if team_variants else ""
    team_like = bool(team_variants)

    person_seed = _commons_person_seed(exact) if (
        (visual_l == "PERSON" and not team_like)
        or genre_l in {"PERSON_PORTRAIT", "PERSON_ACTION"}
    ) else ""

    person_qid = ""
    person_label = ""
    if person_seed and genre_l != "PERSON_ACTION":
        resolved_person = resolve_person_identity(person_seed)
        person_qid = str((resolved_person or {}).get("qid") or "").strip()
        person_label = str((resolved_person or {}).get("label") or "").strip()
        if re.fullmatch(r"Q\d+", person_qid):
            searches.append(
                (
                    f"haswbstatement:P180={person_qid}",
                    "structured-depicts-person",
                    person_label or person_seed,
                )
            )

    structured_types = {"PERSON", "ORGANIZATION", "LOCATION", "PRODUCT"}
    if visual_l in structured_types and not person_qid and genre_l != "PERSON_ACTION":
        structured_query = team_core or exact
        resolved_entity = resolve_wikidata_entity(structured_query)
        entity_qid = str((resolved_entity or {}).get("qid") or "").strip()
        entity_label = str((resolved_entity or {}).get("label") or "").strip()
        if re.fullmatch(r"Q\d+", entity_qid):
            searches.append(
                (
                    f"haswbstatement:P180={entity_qid}",
                    "structured-depicts-entity",
                    entity_label or structured_query,
                )
            )
        if team_like and entity_label:
            resolved_variants = []
            for variant in team_variants:
                scene_suffix = (
                    " celebration"
                    if re.search(
                        r"\b(?:celebrate|celebrates|celebrating|celebration)\b",
                        variant,
                        flags=re.IGNORECASE,
                    )
                    else ""
                )
                resolved_variants.append(f"{entity_label}{scene_suffix}".strip())
            team_variants = list(dict.fromkeys(resolved_variants + team_variants))[:2]

    for variant in team_variants:
        searches.append((variant, "normalized-team", team_core))

    # Manual queries are never silently replaced: the exact literal always stays in the ladder.
    searches.append((exact, "text", ""))

    deduped: list[tuple[str, str, str]] = []
    seen = set()
    for entry in searches:
        q = str(entry[0] or "").strip()
        key_q = q.casefold()
        if not q or key_q in seen:
            continue
        seen.add(key_q)
        deduped.append(entry)
    return deduped[:4]

def fetch_commons_candidates(query: str, used_urls: set[str] | None = None, *_args) -> list[dict[str, Any]]:
    """Search Commons with topic-aware structured/text discovery and open-license filtering."""
    visual_type = str(_args[2] if len(_args) > 2 else "").strip().upper()
    visual_genre = str(_args[3] if len(_args) > 3 else "").strip().upper()
    visual_l = visual_type
    genre_l = visual_genre
    manual_mode = bool(_args[4]) if len(_args) > 4 else False
    provider_page = _provider_page(_args)
    searches = _commons_search_queries(query, visual_type, visual_genre)
    if not searches:
        return []

    urls: list[Any] = []
    seen_file_urls: set[str] = set()
    for search_query, match_mode, matched_entity in searches:
        payload = _api_json(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query",
                "generator": "search",
                "gsrsearch": search_query,
                "gsrnamespace": 6,
                "gsrlimit": MAX_PROVIDER_CANDIDATES,
                "gsroffset": (provider_page - 1) * MAX_PROVIDER_CANDIDATES,
                "prop": "imageinfo|categories",
                "iiprop": "url|mime|extmetadata",
                "iiurlwidth": 1600,
                "iiextmetadatafilter": "LicenseShortName|Artist|LicenseUrl|ImageDescription",
                "cllimit": "max",
                "format": "json",
            },
        )
        pages = payload.get("query", {}).get("pages", {}) if payload else {}
        for page_position, page in enumerate(
            pages.values() if isinstance(pages, dict) else [],
            1,
        ):
            if not isinstance(page, dict):
                continue
            imageinfo = page.get("imageinfo") or []
            if not imageinfo or not isinstance(imageinfo[0], dict):
                continue

            info = imageinfo[0]
            ext = info.get("extmetadata") or {}

            def _meta_value(key):
                value = ext.get(key)
                return value.get("value", "") if isinstance(value, dict) else str(value or "")

            license_code = normalize_license_code(_meta_value("LicenseShortName"))
            if not manual_mode and not is_allowed_license(license_code):
                continue

            source = info.get("thumburl") or info.get("url")
            if not source:
                continue

            description = _meta_value("ImageDescription")
            categories = " ".join(
                str(item.get("title") or "").removeprefix("Category:").strip()
                for item in (page.get("categories") or [])
                if isinstance(item, dict)
            )
            page_title = str(page.get("title", "")).removeprefix("File:").strip()

            metadata = {
                "provider": "Commons",
                "url": str(
                    info.get("descriptionurl")
                    or ("https://commons.wikimedia.org/wiki/" + str(page.get("title", "")))
                ),
                "source_page_url": str(
                    info.get("descriptionurl")
                    or ("https://commons.wikimedia.org/wiki/" + str(page.get("title", "")))
                ),
                "author": _meta_value("Artist"),
                "license": license_code,
                "license_url": _meta_value("LicenseUrl") or LICENSE_URLS.get(license_code, ""),
                "search_title": page_title,
                "search_description": description,
                "search_tags": categories,
                "search_position": ((provider_page - 1) * MAX_PROVIDER_CANDIDATES) + page_position,
                "commons_match_mode": match_mode,
                "commons_matched_entity": matched_entity,
            }
            source_url = str(source)
            if source_url in seen_file_urls:
                continue
            seen_file_urls.add(source_url)
            urls.append((source_url, metadata))

            if len(urls) >= MAX_PROVIDER_CANDIDATES * 2:
                break
        if len(urls) >= MAX_PROVIDER_CANDIDATES * 2:
            break

    return _bounded_downloads(urls, used_urls, limit=MAX_PROVIDER_CANDIDATES)


def fetch_duckduckgo_candidates(
    query: str,
    used_urls: set[str] | None = None,
    *_args,
) -> list[dict[str, Any]]:
    """Search DDG images for the manual human-QC source pool."""
    manual_mode = bool(len(_args) > 4 and isinstance(_args[4], bool) and _args[4])
    if not (manual_mode or allow_unlicensed_visuals()):
        return []
    q = _clean_query(query)
    if not q:
        return []
    try:
        from ddgs import DDGS
    except Exception:
        return []
    try:
        results = DDGS().images(
            q,
            safesearch="moderate",
            max_results=max(8, MAX_PROVIDER_CANDIDATES * 2),
        )
        urls: list[Any] = []
        for position, result in enumerate(results or [], 1):
            if not isinstance(result, dict):
                continue
            image_url = result.get("image") or result.get("thumbnail") or result.get("url")
            if not image_url:
                continue
            urls.append((
                str(image_url),
                {
                    "provider": "DuckDuckGo",
                    "url": str(result.get("url") or image_url),
                    "source_page_url": str(result.get("url") or image_url),
                    "author": "",
                    "license": "",
                    "license_url": "",
                    "search_title": str(result.get("title") or ""),
                    "search_description": str(result.get("title") or ""),
                    "search_tags": str(result.get("source") or ""),
                    "search_position": position,
                },
            ))
        return _bounded_downloads(urls, used_urls)
    except Exception as exc:
        print(
            f"   [Visual Source] DDG raw fetch failed: {type(exc).__name__}: {exc} | query='{q}'",
            flush=True,
        )
        return []


def fetch_pexels_candidates(query: str, used_urls: set[str] | None = None, *_args) -> list[dict[str, Any]]:
    key = str(os.getenv("PEXELS_API_KEY", "")).strip()
    provider_page = _provider_page(_args)
    manual_mode = bool(len(_args) > 4 and isinstance(_args[4], bool) and _args[4])
    q = _clean_query(query)
    if not key or not q:
        return []
    payload = _api_json(
        "https://api.pexels.com/v1/search",
        params={
            "query": q,
            "page": provider_page,
            "per_page": max(8, MAX_PROVIDER_CANDIDATES * 2),
        },
        headers={"Authorization": key, "User-Agent": "ViralShortsFactory/1.0 (+visual-retrieval)"},
    )
    urls: list[str] = []
    for position, photo in enumerate(payload.get("photos", []) if payload else [], 1):
        if not isinstance(photo, dict):
            continue
        src = photo.get("src") or {}
        if isinstance(src, dict):
            author = str(photo.get("photographer") or "")
            url = src.get("large2x") or src.get("large") or src.get("original")
            if url:
                urls.append((str(url), {
                        "provider": "Pexels",
                        "url": str(photo.get("url") or url),
                        "source_page_url": str(photo.get("url") or ""),
                        "author": author,
                        "license": "Pexels License",
                        "license_url": "https://www.pexels.com/license/",
                        "search_title": str(photo.get("alt") or ""),
                        "search_description": str(photo.get("alt") or ""),
                        "search_position": position,
                    }))
    return _bounded_downloads(urls, used_urls, limit=4 if manual_mode else MAX_PROVIDER_CANDIDATES)


def fetch_unsplash_candidates(query: str, used_urls: set[str] | None = None, *_args) -> list[dict[str, Any]]:
    key = str(os.getenv("UNSPLASH_ACCESS_KEY", "")).strip()
    provider_page = _provider_page(_args)
    manual_mode = bool(len(_args) > 4 and isinstance(_args[4], bool) and _args[4])
    q = _clean_query(query)
    if not key or not q:
        return []
    payload = _api_json(
        "https://api.unsplash.com/search/photos",
        params={
            "query": q,
            "page": provider_page,
            "orientation": "portrait",
            "per_page": max(8, MAX_PROVIDER_CANDIDATES * 2),
            "client_id": key,
        },
        headers={"User-Agent": "ViralShortsFactory/1.0 (+visual-retrieval)"},
    )
    urls: list[str] = []
    for position, item in enumerate(payload.get("results", []) if payload else [], 1):
        if not isinstance(item, dict):
            continue
        urls_meta = item.get("urls") or {}
        if isinstance(urls_meta, dict):
            user = item.get("user") or {}
            author = str(user.get("name") or user.get("username") or "") if isinstance(user, dict) else ""
            url = urls_meta.get("regular") or urls_meta.get("full") or urls_meta.get("raw")
            if url:
                urls.append((str(url), {
                        "provider": "Unsplash",
                        "url": str((item.get("links") or {}).get("html") or url),
                        "source_page_url": str((item.get("links") or {}).get("html") or ""),
                        "author": author,
                        "license": "Unsplash License",
                        "license_url": "https://unsplash.com/license",
                        "search_title": str(item.get("alt_description") or ""),
                        "search_description": str(item.get("description") or item.get("alt_description") or ""),
                        "search_position": position,
                    }))
    return _bounded_downloads(urls, used_urls, limit=4 if manual_mode else MAX_PROVIDER_CANDIDATES)


def build_raw_source_plan(
    visual_type: str,
    visual_genre: str = "",
    allow_unlicensed: bool = False,
):
    """Return raw providers ordered for the visual genre.

    The provider layer never decides whether an image is correct. It only
    changes the order in which low-cost sources are searched so obvious
    category mismatches do not consume the verification budget first.
    """
    kind = str(visual_type or "GENERAL_CONTEXT").upper()
    genre = str(visual_genre or "").strip().upper()
    if not genre:
        genre = "PERSON_PORTRAIT" if kind == "PERSON" else "GENERAL_CONTEXT"

    plan = []
    if kind == "PERSON" and genre != "PERSON_ACTION":
        plan.append(("Wikipedia", fetch_wikipedia_person_candidates))

    commons_kinds = {
        "PERSON", "ORGANIZATION", "EVENT", "PRODUCT", "LOCATION", "DOCUMENT",
        "QUOTE", "PROCESS", "CONCEPT",
    }
    commons_genres = {
        "ORG_BRANDING", "ORG_HEADQUARTERS", "TEAM_BRANDING", "TEAM_ACTION",
        "PRODUCT_PHOTO", "PRODUCT_LAUNCH", "LANDMARK", "ARCHITECTURE",
        "PLACE_SCENE", "EVENT_SCENE", "SPORTS_ACTION", "SPORTS_MATCH",
        "TROPHY_AWARD", "DOCUMENT", "SCREENSHOT_UI", "CHART_GRAPH", "MAP",
        "DIAGRAM", "PROCESS", "SCIENCE_VISUAL", "SPACE_VISUAL",
        "HISTORICAL_ARTIFACT", "HISTORICAL_PHOTO", "MEDIA_ARTWORK",
        "MONEY_CURRENCY", "FLAG_SYMBOL",
    }
    if kind in commons_kinds or genre in commons_genres:
        plan.append(("Commons", fetch_commons_candidates))

    try:
        from image_sources_runtime import fetch_openverse_candidates, fetch_pixabay_candidates
    except Exception:
        fetch_openverse_candidates = fetch_pixabay_candidates = None

    plan.append(("Openverse", fetch_openverse_candidates))
    if str(os.getenv("PIXABAY_API_KEY", "")).strip():
            plan.append(("Pixabay", fetch_pixabay_candidates))
    if str(os.getenv("PEXELS_API_KEY", "")).strip():
            plan.append(("Pexels", fetch_pexels_candidates))
    if str(os.getenv("UNSPLASH_ACCESS_KEY", "")).strip():
            plan.append(("Unsplash", fetch_unsplash_candidates))

    if allow_unlicensed:
        plan.append(("DDG", fetch_duckduckgo_candidates))

    plan = [(name, fn) for name, fn in plan if callable(fn)]
    preferred = preferred_sources(genre)
    rank = {name.casefold(): index for index, name in enumerate(preferred)}
    plan.sort(key=lambda item: (rank.get(item[0].casefold(), 999), item[0]))

    deduped = []
    seen = set()
    for item in plan:
        key = item[0].casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


__all__ = [
    "MAX_PROVIDER_CANDIDATES",
    "build_raw_source_plan",
    "resolve_person_identity",
    "resolve_wikidata_entity",
    "fetch_commons_candidates",
    "fetch_duckduckgo_candidates",
    "fetch_pexels_candidates",
    "fetch_unsplash_candidates",
    "fetch_wikipedia_person_candidates",
]
