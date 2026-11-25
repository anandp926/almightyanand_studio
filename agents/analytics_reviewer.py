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
    playbook_update: dict = field(default_factory=dict)
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

    def update_playbook_weights(self, category: str, avg_avd: float, latest_hook: str = "") -> dict:
        """Mutates configs/viral_playbook.json nightly based on real YouTube AVD."""
        pb_path = STUDIO_ROOT / "configs" / "viral_playbook.json"
        if not pb_path.exists():
            return {"updated": False, "reason": "viral_playbook.json not found"}

        pb = json.loads(pb_path.read_text(encoding="utf-8"))
        pillar = pb.get("pillars", {}).get(category)
        if not pillar:
            return {"updated": False, "reason": f"unknown category '{category}'"}

        changes = []

        # 1. Promote winning hooks (>75% AVD) or decay underperforming priors (<65% AVD)
        priors = pillar.get("hook_priors", [])
        if avg_avd >= 75.0:
            for h in priors:
                h["weight"] = round(min(2.0, h.get("weight", 1.0) * 1.05), 2)
            if latest_hook and not any(h["pattern"] == latest_hook for h in priors):
                priors.append({"pattern": latest_hook, "weight": 1.5})
                changes.append("promoted_winning_hook")
        elif avg_avd < 65.0:
            for h in priors:
                h["weight"] = round(max(0.3, h.get("weight", 1.0) * 0.85), 2)
            changes.append("decayed_hook_weights")

        # Keep top 8 highest-weighted hooks so the file stays clean
        pillar["hook_priors"] = sorted(priors, key=lambda x: x.get("weight", 1.0), reverse=True)[:8]

        # 2. Tune hook cut speed & narrator voice rate on low retention
        if avg_avd < 65.0:
            cur_cut = float(pillar.get("hook_cut_seconds", 2.2))
            pillar["hook_cut_seconds"] = round(max(1.5, cur_cut - 0.2), 2)

            speaker_id = pillar.get("default_speaker", "narrator")
            voice_cfg = pb.get("character_voices", {}).get(speaker_id)
            if voice_cfg:
                cur_rate = int(str(voice_cfg.get("rate", "+8%")).replace("%", "").replace("+", ""))
                new_rate = min(14, cur_rate + 2)
                voice_cfg["rate"] = f"+{new_rate}%" if new_rate >= 0 else f"{new_rate}%"
                changes.append(f"tuned_{speaker_id}_rate_to_{voice_cfg['rate']}")

        pb_path.write_text(json.dumps(pb, indent=2, ensure_ascii=False), encoding="utf-8")
        log.info(f"🧠 Playbook mutated for '{category}' (AVD={avg_avd}%): {changes or ['no_change']}")
        return {"updated": True, "changes": changes}

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

        # Mutate configs/viral_playbook.json based on latest category & AVD
        with self.memory._get_connection() as conn:
            latest = conn.execute(
                "SELECT category, title FROM published_topics ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        latest_cat = latest["category"] if latest and latest["category"] else "technology"
        playbook_update = self.update_playbook_weights(category=latest_cat, avg_avd=avg_avd)

        return DailyReviewReport(
            date=today_str,
            channel_name="Almighty Anand",
            views=views,
            watch_time_hours=round(watch_minutes / 60.0, 2),
            avg_retention_percent=avg_avd,
            active_style=active_style,
            pivot_triggered=pivot_needed,
            pivot_reason=pivot_reason,
            playbook_update=playbook_update,
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
            playbook_update={"updated": False, "reason": "dry_run"},
        )


def main():
    parser = argparse.ArgumentParser(description="AnalyticsReviewer Agent")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    report = AnalyticsReviewer(dry_run=args.dry_run).run_daily_review()
    print(json.dumps(asdict(report), indent=2))


if __name__ == "__main__":
    main()