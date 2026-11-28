#!/usr/bin/env python3
"""
memory_store.py — Persistent Topic & Analytics Memory Subsystem

SQLite store for:
  - topics already covered (fuzzy de-duplication by title),
  - the daily plan and active visual style,
  - one row per published video, so analytics can be attributed per video,
  - per-style performance logs that drive the autonomous style pivot.
"""

import hashlib
import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple

log = logging.getLogger("memory_store")

DB_DIR = Path(__file__).resolve().parent.parent / "memory"
DB_PATH = DB_DIR / "published_topics.db"

STYLES_ROTATION = ["commentary_broll", "origami_breakdown", "retro_pixel_art", "anime_epic"]


class MemoryStore:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS published_topics (
                    topic_id TEXT PRIMARY KEY,
                    topic_hash TEXT UNIQUE,
                    title TEXT NOT NULL,
                    normalized_title TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_url TEXT,
                    category TEXT NOT NULL,
                    angle TEXT,
                    visual_style TEXT,
                    status TEXT DEFAULT 'planned',
                    published_date TEXT,
                    views INTEGER DEFAULT 0,
                    avd_percent REAL DEFAULT 0.0,
                    subscribers_gained INTEGER DEFAULT 0,
                    revenue_inr REAL DEFAULT 0.0,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_plans (
                    plan_id TEXT PRIMARY KEY,
                    date TEXT UNIQUE,
                    longform_topic TEXT,
                    shorts_topics TEXT,
                    chosen_style TEXT,
                    pivot_reason TEXT,
                    status TEXT DEFAULT 'active',
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS performance_logs (
                    log_id TEXT PRIMARY KEY,
                    date TEXT NOT NULL,
                    style_preset TEXT NOT NULL,
                    avg_avd REAL,
                    total_views INTEGER,
                    underperforming_days INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS published_videos (
                    video_id TEXT PRIMARY KEY,
                    production_id TEXT,
                    title TEXT,
                    topic TEXT,
                    category TEXT,
                    style TEXT,
                    hook TEXT,
                    published_at TEXT NOT NULL,
                    views INTEGER DEFAULT 0,
                    watch_minutes REAL DEFAULT 0.0,
                    avd_percent REAL,
                    last_checked TEXT,
                    playbook_applied INTEGER DEFAULT 0
                )
            """)
            # Columns added after the first release; existing databases are upgraded in place.
            self._ensure_columns(conn, "published_videos", {
                "series": "TEXT",
                "word_count": "INTEGER",
                "voice_provider": "TEXT",
                "speaker": "TEXT",
                "pace": "REAL",
                "duration_s": "REAL",
                "hook_hold": "REAL",
                "mid_hold": "REAL",
                "end_hold": "REAL",
                "drop_zone": "TEXT",
                "format": "TEXT",
                "has_creator_take": "INTEGER DEFAULT 0",
            })
            conn.commit()

    @staticmethod
    def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict):
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, sql_type in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")

    # ── Topics ────────────────────────────────────────────────────────

    @staticmethod
    def normalize_title(title: str) -> str:
        t = title.lower()
        t = re.sub(r"[^\w\s]", " ", t)
        t = re.sub(r"\s+", " ", t).strip()
        return t

    def is_topic_seen(self, title: str, threshold: float = 0.65) -> Tuple[bool, Optional[str]]:
        norm = self.normalize_title(title)
        tokens = set(norm.split())

        with self._get_connection() as conn:
            rows = conn.execute("SELECT title, normalized_title FROM published_topics").fetchall()
            for row in rows:
                existing_norm = row["normalized_title"]
                if norm == existing_norm:
                    return True, row["title"]

                existing_tokens = set(existing_norm.split())
                if not tokens or not existing_tokens:
                    continue

                intersection = len(tokens & existing_tokens)
                union = len(tokens | existing_tokens)
                jaccard = intersection / union if union > 0 else 0.0
                if jaccard >= threshold:
                    return True, row["title"]

        return False, None

    def record_topic(
        self,
        title: str,
        source: str,
        source_url: str = "",
        category: str = "technology",
        angle: str = "",
        visual_style: str = "commentary_broll",
        status: str = "planned",
    ) -> str:
        norm = self.normalize_title(title)
        topic_hash = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]
        topic_id = f"topic_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{topic_hash[:6]}"
        now = datetime.now(timezone.utc).isoformat()

        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO published_topics (
                    topic_id, topic_hash, title, normalized_title, source,
                    source_url, category, angle, visual_style, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                topic_id, topic_hash, title, norm, source,
                source_url, category, angle, visual_style, status, now
            ))
            conn.commit()
        return topic_id

    def get_published_count(self) -> int:
        with self._get_connection() as conn:
            row = conn.execute("SELECT COUNT(*) as cnt FROM published_topics").fetchone()
            return row["cnt"] if row else 0

    # ── Daily plans & style ───────────────────────────────────────────

    def record_daily_plan(
        self,
        plan_date: str,
        longform_topic: str,
        shorts_topics: List[str],
        chosen_style: str,
        pivot_reason: str = "",
    ) -> str:
        plan_id = f"plan_{plan_date.replace('-', '')}"
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO daily_plans (
                    plan_id, date, longform_topic, shorts_topics, chosen_style,
                    pivot_reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                plan_id, plan_date, longform_topic, json.dumps(shorts_topics),
                chosen_style, pivot_reason, now
            ))
            conn.commit()
        return plan_id

    def get_latest_style(self, default: str = "commentary_broll") -> str:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT chosen_style FROM daily_plans ORDER BY date DESC LIMIT 1"
            ).fetchone()
        return row["chosen_style"] if row and row["chosen_style"] else default

    def record_style_performance(self, cohort_date: str, style: str, avg_avd: float, total_views: int, min_avd: float = 65.0):
        """One row per (publish date, style). Re-measuring the same cohort overwrites its row."""
        log_id = f"perf_{cohort_date.replace('-', '')}_{style}"
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO performance_logs (
                    log_id, date, style_preset, avg_avd, total_views, underperforming_days
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (log_id, cohort_date, style, avg_avd, total_views, 1 if avg_avd < min_avd else 0),
            )
            conn.commit()

    def check_pivot_needed(self, current_style: str, min_avd: float = 65.0) -> Tuple[bool, str, str]:
        """
        True when the three most recent measured cohorts of `current_style` all
        came in under `min_avd`. Returns (should_pivot, new_style, reason).
        """
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT avg_avd FROM performance_logs
                WHERE style_preset = ?
                ORDER BY date DESC LIMIT 3
            """, (current_style,)).fetchall()

            if len(rows) == 3 and all(r["avg_avd"] is not None and r["avg_avd"] < min_avd for r in rows):
                idx = STYLES_ROTATION.index(current_style) if current_style in STYLES_ROTATION else 0
                next_style = STYLES_ROTATION[(idx + 1) % len(STYLES_ROTATION)]
                reason = f"AVD was under {min_avd}% for 3 consecutive days on '{current_style}'. Autonomous pivot triggered to '{next_style}'."
                return True, next_style, reason

        return False, current_style, "Performance within acceptable bounds."

    # ── Published videos (per-video analytics attribution) ────────────

    def record_published_video(
        self,
        video_id: str,
        production_id: str = "",
        title: str = "",
        topic: str = "",
        category: str = "technology",
        style: str = "commentary_broll",
        hook: str = "",
        published_at: Optional[str] = None,
        series: str = "",
        word_count: Optional[int] = None,
        voice_provider: str = "",
        speaker: str = "",
        pace: Optional[float] = None,
        duration_s: Optional[float] = None,
        format: str = "",
        has_creator_take: bool = False,
    ):
        """
        One row per upload, with what the video was made of, so retention can be
        compared like with like. `published_at` is the go-live time (in the
        future for scheduled uploads).
        """
        published_at = published_at or datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO published_videos (
                    video_id, production_id, title, topic, category, style, hook, published_at,
                    series, word_count, voice_provider, speaker, pace, duration_s, format, has_creator_take
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (video_id, production_id, title, topic, category, style, hook, published_at,
                 series, word_count, voice_provider, speaker, pace, duration_s, format, int(has_creator_take)),
            )
            conn.commit()

    def update_video_retention(self, video_id: str, hook_hold: float, mid_hold: float, end_hold: float, drop_zone: str):
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE published_videos SET hook_hold = ?, mid_hold = ?, end_hold = ?, drop_zone = ? WHERE video_id = ?",
                (hook_hold, mid_hold, end_hold, drop_zone, video_id),
            )
            conn.commit()

    def format_performance(self, min_views: int = 100) -> List[dict]:
        """Views-weighted retention per format, for videos with enough views. Best first."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT COALESCE(NULLIF(format, ''), 'unknown') AS format,
                       COUNT(*) AS videos,
                       SUM(views) AS views,
                       ROUND(SUM(avd_percent * views) / SUM(views), 1) AS avd_percent
                FROM published_videos
                WHERE views >= ? AND avd_percent IS NOT NULL
                GROUP BY 1
                ORDER BY avd_percent DESC
                """,
                (min_views,),
            ).fetchall()
        return [dict(r) for r in rows]

    def count_series_episodes(self, series: str) -> int:
        with self._get_connection() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM published_videos WHERE series = ?", (series,)).fetchone()
        return row["cnt"] if row else 0

    def get_videos_for_review(self, min_age_days: int = 2, max_age_days: int = 10) -> List[dict]:
        """Videos old enough for YouTube Analytics to have data, and recent enough to matter."""
        now = datetime.now(timezone.utc)
        newest = (now - timedelta(days=min_age_days)).isoformat()
        oldest = (now - timedelta(days=max_age_days)).isoformat()
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM published_videos
                WHERE published_at <= ? AND published_at >= ?
                ORDER BY published_at ASC
                """,
                (newest, oldest),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_video_metrics(self, video_id: str, views: int, watch_minutes: float, avd_percent: float):
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE published_videos
                SET views = ?, watch_minutes = ?, avd_percent = ?, last_checked = ?
                WHERE video_id = ?
                """,
                (views, watch_minutes, avd_percent, datetime.now(timezone.utc).isoformat(), video_id),
            )
            conn.commit()

    def mark_playbook_applied(self, video_ids: List[str]):
        if not video_ids:
            return
        with self._get_connection() as conn:
            conn.executemany(
                "UPDATE published_videos SET playbook_applied = 1 WHERE video_id = ?",
                [(v,) for v in video_ids],
            )
            conn.commit()
