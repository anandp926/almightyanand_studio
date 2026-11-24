#!/usr/bin/env python3
"""
script_director.py — Transformative Script Writing Agent

Consumes scored topics from TrendScout's priority queue and produces two
script variants per topic:
  1. 16:9 Long-form (8-12 minutes): Storytelling with retention hooks
  2. 9:16 Shorts/Reels (30-60 seconds): Punchy hook-first format

Uses visual-skills templates for structured shot lists and B-roll cues.
Routes LLM calls through OmniRoute with free-tier fallback chains.
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

import redis
import httpx

# ── Configuration ──────────────────────────────────────────────────

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/2")
OMNIROUTE_URL = os.environ.get("OMNIROUTE_URL", "http://localhost:8080")
MIN_TOPIC_SCORE = 7.0  # Only produce scripts for high-scoring topics
SCRIPTS_PER_DAY_CAP = 4  # Maximum scripts to produce per day

TOPIC_QUEUE_KEY = "topics:scored_queue"
SCRIPT_QUEUE_KEY = "scripts:ready_queue"
PRODUCTION_LOG_KEY = "scripts:production_log"
DAILY_COUNT_KEY_PREFIX = "scripts:daily_count"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SCRIPT-DIR] %(levelname)s %(message)s",
)
log = logging.getLogger("script_director")


# ── Script Templates ───────────────────────────────────────────────

LONG_FORM_SYSTEM_PROMPT = """You are the Almighty Anand Script Director. Write a transformative,
engaging YouTube video script (8-12 minutes when spoken aloud at 150 WPM).

RULES:
1. NEVER copy source material — always add original analysis, humor, or unique perspective.
2. Include retention hooks at these timestamps: 0:00-0:30 (killer hook), 3:00, 6:00, 8:00.
3. The first 30 seconds must contain a curiosity gap or shocking statement.
4. Use conversational Indian-English tone — relatable, witty, Almighty Anand's signature style.
5. Include B-roll cue markers [B-ROLL: description] for visual planning.
6. Include [MUSIC: mood] markers for background music cues.
7. End with a strong CTA (subscribe, comment prompt, affiliate link lead-in).

OUTPUT FORMAT (JSON):
{
  "title": "SEO-optimized title (60 chars max)",
  "hook": "First 2 sentences — the viral hook",
  "script": "Full narration script with [B-ROLL] and [MUSIC] markers",
  "shot_list": ["shot 1 description", "shot 2", ...],
  "tags": ["tag1", "tag2", ...],
  "description_seo": "YouTube description (first 150 chars most important)",
  "thumbnail_text": "Max 5 words for thumbnail overlay",
  "estimated_duration_minutes": 10,
  "retention_hooks": {
    "0:30": "hook text",
    "3:00": "hook text",
    "6:00": "hook text",
    "8:00": "hook text"
  },
  "affiliate_opportunities": ["relevant product/service"]
}"""

SHORTS_SYSTEM_PROMPT = """You are the Almighty Anand Shorts Director. Write a punchy,
hook-first YouTube Short / Instagram Reel script (30-60 seconds when spoken at 160 WPM).

RULES:
1. First 3 seconds = scroll-stopping hook (question, shocking fact, or bold claim).
2. Maximum 150 words. Every word must earn its place.
3. Text overlay markers: [TEXT: overlay text] — max 3 overlays.
4. End with a loop-worthy or cliffhanger moment.
5. Conversational, energetic, Almighty Anand's signature voice.
6. Must work WITHOUT audio (captions carry the story).

OUTPUT FORMAT (JSON):
{
  "title": "Title for Shorts/Reels",
  "hook": "First sentence — the scroll-stopper",
  "script": "Full narration with [TEXT] markers",
  "text_overlays": ["overlay 1", "overlay 2", "overlay 3"],
  "visual_cues": ["visual 1", "visual 2"],
  "hashtags": ["#tag1", "#tag2"],
  "estimated_duration_seconds": 45,
  "cta": "Call to action"
}"""


@dataclass
class ScriptPackage:
    """Complete production package for a single topic."""
    topic_title: str
    topic_score: float
    long_form_script: Optional[dict] = None
    shorts_script: Optional[dict] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: str = "pending"  # pending, scripted, dispatched, produced, published
    production_id: str = ""

    def __post_init__(self):
        if not self.production_id:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            slug = "".join(c if c.isalnum() else "_" for c in self.topic_title[:30]).lower()
            self.production_id = f"{ts}_{slug}"


class ScriptDirector:
    """Consumes trending topics and produces transformative scripts."""

    def __init__(self):
        self.rdb = redis.from_url(REDIS_URL, decode_responses=True)
        self.llm_client = httpx.Client(base_url=OMNIROUTE_URL, timeout=120)
        log.info("ScriptDirector initialized")

    # ── LLM Interaction ────────────────────────────────────────────

    def _call_llm(self, system_prompt: str, user_prompt: str) -> dict:
        """Call LLM via OmniRoute and parse JSON response."""
        try:
            response = self.llm_client.post("/v1/chat/completions", json={
                "model": "auto",  # OmniRoute selects best available model
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.8,
                "max_tokens": 4096,
                "response_format": {"type": "json_object"},
            })
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return json.loads(content)
        except httpx.HTTPStatusError as e:
            log.error(f"LLM API error: {e.response.status_code} — {e.response.text[:200]}")
            raise
        except json.JSONDecodeError as e:
            log.error(f"Failed to parse LLM JSON response: {e}")
            raise

    # ── Script Generation ──────────────────────────────────────────

    def generate_long_form(self, topic: dict) -> dict:
        """Generate a 16:9 long-form script (8-12 min)."""
        user_prompt = f"""Create a transformative video script about:

TOPIC: {topic['title']}
CATEGORY: {topic.get('category', 'general')}
SOURCE CONTEXT: {topic.get('transcript_snippet', 'No transcript available')}
SOURCE METADATA: {json.dumps(topic.get('metadata', {}), indent=2)[:500]}

Remember: This must be TRANSFORMATIVE — add original analysis, Almighty Anand's
unique perspective, humor, and insight. Never just restate the source."""

        log.info(f"  🎬 Generating long-form script for: {topic['title'][:50]}...")
        return self._call_llm(LONG_FORM_SYSTEM_PROMPT, user_prompt)

    def generate_shorts(self, topic: dict, long_form_script: dict) -> dict:
        """Generate a 9:16 Shorts/Reels script based on the long-form."""
        user_prompt = f"""Create a YouTube Short / Instagram Reel based on this long-form topic:

TOPIC: {topic['title']}
KEY HOOK FROM LONG-FORM: {long_form_script.get('hook', '')}
CORE MESSAGE: {long_form_script.get('script', '')[:300]}

Distill the most viral-worthy moment into a 30-60 second punchy script.
This should work as a standalone piece AND drive viewers to the full video."""

        log.info(f"  📱 Generating shorts script for: {topic['title'][:50]}...")
        return self._call_llm(SHORTS_SYSTEM_PROMPT, user_prompt)

    # ── Queue Management ───────────────────────────────────────────

    def pop_top_topic(self) -> Optional[dict]:
        """Pop the highest-scored topic from the queue."""
        results = self.rdb.zrevrangebyscore(
            TOPIC_QUEUE_KEY,
            "+inf",
            MIN_TOPIC_SCORE,
            start=0,
            num=1,
            withscores=True,
        )
        if not results:
            return None

        topic_json, score = results[0]
        self.rdb.zrem(TOPIC_QUEUE_KEY, topic_json)
        topic = json.loads(topic_json)
        topic["_score"] = score
        return topic

    def enqueue_script(self, package: ScriptPackage):
        """Push completed script package to production queue."""
        self.rdb.rpush(SCRIPT_QUEUE_KEY, json.dumps(asdict(package)))
        self.rdb.rpush(PRODUCTION_LOG_KEY, json.dumps({
            "production_id": package.production_id,
            "topic": package.topic_title,
            "score": package.topic_score,
            "status": package.status,
            "created_at": package.created_at,
        }))
        log.info(f"  ✅ Script package enqueued: {package.production_id}")

    def get_daily_count(self) -> int:
        """Get number of scripts produced today."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        count = self.rdb.get(f"{DAILY_COUNT_KEY_PREFIX}:{today}")
        return int(count) if count else 0

    def increment_daily_count(self):
        """Increment today's script counter."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        key = f"{DAILY_COUNT_KEY_PREFIX}:{today}"
        self.rdb.incr(key)
        self.rdb.expire(key, 86400 * 2)  # Expire after 2 days

    # ── Main Production Loop ───────────────────────────────────────

    def produce_one(self) -> bool:
        """Attempt to produce one script package. Returns True if successful."""
        # Check daily cap
        daily_count = self.get_daily_count()
        if daily_count >= SCRIPTS_PER_DAY_CAP:
            log.info(f"Daily script cap reached ({daily_count}/{SCRIPTS_PER_DAY_CAP}). "
                     f"Waiting for tomorrow.")
            return False

        # Pop top topic
        topic = self.pop_top_topic()
        if not topic:
            log.info("No topics above score threshold. Waiting for TrendScout.")
            return False

        log.info(f"📝 Producing script package for: {topic['title'][:60]}... "
                 f"(score: {topic['_score']:.1f})")

        package = ScriptPackage(
            topic_title=topic["title"],
            topic_score=topic["_score"],
        )

        try:
            # Generate long-form first
            package.long_form_script = self.generate_long_form(topic)

            # Generate shorts based on long-form
            package.shorts_script = self.generate_shorts(topic, package.long_form_script)

            package.status = "scripted"
            self.enqueue_script(package)
            self.increment_daily_count()

            log.info(f"🎉 Script package complete: {package.production_id}")
            return True

        except Exception as e:
            log.error(f"Script generation failed for '{topic['title'][:40]}': {e}")
            package.status = "failed"
            return False

    def run(self):
        """Main loop — check for topics and produce scripts."""
        log.info("🎬 ScriptDirector starting up...")
        while True:
            try:
                produced = self.produce_one()
                if not produced:
                    time.sleep(300)  # Wait 5 min before checking again
                else:
                    time.sleep(30)   # Brief pause between productions
            except Exception as e:
                log.exception(f"Production loop error: {e}")
                time.sleep(60)


if __name__ == "__main__":
    director = ScriptDirector()
    director.run()
