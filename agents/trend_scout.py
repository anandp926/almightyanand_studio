#!/usr/bin/env python3
"""
trend_scout.py — Trending Topic Discovery Agent

Scrapes trending videos/topics via yt-dlp, RSS feeds, and Google Trends.
Extracts hooks, transcripts, and metadata. Scores by virality × feasibility × channel fit.
Pushes scored topics to Redis priority queue for ScriptDirector.

Runs every 2 hours on the CPU VM.
"""

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

import feedparser
import redis

# ── Configuration ──────────────────────────────────────────────────

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/2")
OMNIROUTE_URL = os.environ.get("OMNIROUTE_URL", "http://localhost:8080")
SCAN_INTERVAL_SECONDS = 7200  # 2 hours

# YouTube categories aligned with Almighty Anand's brand
NICHE_CATEGORIES = ["technology", "finance", "spirituality", "life-hacks", "indian-culture"]

# RSS feeds for niche monitoring
RSS_FEEDS = [
    "https://news.google.com/rss/search?q=technology+trends&hl=en-IN",
    "https://news.google.com/rss/search?q=personal+finance+india&hl=en-IN",
    "https://news.google.com/rss/search?q=hindu+mythology+katha&hl=en-IN",
    "https://www.youtube.com/feeds/videos.xml?channel_id=PLACEHOLDER_CHANNEL_ID",
]

# yt-dlp search queries for competitor analysis
YT_SEARCH_QUERIES = [
    "ytsearch10:trending technology explained 2026",
    "ytsearch10:personal finance tips india 2026",
    "ytsearch10:hindu mythology stories animated",
    "ytsearch10:life hacks viral shorts",
]

TOPIC_QUEUE_KEY = "topics:scored_queue"
SEEN_VIDEOS_KEY = "topics:seen_videos"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [TREND-SCOUT] %(levelname)s %(message)s",
)
log = logging.getLogger("trend_scout")


@dataclass
class TopicCandidate:
    """A discovered trending topic scored for production priority."""
    title: str
    source: str                       # "youtube", "rss", "google_trends"
    source_url: str = ""
    category: str = ""
    view_count: int = 0
    like_count: int = 0
    published: str = ""
    transcript_snippet: str = ""       # First 500 chars of transcript
    hook: str = ""                     # Extracted viral hook
    virality_score: float = 0.0        # 0-10
    feasibility_score: float = 0.0     # 0-10 (can we produce this cheaply?)
    channel_fit_score: float = 0.0     # 0-10
    composite_score: float = 0.0       # Weighted combination
    discovered_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict = field(default_factory=dict)

    def compute_composite(self):
        """Weighted composite: virality × 0.4 + feasibility × 0.3 + fit × 0.3"""
        self.composite_score = round(
            self.virality_score * 0.4 +
            self.feasibility_score * 0.3 +
            self.channel_fit_score * 0.3,
            2
        )


class TrendScout:
    """Discovers, scores, and queues trending topics."""

    def __init__(self):
        self.rdb = redis.from_url(REDIS_URL, decode_responses=True)
        log.info("TrendScout initialized")

    # ── YouTube Scraping ───────────────────────────────────────────

    def scrape_youtube_trends(self) -> list[TopicCandidate]:
        """Use yt-dlp to discover trending videos in target niches."""
        candidates = []

        for query in YT_SEARCH_QUERIES:
            try:
                result = subprocess.run(
                    ["yt-dlp", "--dump-json", "--no-download", "--flat-playlist", query],
                    capture_output=True, text=True, timeout=60,
                )
                if result.returncode != 0:
                    log.warning(f"yt-dlp failed for {query}: {result.stderr[:200]}")
                    continue

                for line in result.stdout.strip().split("\n"):
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    video_id = data.get("id", "")
                    if self.rdb.sismember(SEEN_VIDEOS_KEY, video_id):
                        continue

                    candidate = TopicCandidate(
                        title=data.get("title", "Unknown"),
                        source="youtube",
                        source_url=f"https://youtube.com/watch?v={video_id}",
                        view_count=data.get("view_count", 0) or 0,
                        like_count=data.get("like_count", 0) or 0,
                        published=data.get("upload_date", ""),
                        metadata={
                            "video_id": video_id,
                            "channel": data.get("channel", ""),
                            "duration": data.get("duration", 0),
                            "description": (data.get("description", "") or "")[:300],
                        },
                    )
                    candidates.append(candidate)
                    self.rdb.sadd(SEEN_VIDEOS_KEY, video_id)

            except subprocess.TimeoutExpired:
                log.warning(f"yt-dlp timed out for {query}")
            except Exception as e:
                log.error(f"Error scraping YouTube for {query}: {e}")

        log.info(f"YouTube scan: found {len(candidates)} new videos")
        return candidates

    # ── RSS Feed Scanning ──────────────────────────────────────────

    def scrape_rss_feeds(self) -> list[TopicCandidate]:
        """Parse RSS feeds for trending news topics."""
        candidates = []

        for feed_url in RSS_FEEDS:
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:10]:
                    link = entry.get("link", "")
                    entry_id = entry.get("id", link)
                    if self.rdb.sismember(SEEN_VIDEOS_KEY, entry_id):
                        continue

                    candidate = TopicCandidate(
                        title=entry.get("title", ""),
                        source="rss",
                        source_url=link,
                        published=entry.get("published", ""),
                        category=self._classify_category(entry.get("title", "")),
                        metadata={
                            "feed_url": feed_url,
                            "summary": (entry.get("summary", "") or "")[:300],
                        },
                    )
                    candidates.append(candidate)
                    self.rdb.sadd(SEEN_VIDEOS_KEY, entry_id)

            except Exception as e:
                log.error(f"Error parsing RSS {feed_url}: {e}")

        log.info(f"RSS scan: found {len(candidates)} new topics")
        return candidates

    # ── Transcript Extraction ──────────────────────────────────────

    def extract_transcript(self, video_url: str) -> str:
        """Extract transcript from YouTube video using yt-dlp subtitles."""
        try:
            result = subprocess.run(
                ["yt-dlp", "--write-auto-sub", "--sub-lang", "en",
                 "--skip-download", "--sub-format", "json3",
                 "-o", "/tmp/transcript_%(id)s", video_url],
                capture_output=True, text=True, timeout=30,
            )
            # Find and read the subtitle file
            if result.returncode == 0:
                import glob
                sub_files = glob.glob("/tmp/transcript_*.json3")
                if sub_files:
                    with open(sub_files[0]) as f:
                        subs = json.load(f)
                    text = " ".join(
                        event.get("segs", [{}])[0].get("utf8", "")
                        for event in subs.get("events", [])
                        if event.get("segs")
                    )
                    # Clean up
                    os.remove(sub_files[0])
                    return text[:500]
        except Exception as e:
            log.debug(f"Transcript extraction failed for {video_url}: {e}")
        return ""

    # ── Scoring ────────────────────────────────────────────────────

    def _classify_category(self, title: str) -> str:
        """Simple keyword-based category classification."""
        title_lower = title.lower()
        for cat, keywords in {
            "technology": ["tech", "ai", "software", "gadget", "phone", "app", "code", "programming"],
            "finance": ["money", "invest", "stock", "finance", "budget", "wealth", "crypto", "tax"],
            "spirituality": ["katha", "temple", "hindu", "mythology", "god", "mantra", "vedic", "dharma"],
            "life-hacks": ["hack", "trick", "tip", "diy", "productivity", "routine"],
        }.items():
            if any(kw in title_lower for kw in keywords):
                return cat
        return "general"

    def score_candidate(self, candidate: TopicCandidate) -> TopicCandidate:
        """Score a candidate topic on virality, feasibility, and channel fit."""

        # ── Virality (0-10): based on engagement signals
        if candidate.source == "youtube":
            views = candidate.view_count
            if views > 1_000_000:
                candidate.virality_score = 9.5
            elif views > 500_000:
                candidate.virality_score = 8.0
            elif views > 100_000:
                candidate.virality_score = 7.0
            elif views > 50_000:
                candidate.virality_score = 6.0
            elif views > 10_000:
                candidate.virality_score = 5.0
            else:
                candidate.virality_score = 3.0

            # Boost for high like ratio
            if candidate.view_count > 0:
                like_ratio = candidate.like_count / candidate.view_count
                if like_ratio > 0.05:
                    candidate.virality_score = min(10, candidate.virality_score + 1.0)
        else:
            # RSS/Trends get a baseline
            candidate.virality_score = 5.0

        # ── Feasibility (0-10): can we produce this cost-effectively?
        candidate.feasibility_score = 7.0  # Default; adjust based on complexity
        duration = candidate.metadata.get("duration", 0)
        if duration and duration > 1800:  # Very long source = harder to transform
            candidate.feasibility_score -= 1.0

        # ── Channel Fit (0-10)
        category = candidate.category or self._classify_category(candidate.title)
        candidate.category = category
        fit_scores = {
            "technology": 9.0,
            "finance": 8.5,
            "spirituality": 9.0,
            "life-hacks": 7.5,
            "general": 4.0,
        }
        candidate.channel_fit_score = fit_scores.get(category, 4.0)

        candidate.compute_composite()
        return candidate

    # ── Queue Management ───────────────────────────────────────────

    def enqueue_topic(self, candidate: TopicCandidate):
        """Push scored topic to Redis sorted set (priority queue)."""
        self.rdb.zadd(
            TOPIC_QUEUE_KEY,
            {json.dumps(asdict(candidate)): candidate.composite_score},
        )

    def get_queue_stats(self) -> dict:
        """Return queue statistics."""
        total = self.rdb.zcard(TOPIC_QUEUE_KEY)
        top_score = 0
        if total > 0:
            top = self.rdb.zrevrange(TOPIC_QUEUE_KEY, 0, 0, withscores=True)
            if top:
                top_score = top[0][1]
        return {"total_queued": total, "top_score": top_score}

    # ── Main Scan Loop ─────────────────────────────────────────────

    def scan_once(self):
        """Run a single scan cycle."""
        log.info("═" * 60)
        log.info("Starting trend scan cycle...")

        # Gather candidates from all sources
        candidates = []
        candidates.extend(self.scrape_youtube_trends())
        candidates.extend(self.scrape_rss_feeds())

        # Score and enqueue
        scored = 0
        for candidate in candidates:
            candidate = self.score_candidate(candidate)

            # Only enqueue if composite score meets threshold
            if candidate.composite_score >= 5.5:
                # Try to extract transcript for top YouTube candidates
                if (candidate.source == "youtube"
                        and candidate.composite_score >= 7.0
                        and candidate.source_url):
                    candidate.transcript_snippet = self.extract_transcript(candidate.source_url)

                self.enqueue_topic(candidate)
                scored += 1
                log.info(f"  ✅ [{candidate.composite_score:.1f}] {candidate.title[:60]}...")
            else:
                log.debug(f"  ⏭ [{candidate.composite_score:.1f}] {candidate.title[:60]}... (below threshold)")

        stats = self.get_queue_stats()
        log.info(f"Scan complete: {len(candidates)} found, {scored} enqueued. "
                 f"Queue: {stats['total_queued']} topics (top score: {stats['top_score']:.1f})")

    def run(self):
        """Run the scout on a loop."""
        log.info("🔍 TrendScout starting up...")
        while True:
            try:
                self.scan_once()
            except Exception as e:
                log.exception(f"Scan cycle failed: {e}")
            log.info(f"💤 Sleeping {SCAN_INTERVAL_SECONDS}s until next scan...")
            time.sleep(SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    scout = TrendScout()
    scout.run()
