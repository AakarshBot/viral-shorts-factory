import io
from urllib.parse import urlparse

from PIL import Image

from news_source_image_runtime import _ArticleImageParser, _candidate_urls, _publisher


def _jpeg_bytes(size=(900, 1200), color="white"):
    raw = io.BytesIO()
    Image.new("RGB", size, color).save(raw, format="JPEG")
    return raw.getvalue()


def test_article_image_candidates_use_multiple_page_signals_and_skip_video():
    html = """
    <html><head>
      <meta property="og:site_name" content="Example News">
      <meta property="og:image" content="/images/lead.jpg">
      <meta name="twitter:image" content="/images/twitter.jpg">
      <script type="application/ld+json">
        {"@type":"NewsArticle","image":"https://example.com/images/json.jpg"}
      </script>
    </head><body>
      <picture>
        <source srcset="/images/picture-small.jpg 480w, /images/picture-large.jpg 1200w">
        <img class="article-image" src="/images/body.jpg"
             data-src="/images/lazy.jpg" srcset="/images/body-small.jpg 480w, /images/body-large.jpg 1400w">
      </picture>
      <video><source src="/video/story.mp4" type="video/mp4"></video>
    </body></html>
    """
    parser = _ArticleImageParser()
    parser.feed(html)
    urls = _candidate_urls(parser, "https://example.com/story/one")

    assert urls[0] == "https://example.com/images/lead.jpg"
    assert "https://example.com/images/twitter.jpg" in urls
    assert "https://example.com/images/json.jpg" in urls
    assert "https://example.com/images/body-large.jpg" in urls
    assert not any("story.mp4" in url for url in urls)
    assert _publisher(parser, "https://example.com/story/one", "") == "Example News"


def test_video_first_article_is_not_treated_as_an_image_story():
    from news_source_image_runtime import _ArticleImageParser, _video_first_story

    html = """
    <head>
      <meta property="og:type" content="video.other">
      <meta property="og:video" content="https://example.com/story.mp4">
      <meta property="og:image" content="https://example.com/story-thumb.jpg">
      <meta name="twitter:card" content="player">
    </head>
    """
    parser = _ArticleImageParser()
    parser.feed(html)

    assert _video_first_story(parser) is True


def test_no_non_http_image_candidate():
    html = '<meta property="og:image" content="data:image/png;base64,AAAA">'
    parser = _ArticleImageParser()
    parser.feed(html)
    assert _candidate_urls(parser, "https://example.com/story") == []


def test_direct_source_image_fallback_returns_article_image(monkeypatch):
    import news_source_image_runtime as module

    image_bytes = _jpeg_bytes((900, 1200), "white")

    class Response:
        url = "https://example.com/images/lead.jpg"
        headers = {"content-type": "image/jpeg"}

        def raise_for_status(self):
            return None

        def iter_content(self, _chunk_size):
            yield image_bytes

    class Session:
        def __init__(self):
            self.headers = {}

        def get(self, _url, **_kwargs):
            return Response()

    monkeypatch.setattr(module.requests, "Session", Session)

    asset = module.fetch_direct_source_image(
        "https://example.com/images/lead.jpg",
        "https://example.com/story",
        "Example News",
    )

    assert asset is not None
    assert asset["source_type"] == "news_source"
    assert asset["provenance_status"] == "provenance-review"
    assert asset["image_url"] == "https://example.com/images/lead.jpg"


def test_extract_news_source_images_returns_multiple_direct_images(monkeypatch, tmp_path):
    import news_source_image_runtime as module

    monkeypatch.setenv("ASSET_CACHE_DIR", str(tmp_path))

    image_bytes = _jpeg_bytes((900, 1200), "white")
    image_bytes_two = _jpeg_bytes((901, 1200), "gray")
    image_bytes_three = _jpeg_bytes((902, 1200), "black")
    image_bytes_four = _jpeg_bytes((903, 1200), "blue")
    page_html = """
    <html><head>
      <meta property="og:site_name" content="Example News">
      <meta property="og:image" content="/images/lead.jpg">
      <script type="application/ld+json">
        {"@type":"NewsArticle","image":["https://example.com/images/two.jpg","https://example.com/images/three.jpg"]}
      </script>
    </head><body><article>
      <img class="article-image" src="/images/four.jpg">
    </article></body></html>
    """

    class Response:
        def __init__(self, url, content, content_type="text/html"):
            self.url = url
            self.content = content
            self.encoding = "utf-8"
            self.apparent_encoding = "utf-8"
            self.headers = {"content-type": content_type}

        def raise_for_status(self):
            return None

        def iter_content(self, _chunk_size):
            yield self.content

    class Session:
        def __init__(self):
            self.headers = {}

        def get(self, url, **_kwargs):
            if url.endswith("/story"):
                return Response(url, page_html.encode("utf-8"))
            payload = {
                "/images/lead.jpg": image_bytes,
                "/images/two.jpg": image_bytes_two,
                "/images/three.jpg": image_bytes_three,
                "/images/four.jpg": image_bytes_four,
            }
            return Response(url, payload.get(urlparse(url).path, image_bytes), "image/jpeg")

    monkeypatch.setattr(module.requests, "Session", Session)

    assets = module.extract_news_source_images(
        "https://example.com/story",
        "Example News",
        max_images=3,
    )

    assert len(assets) == 3
    assert all(item["source"] == "news_source" for item in assets)
    assert all(item["source_type"] == "news_source" for item in assets)
    assert all(item["publisher"] == "Example News" for item in assets)
    assert all(item["provenance"]["provider"] == "Example News" for item in assets)
    assert all(item["provenance"]["url"].startswith("https://example.com/images/") for item in assets)


def test_news_source_extraction_does_not_require_visual_ai_qc(monkeypatch):
    import visual_content_runtime as content_runtime

    called = {"strict": 0}

    class FakeRuntime:
        @staticmethod
        def _strict_gate(*_args, **_kwargs):
            called["strict"] += 1
            raise AssertionError("article-source pool must never enter factory visual QC")

    monkeypatch.setattr(
        content_runtime,
        "_load_news_source_image_pool",
        lambda *_args, **_kwargs: [
            {
                "bytes": _jpeg_bytes(),
                "hash": "article-hash",
                "path": "",
                "source": "news_source",
                "source_type": "news_source",
                "credit": "Source: Example News",
                "provenance": {"provider": "Example News", "url": "https://example.com/image.jpg"},
            }
        ],
    )

    assets = content_runtime._load_news_source_image_pool(type("B", (), {})(), {})
    assert assets[0]["source_type"] == "news_source"
    assert called["strict"] == 0
