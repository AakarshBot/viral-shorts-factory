from news_source_image_runtime import _ArticleImageParser, _candidate_urls, _publisher


def test_article_image_priority_and_publisher():
    html = """
    <html><head>
      <meta property="og:site_name" content="Example News">
      <meta property="og:image" content="/images/lead.jpg">
      <meta name="twitter:image" content="/images/twitter.jpg">
    </head><body></body></html>
    """
    parser = _ArticleImageParser()
    parser.feed(html)
    urls = _candidate_urls(parser, "https://example.com/story/one")
    assert urls[0] == "https://example.com/images/lead.jpg"
    assert urls[1] == "https://example.com/images/twitter.jpg"
    assert _publisher(parser, "https://example.com/story/one", "") == "Example News"


def test_no_non_http_image_candidate():
    html = '<meta property="og:image" content="data:image/png;base64,AAAA">'
    parser = _ArticleImageParser()
    parser.feed(html)
    assert _candidate_urls(parser, "https://example.com/story") == []
