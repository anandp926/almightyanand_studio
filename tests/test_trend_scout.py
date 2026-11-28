from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest

from agents import trend_scout
from agents.trend_scout import NoFreshTopicError, TrendScout, parse_feed


def _rss(items: str) -> bytes:
    return f'<?xml version="1.0"?><rss version="2.0"><channel>{items}</channel></rss>'.encode()


def _now_rfc822(hours_ago: float = 1) -> str:
    return format_datetime(datetime.now(timezone.utc) - timedelta(hours=hours_ago))


@pytest.fixture
def scout():
    """A scout with no pillar restriction, so the scoring rules can be tested on every pillar."""
    s = TrendScout(dry_run=True)
    s.pillars = None
    return s


def test_channel_stays_on_its_pillars():
    scout = TrendScout(dry_run=True)
    assert scout.pillars == ["technology"]                       # from configs/series.json
    kwargs = dict(link="https://x.test/a", summary="", published=_now_rfc822(), source="feed",
                  default_category=None, direct_link=True)
    assert scout._candidate_from_item(title="RBI issues new UPI fraud rules for banks", **kwargs) is None
    assert scout._candidate_from_item(title="Layoffs: salary cuts and job losses in corporate India", **kwargs) is None
    tech = scout._candidate_from_item(title="Google Gemini gets a new AI agent tool", **kwargs)
    assert tech is not None and tech.category == "technology"


def test_off_topic_trend_is_dropped_not_relabelled(scout):
    cand = scout._candidate_from_item(
        title="India vs South Africa 3rd ODI live score", link="https://x.test/a", summary="",
        published=_now_rfc822(), source="google_trends_india", default_category=None, direct_link=True,
    )
    assert cand is None


def test_on_topic_item_gets_its_pillar(scout):
    cand = scout._candidate_from_item(
        title="RBI issues new UPI fraud rules for banks", link="https://x.test/a", summary="",
        published=_now_rfc822(), source="feed", default_category=None, direct_link=True,
    )
    assert cand is not None and cand.category == "finance"
    assert cand.channel_fit_score > 5.5


def test_pillar_feed_keeps_item_without_keywords_at_low_fit(scout):
    cand = scout._candidate_from_item(
        title="Statement on developmental policies", link="https://x.test/a", summary="",
        published=_now_rfc822(), source="rbi_press_releases", default_category="finance", direct_link=True,
    )
    assert cand is not None and cand.category == "finance" and cand.channel_fit_score == 5.0


def test_stale_item_is_dropped(scout):
    cand = scout._candidate_from_item(
        title="Google Gemini AI update", link="https://x.test/a", summary="",
        published=_now_rfc822(hours_ago=200), source="feed", default_category=None, direct_link=True,
    )
    assert cand is None


def test_no_candidates_raises_instead_of_inventing_a_topic(scout, monkeypatch):
    def boom(url, timeout=8):
        raise OSError("network down")

    monkeypatch.setattr(TrendScout, "_http_get", staticmethod(boom))
    with pytest.raises(NoFreshTopicError):
        scout.discover_live_candidates()
    with pytest.raises(NoFreshTopicError):
        scout.discover_live_candidate()


def test_no_hardcoded_topic_left_in_module():
    source = open(trend_scout.__file__, encoding="utf-8").read()
    assert "CERT-In High-Alert" not in source
    assert "cert_in_live_alert" not in source


def test_parse_feed_handles_rss_and_atom():
    rss = _rss("<item><title>A</title><link>https://a.test/1</link><description>desc</description>"
               "<pubDate>Tue, 24 Nov 2026 10:00:00 GMT</pubDate></item>")
    items = parse_feed(rss)
    assert items[0]["title"] == "A" and items[0]["link"] == "https://a.test/1" and items[0]["summary"] == "desc"

    atom = (b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>B</title>'
            b'<link rel="alternate" href="https://b.test/2"/><content type="html">body</content>'
            b'<updated>2026-11-24T10:00:00Z</updated></entry></feed>')
    items = parse_feed(atom)
    assert items[0]["title"] == "B" and items[0]["link"] == "https://b.test/2" and items[0]["summary"] == "body"


def test_trends_use_the_linked_article_and_skip_bare_terms(scout, monkeypatch):
    ns = "https://trends.google.com/trending/rss"
    feed = (
        f'<?xml version="1.0"?><rss version="2.0" xmlns:ht="{ns}"><channel>'
        f'<item><title>upi</title><pubDate>{_now_rfc822()}</pubDate><ht:approx_traffic>5000+</ht:approx_traffic>'
        f'<ht:news_item><ht:news_item_title>New UPI limit announced by RBI for bank transfers</ht:news_item_title>'
        f'<ht:news_item_url>https://news.test/upi</ht:news_item_url></ht:news_item></item>'
        f'<item><title>ai tool</title><pubDate>{_now_rfc822()}</pubDate><ht:approx_traffic>200+</ht:approx_traffic></item>'
        f'</channel></rss>'
    ).encode()
    monkeypatch.setattr(TrendScout, "_http_get", staticmethod(lambda url, timeout=8: feed))
    cands = scout.fetch_google_trends_india()
    assert len(cands) == 1
    assert cands[0].source_url == "https://news.test/upi"
    assert cands[0].title.startswith("New UPI limit")
    assert cands[0].direct_link is True


def test_direct_links_rank_ahead_of_redirect_only_items(scout, monkeypatch):
    direct = _rss(f"<item><title>Google Gemini AI update for India</title><link>https://pub.test/1</link>"
                  f"<description>d</description><pubDate>{_now_rfc822(40)}</pubDate></item>")
    news = _rss(f"<item><title>OpenAI ChatGPT AI agent tool update - Outlet</title>"
                f"<link>https://news.google.com/rss/articles/xyz</link><pubDate>{_now_rfc822(1)}</pubDate></item>")

    def fake_get(url, timeout=8):
        if "news.google.com" in url:
            return news
        if "trends.google.com" in url:
            raise OSError("skip")
        return direct

    monkeypatch.setattr(TrendScout, "_http_get", staticmethod(fake_get))
    monkeypatch.setattr(trend_scout, "DIRECT_FEEDS", [("https://pub.test/feed", "pub", None)])
    cands = scout.discover_live_candidates()
    assert cands[0].direct_link is True and cands[0].source == "pub"
    assert any(c.source == "google_news_india" for c in cands[1:])


def test_already_covered_topics_are_skipped(scout, monkeypatch):
    scout.memory.record_topic("Google Gemini AI update for India", source="x")
    feed = _rss(f"<item><title>Google Gemini AI update for India</title><link>https://pub.test/1</link>"
                f"<pubDate>{_now_rfc822()}</pubDate></item>")
    monkeypatch.setattr(TrendScout, "_http_get", staticmethod(lambda url, timeout=8: feed))
    monkeypatch.setattr(trend_scout, "DIRECT_FEEDS", [("https://pub.test/feed", "pub", None)])
    monkeypatch.setattr(trend_scout, "GOOGLE_NEWS_QUERIES", [])
    with pytest.raises(NoFreshTopicError):
        scout.discover_live_candidates()
