"""Fresh dashboard topic discovery with broad recall and soft editorial ranking.

This module is intentionally separate from production story selection. It is the
dashboard's news desk: collect many current candidates, remove only obvious
garbage, score the survivors, and return a diverse portfolio.
"""
from __future__ import annotations

import copy
import os
import threading
import time

from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone
from urllib.parse import urlparse

from event_discovery_runtime import cluster_news_events, fetch_gdelt_articles
import story_ranker as sr


DASHBOARD_DISCOVERY_VERSION = "dashboard-discovery-v2-2026-09"

# This is intentionally a very short anti-duplication cache, not a news freshness
# cache. Streamlit reruns can otherwise launch the entire multi-provider discovery
# desk again before the user has even changed their selection.
DASHBOARD_DISCOVERY_CACHE_TTL_SECONDS = 20.0
_DASHBOARD_DISCOVERY_CACHE: dict[tuple, tuple[float, list[dict]]] = {}
_DASHBOARD_DISCOVERY_CACHE_LOCK = threading.Lock()

GOOGLE_QUERY_LIMIT = 10
GDELT_QUERY_LIMIT = 2
DISCOVERY_TIMEOUT_SECONDS = 8.0
CORE_REQUEST_TIMEOUT_SECONDS = 6.0
SPARSE_CORE_ARTICLE_THRESHOLD = 45
MAX_RAW_ARTICLES = 500
MAX_TREND_ARTICLES = 30
MAX_EVENTS_FOR_RANKING = 180
DASHBOARD_MAX_AGE_HOURS = 72

CRICKET_MARQUEE_QUERY = (
    "Virat Kohli OR Rohit Sharma OR Jasprit Bumrah OR Shubman Gill OR "
    "Rishabh Pant OR Hardik Pandya OR Ravindra Jadeja OR Suryakumar Yadav OR "
    "Yashasvi Jaiswal OR KL Rahul OR Sanju Samson OR Mohammed Siraj OR "
    "Rashid Khan OR Babar Azam OR Pat Cummins OR Travis Head"
)

CRICKET_INDIA_QUERIES = (
    '(India OR Indian OR BCCI OR Pakistan OR Sri Lanka OR Bangladesh) cricket',
    'India cricket (said OR says OR calls OR called OR criticised OR criticized OR praised OR controversy OR row OR debate OR feud)',
    f'India cricket ({CRICKET_MARQUEE_QUERY})',
    'India cricket (record OR first OR fastest OR highest OR historic OR upset OR comeback OR thriller OR scare OR shock)',
    'India cricket (selection OR selected OR dropped OR recalled OR injury OR injured OR captain OR coach OR retirement OR retired OR banned OR suspended)',
    'India women cricket (record OR controversy OR selection OR debut OR upset OR comeback OR milestone)',
    '(Ranji OR Duleep OR "India A" OR U19 OR U23 OR domestic cricket OR state league) India (record OR debut OR selection OR controversy OR upset)',
    'Asia cricket (Japan OR Afghanistan OR Nepal OR UAE OR Hong Kong OR Sri Lanka OR Bangladesh) (record OR upset OR controversy OR debut OR milestone)',
    'India cricket (umpire OR law OR ruling OR bizarre OR unusual OR bizarre call OR controversy OR investigation)',
    'cricket (uncapped OR emerging OR grassroots OR club OR academy) India (debut OR record OR milestone OR controversy OR upset)',
)

CRICKET_GLOBAL_QUERIES = (
    '(cricket OR ICC) (Australia OR England OR South Africa OR New Zealand OR West Indies)',
    'international cricket (said OR says OR calls OR called OR criticised OR criticized OR praised OR controversy OR row OR debate OR feud)',
    f'cricket ({CRICKET_MARQUEE_QUERY})',
    'cricket (record OR first OR fastest OR highest OR historic OR upset OR comeback OR thriller OR scare OR shock)',
    'cricket (selection OR selected OR dropped OR recalled OR injury OR injured OR captain OR coach OR retirement OR retired OR banned OR suspended)',
    'women cricket (record OR controversy OR selection OR debut OR upset OR comeback)',
    'Test cricket (record OR controversy OR upset OR comeback OR milestone)',
    'T20 cricket (record OR controversy OR upset OR comeback OR milestone)',
    'cricket India Pakistan rivalry',
    'cricket major tournament final record controversy',
)

CRICKET_ALL_QUERIES = (
    '(cricket OR ICC OR BCCI OR IPL OR WPL OR PSL) (news OR latest OR record OR controversy OR selection OR injury OR result)',
    'cricket (said OR says OR calls OR called OR praised OR warned OR criticised OR criticized OR controversy OR row OR debate)',
    f'cricket ({CRICKET_MARQUEE_QUERY})',
    'cricket (record OR first OR fastest OR historic OR upset OR comeback OR thriller OR scare OR shock)',
    'cricket (selection OR selected OR dropped OR recalled OR injury OR injured OR captain OR coach OR retirement OR retired)',
    'women cricket (record OR controversy OR selection OR debut OR upset OR comeback)',
    '(Ranji OR Duleep OR "India A" OR U19 OR U23 OR domestic cricket) India',
    'India Pakistan cricket',
    'cricket major tournament final record controversy',
    'cricket emerging player breakthrough milestone',
)

DEFAULT_NON_CRICKET_LANES = {
    "sports": (
        '(India OR Indian OR Asia) sports latest when:3d',
        'India football soccer ISL I-League national team transfer coach controversy when:3d',
        'India tennis badminton athletics table tennis squash latest record tournament when:3d',
        'India hockey kabaddi boxing wrestling martial arts latest record tournament when:3d',
        'India basketball volleyball golf motorsport Formula 1 MotoGP latest when:3d',
        'world international sports latest tournament final record championship when:3d',
        'women sports India world latest record tournament controversy when:3d',
        'Olympics Asian Games Commonwealth Games sports India latest when:7d',
        'sports emerging athlete breakout upset comeback controversy viral reaction when:3d',
        '(FIFA ATP WTA BWF FIH IOC) latest sports news India when:3d',
    ),
    "technology": (
        '(India OR global) (AI OR technology OR chip OR smartphone OR software OR startup) (launch OR breakthrough OR deal OR controversy)',
        '(AI OR technology) (record OR breakthrough OR research OR release OR security)',
    ),
    "business_finance": (
        '(India OR global) (business OR economy OR markets OR startup OR company) (deal OR earnings OR investment OR crisis OR record)',
        '(business OR markets OR economy) (controversy OR surprise OR breakthrough OR major deal)',
    ),
    "entertainment": (
        '(India OR Indian OR Bollywood OR Tollywood) (film OR movie OR actor OR actress OR trailer OR release OR box office OR controversy)',
        '(Hollywood OR global cinema OR music) (release OR controversy OR record OR surprise OR award)',
    ),
    "national_global_affairs": (
        '(India OR global) (government OR policy OR court OR diplomacy OR conflict OR economy) (decision OR crisis OR breakthrough OR major)',
        '(world OR international) (decision OR crisis OR summit OR conflict OR breakthrough)',
    ),
    "health_lifestyle": (
        '(India OR global) (health OR medicine OR healthcare OR nutrition OR wellness) (study OR approval OR breakthrough OR outbreak)',
        '(health OR medicine OR science) (record OR breakthrough OR surprising OR new study)',
    ),
    "regional_state_news": (
        '(Telangana OR Hyderabad OR Andhra Pradesh OR Amaravati) (major OR development OR government OR business OR infrastructure OR technology)',
        '(Hyderabad OR Telangana OR Andhra Pradesh) (controversy OR breakthrough OR record OR major decision)',
    ),
}


def _source_host(item: dict) -> str:
    raw = str(item.get("url") or item.get("link") or "").strip()
    if not raw:
        return ""
    try:
        return urlparse(raw).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def _dedupe_articles(items: list[dict], limit: int = MAX_RAW_ARTICLES) -> list[dict]:
    seen: set[str] = set()
    output: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = sr._canonical_url(item.get("url") or item.get("link"))
        title_key = " ".join(sorted(sr._tokens(item.get("title", ""))))
        key = url or ("title:" + title_key)
        if not key or key in seen:
            continue
        seen.add(key)
        copied = dict(item)
        if not str(copied.get("source") or copied.get("publisher") or "").strip():
            copied["source"] = _source_host(copied)
        output.append(copied)
        if len(output) >= limit:
            break
    return output


def _query_lanes(
    genre_key: str,
    genre_cfg: dict,
    *,
    requested_topic: str = "",
    cricket_scope: str = "",
) -> list[str]:
    queries: list[str] = []
    if requested_topic:
        queries.append(requested_topic)

    is_cricket = genre_key == "sports_stories_of_day"
    if is_cricket:
        scope = str(cricket_scope or "").strip().casefold()
        if scope == "india / asia":
            lanes = CRICKET_INDIA_QUERIES
        elif scope == "global":
            lanes = CRICKET_GLOBAL_QUERIES
        else:
            lanes = CRICKET_ALL_QUERIES
        queries.extend(lanes)
    else:
        # Sports has a deliberately partitioned ten-lane desk. Do not prepend
        # the legacy single-bucket config queries because they would consume the
        # query budget and hide several specialist sports lenses.
        if genre_key == "sports":
            queries.extend(DEFAULT_NON_CRICKET_LANES["sports"])
        else:
            for key in ("india_gnews_q", "global_gnews_q", "gnews_q"):
                value = str(genre_cfg.get(key) or "").strip()
                if value:
                    queries.append(value)
            queries.extend(DEFAULT_NON_CRICKET_LANES.get(genre_key, ()))

            niche = str(
                sr.NICHE_DISCOVERY_QUERIES.get(genre_key, "")
            ).strip()
            if niche:
                queries.append(niche)

    seen: set[str] = set()
    result: list[str] = []
    for query in queries:
        cleaned = " ".join(str(query or "").split()).strip()
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
        if len(result) >= GOOGLE_QUERY_LIMIT:
            break
    return result


def _direct_feed_url(custom_rss_url: str) -> str:
    value = str(custom_rss_url or "").strip()
    if not value:
        return ""
    if sr._google_news_query_from_url(value):
        return ""
    if sr._is_reddit_json_url(value):
        return ""
    return value


def _trend_matches(story: dict, trend_rows: list[dict]) -> float:
    title_tokens = sr._tokens(story.get("title", ""))
    if not title_tokens:
        return 0.0
    best = 0.0
    for trend in trend_rows:
        if not isinstance(trend, dict):
            continue
        trend_tokens = sr._tokens(
            f"{trend.get('trend_query', '')} {trend.get('title', '')}"
        )
        if not trend_tokens:
            continue
        overlap = len(title_tokens & trend_tokens) / max(1, len(title_tokens | trend_tokens))
        if overlap >= 0.30:
            best = max(best, sr._safe_float(trend.get("trend_bonus")) or 0.0)
    return min(4.0, best)


def _collect_articles(
    bot,
    genre_key: str,
    genre_cfg: dict,
    *,
    requested_topic: str = "",
    custom_rss_url: str = "",
    cricket_scope: str = "",
) -> tuple[list[dict], list[str]]:
    """Collect current factual coverage first, then pay for fallback/signals only when useful."""
    queries = _query_lanes(
        genre_key,
        genre_cfg,
        requested_topic=requested_topic,
        cricket_scope=cricket_scope,
    )

    direct_feed = _direct_feed_url(custom_rss_url or genre_cfg.get("rss_url"))
    official_urls = sr._official_feed_urls(genre_key, genre_cfg)

    if genre_key == "sports":
        gdelt_query = (
            "India sports football tennis badminton hockey athletics basketball kabaddi boxing "
            "wrestling motorsport women records transfer controversy latest"
        )
    elif genre_key == "sports_stories_of_day":
        scope = str(cricket_scope or "").strip().casefold()
        gdelt_query = (
            "India cricket Pakistan BCCI women domestic selection injury record controversy"
            if scope == "india / asia"
            else "international cricket ICC Australia England South Africa selection injury record controversy"
        )
    elif queries:
        gdelt_query = queries[0]
    else:
        gdelt_query = ""

    core_jobs: list[tuple[str, object]] = []
    for index, query in enumerate(queries, 1):
        core_jobs.append((
            f"Google News lane {index}",
            sr._google_news_search_items,
            (query, genre_key, 50),
            {"timeout": CORE_REQUEST_TIMEOUT_SECONDS},
        ))
    if direct_feed:
        core_jobs.append((
            "Configured RSS",
            sr._rss_items,
            (direct_feed, genre_key, "rss", 80),
            {"timeout": CORE_REQUEST_TIMEOUT_SECONDS},
        ))
    if official_urls:
        core_jobs.append(("Official feeds", sr._official_feed_items, (genre_key, genre_cfg), {}))

    trend_geos = ("IN", "US") if genre_key == "sports" else ("IN",)
    signal_jobs: list[tuple[str, object, tuple, dict]] = [
        (f"Google Trends {geo}", sr._google_trends_items, (geo, 10), {"timeout": 4.0})
        for geo in trend_geos
    ]
    if os.getenv("REDDIT_DISCOVERY_ENABLED", "0").strip().lower() in {"1", "true", "yes"}:
        signal_jobs.append(("Reddit", sr._reddit_items, (genre_key, "", 40), {}))

    jobs = core_jobs + signal_jobs
    pool = ThreadPoolExecutor(
        max_workers=min(16, max(1, len(jobs))),
        thread_name_prefix="dashboard-discovery-v2",
    )
    started = {}
    futures = {}
    try:
        for label, fn, args, kwargs in jobs:
            future = pool.submit(fn, *args, **kwargs)
            futures[future] = label
            started[future] = time.monotonic()

        done, pending = wait(tuple(futures), timeout=DISCOVERY_TIMEOUT_SECONDS)

        raw: list[dict] = []
        social_rows: list[dict] = []
        trend_rows: list[dict] = []

        for future in done:
            label = futures[future]
            elapsed = time.monotonic() - started.get(future, time.monotonic())
            try:
                result = future.result() or []
            except Exception as exc:
                print(
                    f"   [Dashboard Discovery v2] {label} failed ({type(exc).__name__}) after {elapsed:.2f}s; continuing.",
                    flush=True,
                )
                continue
            if label.startswith("Reddit"):
                social_rows.extend(result)
            elif label.startswith("Google Trends"):
                trend_rows.extend(result[:MAX_TREND_ARTICLES])
            else:
                raw.extend(result)
            print(
                f"   [Dashboard Discovery v2] {label}: {len(result)} row(s) in {elapsed:.2f}s.",
                flush=True,
            )

        for future in pending:
            label = futures[future]
            elapsed = time.monotonic() - started.get(future, time.monotonic())
            future.cancel()
            print(
                f"   [Dashboard Discovery v2] {label}: timeout after {elapsed:.2f}s.",
                flush=True,
            )

        raw = _dedupe_articles(raw)
        print(
            f"   [Dashboard Discovery v2] collected {len(raw)} unique factual articles from "
            f"{len(queries)} Google lanes + feeds; secondary signals={len(social_rows) + len(trend_rows)}.",
            flush=True,
        )
    finally:
        # Provider HTTP calls have shorter timeouts than the collector boundary.
        # The shutdown remains non-blocking only as a final safety net.
        pool.shutdown(wait=False, cancel_futures=True)

    # GDELT is a conditional factual backstop, never a parallel dependency. This
    # prevents its latency/failure from delaying healthy Google/direct coverage.
    if gdelt_query and len(raw) < SPARSE_CORE_ARTICLE_THRESHOLD:
        try:
            fallback = fetch_gdelt_articles(
                gdelt_query,
                timespan="72h",
                max_records=75,
                timeout=4.0,
            )
            raw = _dedupe_articles([*raw, *fallback])
            print(
                f"   [Dashboard Discovery v2] conditional GDELT fallback: +{len(fallback)} raw row(s); "
                f"factual pool now {len(raw)}.",
                flush=True,
            )
        except Exception as exc:
            print(
                f"   [Dashboard Discovery v2] conditional GDELT failed ({type(exc).__name__}); continuing.",
                flush=True,
            )

    if trend_rows:
        for row in trend_rows:
            if not sr._discovery_category_allowed(genre_key, row):
                continue
            if row.get("url"):
                # Trend-linked articles are enrichment signals, not an independent
                # source family. Event clustering will dedupe them with the factual
                # article already found through another lane.
                raw.append(row)
        raw = _dedupe_articles(raw)

    social_titles = [
        str(row.get("title") or "").strip()
        for row in social_rows
        if isinstance(row, dict) and str(row.get("title") or "").strip()
    ]

    for story in raw:
        story["social_signal_raw"] = sr._social_signal(story.get("title", ""), social_titles)
        story["trend_bonus"] = _trend_matches(story, trend_rows)

    return raw, social_titles


def _hard_dashboard_pass(story: dict, genre_key: str, requested_topic: str) -> bool:
    title = str(story.get("title") or "").strip()
    if len(title) < 12:
        return False

    safe, hits = sr._safety_gate(story)
    if not safe:
        story["discovery_rejection"] = "Safety/content policy gate"
        story["safety_hits"] = hits
        return False

    if not sr._source_page_pass(story):
        return False
    if genre_key == "sports_stories_of_day":
        # Cricket discovery is intentionally permissive because the dashboard has
        # human QC. Keep only safety, provenance, article-page and utility-page
        # protections; do not hide unusual or weakly packaged cricket leads.
        if not sr._cricket_service_title_pass(story):
            story["discovery_rejection"] = "Low-value cricket service article"
            return False
        if not sr._discovery_source_pass(story):
            return False
    else:
        if not sr._headline_noise_pass(story):
            return False
        if not sr._discovery_source_pass(story):
            return False

    age = sr._age_hours(story)
    if age == 9999.0 or age > DASHBOARD_MAX_AGE_HOURS:
        story["discovery_rejection"] = "Missing or stale publication date"
        return False
    story["age_hours"] = round(age, 2)

    if genre_key == "sports_stories_of_day":
        # Factual cricket lanes are already query-scoped to cricket. Only
        # cross-category trend items need an explicit cricket relevance check.
        collection_source = str(story.get("collection_source") or "").strip().casefold()
        if collection_source == "google_trends" and not sr._cricket_relevance_pass(story, genre_key):
            return False
    if requested_topic and not sr._requested_topic_pass(story, requested_topic):
        return False

    return True


def _rank_dashboard_events(
    events: list[dict],
    *,
    conn,
    target_category: str,
    target_format: str,
    target_language: str,
    social_titles: list[str],
    ai_cricket: bool,
    max_candidates: int,
) -> list[dict]:
    rows = sr._load_history(conn)
    ranked: list[dict] = []

    for event in events:
        if not _hard_dashboard_pass(
            event,
            target_category,
            "",
        ):
            continue

        scored = sr._editorial_score(
            event,
            rows,
            target_category,
            target_format,
            target_language,
            social_titles,
            ai_cricket,
        )

        scored["dashboard_discovery_version"] = DASHBOARD_DISCOVERY_VERSION
        scored["dashboard_soft_eligible"] = True

        # Dashboard eligibility is intentionally softer than production
        # selection. Strong hooks, source support and Shorts fit affect order;
        # they do not silently delete otherwise legitimate current events.
        sources = max(
            sr._safe_float(scored.get("event_source_count")) or 0.0,
            sr._safe_float(scored.get("event_total_publisher_count")) or 0.0,
        )
        if sources <= 1:
            scored["dashboard_quality_note"] = "single-source"
        elif sources >= 3:
            scored["dashboard_quality_note"] = "well-corroborated"
        else:
            scored["dashboard_quality_note"] = "multi-source"

        ranked.append(scored)

    ranked.sort(
        key=lambda item: (
            sr._safe_float(item.get("candidate_score")) or -9999.0,
            sr._safe_float(item.get("freshness_score")) or 0.0,
            sr._safe_float(item.get("event_source_count")) or 0.0,
        ),
        reverse=True,
    )

    # Keep a larger scoring pool than the final dashboard. Diversity is applied
    # after scoring, not before, so a dominant news cycle cannot starve other
    # subjects during the early funnel.
    ranked = ranked[:MAX_EVENTS_FOR_RANKING]
    selected = sr.diversity_rerank(ranked, max_items=max_candidates)

    for rank, story in enumerate(selected, 1):
        story["discovery_rank"] = rank
        story["story_url"] = sr._story_url(story)
        story["source_label"] = sr._source_label(story)
        story["story_key"] = sr._story_key(story)
        story["discovery_reason"] = sr._candidate_reason(story)

    return selected


def _dashboard_discovery_cache_key(
    genre_key: str,
    requested_topic: str,
    custom_rss_url: str,
    cricket_scope: str,
    target_category: str,
    target_format: str,
    target_language: str,
    ai_cricket: bool,
    max_candidates: int,
) -> tuple:
    """Build a stable key for the short-lived rerun de-duplication cache."""
    return (
        str(genre_key or "").strip().casefold(),
        str(requested_topic or "").strip().casefold(),
        str(custom_rss_url or "").strip(),
        str(cricket_scope or "").strip().casefold(),
        str(target_category or "").strip().casefold(),
        str(target_format or "").strip().casefold(),
        str(target_language or "").strip().casefold(),
        bool(ai_cricket),
        int(max_candidates),
    )


def clear_dashboard_discovery_cache() -> None:
    """Force the next explicit dashboard discovery click to perform a fresh provider sweep."""
    with _DASHBOARD_DISCOVERY_CACHE_LOCK:
        _DASHBOARD_DISCOVERY_CACHE.clear()


def discover_dashboard_topics(
    bot,
    genre_key: str,
    genre_cfg: dict,
    *,
    conn=None,
    requested_topic: str = "",
    custom_rss_url: str = "",
    cricket_scope: str = "",
    target_category: str = "",
    target_format: str = "regular",
    target_language: str = "english",
    ai_cricket: bool = False,
    max_candidates: int = 60,
) -> list[dict]:
    """Collect and rank the dashboard portfolio without production gates."""
    max_candidates = max(1, min(60, int(max_candidates or 60)))
    cache_key = _dashboard_discovery_cache_key(
        genre_key,
        requested_topic,
        custom_rss_url,
        cricket_scope,
        target_category,
        target_format,
        target_language,
        ai_cricket,
        max_candidates,
    )
    now = time.monotonic()

    with _DASHBOARD_DISCOVERY_CACHE_LOCK:
        cached = _DASHBOARD_DISCOVERY_CACHE.get(cache_key)
        if cached is not None:
            cached_at, cached_topics = cached
            if now - cached_at <= DASHBOARD_DISCOVERY_CACHE_TTL_SECONDS:
                print(
                    "   [Dashboard Discovery v2] cache hit; reusing the "
                    "just-built topic portfolio for this Streamlit rerun.",
                    flush=True,
                )
                return copy.deepcopy(cached_topics)
            _DASHBOARD_DISCOVERY_CACHE.pop(cache_key, None)

    raw, social_titles = _collect_articles(
        bot,
        genre_key,
        genre_cfg,
        requested_topic=requested_topic,
        custom_rss_url=custom_rss_url,
        cricket_scope=cricket_scope,
    )

    # Cluster first. The event layer collapses repeated publisher coverage while
    # preserving source evidence on the representative event.
    events = cluster_news_events(raw)
    for event in events:
        event["recommended_category"] = sr._infer_discovery_category(event)

    uploaded = sr._load_uploaded_story_identities(conn)
    filtered = [
        event
        for event in events
        if _hard_dashboard_pass(event, genre_key, requested_topic)
        and not sr._uploaded_story_match(event, uploaded)
    ]

    print(
        f"   [Dashboard Discovery v2] factual={len(raw)} -> events={len(events)} -> "
        f"hard-eligible={len(filtered)} -> upload-suppressed={len(events) - len(filtered)}",
        flush=True,
    )

    selected = _rank_dashboard_events(
        filtered,
        conn=conn,
        target_category=target_category or genre_key,
        target_format=target_format,
        target_language=target_language,
        social_titles=social_titles,
        ai_cricket=ai_cricket,
        max_candidates=max_candidates,
    )
    print(
        f"   [Dashboard Discovery v2] final dashboard topics={len(selected)}",
        flush=True,
    )

    cached_selected = copy.deepcopy(selected)
    with _DASHBOARD_DISCOVERY_CACHE_LOCK:
        now = time.monotonic()
        # Keep this process-local cache tiny and disposable.
        expired = [
            key
            for key, (cached_at, _topics) in _DASHBOARD_DISCOVERY_CACHE.items()
            if now - cached_at > DASHBOARD_DISCOVERY_CACHE_TTL_SECONDS
        ]
        for key in expired:
            _DASHBOARD_DISCOVERY_CACHE.pop(key, None)
        _DASHBOARD_DISCOVERY_CACHE[cache_key] = (now, cached_selected)

    return copy.deepcopy(cached_selected)


__all__ = ["DASHBOARD_DISCOVERY_VERSION", "discover_dashboard_topics", "clear_dashboard_discovery_cache"]
