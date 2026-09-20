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
from typing import Any

import requests

from visual_taxonomy_runtime import preferred_sources
from visual_licensing_runtime import (
    LICENSE_URLS,
    is_allowed_license,
    licensed_candidate,
    normalize_license_code,
)

DEFAULT_TIMEOUT = max(3, int(os.getenv("VISUAL_PROVIDER_TIMEOUT_SECONDS", "8")))
MAX_PROVIDER_CANDIDATES = max(1, min(6, int(os.getenv("VISUAL_PROVIDER_CANDIDATES", "6"))))


def _clean_query(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:240]


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
    try:
        response = requests.get(
            url,
            timeout=DEFAULT_TIMEOUT,
            headers={"User-Agent": "ViralShortsFactory/1.0 (+visual-retrieval)"},
            allow_redirects=True,
        )
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
        return licensed_candidate(accepted, meta)
    except Exception:
        return None


def _api_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    try:
        response = requests.get(
            url,
            params=params or {},
            headers=headers or {"User-Agent": "ViralShortsFactory/1.0 (+visual-retrieval)"},
            timeout=DEFAULT_TIMEOUT,
        )
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


def _bounded_downloads(urls: list[Any], used_urls: set[str] | None, limit: int = MAX_PROVIDER_CANDIDATES) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
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
        seen_urls.add(url)
        data = _download_image(url, used_urls, metadata)
        if data:
            candidates.append(data)
            if len(candidates) >= limit:
                break
    return candidates


def fetch_wikipedia_person_candidates(query: str, used_urls: set[str] | None = None, *_args) -> list[dict[str, Any]]:
    """Resolve near-exact Wikipedia person pages in one API call."""
    entity = _clean_query(query)
    if not entity:
        return []
    payload = _api_json(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "query",
            "generator": "search",
            "gsrsearch": entity,
            "redirects": 1,
            "gsrnamespace": 0,
            "gsrlimit": MAX_PROVIDER_CANDIDATES,
            "prop": "pageimages|pageprops",
            "piprop": "name|original|thumbnail",
            "ppprop": "wikibase_item",
            "pilicense": "free",
            "pithumbsize": 1600,
            "format": "json",
        },
    )
    pages = payload.get("query", {}).get("pages", {}) if payload else {}
    urls: list[Any] = []
    for page_position, page in enumerate(pages.values() if isinstance(pages, dict) else [], 1):
        if not isinstance(page, dict):
            continue
        title = str(page.get("title", "")).strip()
        # Wikipedia's search engine is the relevance filter. Do not impose a
        # brittle token-level name match here: legitimate pages commonly use
        # compacted names, punctuation, initials, aliases, transliterations or
        # redirects. The authoritative identity decision happens later at the
        # semantic visual-QA boundary.
        file_name = str(page.get("pageimage") or "").strip()
        if not file_name:
            continue
        info_payload = _api_json(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "prop": "imageinfo",
                "titles": "File:" + file_name,
                "iiprop": "url|mime|extmetadata",
                "iiurlwidth": 1600,
                "iiextmetadatafilter": "LicenseShortName|Artist|LicenseUrl|ImageDescription",
                "format": "json",
            },
        )
        file_pages = info_payload.get("query", {}).get("pages", {}) if info_payload else {}
        info = None
        for file_page in file_pages.values() if isinstance(file_pages, dict) else []:
            imageinfo = file_page.get("imageinfo") or [] if isinstance(file_page, dict) else []
            if imageinfo and isinstance(imageinfo[0], dict):
                info = imageinfo[0]
                break
        if not info:
            continue
        ext = info.get("extmetadata") or {}
        def _meta_value(key):
            value = ext.get(key)
            return value.get("value", "") if isinstance(value, dict) else str(value or "")
        license_code = normalize_license_code(_meta_value("LicenseShortName"))
        if not is_allowed_license(license_code):
            continue
        source = info.get("thumburl") or info.get("url") or ((page.get("thumbnail") or {}).get("source"))
        if source:
            urls.append((
                str(source),
                {
                    "provider": "Wikipedia",
                    "url": str(info.get("descriptionurl") or ("https://en.wikipedia.org/wiki/File:" + file_name)),
                    "author": _meta_value("Artist"),
                    "license": license_code,
                    "license_url": _meta_value("LicenseUrl") or LICENSE_URLS.get(license_code, ""),
                    "search_title": title,
                    "search_description": _meta_value("ImageDescription"),
                    "search_position": page_position,
                },
            ))
    return _bounded_downloads(urls, used_urls)


def _commons_search_query(query: str) -> str:
    """Use the vocabulary Commons actually uses for match/event media."""
    q = _clean_query(query)
    q = re.sub(r"\bversus\b", "v", q, flags=re.IGNORECASE)
    q = re.sub(r"\bvs\.?\b", "v", q, flags=re.IGNORECASE)
    return q


_COMMONS_PERSON_NOISE = {
    "action",
    "actions",
    "bat",
    "batted",
    "batter",
    "batting",
    "bowler",
    "bowling",
    "celebrate",
    "celebrating",
    "celebration",
    "cricket",
    "game",
    "games",
    "inning",
    "innings",
    "match",
    "matches",
    "playing",
    "player",
    "players",
    "shot",
    "shots",
    "sports",
    "sport",
    "stadium",
    "team",
    "teams",
    "training",
    "women",
    "womens",
    "woman",
    "world",
    "cup",
}


def _commons_person_seed(query: str) -> str:
    """Extract a compact person-name candidate from a manual search phrase."""
    tokens = re.findall(r"[A-Za-z][A-Za-z'’.-]*", _clean_query(query))
    kept = [
        token
        for token in tokens
        if token.casefold() not in _COMMONS_PERSON_NOISE
        and len(token) > 1
    ]
    if len(kept) < 2:
        return ""
    return " ".join(kept[:4]).strip()


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
    person_seed = _commons_person_seed(exact)
    person_qid = ""
    person_label = ""
    if person_seed:
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
    if str(visual_type or "").strip().upper() in structured_types and not person_qid:
        resolved_entity = resolve_wikidata_entity(exact)
        entity_qid = str((resolved_entity or {}).get("qid") or "").strip()
        entity_label = str((resolved_entity or {}).get("label") or "").strip()
        if re.fullmatch(r"Q\d+", entity_qid):
            searches.append(
                (
                    f"haswbstatement:P180={entity_qid}",
                    "structured-depicts-entity",
                    entity_label or exact,
                )
            )

    # Always retain the literal query. Scene-specific refinements are already
    # generated upstream, so this provider remains bounded to two searches.
    searches.append((exact, "text", ""))
    return searches


def fetch_commons_candidates(query: str, used_urls: set[str] | None = None, *_args) -> list[dict[str, Any]]:
    """Search Commons with topic-aware structured/text discovery and open-license filtering."""
    visual_type = str(_args[2] if len(_args) > 2 else "").strip().upper()
    visual_genre = str(_args[3] if len(_args) > 3 else "").strip().upper()
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
            if not is_allowed_license(license_code):
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
                "author": _meta_value("Artist"),
                "license": license_code,
                "license_url": _meta_value("LicenseUrl") or LICENSE_URLS.get(license_code, ""),
                "search_title": page_title,
                "search_description": description,
                "search_tags": categories,
                "search_position": page_position,
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


def fetch_pexels_candidates(query: str, used_urls: set[str] | None = None, *_args) -> list[dict[str, Any]]:
    key = str(os.getenv("PEXELS_API_KEY", "")).strip()
    q = _clean_query(query)
    if not key or not q:
        return []
    payload = _api_json(
        "https://api.pexels.com/v1/search",
        params={"query": q, "per_page": max(8, MAX_PROVIDER_CANDIDATES * 2)},
        headers={"Authorization": key, "User-Agent": "ViralShortsFactory/1.0 (+visual-retrieval)"},
    )
    urls: list[str] = []
    for position, photo in enumerate(payload.get("photos", []) if payload else [], 1):
        if not isinstance(photo, dict):
            continue
        src = photo.get("src") or {}
        if isinstance(src, dict):
            author = str(photo.get("photographer") or "")
            for url in (src.get("large2x"), src.get("large"), src.get("original")):
                if url:
                    urls.append((str(url), {
                        "provider": "Pexels",
                        "url": str(photo.get("url") or url),
                        "author": author,
                        "license": "Pexels License",
                        "license_url": "https://www.pexels.com/license/",
                        "search_title": str(photo.get("alt") or ""),
                        "search_description": str(photo.get("alt") or ""),
                        "search_position": position,
                    }))
    return _bounded_downloads(urls, used_urls)


def fetch_unsplash_candidates(query: str, used_urls: set[str] | None = None, *_args) -> list[dict[str, Any]]:
    key = str(os.getenv("UNSPLASH_ACCESS_KEY", "")).strip()
    q = _clean_query(query)
    if not key or not q:
        return []
    payload = _api_json(
        "https://api.unsplash.com/search/photos",
        params={"query": q, "orientation": "portrait", "per_page": max(8, MAX_PROVIDER_CANDIDATES * 2), "client_id": key},
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
            for url in (urls_meta.get("regular"), urls_meta.get("full"), urls_meta.get("raw")):
                if url:
                    urls.append((str(url), {
                        "provider": "Unsplash",
                        "url": str((item.get("links") or {}).get("html") or url),
                        "author": author,
                        "license": "Unsplash License",
                        "license_url": "https://unsplash.com/license",
                        "search_title": str(item.get("alt_description") or ""),
                        "search_description": str(item.get("description") or item.get("alt_description") or ""),
                        "search_position": position,
                    }))
    return _bounded_downloads(urls, used_urls)


def build_raw_source_plan(visual_type: str, visual_genre: str = ""):
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
    if kind == "PERSON":
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
    "fetch_pexels_candidates",
    "fetch_unsplash_candidates",
    "fetch_wikipedia_person_candidates",
]
