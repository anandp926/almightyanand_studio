from agents import source_fetcher
from agents.source_fetcher import build_source_context, html_to_text, is_sufficient


def test_html_to_text_drops_scripts_nav_and_short_blocks():
    html = """
    <html><head><style>p{}</style><script>var x = "not text";</script></head><body>
    <nav><a>Home</a><a>Markets</a></nav>
    <p>The Reserve Bank of India raised the UPI limit for tax payments on Monday, effective immediately.</p>
    <p>Share</p>
    <footer>Copyright notice that is long enough to pass the length filter easily, repeated.</footer>
    </body></html>"""
    text = html_to_text(html, min_block_chars=60)
    assert "Reserve Bank of India" in text
    assert "not text" not in text and "Markets" not in text and "Share" not in text and "Copyright" not in text


def test_context_uses_feed_summary_and_respects_minimum(monkeypatch):
    monkeypatch.setattr(source_fetcher, "fetch_article_text", lambda url, timeout=10: "")
    ctx = build_source_context("Title", summary="<p>Short summary.</p>", source_url="https://x.test")
    assert ctx == "Short summary."
    assert not is_sufficient(ctx)
    assert is_sufficient("x" * 600)


def test_non_http_urls_are_not_fetched():
    assert source_fetcher.fetch_article_text("file:///etc/passwd") == ""
    assert source_fetcher.fetch_article_text("") == ""
