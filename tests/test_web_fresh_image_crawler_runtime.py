import io
from datetime import datetime, timedelta, timezone

from PIL import Image

import web_fresh_image_crawler_runtime as crawler


def _jpeg_bytes(size=(1200, 1600), color=(80, 90, 100)):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def _asset(index, status="crawler-high-confidence", kind="news-article"):
    data = _jpeg_bytes(color=(50 + index, 90, 130))
    return {
        "bytes": data,
        "hash": f"hash-{index}",
        "source": "web_crawler",
        "source_type": "web_crawler",
        "source_name": "Example Sports",
        "credit": "Source: Example Sports",
        "query": "Virat Kohli latest statement",
        "source_page_url": f"https://example.com/story/{index}",
        "source_image_url": f"https://cdn.example.com/{index}.jpg",
        "publisher": "Example Sports",
        "article_title": "Virat Kohli latest statement",
        "published_at": datetime.now(timezone.utc).isoformat(),
        "crawler_age_hours": 4.0 if kind == "news-article" else None,
        "crawler_freshness_basis": "publication-date" if kind == "news-article" else "profile-page",
        "crawler_image_method": "og:image",
        "crawler_confidence": "high" if status == "crawler-high-confidence" else "ai-verified",
        "crawler_relevance": 0.9,
        "crawler_action_score": 2,
        "visual_type": "PERSON",
        "visual_genre": "PERSON_ACTION",
        "provenance": {
            "provider": "Example Sports",
            "url": f"https://example.com/story/{index}",
            "license": "Unverified web source",
            "license_url": f"https://example.com/story/{index}",
        },
        "provenance_status": "provenance-review",
        "priority": 100 - index,
        "search_text": "Virat Kohli latest statement batting match",
        "status": status,
        "used": False,
        "crawler_page_kind": kind,
    }


def test_title_match_rejects_headline_only_false_positive():
    assert crawler._title_match(
        "Virat Kohli reacts to retirement rumours",
        "Virat Kohli reacts to retirement rumours after match",
        "Virat Kohli",
    ) > 0
    assert crawler._title_match(
        "Virat Kohli reacts to retirement rumours",
        "Virat Kohli returns for training",
        "Virat Kohli",
    ) == 0


def test_google_news_rss_article_redirect_is_usable_but_publisher_rss_is_not():
    google_news_url = "https://news.google.com/rss/articles/CBMiExample?ceid=IN:en"
    publisher_rss_url = "https://example.com/rss/articles/story"

    assert crawler._article_url_is_usable(google_news_url)
    assert not crawler._article_url_is_usable(publisher_rss_url)


def test_recent_articles_accept_google_news_rss_fallback(monkeypatch):
    now = datetime.now(timezone.utc)
    fresh = (now - timedelta(hours=2)).isoformat()
    google_news_url = "https://news.google.com/rss/articles/CBMiExample?ceid=IN:en"

    monkeypatch.setattr(crawler, "_ddgs_news", lambda *_args: [])
    monkeypatch.setattr(
        crawler,
        "_google_news_rss",
        lambda *_args: [{
            "title": "Virat Kohli reacts to retirement rumours",
            "url": google_news_url,
            "date": fresh,
            "body": "",
            "source": "Example Sports",
        }],
    )

    articles = crawler._collect_recent_articles(
        ["Virat Kohli reacts to retirement rumours"],
        "Virat Kohli reacts to retirement rumours",
        "Virat Kohli",
        now,
    )

    assert [item["url"] for item in articles] == [google_news_url]


def test_news_search_combines_ddgs_and_google_news(monkeypatch):
    calls = []

    def fake_ddgs(query, timelimit):
        calls.append((query, timelimit))
        if timelimit == "d":
            return [
                {"title": "Fresh story one", "url": "https://one.example/story"},
                {"title": "Fresh story two", "url": "https://two.example/story"},
            ]
        return [
            {"title": "Wider story", "url": "https://three.example/story"},
        ]

    monkeypatch.setattr(crawler, "_ddgs_news", fake_ddgs)
    monkeypatch.setattr(
        crawler,
        "_google_news_rss",
        lambda _query: [
            {"title": "RSS story one", "url": "https://four.example/story"},
            {"title": "Fresh story one", "url": "https://one.example/story"},
            {"title": "RSS story two", "url": "https://five.example/story"},
        ],
    )

    results = crawler._news_search("Virat Kohli latest")
    urls = [item["url"] for item in results]
    assert urls[:5] == [
        "https://one.example/story",
        "https://two.example/story",
        "https://four.example/story",
        "https://five.example/story",
        "https://three.example/story",
    ]
    assert ("Virat Kohli latest", "d") in calls
    assert ("Virat Kohli latest", "w") in calls


def test_static_source_fallback_becomes_candidates_when_playwright_is_missing(monkeypatch):
    from news_source_image_runtime import extract_news_source_images
    from web_browser_image_runtime import scrape_web_pages

    now = datetime.now(timezone.utc)
    page = {
        "title": "Virat Kohli reacts to retirement rumours",
        "url": "https://example.com/story",
        "date": (now - timedelta(hours=2)).isoformat(),
        "_crawler_query": "Virat Kohli reacts to retirement rumours",
        "source": "Example Sports",
    }
    static_asset = {
        "bytes": _jpeg_bytes(),
        "hash": "static-hash",
        "image_url": "https://cdn.example.com/fallback.jpg",
        "page_url": "https://example.com/story",
        "publisher": "Example Sports",
        "credit": "Source: Example Sports",
        "method": "og:image",
        "source": "news_source",
        "source_type": "news_source",
        "status": "article-source",
        "provenance": {},
    }

    monkeypatch.setattr(
        "web_browser_image_runtime.scrape_web_pages",
        lambda _requests: [{
            "assets": [],
            "error": "ModuleNotFoundError: No module named 'playwright'",
        }],
    )
    monkeypatch.setattr(
        "news_source_image_runtime.extract_news_source_images",
        lambda *_args, **_kwargs: [static_asset],
    )

    (
        candidates,
        image_count,
        title_mismatch,
        diagnostics,
    ) = crawler._scrape_browser_pages(
        [page],
        page["title"],
        "Virat Kohli",
        now,
        profile_page=False,
        max_images_per_page=6,
    )

    assert image_count == 1
    assert title_mismatch == 0
    assert diagnostics["browser_failures"] == 1
    assert diagnostics["static_fallback_images"] == 1
    assert len(candidates) == 1
    assert candidates[0]["source_page_url"] == page["url"]
    assert candidates[0]["source_image_url"] == static_asset["image_url"]



def test_manual_query_uses_same_crawler_without_requiring_original_headline(monkeypatch):
    now = datetime.now(timezone.utc)
    calls = []

    articles = [{
        "title": "Kohli joins batting session before weekend match",
        "url": "https://one.example/story",
        "date": (now - timedelta(hours=3)).isoformat(),
        "_crawler_query": "Virat Kohli batting",
        "source": "Example Sports",
    }]

    def fake_collect(queries, story_title, entity, current_time):
        calls.append((queries, story_title, entity))
        return articles

    monkeypatch.setattr(crawler, "_collect_recent_articles", fake_collect)
    monkeypatch.setattr(
        crawler,
        "_scrape_browser_pages",
        lambda *_args, **_kwargs: ([_asset(1)], 1, 0),
    )
    monkeypatch.setattr(crawler, "_collect_profile_pages", lambda *_args: [])

    result = crawler.crawl_fresh_web_images(
        {"title": "Completely different selected headline"},
        [{"primary_entity": "Virat Kohli"}],
        manual_query="Virat Kohli batting",
    )

    assert calls == [(["Virat Kohli batting"], "Virat Kohli batting", "Virat Kohli")]
    assert len(result["assets"]) == 1
    assert result["manual_query"] == "Virat Kohli batting"
    assert result["queries"] == ["Virat Kohli batting"]


def test_crawler_distinguishes_no_articles_browser_failure_and_rejected_images(monkeypatch):
    empty = crawler.crawl_fresh_web_images(
        {"title": "No coverage story"},
        [],
    )
    assert empty["failure_state"] == "no_articles_found"

    now = datetime.now(timezone.utc)
    page = {
        "title": "Virat Kohli story",
        "url": "https://example.com/story",
        "date": (now - timedelta(hours=2)).isoformat(),
        "_crawler_query": "Virat Kohli story",
        "source": "Example Sports",
    }
    monkeypatch.setattr(crawler, "_collect_recent_articles", lambda *_args: [page])
    monkeypatch.setattr(crawler, "_collect_profile_pages", lambda *_args: [])
    monkeypatch.setattr(
        crawler,
        "_scrape_browser_pages",
        lambda *_args, **_kwargs: (
            [],
            0,
            0,
            {"browser_failures": 1, "static_fallback_images": 0},
        ),
    )
    browser_failed = crawler.crawl_fresh_web_images(
        {"title": page["title"]},
        [{"primary_entity": "Virat Kohli"}],
    )
    assert browser_failed["failure_state"] == "browser_unavailable"

    rejected_asset = _asset(7)
    monkeypatch.setattr(
        crawler,
        "_scrape_browser_pages",
        lambda *_args, **_kwargs: ([rejected_asset], 1, 0),
    )
    monkeypatch.setattr(
        crawler,
        "_verify_ambiguous",
        lambda candidates, entity: ([], 1, len(candidates)),
    )
    rejected = crawler.crawl_fresh_web_images(
        {"title": page["title"]},
        [{"primary_entity": "Virat Kohli"}],
    )
    assert rejected["failure_state"] == "images_found_but_rejected"

def test_related_article_score_accepts_paraphrased_coverage():
    assert crawler._related_article_score(
        "Virat Kohli reacts retirement rumours",
        "Kohli opens up on his future after fresh retirement speculation",
        "Virat Kohli reacts to retirement rumours",
        "Virat Kohli",
    ) > 0


def test_related_article_score_rejects_unrelated_figure_only_story():
    assert crawler._related_article_score(
        "Virat Kohli reacts retirement rumours",
        "Virat Kohli attends a family wedding in Mumbai",
        "Virat Kohli reacts to retirement rumours",
        "Virat Kohli",
    ) == 0


def test_candidate_gate_accepts_paraphrased_publisher_headline():
    now = datetime.now(timezone.utc)
    asset = _asset(1)
    asset["page_title"] = "Kohli opens up on his future after fresh retirement speculation"
    page = {
        "title": asset["page_title"],
        "date": (now - timedelta(hours=2)).isoformat(),
        "url": asset["source_page_url"],
        "source": "Example Sports",
    }

    candidate = crawler._candidate_from_browser_asset(
        asset,
        page,
        "Virat Kohli reacts to retirement rumours",
        "Virat Kohli reacts to retirement rumours",
        "Virat Kohli",
        now,
    )

    assert candidate is not None


def test_recent_articles_balance_publishers_and_keep_related_coverage(monkeypatch):
    now = datetime.now(timezone.utc)
    fresh = (now - timedelta(hours=2)).isoformat()

    def fake_search(query):
        return [
            {
                "title": "Kohli opens up on his future after fresh retirement speculation",
                "url": "https://one.example/story-1",
                "date": fresh,
                "body": "",
            },
            {
                "title": "Virat Kohli addresses retirement rumours after recent match",
                "url": "https://two.example/story-2",
                "date": fresh,
                "body": "",
            },
            {
                "title": "Virat Kohli attends a family wedding in Mumbai",
                "url": "https://three.example/story-3",
                "date": fresh,
                "body": "",
            },
        ]

    monkeypatch.setattr(crawler, "_news_search", fake_search)
    articles = crawler._collect_recent_articles(
        ["Virat Kohli reacts to retirement rumours"],
        "Virat Kohli reacts to retirement rumours",
        "Virat Kohli",
        now,
    )

    urls = {item["url"] for item in articles}
    assert urls == {
        "https://one.example/story-1",
        "https://two.example/story-2",
    }


def test_recent_articles_require_query_terms_in_title_and_rank_newest(monkeypatch):
    now = datetime.now(timezone.utc)
    fresh = (now - timedelta(hours=3)).isoformat()
    older = (now - timedelta(hours=18)).isoformat()
    stale = (now - timedelta(hours=90)).isoformat()

    def fake_search(query):
        return [
            {"title": "Virat Kohli returns after retirement rumours", "url": "https://a.example/story", "date": older, "body": ""},
            {"title": "Virat Kohli reacts to retirement rumours", "url": "https://b.example/story", "date": fresh, "body": ""},
            {"title": "Virat Kohli training update", "url": "https://c.example/story", "date": fresh, "body": ""},
            {"title": "Virat Kohli reacts to retirement rumours", "url": "https://d.example/story", "date": stale, "body": ""},
        ]

    monkeypatch.setattr(crawler, "_news_search", fake_search)

    articles = crawler._collect_recent_articles(
        ["Virat Kohli reacts to retirement rumours"],
        "Virat Kohli reacts to retirement rumours",
        "Virat Kohli",
        now,
    )

    assert [item["url"] for item in articles] == [
        "https://b.example/story",
        "https://a.example/story",
    ]
    assert all(item["title_match"] > 0 for item in articles)


def test_browser_first_pipeline_skips_profile_rescue_after_ten_images(monkeypatch):
    now = datetime.now(timezone.utc)
    pages = [{
        "title": "Virat Kohli reacts to retirement rumours",
        "url": "https://example.com/story",
        "date": (now - timedelta(hours=2)).isoformat(),
        "_crawler_query": "Virat Kohli reacts to retirement rumours",
        "source": "Example Sports",
    }]

    monkeypatch.setattr(crawler, "_collect_recent_articles", lambda *_args: pages)
    monkeypatch.setattr(
        crawler,
        "_scrape_browser_pages",
        lambda *_args, **kwargs: ([_asset(index) for index in range(10)], 10, 0),
    )

    def fail_profile(*_args):
        raise AssertionError("profile-page rescue must not run after ten current images")

    monkeypatch.setattr(crawler, "_collect_profile_pages", fail_profile)

    result = crawler.crawl_fresh_web_images(
        {"title": pages[0]["title"]},
        [{"primary_entity": "Virat Kohli"}],
    )

    assert len(result["assets"]) == 10
    assert result["profile_pages"] == 0
    assert result["rejection_counts"]["final_images"] == 10


def test_profile_pages_fill_sparse_current_news_pool(monkeypatch):
    now = datetime.now(timezone.utc)
    articles = [{
        "title": "Virat Kohli reacts to retirement rumours",
        "url": "https://example.com/story",
        "date": (now - timedelta(hours=2)).isoformat(),
        "_crawler_query": "Virat Kohli reacts to retirement rumours",
        "source": "Example Sports",
    }]
    profiles = [{
        "title": "Virat Kohli player profile",
        "url": "https://profile.example.com/virat",
        "source": "Example Sports",
    }]

    monkeypatch.setattr(crawler, "_collect_recent_articles", lambda *_args: articles)
    monkeypatch.setattr(crawler, "_collect_profile_pages", lambda *_args: profiles)

    calls = []

    def fake_scrape(pages, *_args, profile_page=False, **_kwargs):
        calls.append((profile_page, len(pages)))
        if profile_page:
            return [_asset(index + 100, kind="profile") for index in range(7)], 7, 0
        return [_asset(index) for index in range(3)], 3, 0

    monkeypatch.setattr(crawler, "_scrape_browser_pages", fake_scrape)

    result = crawler.crawl_fresh_web_images(
        {"title": articles[0]["title"]},
        [{"primary_entity": "Virat Kohli"}],
    )

    assert len(result["assets"]) == 10
    assert result["profile_pages"] == 1
    assert calls == [(False, 1), (True, 1)]


def test_web_lane_reports_underfill_without_using_image_search(monkeypatch):
    now = datetime.now(timezone.utc)
    articles = [{
        "title": "Virat Kohli reacts to retirement rumours",
        "url": "https://example.com/story",
        "date": (now - timedelta(hours=2)).isoformat(),
        "_crawler_query": "Virat Kohli reacts to retirement rumours",
        "source": "Example Sports",
    }]

    monkeypatch.setattr(crawler, "_collect_recent_articles", lambda *_args: articles)
    monkeypatch.setattr(crawler, "_collect_profile_pages", lambda *_args: [])
    monkeypatch.setattr(
        crawler,
        "_scrape_browser_pages",
        lambda *_args, **_kwargs: ([_asset(1), _asset(2)], 2, 0),
    )

    result = crawler.crawl_fresh_web_images(
        {"title": articles[0]["title"]},
        [{"primary_entity": "Virat Kohli"}],
    )

    assert len(result["assets"]) == 2
    assert result["rejection_counts"]["web_pool_underfilled"] == 1
    assert "image_search_raw" not in result["rejection_counts"]


def test_browser_runtime_self_heals_missing_playwright_package(monkeypatch):
    import web_browser_image_runtime as browser

    calls = []

    class Completed:
        returncode = 0

    def fake_run(command, **_kwargs):
        calls.append(command)
        return Completed()

    monkeypatch.setattr(browser, "_PLAYWRIGHT_PACKAGE_INSTALL_ATTEMPTED", False)
    monkeypatch.setattr(browser.subprocess, "run", fake_run)

    assert browser._install_playwright_package_once()
    assert calls == [[
        browser.sys.executable,
        "-m",
        "pip",
        "install",
        "playwright>=1.52,<2",
    ]]


def test_browser_title_match_accepts_surname_only_entity_coverage():
    from web_browser_image_runtime import _title_match as browser_title_match

    assert browser_title_match(
        "Virat Kohli",
        "Kohli opens up on his future after fresh retirement speculation",
        "Virat Kohli",
    ) > 0


def test_browser_candidate_extraction_prioritises_article_action_images():
    from web_browser_image_runtime import _build_candidates

    data = {
        "meta": {
            "og:image": "https://example.com/hero.jpg",
            "og:site_name": "Example Sports",
        },
        "jsonLd": [],
        "linkImages": [],
        "backgrounds": [],
        "noscripts": [],
        "imageData": [
            {
                "currentSrc": "https://example.com/batting.jpg",
                "src": "https://example.com/batting.jpg",
                "alt": "Virat Kohli batting during the match",
                "title": "",
                "className": "article-image",
                "contextText": "Virat Kohli batting during the match",
                "inArticle": True,
                "inFigure": True,
                "width": 1800,
                "height": 1200,
            },
            {
                "currentSrc": "https://example.com/logo.jpg",
                "src": "https://example.com/logo.jpg",
                "alt": "Example Sports logo",
                "title": "",
                "className": "logo",
                "contextText": "",
                "inArticle": False,
                "inFigure": False,
                "width": 1000,
                "height": 500,
            },
        ],
    }

    candidates = _build_candidates(
        data,
        "https://example.com/story",
        "Virat Kohli batting during the match",
        "Virat Kohli",
        "Virat Kohli batting during the match",
        4,
    )

    urls = [item["url"] for item in candidates]
    assert "https://example.com/batting.jpg" in urls
    assert "https://example.com/logo.jpg" not in urls


def test_visual_provider_fallback_queries_are_derived_without_manual_queries():
    from visual_content_runtime import _build_visual_fallback_queries

    queries = _build_visual_fallback_queries(
        [
            {
                "factual_primary_entity": "Virat Kohli",
                "visual_intent": "batting during the match",
            },
            {
                "primary_entity": "Australia",
                "visual_context": "team celebration",
            },
        ],
        "Virat Kohli reacts after the match",
    )

    assert queries[0] == "Virat Kohli batting during the match"
    assert queries[1] == "Australia team celebration"
