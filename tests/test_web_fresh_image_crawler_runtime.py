import io
import sys
import types
from datetime import datetime, timedelta, timezone

from PIL import Image

import web_fresh_image_crawler_runtime as crawler


def _jpeg_bytes(color=(80, 90, 100)):
    buffer = io.BytesIO()
    Image.new("RGB", (1200, 1600), color).save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def _news_result(title, url, date, image="", source="Example Cricket"):
    return {
        "title": title,
        "url": url,
        "date": date,
        "body": f"{title} {source}",
        "image": image,
        "source": source,
    }


def _install_fake_ddgs(monkeypatch, results_by_query):
    class FakeDDGS:
        def __init__(self, *args, **kwargs):
            pass

        def news(self, query, **kwargs):
            return list(results_by_query.get(query, []))

    monkeypatch.setitem(sys.modules, "ddgs", types.SimpleNamespace(DDGS=FakeDDGS))


def _install_fake_ddgs_images(monkeypatch, results_by_query):
    class FakeDDGS:
        def __init__(self, *args, **kwargs):
            pass

        def images(self, query, **kwargs):
            return list(results_by_query.get(query, []))

        def news(self, query, **kwargs):
            return []

    monkeypatch.setitem(sys.modules, "ddgs", types.SimpleNamespace(DDGS=FakeDDGS))


def test_image_search_uses_explicit_bing_backend(monkeypatch):
    query = "Virat Kohli cricket photo"
    calls = []

    class FakeDDGS:
        def __init__(self, *args, **kwargs):
            pass

        def images(self, query, **kwargs):
            calls.append(dict(kwargs))
            return [{
                "title": "Virat Kohli cricket photo",
                "image": "https://cdn.example.com/virat.jpg",
                "url": "https://publisher.example.com/story",
                "width": 1600,
                "height": 1000,
                "source": "Bing",
            }]

    monkeypatch.setitem(sys.modules, "ddgs", types.SimpleNamespace(DDGS=FakeDDGS))
    monkeypatch.setattr(
        "news_source_image_runtime.fetch_direct_source_image",
        lambda image_url, page_url="", publisher_hint="": {
            "bytes": _jpeg_bytes(),
            "image_url": image_url,
            "page_url": page_url,
            "publisher": publisher_hint or "publisher.example.com",
        },
    )
    monkeypatch.setattr(
        "news_source_image_runtime.extract_news_source_images",
        lambda *_args, **_kwargs: [],
    )

    result = crawler.crawl_fresh_web_images(
        {"title": query},
        [{"primary_entity": "Virat Kohli", "voiceover": "Virat Kohli story."}],
        story_title=query,
    )

    assert len(result["assets"]) == 1
    assert calls
    assert calls[0]["backend"] == "bing"


def test_direct_image_failures_fall_back_to_scraping_image_result_source_pages(monkeypatch):
    query = "Virat Kohli cricket action"
    image_results = [
        {
            "title": f"Virat Kohli cricket action photo {index}",
            "image": f"https://cdn.example.com/{index}.jpg",
            "url": f"https://publisher.example.com/story/{index}",
            "width": 1600,
            "height": 1000,
            "source": "Bing",
        }
        for index in range(6)
    ]
    _install_fake_ddgs_images(monkeypatch, {query: image_results})

    monkeypatch.setattr(
        "news_source_image_runtime.fetch_direct_source_image",
        lambda *_args, **_kwargs: None,
    )
    def scrape_source_page(url, publisher_hint="", max_images=6):
        index = int(url.rsplit("/", 1)[-1])
        buffer = io.BytesIO()
        Image.new("RGB", (1200 + index * 40, 1600), (20 + index * 40, 80, 140)).save(
            buffer, format="JPEG", quality=95
        )
        return [{
            "bytes": buffer.getvalue(),
            "publisher": publisher_hint or "publisher.example.com",
            "method": "og:image",
            "image_url": f"{url}/hero.jpg",
            "page_url": url,
        }]

    monkeypatch.setattr(
        "news_source_image_runtime.extract_news_source_images",
        scrape_source_page,
    )

    result = crawler.crawl_fresh_web_images(
        {"title": query},
        [{"primary_entity": "Virat Kohli", "voiceover": "Virat Kohli is the subject."}],
        story_title=query,
    )

    assert len(result["assets"]) == 5
    assert result["assets"][0]["source_type"] == "web_crawler"
    assert result["assets"][0]["crawler_freshness_basis"] == "search-window:7d"
    assert result["rejection_counts"]["image_search_raw"] == 6
    assert result["rejection_counts"]["image_search_downloaded"] == 0
    assert result["rejection_counts"]["image_result_pages"] == 5
    assert result["rejection_counts"]["image_result_page_images"] == 5


def test_general_web_search_recovers_when_image_and_news_search_are_empty(monkeypatch):
    query = "MS Dhoni latest cricket story"

    class FakeDDGS:
        def __init__(self, *args, **kwargs):
            pass

        def images(self, query, **kwargs):
            return []

        def news(self, query, **kwargs):
            return []

        def text(self, query, **kwargs):
            return [{
                "title": "MS Dhoni latest cricket story",
                "href": "https://publisher.example.com/ms-dhoni",
                "body": "MS Dhoni latest cricket story",
            }]

    monkeypatch.setitem(sys.modules, "ddgs", types.SimpleNamespace(DDGS=FakeDDGS))
    monkeypatch.setattr(
        "news_source_image_runtime.extract_news_source_images",
        lambda url, publisher_hint="", max_images=6: [{
            "bytes": _jpeg_bytes((140, 80, 60)),
            "publisher": publisher_hint or "publisher.example.com",
            "method": "og:image",
            "image_url": f"{url}/hero.jpg",
            "page_url": url,
        }],
    )

    result = crawler.crawl_fresh_web_images(
        {"title": query},
        [{"primary_entity": "MS Dhoni", "voiceover": "MS Dhoni is the subject."}],
        story_title=query,
    )

    assert len(result["assets"]) == 1
    assert result["assets"][0]["source_type"] == "web_crawler"
    assert result["assets"][0]["crawler_freshness_basis"] == "search-window:7d"
    assert result["assets"][0]["crawler_age_hours"] is None
    assert result["rejection_counts"]["web_search_pages"] == 1
    assert result["rejection_counts"]["web_search_images"] == 1


def test_crawler_keeps_only_recent_article_coverage(monkeypatch):
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(hours=20)).isoformat()
    stale = (now - timedelta(hours=80)).isoformat()
    query = "Virat Kohli reacts to latest cricket story"

    _install_fake_ddgs(
        monkeypatch,
        {
            query: [
                _news_result(
                    "Virat Kohli reacts to latest cricket story",
                    "https://example.com/recent",
                    recent,
                ),
                _news_result("Virat Kohli old archive story", "https://example.com/stale", stale),
            ]
        },
    )
    monkeypatch.setattr(
        "news_source_image_runtime.extract_news_source_images",
        lambda url, publisher_hint="", max_images=6: [{
            "bytes": _jpeg_bytes(),
            "publisher": publisher_hint or "Example Cricket",
            "method": "og:image",
            "image_url": f"{url}/image.jpg",
            "page_url": url,
        }],
    )

    result = crawler.crawl_fresh_web_images(
        {"title": query},
        [{"primary_entity": "Virat Kohli", "voiceover": "Virat Kohli story."}],
        story_title=query,
    )

    assert result["articles"] == 1
    assert len(result["assets"]) == 1
    assert result["assets"][0]["crawler_age_hours"] < 72


def test_direct_image_search_builds_diverse_pool_without_article_scraping(monkeypatch):
    query = "Virat Kohli reacts after match"
    image_results = [
        {
            "title": f"Virat Kohli action photo {index}",
            "image": f"https://cdn.example.com/{index}.jpg",
            "url": f"https://publisher.example.com/story/{index}",
            "width": 1600,
            "height": 1000,
            "source": "Bing",
        }
        for index in range(10)
    ]
    _install_fake_ddgs_images(monkeypatch, {query: image_results})
    monkeypatch.setattr(
        "news_source_image_runtime.fetch_direct_source_image",
        lambda image_url, page_url="", publisher_hint="": {
            "bytes": _jpeg_bytes((40 + int(image_url.rsplit("/", 1)[-1].split(".")[0]) * 10, 80, 120)),
            "image_url": image_url,
            "page_url": page_url,
            "publisher": "publisher.example.com",
        },
    )

    def fail_article_scrape(*_args, **_kwargs):
        raise AssertionError("article scraping must not run after the direct image pool reaches 10")

    monkeypatch.setattr(
        "news_source_image_runtime.extract_news_source_images",
        fail_article_scrape,
    )

    result = crawler.crawl_fresh_web_images(
        {"title": query},
        [{"primary_entity": "Virat Kohli", "voiceover": "Virat Kohli is the subject."}],
        story_title=query,
    )

    assert len(result["assets"]) == 10
    assert result["rejection_counts"]["image_search_raw"] == 10
    assert result["rejection_counts"]["article_images"] == 0
    assert result["assets"][0]["source_type"] == "web_image_search"
    assert result["assets"][0]["source_page_url"].startswith("https://publisher.example.com/story/")
    assert result["assets"][0]["crawler_freshness_basis"] == "search-window:7d"


def test_materialized_direct_crawler_asset_is_tagged_for_dashboard_pool(tmp_path):
    from types import SimpleNamespace
    from visual_retrieval_runtime import materialize_manual_visual_pool

    bot = SimpleNamespace(ASSETS_DIR=str(tmp_path))
    assets = materialize_manual_visual_pool(
        bot,
        [{
            "bytes": _jpeg_bytes(),
            "hash": "crawler-hash",
            "source": "web_image_search",
            "source_type": "web_image_search",
            "source_page_url": "https://example.com/story",
            "source_image_url": "https://cdn.example.com/image.jpg",
            "provenance": {"provider": "Example News", "url": "https://example.com/story"},
            "status": "crawler-ai-verified",
            "provenance_status": "provenance-review",
        }],
        pool_id="crawler-dashboard-test",
    )

    assert len(assets) == 1
    assert assets[0]["pool_origin"] == "web-crawler"
    assert assets[0]["source_page_url"] == "https://example.com/story"
    assert assets[0]["source_image_url"] == "https://cdn.example.com/image.jpg"


def test_high_confidence_article_images_bypass_gemini(monkeypatch):
    now = datetime.now(timezone.utc)
    query = "Virat Kohli statement after match"
    _install_fake_ddgs(monkeypatch, {
        query: [_news_result(query, "https://example.com/story", (now - timedelta(hours=8)).isoformat())]
    })
    monkeypatch.setattr(
        "news_source_image_runtime.extract_news_source_images",
        lambda *_args, **_kwargs: [{
            "bytes": _jpeg_bytes(),
            "publisher": "Example Cricket",
            "method": "og:image",
            "image_url": "https://example.com/hero.jpg",
            "page_url": "https://example.com/story",
        }],
    )

    def fail_ai(*_args, **_kwargs):
        raise AssertionError("high-confidence crawler image must not invoke Gemini")

    monkeypatch.setattr("visual_qa_runtime.strict_gemini_check_batch", fail_ai)

    result = crawler.crawl_fresh_web_images(
        {"title": query},
        [{"primary_entity": "Virat Kohli", "voiceover": "Virat Kohli story."}],
        story_title=query,
    )

    assert len(result["assets"]) == 1
    assert result["assets"][0]["status"] == "crawler-high-confidence"
    assert result["ai_checked"] == 0


def test_ambiguous_crawler_images_use_one_bounded_ai_check(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    now = datetime.now(timezone.utc)
    query = "Virat Kohli responds after match"
    _install_fake_ddgs(monkeypatch, {
        query: [_news_result(query, "https://example.com/story", (now - timedelta(hours=12)).isoformat())]
    })
    monkeypatch.setattr(
        "news_source_image_runtime.extract_news_source_images",
        lambda *_args, **_kwargs: [{
            "bytes": _jpeg_bytes((110, 120, 130)),
            "publisher": "Example Cricket",
            "method": "article-img",
            "image_url": "https://example.com/body-image.jpg",
            "page_url": "https://example.com/story",
        }],
    )
    calls = {"count": 0}

    def fake_ai(images, *args, **kwargs):
        calls["count"] += 1
        return {0: True}

    monkeypatch.setattr("visual_qa_runtime.strict_gemini_check_batch", fake_ai)
    monkeypatch.setattr("visual_qa_runtime.start_visual_qa_scene", lambda: None)
    monkeypatch.setattr("visual_qa_runtime.get_last_visual_qa_failure", lambda: "")

    result = crawler.crawl_fresh_web_images(
        {"title": query},
        [{"primary_entity": "Virat Kohli", "voiceover": "Virat Kohli story."}],
        story_title=query,
    )

    assert len(result["assets"]) == 1
    assert result["assets"][0]["status"] == "crawler-ai-verified"
    assert result["ai_checked"] == 1
    assert calls["count"] == 1


def test_crawler_preserves_publisher_for_overlay(monkeypatch):
    now = datetime.now(timezone.utc)
    query = "Jasprit Bumrah latest cricket story"
    _install_fake_ddgs(monkeypatch, {
        query: [_news_result(query, "https://example.com/bumrah", (now - timedelta(hours=4)).isoformat(), source="Cricket Source")]
    })
    monkeypatch.setattr(
        "news_source_image_runtime.extract_news_source_images",
        lambda *_args, **_kwargs: [{
            "bytes": _jpeg_bytes((150, 90, 70)),
            "publisher": "Cricket Source",
            "method": "og:image",
            "image_url": "https://example.com/bumrah.jpg",
            "page_url": "https://example.com/bumrah",
        }],
    )

    result = crawler.crawl_fresh_web_images(
        {"title": query},
        [{"primary_entity": "Jasprit Bumrah", "voiceover": "Jasprit Bumrah story."}],
        story_title=query,
    )

    assert result["assets"][0]["source"] == "web_crawler"
    assert result["assets"][0]["source_name"] == "Cricket Source"
    assert result["assets"][0]["credit"] == "Source: Cricket Source"
    assert result["assets"][0]["provenance"]["provider"] == "Cricket Source"
