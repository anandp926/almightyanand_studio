#!/usr/bin/env python3
"""
analytics_reviewer.py — Live YouTube Analytics & Self-Learning Feedback Agent
"""

import argparse
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agents.memory_store import MemoryStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ANALYTICS] %(levelname)s %(message)s",
)
log = logging.getLogger("analytics_reviewer")

STUDIO_ROOT = Path(__file__).resolve().parent.parent
TOKEN_PATH = STUDIO_ROOT / "configs" / "youtube_token.json"


@dataclass
class DailyReviewReport:
    date: str
    channel_name: str
    views: int
    watch_time_hours: float
    avg_retention_percent: float
    active_style: str
    pivot_triggered: bool
    pivot_reason: str
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class AnalyticsReviewer:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.memory = MemoryStore()

    def _get_credentials(self):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        if not TOKEN_PATH.exists():
            raise FileNotFoundError(f"YouTube OAuth token missing at {TOKEN_PATH}")
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH))
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            TOKEN_PATH.write_text(creds.to_json())
        return creds

    def _get_latest_style(self) -> str:
        with self.memory._get_connection() as conn:
            row = conn.execute(
                "SELECT chosen_style FROM daily_plans ORDER BY date DESC LIMIT 1"
            ).fetchone()
            return row["chosen_style"] if row and row["chosen_style"] else "commentary_broll"

    def run_daily_review(self) -> DailyReviewReport:
        if self.dry_run:
            return self.review_dry_run()

        from googleapiclient.discovery import build

        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        start_str = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")
        active_style = self._get_latest_style()
        views = 0
        watch_minutes = 0.0
        avg_avd = 70.0  # neutral default if channel has < 24h analytics lag

        try:
            creds = self._get_credentials()
            yt_analytics = build("youtubeAnalytics", "v2", credentials=creds)
            resp = yt_analytics.reports().query(
                ids="channel==MINE",
                startDate=start_str,
                endDate=today_str,
                metrics="views,estimatedMinutesWatched,averageViewPercentage",
            ).execute()
            rows = resp.get("rows", [])
            if rows:
                views = int(rows[0][0] or 0)
                watch_minutes = float(rows[0][1] or 0.0)
                avg_avd = round(float(rows[0][2] or 0.0), 2)
            log.info(f"📈 Live YouTube Analytics (72h): views={views}, AVD={avg_avd}%")
        except Exception as e:
            log.warning(f"⚠️ YouTube Analytics API unavailable ({e}); falling back to channel statistics.")
            try:
                creds = self._get_credentials()
                yt_data = build("youtube", "v3", credentials=creds)
                ch = yt_data.channels().list(part="statistics", mine=True).execute()
                if ch.get("items"):
                    views = int(ch["items"][0]["statistics"].get("viewCount", 0))
            except Exception as inner:
                log.warning(f"⚠️ Channel statistics fallback failed: {inner}")

        # Persist into SQLite performance_logs so check_pivot_needed() works autonomously
        log_id = f"perf_{today_str.replace('-', '')}_{active_style}"
        with self.memory._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO performance_logs (
                    log_id, date, style_preset, avg_avd, total_views, underperforming_days
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (log_id, today_str, active_style, avg_avd, views, 1 if avg_avd < 65.0 else 0),
            )
            conn.commit()

        pivot_needed, next_style, pivot_reason = self.memory.check_pivot_needed(active_style)
        if pivot_needed:
            log.warning(f"🔄 SELF-LEARNING PIVOT: {pivot_reason} -> Next style: {next_style}")

        return DailyReviewReport(
            date=today_str,
            channel_name="Almighty Anand",
            views=views,
            watch_time_hours=round(watch_minutes / 60.0, 2),
            avg_retention_percent=avg_avd,
            active_style=active_style,
            pivot_triggered=pivot_needed,
            pivot_reason=pivot_reason,
        )

    def review_dry_run(self) -> DailyReviewReport:
        return DailyReviewReport(
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            channel_name="Almighty Anand",
            views=14200,
            watch_time_hours=186.4,
            avg_retention_percent=74.2,
            active_style="commentary_broll",
            pivot_triggered=False,
            pivot_reason="Dry-run mode.",
        )


def main():
    parser = argparse.ArgumentParser(description="AnalyticsReviewer Agent")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    report = AnalyticsReviewer(dry_run=args.dry_run).run_daily_review()
    print(json.dumps(asdict(report), indent=2))


if __name__ == "__main__":
    main()