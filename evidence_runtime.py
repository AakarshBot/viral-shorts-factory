"""Phase 2 local-first evidence engine.

Discovery providers find leads. This module fetches real pages, extracts article text,
normalises source metadata, extracts conservative claims, corroborates them across
independent domains, and records conflicts before the script writer sees the evidence.
"""
from __future__ import annotations

import html
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

import requests

DEFAULT_MAX_SOURCES = 5
MAX_HTML_BYTES = 3_000_000
MAX_TEXT_CHARS = 18_000
MAX_CLAIMS_PER_SOURCE = 14
MAX_CLAIMS_IN_PROMPT = 24

_PRIMARY_SUFFIXES = (".gov", ".gov.in", ".nic.in")
_PRIMARY_DOMAINS = {
    "nasa.gov", "esa.int", "who.int", "un.org", "fifa.com",
    "icc-cricket.com", "olympics.com", "formula1.com", "bcci.tv",
}
_DISCOVERY_ONLY = {
    "reddit.com", "x.com", "twitter.com", "facebook.com",
    "instagram.com", "tiktok.com",
}
_SCIENCE_TERMS = {
    "science", "scientist", "scientists", "research", "researcher",
    "researchers", "study", "studies", "paper", "journal", "experiment",
    "astronomy", "astrophysics", "physics", "chemistry", "biology",
    "genetics", "genome", "medicine", "clinical", "drug", "vaccine",
    "disease", "planet", "planetary", "space", "nasa", "esa", "telescope",
    "asteroid", "galaxy", "quantum", "particle", "climate", "fossil",
    "archaeology", "paleontology", "ecology", "neuroscience",
}
_CLAIM_STOPWORDS = {
    "about", "after", "again", "against", "among", "because", "before",
    "being", "between", "could", "from", "have", "having", "into", "more",
    "most", "other", "over", "said", "same", "some", "than", "that",
    "their", "there", "these", "they", "this", "those", "through", "under",
    "very", "were", "what", "when", "where", "which", "while", "with",
    "would", "your", "also", "just", "news", "latest", "report", "reports",
    "according", "official", "officials",
}
_FACTUAL_MARKERS = (
    "announced", "said", "reported", "confirmed", "revealed", "launched",
    "launch", "won", "lost", "signed", "approved", "rejected", "found",
    "finds", "study", "research", "data", "percent", "million", "billion",
    "will", "has", "have", "was", "were", "is", "are", "today", "yesterday",
)
_BOILERPLATE = {
    "advertisement", "advertisements", "read more", "sign up", "subscribe",
    "newsletter", "cookie policy", "privacy policy", "terms of use",
    "click here", "follow us", "share this article",
}
_CONFLICT_PAIRS = (
    ("won", "lost"), ("win", "lose"), ("wins", "loses"),
    ("approved", "rejected"), ("approve", "reject"),
    ("launched", "delayed"), ("launch", "delay"),
    ("increased", "decreased"), ("increase", "decrease"),
    ("rose", "fell"), ("rises", "falls"),
    ("confirmed", "denied"), ("alive", "dead"), ("open", "closed"),
)
_HTML_BLOCK = {
    "article", "aside", "blockquote", "br", "div", "figcaption", "footer",
    "h1", "h2", "h3", "h4", "h5", "h6", "header", "li", "main", "nav",
    "p", "pre", "section", "table", "tr", "td", "th",
}
_HTML_SKIP = {"script", "style", "noscript", "svg", "canvas", "form", "nav", "footer", "header", "aside"}


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def domain(url: Any) -> str:
    try:
        return urlparse(clean(url)).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def event_query(story: Dict[str, Any]) -> str:
    title = clean(story.get("title") or story.get("topic"))
    entities = story.get("event_entities") or []
    if isinstance(entities, str):
        entities = [entities]
    extra = " ".join(clean(x) for x in entities[:5] if clean(x))
    return re.sub(r"\s+", " ", " ".join(x for x in (title, extra) if x)).strip()[:220]


def science_story(story: Dict[str, Any]) -> bool:
    blob = clean(" ".join(str(story.get(k) or "") for k in
                           ("title", "description", "summary", "text", "genre", "category"))).lower()
    tokens = set(re.findall(r"[a-z][a-z-]{2,}", blob))
    return len(tokens & _SCIENCE_TERMS) >= 2


def source_publisher(source: Dict[str, Any]) -> str:
    for key in ("publisher", "source", "source_name"):
        value = clean(source.get(key))
        if value:
            return value
    return domain(source.get("url")) or "Unknown source"


def source_tier(source: Dict[str, Any]) -> str:
    explicit = clean(source.get("tier")).upper()
    if explicit in {"A", "B", "C"}:
        return explicit
    kind = clean(source.get("source_kind")).lower()
    host = domain(source.get("url"))
    if kind == "primary_research" or clean(source.get("collection_source")).lower() == "official":
        return "A"
    if host.endswith(_PRIMARY_SUFFIXES) or host in _PRIMARY_DOMAINS:
        return "A"
    if host in _DISCOVERY_ONLY or kind == "discovery_only":
        return "C"
    return "B"


def make_source(raw: Dict[str, Any], source_kind: Optional[str] = None) -> Dict[str, Any]:
    item = dict(raw)
    item["url"] = clean(item.get("url") or item.get("link"))
    item["title"] = clean(item.get("title"))
    item["publisher"] = source_publisher(item)
    item["discovery_snippet"] = clean(item.get("snippet") or item.get("body") or item.get("description"))
    item["discovery_provider"] = clean(item.get("discovery_provider") or item.get("collection_source"))
    item["collection_source"] = clean(item.get("collection_source"))
    item["source_kind"] = clean(source_kind or item.get("source_kind"))
    item["discovery_published_at"] = clean(
        item.get("published_at") or item.get("publishedAt") or item.get("published")
    )
    item["preextracted_text"] = clean(item.get("preextracted_text"))
    item["domain"] = domain(item.get("url"))
    item["tier"] = source_tier(item)
    return item


def _distinct_sources(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    priority = {"A": 0, "B": 1, "C": 2}
    ordered = sorted(
        list(items),
        key=lambda item: (
            priority.get(item.get("tier"), 2),
            0 if item.get("discovery_provider") == "event" else 1,
            item.get("domain") or "",
        ),
    )
    chosen, seen_urls, seen_domains = [], set(), set()
    for item in ordered:
        url = clean(item.get("url")).lower()
        host = clean(item.get("domain")).lower()
        if not url and not item.get("preextracted_text"):
            continue
        if url and url in seen_urls:
            continue
        if host and host in seen_domains:
            continue
        chosen.append(item)
        if url:
            seen_urls.add(url)
        if host:
            seen_domains.add(host)
    return chosen


def _ddg_sources(query: str) -> List[Dict[str, Any]]:
    if not query:
        return []
    try:
        from ddgs import DDGS
    except ImportError:
        return []
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=8))
    except Exception as exc:
        print(f"   [Research] DDG source discovery unavailable: {type(exc).__name__}", flush=True)
        return []
    return [
        make_source({
            "url": item.get("href") or item.get("url"),
            "title": item.get("title"),
            "publisher": item.get("domain") or item.get("source"),
            "snippet": item.get("body") or item.get("description"),
            "source_kind": "discovery_search",
            "discovery_provider": "ddg",
        })
        for item in results
        if clean(item.get("href") or item.get("url"))
    ]


def _abstract_from_inverted_index(index: Any) -> str:
    if not isinstance(index, dict):
        return ""
    pairs = []
    for word, positions in index.items():
        if isinstance(positions, list):
            for position in positions:
                try:
                    pairs.append((int(position), str(word)))
                except (TypeError, ValueError):
                    pass
    return " ".join(word for _, word in sorted(pairs))


def _openalex_sources(story: Dict[str, Any]) -> List[Dict[str, Any]]:
    if os.getenv("OPENALEX_ENABLED", "1").strip().lower() in {"0", "false", "no"}:
        return []
    if not science_story(story):
        return []
    query = event_query(story)
    if not query:
        return []
    params = {
        "search": query,
        "per-page": "5",
        "select": "id,display_name,publication_date,doi,type,primary_location,best_oa_location,abstract_inverted_index",
    }
    key = clean(os.getenv("OPENALEX_API_KEY"))
    if key:
        params["api_key"] = key
    try:
        response = requests.get(
            "https://api.openalex.org/works",
            params=params,
            headers={"User-Agent": "ViralShortsFactory/2.0"},
            timeout=10,
        )
        if response.status_code != 200:
            print(f"   [Research] OpenAlex skipped: HTTP {response.status_code}.", flush=True)
            return []
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"   [Research] OpenAlex unavailable: {type(exc).__name__}.", flush=True)
        return []
    output = []
    for item in (payload.get("results") or [])[:5]:
        if not isinstance(item, dict):
            continue
        primary_location = item.get("primary_location") or {}
        primary_source = primary_location.get("source") or {}
        oa_location = item.get("best_oa_location") or {}
        landing = (
            clean(oa_location.get("landing_page_url"))
            or clean(primary_location.get("landing_page_url"))
            or clean(item.get("doi"))
        )
        title = clean(item.get("display_name"))
        if not title:
            continue
        output.append(make_source({
            "url": landing,
            "title": title,
            "publisher": clean(primary_source.get("display_name")) or "Scholarly work",
            "source_kind": "primary_research",
            "discovery_provider": "openalex",
            "collection_source": "openalex",
            "published_at": item.get("publication_date"),
            "preextracted_text": _abstract_from_inverted_index(item.get("abstract_inverted_index")),
            "openalex_id": item.get("id"),
            "doi": item.get("doi"),
        }))
    return output


def discover_sources(story: Dict[str, Any], max_sources: int = DEFAULT_MAX_SOURCES) -> List[Dict[str, Any]]:
    """Find real source URLs while avoiding redundant discovery when event evidence already fills the source budget."""
    try:
        limit = max(1, int(max_sources or DEFAULT_MAX_SOURCES))
    except (TypeError, ValueError):
        limit = DEFAULT_MAX_SOURCES

    candidates = []
    for item in story.get("event_evidence") or []:
        if isinstance(item, dict):
            copy = dict(item)
            copy.setdefault("discovery_provider", "event")
            candidates.append(make_source(copy, "event_source"))

    original = clean(story.get("url") or story.get("link"))
    if original:
        candidates.append(make_source({
            "url": original,
            "title": story.get("title"),
            "publisher": story.get("source") or story.get("publisher"),
            "description": story.get("description") or story.get("summary") or story.get("text"),
            "discovery_provider": "event",
            "published_at": story.get("publishedAt") or story.get("published_at"),
        }, "event_source"))

    base = _distinct_sources(candidates)
    if len(base) >= limit:
        return base[:limit]

    query = event_query(story)
    if not query:
        return base[:limit]

    if science_story(story):
        has_primary = any(item.get("tier") == "A" for item in base)
        first_provider = _ddg_sources if has_primary else _openalex_sources
        second_provider = _openalex_sources if has_primary else _ddg_sources
        # Once the selected-story evidence is insufficient, both independent
        # science discovery backends are useful. Run them together so the second
        # source adds recall without adding serial wall-clock time.
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="evidence-discovery") as pool:
            futures = [
                pool.submit(first_provider, story if first_provider is _openalex_sources else query),
                pool.submit(second_provider, story if second_provider is _openalex_sources else query),
            ]
            for future in as_completed(futures):
                try:
                    candidates.extend(future.result())
                except Exception as exc:
                    print(f"   [Research] Independent discovery backend failed: {type(exc).__name__}", flush=True)
    else:
        candidates.extend(_ddg_sources(query))

    return _distinct_sources(candidates)[:limit]


class _ReadableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in _HTML_SKIP:
            self.skip_depth += 1
        elif self.skip_depth == 0 and tag in _HTML_BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _HTML_SKIP:
            self.skip_depth = max(0, self.skip_depth - 1)
        elif self.skip_depth == 0 and tag in _HTML_BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth == 0:
            value = clean(data)
            if value:
                self.parts.append(value)


def _clean_article_text(text: Any) -> str:
    raw = html.unescape(str(text or "")).replace("\u200b", " ")
    lines, seen = [], set()
    for line in re.split(r"\n+", raw):
        value = re.sub(r"\s+", " ", line).strip()
        if not value:
            continue
        folded = value.casefold()
        if folded in _BOILERPLATE:
            continue
        if len(value) < 20 and not re.search(r"[.!?]", value):
            continue
        if folded in seen:
            continue
        seen.add(folded)
        lines.append(value)
    return "\n".join(lines)[:MAX_TEXT_CHARS]


def _fallback_html_text(raw_html: str) -> str:
    parser = _ReadableHTMLParser()
    parser.feed(raw_html)
    parser.close()
    return _clean_article_text("\n".join(parser.parts))


def _decode_response(response: requests.Response) -> str:
    data = response.content[:MAX_HTML_BYTES]
    encoding = response.encoding or response.apparent_encoding or "utf-8"
    return data.decode(encoding, errors="replace")


def _iso_datetime(value: Any) -> str:
    if isinstance(value, datetime):
        dt = value
    else:
        raw = clean(value)
        if not raw:
            return ""
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return raw
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def extract_article_source(source: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch one real page and extract article text and source metadata."""
    record = dict(source)
    url = clean(record.get("url"))
    record.update({
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "final_url": url,
        "extraction_status": "failed",
        "extraction_method": "",
        "clean_text": "",
        "word_count": 0,
        "authors": [],
        "language": "",
        "published_at": _iso_datetime(record.get("discovery_published_at")),
    })

    preextracted = clean(record.get("preextracted_text"))
    if preextracted:
        text = _clean_article_text(preextracted)
        if text:
            record["clean_text"] = text
            record["word_count"] = len(text.split())
            record["extraction_status"] = "ok"
            record["extraction_method"] = "openalex_abstract"
            return record

    if not url:
        record["error"] = "No source URL available."
        return record

    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 Chrome/151 Safari/537.36 ViralShortsFactory/2.0",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.8",
            },
            timeout=12,
            allow_redirects=True,
        )
        record["status_code"] = response.status_code
        if response.status_code >= 400:
            record["error"] = f"HTTP {response.status_code}"
            return record

        record["final_url"] = clean(response.url) or url
        record["domain"] = domain(record["final_url"]) or record.get("domain", "")
        raw_html = _decode_response(response)
        extracted, method = "", ""

        try:
            from newspaper import Article
            article = Article(record["final_url"], fetch_images=False, memoize_articles=False)
            article.download(input_html=raw_html, ignore_read_more=True)
            try:
                from newspaper.article import ArticleDownloadState
                article.download_state = ArticleDownloadState.SUCCESS
            except ImportError:
                pass
            article.parse()
            extracted = _clean_article_text(article.text)
            method = "newspaper4k"
            record["title"] = clean(article.title) or record.get("title") or ""
            record["authors"] = [clean(a) for a in article.authors if clean(a)]
            record["language"] = clean(article.meta_lang)
            record["publisher"] = clean(article.meta_site_name) or record.get("publisher") or record["domain"]
            record["published_at"] = _iso_datetime(article.publish_date) or record.get("published_at") or ""
        except Exception as exc:
            record["extractor_warning"] = f"newspaper4k: {type(exc).__name__}"
            extracted = _fallback_html_text(raw_html)
            method = "html_fallback"

        if len(extracted.split()) < 40:
            fallback = _fallback_html_text(raw_html)
            if len(fallback.split()) > len(extracted.split()):
                extracted, method = fallback, "html_fallback"

        record["clean_text"] = extracted[:MAX_TEXT_CHARS]
        record["word_count"] = len(extracted.split())
        record["extraction_method"] = method
        record["extraction_status"] = "ok" if record["word_count"] >= 40 else "thin"
        record["title"] = clean(record.get("title")) or clean(source.get("title"))
        record["publisher"] = clean(record.get("publisher")) or clean(source.get("publisher")) or record["domain"]
        if record["extraction_status"] == "thin":
            record["error"] = "Downloaded page contained too little article text."
    except requests.RequestException as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"

    record["tier"] = source_tier(record)
    return record


def _sentence_chunks(text: str) -> List[str]:
    normalized = re.sub(r"\s+", " ", text or "").strip()
    if not normalized:
        return []
    return [
        re.sub(r"\s+", " ", part).strip()
        for part in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])|\n+", normalized)
        if part.strip()
    ]


def _looks_like_claim(sentence: str) -> bool:
    words = sentence.split()
    if len(words) < 8 or len(words) > 90:
        return False
    folded = sentence.casefold()
    if any(item in folded for item in ("cookie", "newsletter", "subscribe", "advertisement")):
        return False
    marker = any(item in folded for item in _FACTUAL_MARKERS)
    has_number = bool(re.search(r"\b\d+(?:[.,]\d+)?%?\b", sentence))
    has_name = bool(re.search(r"\b[A-Z][a-z]{2,}\b", sentence))
    return marker or has_number or has_name


def _claim_tokens(text: str, replace_numbers: bool = True) -> set:
    value = str(text or "").casefold()
    if replace_numbers:
        value = re.sub(
            r"\b(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?:\s?(?:%|percent|million|billion|thousand))?\b",
            " NUM ",
            value,
        )
    tokens = re.findall(r"[a-z][a-z0-9'-]{2,}", value)
    return {token for token in tokens if token not in _CLAIM_STOPWORDS}


def claim_similarity(left: str, right: str) -> float:
    a, b = _claim_tokens(left), _claim_tokens(right)
    if not a or not b:
        return 0.0
    jaccard = len(a & b) / max(1, len(a | b))
    sequence = SequenceMatcher(None, " ".join(sorted(a)), " ".join(sorted(b))).ratio()
    return max(jaccard, sequence * 0.85)


def _number_tokens(text: str) -> List[str]:
    return re.findall(
        r"\b(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?:\s?(?:%|percent|million|billion|thousand))?\b",
        str(text or "").casefold(),
    )


def _opposite_hit(left: str, right: str) -> bool:
    a, b = _claim_tokens(left, False), _claim_tokens(right, False)
    for first, second in _CONFLICT_PAIRS:
        if (first in a and second in b) or (second in a and first in b):
            return True
    return bool(re.search(r"\b(?:not|no|never|without)\b", left.casefold())) != bool(
        re.search(r"\b(?:not|no|never|without)\b", right.casefold())
    )


def claims_conflict(left: str, right: str) -> bool:
    similarity = claim_similarity(left, right)
    if similarity < 0.55:
        return False
    left_numbers, right_numbers = _number_tokens(left), _number_tokens(right)
    if left_numbers and right_numbers and left_numbers != right_numbers:
        return True
    left_years = set(re.findall(r"\b20\d{2}\b", left))
    right_years = set(re.findall(r"\b20\d{2}\b", right))
    if left_years and right_years and left_years != right_years and similarity >= 0.62:
        return True
    return _opposite_hit(left, right)


def extract_claims(source: Dict[str, Any]) -> List[Dict[str, Any]]:
    claims = []
    for sentence in _sentence_chunks(clean(source.get("clean_text"))):
        if not _looks_like_claim(sentence):
            continue
        claims.append({
            "claim_id": f"{clean(source.get('source_id'))}-{len(claims) + 1}",
            "text": sentence,
            "source_id": clean(source.get("source_id")),
            "publisher": clean(source.get("publisher")),
            "domain": clean(source.get("domain")),
            "tier": source_tier(source),
        })
        if len(claims) >= MAX_CLAIMS_PER_SOURCE:
            break
    return claims


def _source_id(source: Dict[str, Any], index: int) -> str:
    raw = clean(source.get("url")) or clean(source.get("openalex_id")) or str(index)
    slug = re.sub(r"[^a-z0-9]+", "-", raw.casefold()).strip("-")
    return f"src-{index + 1}-{slug[:48] or 'source'}"


def _merge_claims(raw_claims: List[Dict[str, Any]]) -> tuple:
    groups = []
    for claim in raw_claims:
        best, best_score = None, 0.0
        for group in groups:
            score = max(claim_similarity(claim["text"], item["text"]) for item in group)
            if score > best_score:
                best, best_score = group, score
        if best is not None and best_score >= 0.60:
            best.append(claim)
        else:
            groups.append([claim])

    tier_rank = {"A": 0, "B": 1, "C": 2}
    merged, conflicts = [], []
    for index, group in enumerate(groups, 1):
        domains = {item.get("domain") for item in group if item.get("domain")}
        publishers = {item.get("publisher") for item in group if item.get("publisher")}
        representative = sorted(
            group,
            key=lambda item: (tier_rank.get(item.get("tier"), 2), -len(item.get("text", ""))),
        )[0]
        conflict_details = []
        for left_index, left in enumerate(group):
            for right in group[left_index + 1:]:
                if left.get("domain") and left.get("domain") == right.get("domain"):
                    continue
                if claims_conflict(left["text"], right["text"]):
                    conflict_details.append({
                        "left": left["text"],
                        "right": right["text"],
                        "left_source": left.get("publisher") or left.get("domain"),
                        "right_source": right.get("publisher") or right.get("domain"),
                    })

        if conflict_details:
            status = "conflicted"
        elif len(domains) >= 2:
            status = "corroborated"
        elif any(item.get("tier") == "A" for item in group):
            status = "primary_only"
        else:
            status = "single_source"

        merged_item = {
            "claim_id": f"claim-{index}",
            "text": representative["text"],
            "status": status,
            "best_tier": sorted(
                (item.get("tier", "C") for item in group),
                key=lambda value: tier_rank.get(value, 2),
            )[0],
            "support_count": len(group),
            "independent_source_count": len(domains),
            "independent_publishers": sorted(publishers),
            "source_ids": [item.get("source_id") for item in group],
            "variants": [item["text"] for item in group if item["text"] != representative["text"]],
        }
        merged.append(merged_item)
        if conflict_details:
            conflicts.append({"claim_id": merged_item["claim_id"], "details": conflict_details})

    status_order = {"corroborated": 0, "primary_only": 1, "single_source": 2, "conflicted": 3}
    merged.sort(
        key=lambda item: (
            status_order.get(item.get("status"), 4),
            -int(item.get("independent_source_count") or 0),
            -int(item.get("support_count") or 0),
        )
    )
    return merged[:MAX_CLAIMS_IN_PROMPT], conflicts


def build_evidence_pack(story: Dict[str, Any], sources: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    candidates = list(sources if sources is not None else discover_sources(story))
    enriched = [None] * len(candidates)
    if candidates:
        # Article extraction is network-bound. Running the bounded source set
        # concurrently removes the old N×12s worst-case serial bottleneck while
        # retaining deterministic source ordering for IDs and audit records.
        worker_count = min(5, len(candidates))
        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="evidence-extraction",
        ) as pool:
            future_map = {}
            for index, source in enumerate(candidates):
                record = dict(source)
                record["source_id"] = _source_id(record, index)
                future_map[
                    pool.submit(extract_article_source, record)
                ] = index

            for future in as_completed(future_map):
                index = future_map[future]
                try:
                    enriched[index] = future.result()
                except Exception as exc:
                    # extract_article_source is already defensive, but keep the
                    # evidence pack total and auditable if a worker unexpectedly raises.
                    record = dict(candidates[index])
                    record["source_id"] = _source_id(record, index)
                    record["extraction_status"] = "failed"
                    record["extraction_method"] = ""
                    record["clean_text"] = ""
                    record["word_count"] = 0
                    record["error"] = f"{type(exc).__name__}: {exc}"
                    enriched[index] = record

        enriched = [item for item in enriched if isinstance(item, dict)]

    usable = [
        item for item in enriched
        if item.get("extraction_status") == "ok" and item.get("tier") in {"A", "B"}
    ]
    raw_claims = []
    for source in usable:
        raw_claims.extend(extract_claims(source))
    claims, conflicts = _merge_claims(raw_claims)

    source_summaries = []
    for item in enriched:
        source_summaries.append({
            "source_id": item.get("source_id"),
            "url": item.get("final_url") or item.get("url"),
            "publisher": item.get("publisher"),
            "domain": item.get("domain"),
            "title": item.get("title"),
            "authors": item.get("authors") or [],
            "published_at": item.get("published_at"),
            "fetched_at": item.get("fetched_at"),
            "language": item.get("language"),
            "tier": item.get("tier"),
            "collection_source": item.get("collection_source"),
            "discovery_provider": item.get("discovery_provider"),
            "source_kind": item.get("source_kind"),
            "extraction_status": item.get("extraction_status"),
            "extraction_method": item.get("extraction_method"),
            "word_count": item.get("word_count"),
            "status_code": item.get("status_code"),
            "error": item.get("error"),
            "extractor_warning": item.get("extractor_warning"),
            "clean_text_preview": clean(item.get("clean_text"))[:700],
        })

    counts = {
        "discovered_sources": len(candidates),
        "downloaded_pages": sum(1 for item in enriched if item.get("extraction_status") in {"ok", "thin"}),
        "usable_sources": len(usable),
        "primary_sources": sum(1 for item in usable if item.get("tier") == "A"),
        "independent_publishers": len({item.get("publisher") for item in usable if item.get("publisher")}),
        "independent_domains": len({item.get("domain") for item in usable if item.get("domain")}),
        "claims": len(claims),
        "corroborated_claims": sum(1 for item in claims if item.get("status") == "corroborated"),
        "primary_only_claims": sum(1 for item in claims if item.get("status") == "primary_only"),
        "single_source_claims": sum(1 for item in claims if item.get("status") == "single_source"),
        "conflicted_claims": sum(1 for item in claims if item.get("status") == "conflicted"),
    }

    if not usable or not claims:
        status = "insufficient_evidence"
    elif counts["conflicted_claims"]:
        status = "ready_with_conflicts"
    elif counts["independent_domains"] >= 2 and counts["corroborated_claims"]:
        status = "ready"
    else:
        status = "degraded_single_source"

    return {
        "version": "phase-2-v1",
        "status": status,
        "event": {
            "event_id": clean(story.get("event_id")),
            "title": clean(story.get("title")),
            "story_url": clean(story.get("url") or story.get("link")),
            "article_count": story.get("event_article_count"),
            "publisher_count": story.get("event_source_count"),
        },
        "counts": counts,
        "sources": source_summaries,
        "claims": claims,
        "conflicts": conflicts,
    }


def format_evidence_pack_for_script(pack: Dict[str, Any]) -> str:
    lines = [
        "PHASE 2 EVIDENCE PACK",
        f"STATUS: {pack.get('status', 'unknown')}",
        "",
        "SOURCE HIERARCHY:",
        "A = primary authority or primary research",
        "B = reputable independent reporting",
        "C = discovery-only/social material; NEVER treat C as established fact",
        "",
        "VERIFIED SOURCE METADATA:",
    ]
    for source in pack.get("sources", []):
        if source.get("extraction_status") not in {"ok", "thin"}:
            continue
        tier = source.get("tier") or "C"
        publisher = source.get("publisher") or source.get("domain") or "Unknown source"
        published = source.get("published_at") or "date not extracted"
        title = source.get("title") or "Untitled source"
        url = source.get("url") or "no URL"
        method = source.get("extraction_method") or "unknown"
        lines.append(f"[{tier}] {publisher} | {published} | {method}")
        lines.append(f"    {title}")
        lines.append(f"    {url}")

    lines += [
        "",
        "CLAIMS:",
        "Use CORROBORATED claims first. PRIMARY_ONLY claims may be used cautiously. "
        "SINGLE_SOURCE claims require cautious wording. CONFLICTED claims must not be presented as settled fact.",
    ]
    for claim in pack.get("claims", []):
        status = str(claim.get("status") or "single_source").upper()
        count = int(claim.get("independent_source_count") or 0)
        publishers = ", ".join(claim.get("independent_publishers") or [])
        lines.append(
            f"[{status}] {clean(claim.get('text'))} "
            f"(independent sources: {count}; publishers: {publishers or 'unknown'})"
        )
        for variant in (claim.get("variants") or [])[:2]:
            lines.append(f"    variant: {clean(variant)}")

    if pack.get("conflicts"):
        lines += ["", "CONFLICTS — DO NOT SETTLE THESE WITHOUT NEW EVIDENCE:"]
        for conflict in pack["conflicts"][:8]:
            for detail in conflict.get("details") or []:
                lines.append(
                    f"- {detail.get('left_source')}: {detail.get('left')} "
                    f"vs {detail.get('right_source')}: {detail.get('right')}"
                )

    discovery_sources = [source for source in pack.get("sources", []) if source.get("tier") == "C"]
    if discovery_sources:
        lines += ["", "DISCOVERY-ONLY SOURCES:"]
        for source in discovery_sources[:6]:
            lines.append(
                f"- {source.get('publisher') or source.get('domain')}: "
                f"{source.get('title') or 'discovery lead'} — never use as standalone factual proof."
            )
    return "\n".join(lines)[:20_000]
