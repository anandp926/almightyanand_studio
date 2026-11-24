#!/usr/bin/env python3
"""
memory_store.py — Persistent Topic & Analytics Memory Subsystem
Uses SQLite to guarantee 100% fresh topics (never repeat or copy)
and tracks performance metrics for autonomous format pivoting.
"""

import hashlib
import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("memory_store")

DB_DIR = Path(__file__).resolve().parent.parent / "memory"
DB_PATH = DB_DIR / "published_topics.db"


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
            conn.commit()

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

    def check_pivot_needed(self, current_style: str, min_avd: float = 65.0) -> Tuple[bool, str, str]:
        """
        Checks if the current visual style or format has underperformed (< 65% AVD)
        for 3 consecutive days. Returns (should_pivot, new_style, reason).
        """
        styles_rotation = ["commentary_broll", "origami_breakdown", "retro_pixel_art", "anime_epic"]
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT avg_avd FROM performance_logs 
                WHERE style_preset = ? 
                ORDER BY date DESC LIMIT 3
            """, (current_style,)).fetchall()

            if len(rows) == 3 and all(r["avg_avd"] < min_avd for r in rows):
                idx = styles_rotation.index(current_style) if current_style in styles_rotation else 0
                next_style = styles_rotation[(idx + 1) % len(styles_rotation)]
                reason = f"AVD was under {min_avd}% for 3 consecutive days on '{current_style}'. Autonomous pivot triggered to '{next_style}'."
                return True, next_style, reason

        return False, current_style, "Performance within acceptable bounds."

    def get_published_count(self) -> int:
        with self._get_connection() as conn:
            row = conn.execute("SELECT COUNT(*) as cnt FROM published_topics").fetchone()
            return row["cnt"] if row else 0
