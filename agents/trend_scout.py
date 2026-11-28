#!/usr/bin/env python3
"""
trend_scout.py — Trending Topic Discovery Agent

Collects live candidates from publisher RSS feeds, Google News India and
Google Trends India, drops PR/interview fluff, de-duplicates against the
SQLite MemoryStore, and ranks what is left.

Rules:
  - A candidate must match at least one channel-pillar keyword, unless it came
    from a pillar-specific search. Off-topic trends (cricket, celebrities) are
    dropped instead of being relabelled "technology".
  - Only pillars listed in configs/series.json are considered, so the channel
    stays on its subject.
  - There is no built-in fallback topic. If nothing fresh and relevant is
    found, NoFreshTopicError is raised and the caller skips the cycle.

CLI Entrypoint:
    python -m agents.trend_scout [--live] [--top N]
"""

import argparse
import json
import logging
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional, Tuple

from agents.memory_store import MemoryStore
from agents.settings import env

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [TREND-SCOUT] %(levelname)s %(message)s",
)
log = logging.getLogger("trend_scout")

TOPIC_QUEUE_KEY = "topics:scored_queue"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
MAX_AGE_HOURS = 72

ATOM_NS = "{http://www.w3.org/2005/Atom}"
CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}"
TRENDS_NS = "{https://trends.google.com/trending/rss}"

PILLAR_KEYWORDS = {
    "finance": ["rbi", "bank", "scam", "fraud", "cyber", "penalty", "mandate", "tax", "charge", "debit", "upi", "card"],
    "technology": ["ai", "tool", "hack", "update", "google", "microsoft", "deepseek", "chatgpt", "gemini", "cyberattack", "security", "zero-day", "agent"],
    "career": ["job", "salary", "layoff", "promotion", "corporate", "remote", "productivity", "interview"],
}

# Publisher feeds with direct article links, so the article text can be fetched
# for grounding. (url, source name, pillar or None). None means an item must
# match a pillar keyword to be considered; that keeps routine notices and
# off-topic stories out. Set a pillar only for a feed where every item is usable.
DIRECT_FEEDS: List[Tuple[str, str, Optional[str]]] = [
    ("https://www.rbi.org.in/pressreleases_rss.xml", "rbi_press_releases", None),
    ("https://www.rbi.org.in/notifications_rss.xml", "rbi_notifications", None),
    ("https://feeds.feedburner.com/gadgets360-latest", "gadgets360", None),
    ("https://economictimes.indiatimes.com/tech/rssfeeds/13357270.cms", "economic_times_tech", None),
    ("https://blog.google/rss/", "google_blog", None),
    ("https://techcrunch.com/feed/", "techcrunch", None),
    ("https://www.theverge.com/rss/index.xml", "the_verge", None),
]

# (query, pillar used when a result matches no pillar keyword)
GOOGLE_NEWS_QUERIES: List[Tuple[str, str]] = [
    ("OpenAI+OR+Google+Gemini+OR+DeepSeek+OR+AI+agent+launch", "technology"),
    ("RBI+OR+UPI+OR+cyber+scam+alert+India", "finance"),
    ("new+free+AI+tool+OR+developer+breakthrough", "technology"),
]

# Filter out corporate PR fluff, executive interviews, and sponsored articles
PR_INTERVIEW_PATTERNS = [
    r"\bin conversation with\b",
    r"\bexclusive interview\b",
    r"\bq&a\b",
    r"\bsponsored\b",
    r"\bpartner content\b",
    r"\bbrand spotlight\b",
    r"\bpress release\b",
    r"\bwebinar\b",
    r"\bceo says\b",
    r"\bmd & ceo\b",
    r"\bco-founder explains\b",
    r"\batmanirbhar\b.*\bbusiness\b",
]


class NoFreshTopicError(RuntimeError):
    """No fresh, on-topic candidate was found. The caller should skip this cycle."""


@dataclass
class TopicCandidate:
    title: str
    source: str
    source_url: str = ""
    category: str = "technology"
    summary: str = ""
    direct_link: bool = False
    view_count: int = 0
    like_count: int = 0
    published: str = ""
    transcript_snippet: str = ""
    hook: str = ""
    virality_score: float = 0.0
    feasibility_score: float = 0.0
    channel_fit_score: float = 0.0
    composite_score: float = 0.0
    discovered_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict = field(default_factory=dict)

    def compute_composite(self):
        self.composite_score = round(
            self.virality_score * 0.4 +
            self.feasibility_score * 0.3 +
            self.channel_fit_score * 0.3,
            2,
        )


def _text(el) -> str:
    return (el.text or "").strip() if el is not None else ""


def parse_feed(xml_bytes: bytes) -> List[dict]:
    """Parses RSS 2.0 or Atom into dicts with title, link, summary, published."""
    root = ET.fromstring(xml_bytes)
    items = []

    for item in root.iter("item"):
        items.append({
            "title": _text(item.find("title")),
            "link": _text(item.find("link")),
            "summary": _text(item.find(f"{CONTENT_NS}encoded")) or _text(item.find("description")),
            "published": _text(item.find("pubDate")),
            "_element": item,
        })

    for entry in root.iter(f"{ATOM_NS}entry"):
        link = ""
        for link_el in entry.findall(f"{ATOM_NS}link"):
            if link_el.get("rel", "alternate") == "alternate" and link_el.get("href"):
                link = link_el.get("href")
                break
        items.append({
            "title": _text(entry.find(f"{ATOM_NS}title")),
            "link": link,
            "summary": _text(entry.find(f"{ATOM_NS}content")) or _text(entry.find(f"{ATOM_NS}summary")),
            "published": _text(entry.find(f"{ATOM_NS}published")) or _text(entry.find(f"{ATOM_NS}updated")),
            "_element": entry,
        })

    return items


def age_hours(published: str, now: Optional[datetime] = None) -> Optional[float]:
    """Hours since `published` (RFC 822 or ISO 8601). None if it cannot be parsed."""
    if not published:
        return None
    dt = None
    try:
        dt = parsedate_to_datetime(published)
    except Exception:
        try:
            dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return max(0.0, (now - dt).total_seconds() / 3600.0)


def recency_score(hours: Optional[float]) -> float:
    if hours is None:
        return 7.5
    if hours <= 12:
        return 9.2
    if hours <= 24:
        return 8.8
    if hours <= 48:
        return 8.0
    return 7.0


class TrendScout:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.memory = MemoryStore()
        self.pillars = self._load_pillars()
        self.rdb = None
        if not self.dry_run:
            try:
                import redis
                self.rdb = redis.from_url(env("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
            except Exception as e:
                log.warning(f"Redis unavailable ({e}), running in local mode")

    @staticmethod
    def _load_pillars() -> Optional[List[str]]:
        """Pillars the channel covers (configs/series.json). None means all."""
        from agents.script_director import enabled_pillars

        return enabled_pillars()

    # ── Scoring ───────────────────────────────────────────────────────

    @staticmethod
    def _is_pr_or_interview(title: str) -> bool:
        t = title.lower()
        return any(re.search(pat, t) for pat in PR_INTERVIEW_PATTERNS)

    @staticmethod
    def match_pillar(text: str) -> Tuple[Optional[str], int]:
        """Best-matching pillar and its keyword hit count. (None, 0) when nothing matches."""
        t = text.lower()
        best_cat: Optional[str] = None
        max_matches = 0
        for cat, keywords in PILLAR_KEYWORDS.items():
            matches = sum(1 for kw in keywords if re.search(r"\b" + re.escape(kw) + r"\b", t))
            if matches > max_matches:
                max_matches = matches
                best_cat = cat
        return best_cat, max_matches

    def _score_channel_fit(self, text: str, default_category: Optional[str] = None) -> Tuple[float, Optional[str]]:
        """
        (fit score, category). Category is None when the text matches no pillar
        keyword and the source has no pillar of its own; such items are dropped.
        """
        cat, matches = self.match_pillar(text)
        if matches == 0:
            if default_category is None:
                return 0.0, None
            return 5.0, default_category
        return round(min(9.8, 5.5 + matches * 1.2), 1), cat

    # ── Fetching ──────────────────────────────────────────────────────

    @staticmethod
    def _http_get(url: str, timeout: int = 8) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()

    def _candidate_from_item(
        self,
        title: str,
        link: str,
        summary: str,
        published: str,
        source: str,
        default_category: Optional[str],
        direct_link: bool,
        virality: Optional[float] = None,
        fit_text: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> Optional[TopicCandidate]:
        title = (title or "").strip()
        if not title or self._is_pr_or_interview(title):
            return None

        hours = age_hours(published)
        if hours is not None and hours > MAX_AGE_HOURS:
            return None

        fit_score, cat = self._score_channel_fit(fit_text or title, default_category)
        if cat is None:
            return None
        if self.pillars is not None and cat not in self.pillars:
            return None

        cand = TopicCandidate(
            title=title,
            source=source,
            source_url=link or "",
            category=cat,
            summary=summary or "",
            direct_link=direct_link,
            published=published or "",
            virality_score=virality if virality is not None else recency_score(hours),
            feasibility_score=8.5,
            channel_fit_score=fit_score,
            metadata=metadata or {},
        )
        cand.compute_composite()
        return cand

    def fetch_direct_feeds(self) -> List[TopicCandidate]:
        candidates = []
        for url, source, pillar in DIRECT_FEEDS:
            try:
                items = parse_feed(self._http_get(url))
            except Exception as e:
                log.warning(f"Feed '{source}' fetch error: {e}")
                continue
            for item in items[:15]:
                cand = self._candidate_from_item(
                    title=item["title"],
                    link=item["link"],
                    summary=item["summary"],
                    published=item["published"],
                    source=source,
                    default_category=pillar,
                    direct_link=True,
                )
                if cand:
                    candidates.append(cand)
        return candidates

    def fetch_google_news_india(self) -> List[TopicCandidate]:
        candidates = []
        for query, pillar in GOOGLE_NEWS_QUERIES:
            if self.pillars is not None and pillar not in self.pillars:
                continue
            url = f"https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
            try:
                items = parse_feed(self._http_get(url))
            except Exception as e:
                log.warning(f"Google News India RSS fetch error: {e}")
                continue
            for item in items[:6]:
                raw_title = item["title"]
                clean_title = re.sub(r" - [^-]+$", "", raw_title).strip()
                # Google News links are redirect pages, so the article text is
                # usually not fetchable from here; these rank below direct links.
                cand = self._candidate_from_item(
                    title=clean_title,
                    link=item["link"],
                    summary="",
                    published=item["published"],
                    source="google_news_india",
                    default_category=pillar,
                    direct_link=False,
                    metadata={"source_outlet": raw_title.split(" - ")[-1] if " - " in raw_title else "News"},
                )
                if cand:
                    candidates.append(cand)
        return candidates

    def fetch_google_trends_india(self) -> List[TopicCandidate]:
        candidates = []
        try:
            items = parse_feed(self._http_get("https://trends.google.com/trending/rss?geo=IN"))
        except Exception as e:
            log.warning(f"Google Trends India RSS fetch error: {e}")
            return candidates

        for item in items:
            el = item["_element"]
            term = item["title"]
            traffic_str = _text(el.find(f"{TRENDS_NS}approx_traffic")) or "100+"
            traffic_num = int(re.sub(r"[^\d]", "", traffic_str) or 100)

            news_title, news_url, news_snippet = "", "", ""
            news_el = el.find(f"{TRENDS_NS}news_item")
            if news_el is not None:
                news_title = _text(news_el.find(f"{TRENDS_NS}news_item_title"))
                news_url = _text(news_el.find(f"{TRENDS_NS}news_item_url"))
                news_snippet = _text(news_el.find(f"{TRENDS_NS}news_item_snippet"))

            # A bare search term has no article behind it and cannot be grounded.
            if not news_url:
                continue

            cand = self._candidate_from_item(
                title=news_title or term,
                link=news_url,
                summary=news_snippet,
                published=item["published"],
                source="google_trends_india",
                default_category=None,
                direct_link=True,
                virality=min(9.5, 7.5 + (traffic_num / 5000.0)),
                fit_text=f"{term} {news_title}",
                metadata={"traffic": traffic_str, "trend_term": term},
            )
            if cand:
                cand.view_count = traffic_num
                candidates.append(cand)
        return candidates

    # ── Selection ─────────────────────────────────────────────────────

    def discover_live_candidates(self, limit: Optional[int] = None) -> List[TopicCandidate]:
        """
        Fresh, on-topic candidates, best first. Candidates with a direct article
        link come before redirect-only ones because only those can be grounded.
        Raises NoFreshTopicError when there are none.
        """
        log.info("🔍 Scouting live trends across publisher feeds, Google News India & Google Trends India...")
        pool: List[TopicCandidate] = []
        pool.extend(self.fetch_direct_feeds())
        pool.extend(self.fetch_google_trends_india())
        pool.extend(self.fetch_google_news_india())

        fresh: List[TopicCandidate] = []
        seen_titles = set()
        for cand in pool:
            norm = self.memory.normalize_title(cand.title)
            if not norm or norm in seen_titles:
                continue
            seen_titles.add(norm)
            seen, existing = self.memory.is_topic_seen(cand.title)
            if seen:
                log.info(f"⏭ Skipping already covered topic: '{cand.title}' (matched: '{existing}')")
                continue
            fresh.append(cand)

        if not fresh:
            raise NoFreshTopicError(
                f"No fresh on-topic candidates (pool size {len(pool)}). Skipping instead of inventing a topic."
            )

        fresh.sort(key=lambda c: (c.direct_link, c.composite_score), reverse=True)
        log.info(f"🎯 {len(fresh)} fresh candidates. Top: '{fresh[0].title}' ({fresh[0].composite_score}/10, {fresh[0].source})")
        return fresh[:limit] if limit else fresh

    def discover_live_candidate(self) -> TopicCandidate:
        """Best single candidate. Raises NoFreshTopicError when there is none."""
        return self.discover_live_candidates(limit=1)[0]

    def enqueue_topic(self, candidate: TopicCandidate):
        if self.dry_run or not self.rdb:
            log.info(f"[LOCAL] Enqueued topic: '{candidate.title}' (Score: {candidate.composite_score})")
            return
        self.rdb.zadd(TOPIC_QUEUE_KEY, {json.dumps(asdict(candidate)): candidate.composite_score})
        log.info(f"Enqueued to Redis: {candidate.title}")


def main():
    parser = argparse.ArgumentParser(description="TrendScout Discovery Agent")
    parser.add_argument("--dry-run", action="store_true", help="Run without external Redis")
    parser.add_argument("--live", action="store_true", help="Enqueue the top candidate to Redis")
    parser.add_argument("--top", type=int, default=5, help="How many candidates to print")
    args = parser.parse_args()

    scout = TrendScout(dry_run=args.dry_run or not args.live)
    try:
        topics = scout.discover_live_candidates(limit=args.top)
    except NoFreshTopicError as e:
        print(f"No candidate: {e}")
        return
    scout.enqueue_topic(topics[0])
    print("\n--- TrendScout Candidates (best first) ---")
    print(json.dumps([asdict(t) for t in topics], indent=2))


if __name__ == "__main__":
    main()
