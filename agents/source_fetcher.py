#!/usr/bin/env python3
"""
source_fetcher.py — Fetches the article behind a topic so scripts can be grounded.

The script LLM used to receive only a headline, which left it nothing to
fact-check against. This module turns a candidate's feed summary and article
page into plain text. If there is not enough text, the topic is not scripted.

CLI:
    python -m agents.source_fetcher <url>
"""

import logging
import re
import sys
import urllib.request
from html.parser import HTMLParser
from typing import List

from agents.settings import env_int

log = logging.getLogger("source_fetcher")

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AlmightyAnandStudio/2.0 (+source-grounding)"
MAX_DOWNLOAD_BYTES = 1_500_000
MAX_CONTEXT_CHARS = 6000

_SKIP_TAGS = {"script", "style", "noscript", "nav", "footer", "header", "aside", "form", "svg", "button", "select", "iframe"}
_BLOCK_TAGS = {"p", "li", "h1", "h2", "h3", "h4", "blockquote", "div", "section", "article", "br", "td", "tr", "ul", "ol"}


def min_source_chars() -> int:
    """Minimum characters of source text required before a topic may be scripted."""
    return env_int("MIN_SOURCE_CHARS", 600)


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._buffer: List[str] = []
        self.blocks: List[str] = []

    def _flush(self):
        text = re.sub(r"\s+", " ", "".join(self._buffer)).strip()
        self._buffer = []
        if text:
            self.blocks.append(text)

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _BLOCK_TAGS:
            self._flush()

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._buffer.append(data)

    def close(self):
        super().close()
        self._flush()


def html_to_text(html: str, min_block_chars: int = 0) -> str:
    """Visible text of an HTML fragment or page. Short blocks (menus, labels) can be dropped."""
    if not html:
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception as e:
        log.debug(f"HTML parse issue: {e}")
    blocks = [b for b in parser.blocks if len(b) >= min_block_chars]
    seen = set()
    unique = []
    for block in blocks:
        if block not in seen:
            seen.add(block)
            unique.append(block)
    return "\n".join(unique)


def fetch_article_text(url: str, timeout: int = 10) -> str:
    """Download a page and return its body text. Returns '' on any failure."""
    if not url or not url.lower().startswith(("http://", "https://")):
        return ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.8"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if content_type and "html" not in content_type and "text" not in content_type:
                return ""
            raw = resp.read(MAX_DOWNLOAD_BYTES)
            charset = resp.headers.get_content_charset() or "utf-8"
        html = raw.decode(charset, errors="replace")
    except Exception as e:
        log.info(f"Source fetch failed for {url}: {e}")
        return ""
    # 60+ characters keeps paragraphs and drops navigation, bylines and buttons.
    return html_to_text(html, min_block_chars=60)


def build_source_context(title: str, summary: str = "", source_url: str = "", fetch: bool = True) -> str:
    """
    Text the script may draw facts from: feed summary first, then the article body.
    The headline is not included here; callers pass it separately.
    """
    parts: List[str] = []
    summary_text = html_to_text(summary) if summary else ""
    if summary_text and summary_text.strip().lower() != (title or "").strip().lower():
        parts.append(summary_text)
    if fetch and source_url:
        article = fetch_article_text(source_url)
        if article:
            parts.append(article)
    context = "\n".join(parts).strip()
    return context[:MAX_CONTEXT_CHARS]


def is_sufficient(context: str) -> bool:
    return len((context or "").strip()) >= min_source_chars()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [SOURCE] %(levelname)s %(message)s")
    if len(sys.argv) < 2:
        print("usage: python -m agents.source_fetcher <url>")
        sys.exit(2)
    text = build_source_context(title="", source_url=sys.argv[1])
    print(f"--- {len(text)} chars (need {min_source_chars()}) ---")
    print(text)


if __name__ == "__main__":
    main()
