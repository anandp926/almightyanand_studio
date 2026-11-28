#!/usr/bin/env python3
"""
analytics_reviewer.py — YouTube Analytics & Self-Learning Feedback Agent

Learns only from real, per-video data:

  - Metrics are pulled per video (not channel-wide) for videos published 2–10
    days ago, since YouTube Analytics lags by a couple of days.
  - A video counts as signal only once it has ANALYTICS_MIN_VIEWS views.
  - If the API is unavailable or there is no data yet, nothing is recorded and
    nothing is changed. There is no default AVD.
  - Style performance is logged per publish-day cohort, so the 3-day pivot
    rule compares three different days of videos.
  - Playbook changes are applied once per video, to that video's own pillar,
    and are written to memory/playbook_state.json (configs/ stays untouched).
  - The retention curve of each mature video shows WHERE viewers leave (hook,
    middle or end), and the fix is aimed there: a hook drop decays the hook
    priors, a middle drop shortens the script and quickens the voice, an end
    drop shortens the script.
"""

import argparse
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from agents.memory_store import MemoryStore
from agents.playbook import load_playbook, save_learned_state
from agents.settings import env_float, env_int

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ANALYTICS] %(levelname)s %(message)s",
)
log = logging.getLogger("analytics_reviewer")

REVIEW_MIN_AGE_DAYS = 2     # younger videos have no reliable analytics yet
REVIEW_MAX_AGE_DAYS = 10
COHORT_AGE_DAYS = 3         # a publish-day cohort is judged once it is this old
HIGH_AVD = 75.0
LOW_AVD = 65.0

# Retention-curve zones as fractions of the video, and the share of viewers
# that must survive each zone. These are starting heuristics, not YouTube numbers.
ZONE_POINTS = {"start": 0.02, "after_hook": 0.15, "late": 0.60, "finish": 0.95}
HOOK_HOLD_MIN = 0.70
MID_HOLD_MIN = 0.75
END_HOLD_MIN = 0.75
MIN_SCRIPT_WORDS = 55


@dataclass
class DailyReviewReport:
    date: str
    channel_name: str
    data_available: bool
    videos_reviewed: int
    views: int
    watch_time_hours: float
    avg_retention_percent: Optional[float]
    active_style: str
    pivot_triggered: bool
    pivot_reason: str
    playbook_update: dict = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def weighted_avd(videos: List[dict]) -> Optional[float]:
    """Views-weighted average view percentage. None when there are no views."""
    total_views = sum(v["views"] for v in videos)
    if total_views <= 0:
        return None
    return round(sum(v["avd_percent"] * v["views"] for v in videos) / total_views, 2)


def summarize_retention(curve: List[tuple]) -> Optional[dict]:
    """
    Reduces an audience-retention curve [(elapsed ratio, watch ratio), ...] to
    the share of viewers kept through each zone and the zone that loses most.
    Returns None for an unusable curve.
    """
    points = sorted((float(x), float(y)) for x, y in curve if x is not None and y is not None)
    if len(points) < 10:
        return None

    def value_at(target: float) -> float:
        return min(points, key=lambda p: abs(p[0] - target))[1]

    start, after_hook = value_at(ZONE_POINTS["start"]), value_at(ZONE_POINTS["after_hook"])
    late, finish = value_at(ZONE_POINTS["late"]), value_at(ZONE_POINTS["finish"])
    if start <= 0 or after_hook <= 0 or late <= 0:
        return None

    hook_hold = round(min(1.5, after_hook / start), 3)
    mid_hold = round(min(1.5, late / after_hook), 3)
    end_hold = round(min(1.5, finish / late), 3)
    if hook_hold < HOOK_HOLD_MIN:
        zone = "hook"
    elif mid_hold < MID_HOLD_MIN:
        zone = "middle"
    elif end_hold < END_HOLD_MIN:
        zone = "end"
    else:
        zone = "none"
    return {"hook_hold": hook_hold, "mid_hold": mid_hold, "end_hold": end_hold, "drop_zone": zone}


def dominant_zone(videos: List[dict]) -> Optional[str]:
    """Most common drop zone among videos that have one. None when no curve was available."""
    zones = [v.get("drop_zone") for v in videos if v.get("drop_zone")]
    if not zones:
        return None
    return max(sorted(set(zones)), key=zones.count)


def _parse_rate(rate: str) -> int:
    try:
        return int(str(rate).replace("%", "").replace("+", "").strip())
    except ValueError:
        return 8


class AnalyticsReviewer:
    def __init__(self, dry_run: bool = False, memory: Optional[MemoryStore] = None):
        self.dry_run = dry_run
        self.memory = memory or MemoryStore()
        self.min_views = env_int("ANALYTICS_MIN_VIEWS", 100)
        self.min_avd = env_float("PIVOT_MIN_AVD", LOW_AVD)

    # ── YouTube Analytics ─────────────────────────────────────────────

    def fetch_video_metrics(self, videos: List[dict]) -> Dict[str, dict]:
        """
        {video_id: {views, watch_minutes, avd_percent}} for the given videos.
        Raises on API failure; the caller treats that as "no data".
        """
        from googleapiclient.discovery import build

        from agents.publisher import load_youtube_credentials

        yt_analytics = build("youtubeAnalytics", "v2", credentials=load_youtube_credentials())
        start = min(v["published_at"] for v in videos)[:10]
        end = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        ids = [v["video_id"] for v in videos]

        metrics: Dict[str, dict] = {}
        for i in range(0, len(ids), 200):
            batch = ids[i:i + 200]
            resp = yt_analytics.reports().query(
                ids="channel==MINE",
                startDate=start,
                endDate=end,
                metrics="views,estimatedMinutesWatched,averageViewPercentage",
                dimensions="video",
                filters="video==" + ",".join(batch),
                sort="-views",
                maxResults=200,
            ).execute()
            for row in resp.get("rows", []) or []:
                metrics[row[0]] = {
                    "views": int(row[1] or 0),
                    "watch_minutes": float(row[2] or 0.0),
                    "avd_percent": round(float(row[3] or 0.0), 2),
                }
        return metrics

    def fetch_retention_curve(self, video_id: str) -> List[tuple]:
        """[(elapsed ratio, audience watch ratio)] for one video. Raises on API failure."""
        from googleapiclient.discovery import build

        from agents.publisher import load_youtube_credentials

        yt_analytics = build("youtubeAnalytics", "v2", credentials=load_youtube_credentials())
        resp = yt_analytics.reports().query(
            ids="channel==MINE",
            startDate="2020-01-01",
            endDate=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            metrics="audienceWatchRatio",
            dimensions="elapsedVideoTimeRatio",
            filters=f"video=={video_id}",
        ).execute()
        return [(row[0], row[1]) for row in resp.get("rows", []) or []]

    # ── Playbook learning ─────────────────────────────────────────────

    def update_playbook_weights(
        self,
        category: str,
        avg_avd: float,
        winning_hook: str = "",
        drop_zone: Optional[str] = None,
    ) -> dict:
        """
        Adjusts one pillar from a measured AVD and saves the result to the
        runtime overlay.

        High AVD: boost the hook priors; `winning_hook` (the hook line of a
        high-retention video) joins them.
        Low AVD: the fix depends on where viewers left (`drop_zone`):
          - "hook"   : decay the hook priors, cut the hook faster.
          - "middle" : shorten the script, quicken the default voice.
          - "end"    : shorten the script.
          - "none"   : nothing; the curve shows no single weak zone.
          - None     : no curve was available; treat as a hook problem.
        """
        pb = load_playbook()
        pillar = pb.get("pillars", {}).get(category)
        if not pillar:
            return {"updated": False, "reason": f"unknown category '{category}'"}

        changes = []
        priors = pillar.get("hook_priors", [])

        if avg_avd >= HIGH_AVD:
            for h in priors:
                h["weight"] = round(min(2.0, h.get("weight", 1.0) * 1.05), 2)
            changes.append("boosted_hook_weights")
            if winning_hook and not any(h["pattern"] == winning_hook for h in priors):
                priors.append({"pattern": winning_hook, "weight": 1.5})
                changes.append("promoted_winning_hook")
        elif avg_avd < LOW_AVD:
            if drop_zone in (None, "hook"):
                for h in priors:
                    h["weight"] = round(max(0.3, h.get("weight", 1.0) * 0.85), 2)
                changes.append("decayed_hook_weights")
                cur_cut = float(pillar.get("hook_cut_seconds", 2.2))
                pillar["hook_cut_seconds"] = round(max(1.5, cur_cut - 0.2), 2)

            if drop_zone in ("middle", "end"):
                words = int(pillar.get("script_words_target", 80))
                new_words = max(MIN_SCRIPT_WORDS, words - 5)
                if new_words != words:
                    pillar["script_words_target"] = new_words
                    changes.append(f"script_words_target_to_{new_words}")

            if drop_zone == "middle":
                speaker_id = pillar.get("default_speaker", "narrator")
                voice_cfg = pb.get("character_voices", {}).get(speaker_id)
                if voice_cfg:
                    new_rate = min(14, _parse_rate(voice_cfg.get("rate", "+8%")) + 2)
                    voice_cfg["rate"] = f"+{new_rate}%" if new_rate >= 0 else f"{new_rate}%"
                    voice_cfg["pace"] = round(min(1.2, float(voice_cfg.get("pace", 1.05)) + 0.02), 2)
                    changes.append(f"quickened_{speaker_id}_to_pace_{voice_cfg['pace']}")

        # Keep the 8 highest-weighted hooks
        pillar["hook_priors"] = sorted(priors, key=lambda x: x.get("weight", 1.0), reverse=True)[:8]

        if not changes:
            reason = (
                f"AVD {avg_avd}% is low but the retention curve shows no single weak zone"
                if avg_avd < LOW_AVD else f"AVD {avg_avd}% is in the neutral band"
            )
            return {"updated": False, "reason": reason}

        save_learned_state(pb)
        log.info(f"🧠 Playbook updated for '{category}' (AVD={avg_avd}%, drop zone={drop_zone}): {changes}")
        return {"updated": True, "changes": changes, "avd": avg_avd, "drop_zone": drop_zone}

    # ── Daily review ──────────────────────────────────────────────────

    def run_daily_review(self) -> DailyReviewReport:
        if self.dry_run:
            return self.review_dry_run()

        now = datetime.now(timezone.utc)
        today_str = now.strftime("%Y-%m-%d")
        active_style = self.memory.get_latest_style()

        def no_data(reason: str) -> DailyReviewReport:
            log.info(f"📭 No analytics signal: {reason} Nothing recorded, nothing changed.")
            return DailyReviewReport(
                date=today_str,
                channel_name="Almighty Anand",
                data_available=False,
                videos_reviewed=0,
                views=0,
                watch_time_hours=0.0,
                avg_retention_percent=None,
                active_style=active_style,
                pivot_triggered=False,
                pivot_reason="No data.",
                playbook_update={"updated": False, "reason": "no_data"},
                notes=[reason],
            )

        videos = self.memory.get_videos_for_review(REVIEW_MIN_AGE_DAYS, REVIEW_MAX_AGE_DAYS)
        if not videos:
            return no_data(f"No videos published {REVIEW_MIN_AGE_DAYS}–{REVIEW_MAX_AGE_DAYS} days ago.")

        try:
            metrics = self.fetch_video_metrics(videos)
        except Exception as e:
            log.warning(f"⚠️ YouTube Analytics API unavailable: {e}")
            return no_data(f"YouTube Analytics API unavailable ({e}).")

        measured: List[dict] = []
        for video in videos:
            m = metrics.get(video["video_id"])
            if not m:
                continue
            self.memory.update_video_metrics(video["video_id"], m["views"], m["watch_minutes"], m["avd_percent"])
            video.update(m)
            if m["views"] >= self.min_views:
                measured.append(video)

        if not measured:
            return no_data(f"No reviewed video has reached {self.min_views} views yet.")

        # 1. Style performance, one row per (publish day, style) cohort that is old enough
        cohort_cutoff = (now - timedelta(days=COHORT_AGE_DAYS)).isoformat()
        cohorts: Dict[tuple, List[dict]] = defaultdict(list)
        for video in measured:
            if video["published_at"] <= cohort_cutoff:
                cohorts[(video["published_at"][:10], video["style"] or "commentary_broll")].append(video)
        for (cohort_date, style), vids in cohorts.items():
            avd = weighted_avd(vids)
            if avd is not None:
                self.memory.record_style_performance(
                    cohort_date, style, avd, sum(v["views"] for v in vids), self.min_avd
                )

        pivot_needed, next_style, pivot_reason = self.memory.check_pivot_needed(active_style, self.min_avd)
        if pivot_needed:
            log.warning(f"🔄 SELF-LEARNING PIVOT: {pivot_reason} -> Next style: {next_style}")

        # 2. Playbook: each mature video is applied once, to its own pillar
        pending = [v for v in measured if v["published_at"] <= cohort_cutoff and not v.get("playbook_applied")]
        for video in pending:
            try:
                summary = summarize_retention(self.fetch_retention_curve(video["video_id"]))
            except Exception as e:
                log.info(f"No retention curve for {video['video_id']}: {e}")
                summary = None
            if summary:
                self.memory.update_video_retention(video["video_id"], **summary)
                video.update(summary)
            else:
                video["drop_zone"] = None

        by_category: Dict[str, List[dict]] = defaultdict(list)
        for video in pending:
            by_category[video["category"] or "technology"].append(video)

        playbook_updates = {}
        for category, vids in by_category.items():
            avd = weighted_avd(vids)
            if avd is None:
                continue
            best = max(vids, key=lambda v: v["avd_percent"])
            winning_hook = best["hook"] if best["avd_percent"] >= HIGH_AVD and best.get("hook") else ""
            weak = [v for v in vids if v["avd_percent"] < LOW_AVD]
            playbook_updates[category] = self.update_playbook_weights(
                category, avd, winning_hook, drop_zone=dominant_zone(weak)
            )
        self.memory.mark_playbook_applied([v["video_id"] for v in pending])

        total_views = sum(v["views"] for v in measured)
        overall_avd = weighted_avd(measured)
        log.info(f"📈 Reviewed {len(measured)} videos: views={total_views}, AVD={overall_avd}%")

        return DailyReviewReport(
            date=today_str,
            channel_name="Almighty Anand",
            data_available=True,
            videos_reviewed=len(measured),
            views=total_views,
            watch_time_hours=round(sum(v["watch_minutes"] for v in measured) / 60.0, 2),
            avg_retention_percent=overall_avd,
            active_style=active_style,
            pivot_triggered=pivot_needed,
            pivot_reason=pivot_reason,
            playbook_update=playbook_updates or {"updated": False, "reason": "no mature videos pending"},
        )

    def review_dry_run(self) -> DailyReviewReport:
        return DailyReviewReport(
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            channel_name="Almighty Anand",
            data_available=False,
            videos_reviewed=0,
            views=0,
            watch_time_hours=0.0,
            avg_retention_percent=None,
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
