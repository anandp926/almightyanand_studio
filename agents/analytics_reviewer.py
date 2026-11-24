#!/usr/bin/env python3
"""
analytics_reviewer.py — Daily Channel Analytics & Self-Improvement Agent

Daily cron at 06:00 UTC: Pulls YouTube Analytics API data, tracks performance
metrics, feeds insights back into prompt weights for ScriptDirector, and
generates actionable daily reports.

Monitors:
  - Retention curves, CTR, impressions, views
  - Subscriber delta and revenue
  - Copyright claims and policy strikes
  - Budget burn rate vs. limits
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Optional

import redis

# ── Configuration ──────────────────────────────────────────────────

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/2")
REPORTS_DIR = os.environ.get("REPORTS_DIR", "/app/data/reports")

ANALYTICS_KEY_PREFIX = "analytics"
PROMPT_WEIGHTS_KEY = "prompt:weights"
BUDGET_TRACKER_KEY = "budget:daily_spend"

# Budget limits
MONTHLY_BUDGET = 100.0
DAILY_BUDGET_SOFT = 3.33     # $100/30 days
BURN_RATE_ALERT_DAYS = 3     # Alert if over budget for N consecutive days

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ANALYTICS] %(levelname)s %(message)s",
)
log = logging.getLogger("analytics_reviewer")


@dataclass
class DailyMetrics:
    """Channel metrics for a single day."""
    date: str
    views: int = 0
    watch_time_hours: float = 0.0
    subscribers_gained: int = 0
    subscribers_lost: int = 0
    subscriber_delta: int = 0
    impressions: int = 0
    ctr_percent: float = 0.0
    avg_view_duration_seconds: float = 0.0
    avg_retention_percent: float = 0.0
    revenue_usd: float = 0.0
    top_videos: list = field(default_factory=list)
    copyright_claims: int = 0
    community_strikes: int = 0


@dataclass
class VideoMetrics:
    """Per-video performance metrics."""
    video_id: str
    title: str
    views: int = 0
    ctr_percent: float = 0.0
    avg_retention_percent: float = 0.0
    avg_view_duration_seconds: float = 0.0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    impressions: int = 0
    published_at: str = ""


@dataclass
class DailyReport:
    """Generated daily report with insights and recommendations."""
    date: str
    metrics: Optional[DailyMetrics] = None
    budget_status: dict = field(default_factory=dict)
    insights: list = field(default_factory=list)
    recommendations: list = field(default_factory=list)
    prompt_weight_updates: dict = field(default_factory=dict)
    alerts: list = field(default_factory=list)
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class AnalyticsReviewer:
    """Daily analytics reviewer and self-improvement agent."""

    def __init__(self):
        self.rdb = redis.from_url(REDIS_URL, decode_responses=True)
        self.youtube_analytics = None
        os.makedirs(REPORTS_DIR, exist_ok=True)
        log.info("AnalyticsReviewer initialized")

    # ── YouTube Analytics ──────────────────────────────────────────

    def _init_analytics(self):
        """Initialize YouTube Analytics API client."""
        if self.youtube_analytics:
            return

        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            creds_path = os.environ.get("YOUTUBE_OAUTH_CREDS", "/app/data/youtube_creds.json")
            creds = Credentials.from_authorized_user_file(creds_path)
            self.youtube_analytics = build("youtubeAnalytics", "v2", credentials=creds)
            log.info("YouTube Analytics API initialized")
        except Exception as e:
            log.error(f"Failed to initialize YouTube Analytics: {e}")

    def fetch_daily_metrics(self, date: str) -> DailyMetrics:
        """Fetch channel metrics for a specific date (YYYY-MM-DD)."""
        self._init_analytics()
        metrics = DailyMetrics(date=date)

        if not self.youtube_analytics:
            log.warning("YouTube Analytics not available, using cached/mock data")
            return self._get_cached_metrics(date)

        try:
            response = self.youtube_analytics.reports().query(
                ids="channel==MINE",
                startDate=date,
                endDate=date,
                metrics="views,estimatedMinutesWatched,subscribersGained,subscribersLost,"
                        "impressions,impressionClickThroughRate,averageViewDuration,"
                        "averageViewPercentage,estimatedRevenue",
                dimensions="day",
            ).execute()

            if response.get("rows"):
                row = response["rows"][0]
                metrics.views = int(row[1])
                metrics.watch_time_hours = float(row[2]) / 60
                metrics.subscribers_gained = int(row[3])
                metrics.subscribers_lost = int(row[4])
                metrics.subscriber_delta = metrics.subscribers_gained - metrics.subscribers_lost
                metrics.impressions = int(row[5])
                metrics.ctr_percent = float(row[6]) * 100
                metrics.avg_view_duration_seconds = float(row[7])
                metrics.avg_retention_percent = float(row[8])
                metrics.revenue_usd = float(row[9])

            # Fetch top performing videos
            metrics.top_videos = self._fetch_top_videos(date)

        except Exception as e:
            log.error(f"Failed to fetch analytics for {date}: {e}")

        # Cache metrics
        self.rdb.set(
            f"{ANALYTICS_KEY_PREFIX}:daily:{date}",
            json.dumps(asdict(metrics)),
            ex=86400 * 90,  # Cache for 90 days
        )

        return metrics

    def _fetch_top_videos(self, date: str, limit: int = 10) -> list:
        """Fetch top performing videos for a date."""
        try:
            response = self.youtube_analytics.reports().query(
                ids="channel==MINE",
                startDate=date,
                endDate=date,
                metrics="views,impressionClickThroughRate,averageViewPercentage,"
                        "likes,comments,shares",
                dimensions="video",
                sort="-views",
                maxResults=limit,
            ).execute()

            videos = []
            for row in response.get("rows", []):
                videos.append({
                    "video_id": row[0],
                    "views": int(row[1]),
                    "ctr": float(row[2]) * 100,
                    "retention": float(row[3]),
                    "likes": int(row[4]),
                    "comments": int(row[5]),
                    "shares": int(row[6]),
                })
            return videos
        except Exception as e:
            log.error(f"Failed to fetch top videos: {e}")
            return []

    def _get_cached_metrics(self, date: str) -> DailyMetrics:
        """Get cached metrics from Redis."""
        cached = self.rdb.get(f"{ANALYTICS_KEY_PREFIX}:daily:{date}")
        if cached:
            data = json.loads(cached)
            return DailyMetrics(**data)
        return DailyMetrics(date=date)

    # ── Budget Tracking ────────────────────────────────────────────

    def get_budget_status(self) -> dict:
        """Calculate current budget status."""
        today = datetime.now(timezone.utc)
        month_start = today.replace(day=1).strftime("%Y-%m-%d")
        days_in_month = 30
        days_elapsed = today.day

        # Sum daily spends from Redis
        total_spend = 0.0
        for day in range(1, days_elapsed + 1):
            date = today.replace(day=day).strftime("%Y-%m-%d")
            daily = self.rdb.get(f"{BUDGET_TRACKER_KEY}:{date}")
            if daily:
                total_spend += float(daily)

        daily_avg = total_spend / max(days_elapsed, 1)
        projected_monthly = daily_avg * days_in_month
        remaining = MONTHLY_BUDGET - total_spend

        status = {
            "month": today.strftime("%Y-%m"),
            "days_elapsed": days_elapsed,
            "total_spend": round(total_spend, 2),
            "daily_average": round(daily_avg, 2),
            "projected_monthly": round(projected_monthly, 2),
            "remaining": round(remaining, 2),
            "budget": MONTHLY_BUDGET,
            "on_track": projected_monthly <= MONTHLY_BUDGET,
            "burn_rate_pct": round((total_spend / MONTHLY_BUDGET) * 100, 1),
        }

        # Check for consecutive over-budget days
        over_budget_streak = 0
        for i in range(BURN_RATE_ALERT_DAYS):
            date = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            daily = self.rdb.get(f"{BUDGET_TRACKER_KEY}:{date}")
            if daily and float(daily) > DAILY_BUDGET_SOFT:
                over_budget_streak += 1

        status["over_budget_streak"] = over_budget_streak
        status["should_pause_nonessential"] = over_budget_streak >= BURN_RATE_ALERT_DAYS

        return status

    # ── Prompt Weight Updates ──────────────────────────────────────

    def update_prompt_weights(self, metrics: DailyMetrics) -> dict:
        """Analyze metrics and update ScriptDirector prompt weights."""
        weights = {}

        # Load current weights
        current = self.rdb.get(PROMPT_WEIGHTS_KEY)
        if current:
            weights = json.loads(current)

        # Analyze top performing videos to find patterns
        for video in metrics.top_videos:
            if video.get("retention", 0) > 60:
                weights["high_retention_boost"] = weights.get("high_retention_boost", 1.0) + 0.05
            if video.get("ctr", 0) > 8.0:
                weights["high_ctr_boost"] = weights.get("high_ctr_boost", 1.0) + 0.05

        # Adjust based on overall metrics
        if metrics.ctr_percent > 6.0:
            weights["title_style_keep"] = True
        elif metrics.ctr_percent < 3.0:
            weights["title_style_change"] = True
            weights.setdefault("title_experiments", []).append(metrics.date)

        if metrics.avg_retention_percent > 50:
            weights["script_style_keep"] = True
        elif metrics.avg_retention_percent < 30:
            weights["increase_hooks"] = True
            weights["shorter_segments"] = True

        # Cap weight values
        for key in weights:
            if isinstance(weights[key], (int, float)) and weights[key] > 2.0:
                weights[key] = 2.0

        # Save updated weights
        self.rdb.set(PROMPT_WEIGHTS_KEY, json.dumps(weights))
        log.info(f"  📊 Updated {len(weights)} prompt weights")

        return weights

    # ── Report Generation ──────────────────────────────────────────

    def generate_insights(self, metrics: DailyMetrics, budget: dict) -> tuple[list, list, list]:
        """Generate insights, recommendations, and alerts."""
        insights = []
        recommendations = []
        alerts = []

        # View performance
        if metrics.views > 0:
            insights.append(f"📊 {metrics.views:,} views | "
                          f"{metrics.watch_time_hours:.1f}h watch time | "
                          f"+{metrics.subscriber_delta} subs")

        # CTR analysis
        if metrics.ctr_percent > 8.0:
            insights.append(f"🔥 Excellent CTR: {metrics.ctr_percent:.1f}% — thumbnails/titles are working!")
        elif metrics.ctr_percent < 3.0 and metrics.impressions > 100:
            insights.append(f"⚠ Low CTR: {metrics.ctr_percent:.1f}% — consider A/B testing thumbnails")
            recommendations.append("Test new thumbnail styles: brighter colors, face close-ups, "
                                  "or curiosity-gap text overlays")

        # Retention analysis
        if metrics.avg_retention_percent > 50:
            insights.append(f"✅ Strong retention: {metrics.avg_retention_percent:.0f}% — "
                          f"content is engaging!")
        elif metrics.avg_retention_percent < 30 and metrics.views > 50:
            insights.append(f"⚠ Low retention: {metrics.avg_retention_percent:.0f}% — "
                          f"viewers dropping off early")
            recommendations.append("Add more hooks in first 30 seconds; "
                                  "break long segments into 2-min chunks with mini-hooks")

        # Revenue
        if metrics.revenue_usd > 0:
            insights.append(f"💰 Revenue: ${metrics.revenue_usd:.2f}")

        # Budget alerts
        if budget.get("should_pause_nonessential"):
            alerts.append(f"🚨 BUDGET ALERT: Over-budget for {budget['over_budget_streak']} "
                         f"consecutive days! Pausing non-critical jobs.")
            recommendations.append("Reduce GPU usage; batch more jobs; use free LLM tiers only")

        if not budget.get("on_track"):
            alerts.append(f"⚠ Projected monthly spend: ${budget['projected_monthly']:.2f} "
                         f"(over ${budget['budget']:.0f} budget)")

        # Copyright/strikes
        if metrics.copyright_claims > 0:
            alerts.append(f"⚠ {metrics.copyright_claims} copyright claims detected!")
            recommendations.append("Review flagged videos; replace copyrighted segments; "
                                  "increase use of AI-generated B-roll")

        if metrics.community_strikes > 0:
            alerts.append(f"🚨 {metrics.community_strikes} community guideline strikes!")
            recommendations.append("URGENT: Review content guidelines; pause automated uploads "
                                  "until strikes are resolved")

        return insights, recommendations, alerts

    def generate_daily_report(self, date: str) -> DailyReport:
        """Generate comprehensive daily report."""
        log.info(f"📋 Generating daily report for {date}...")

        metrics = self.fetch_daily_metrics(date)
        budget = self.get_budget_status()
        prompt_updates = self.update_prompt_weights(metrics)
        insights, recommendations, alerts = self.generate_insights(metrics, budget)

        report = DailyReport(
            date=date,
            metrics=metrics,
            budget_status=budget,
            insights=insights,
            recommendations=recommendations,
            prompt_weight_updates=prompt_updates,
            alerts=alerts,
        )

        # Save report
        report_path = os.path.join(REPORTS_DIR, f"daily_report_{date}.md")
        self._write_markdown_report(report, report_path)

        # Store in Redis for quick access
        self.rdb.set(
            f"{ANALYTICS_KEY_PREFIX}:report:{date}",
            json.dumps(asdict(report)),
            ex=86400 * 90,
        )

        return report

    def _write_markdown_report(self, report: DailyReport, path: str):
        """Write report as Markdown file."""
        lines = [
            f"# 📊 Daily Report — {report.date}",
            f"> Generated: {report.generated_at}",
            "",
        ]

        # Alerts first (if any)
        if report.alerts:
            lines.append("## 🚨 Alerts")
            for alert in report.alerts:
                lines.append(f"- {alert}")
            lines.append("")

        # Key Metrics
        if report.metrics:
            m = report.metrics
            lines.extend([
                "## 📈 Key Metrics",
                "",
                "| Metric | Value |",
                "|--------|-------|",
                f"| Views | {m.views:,} |",
                f"| Watch Time | {m.watch_time_hours:.1f} hours |",
                f"| Subscribers | +{m.subscribers_gained} / -{m.subscribers_lost} (Δ{m.subscriber_delta}) |",
                f"| Impressions | {m.impressions:,} |",
                f"| CTR | {m.ctr_percent:.1f}% |",
                f"| Avg Retention | {m.avg_retention_percent:.0f}% |",
                f"| Avg View Duration | {m.avg_view_duration_seconds:.0f}s |",
                f"| Revenue | ${m.revenue_usd:.2f} |",
                "",
            ])

        # Budget
        b = report.budget_status
        if b:
            status_emoji = "✅" if b.get("on_track") else "⚠"
            lines.extend([
                "## 💰 Budget Status",
                "",
                f"| Metric | Value |",
                f"|--------|-------|",
                f"| Month | {b.get('month', 'N/A')} |",
                f"| Spent | ${b.get('total_spend', 0):.2f} / ${b.get('budget', 100):.0f} |",
                f"| Daily Avg | ${b.get('daily_average', 0):.2f} |",
                f"| Projected | {status_emoji} ${b.get('projected_monthly', 0):.2f} |",
                f"| Remaining | ${b.get('remaining', 0):.2f} |",
                "",
            ])

        # Insights
        if report.insights:
            lines.append("## 💡 Insights")
            for insight in report.insights:
                lines.append(f"- {insight}")
            lines.append("")

        # Recommendations
        if report.recommendations:
            lines.append("## 🎯 Recommendations")
            for rec in report.recommendations:
                lines.append(f"- {rec}")
            lines.append("")

        with open(path, "w") as f:
            f.write("\n".join(lines))

        log.info(f"  📝 Report saved: {path}")

    # ── Main Loop ──────────────────────────────────────────────────

    def run(self):
        """Run daily analytics review."""
        log.info("📊 AnalyticsReviewer starting up...")

        while True:
            try:
                now = datetime.now(timezone.utc)
                yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

                # Check if we already ran today
                already_ran = self.rdb.get(f"{ANALYTICS_KEY_PREFIX}:last_run")
                if already_ran == now.strftime("%Y-%m-%d"):
                    # Wait until next 06:00 UTC
                    next_run = now.replace(hour=6, minute=0, second=0, microsecond=0)
                    if next_run <= now:
                        next_run += timedelta(days=1)
                    wait_seconds = (next_run - now).total_seconds()
                    log.info(f"Already ran today. Next run in {wait_seconds/3600:.1f}h")
                    time.sleep(min(wait_seconds, 3600))  # Check hourly
                    continue

                # Generate report for yesterday
                report = self.generate_daily_report(yesterday)

                # Mark as ran
                self.rdb.set(f"{ANALYTICS_KEY_PREFIX}:last_run", now.strftime("%Y-%m-%d"))

                # Handle alerts
                if report.alerts:
                    log.warning(f"⚠ {len(report.alerts)} alert(s) generated!")
                    if report.budget_status.get("should_pause_nonessential"):
                        log.warning("🛑 Pausing non-essential jobs due to budget overrun")
                        self.rdb.set("system:pause_nonessential", "true", ex=86400)

                log.info(f"✅ Daily report complete for {yesterday}")
                log.info(f"  Views: {report.metrics.views:,} | "
                        f"CTR: {report.metrics.ctr_percent:.1f}% | "
                        f"Budget: ${report.budget_status.get('total_spend', 0):.2f}/"
                        f"${report.budget_status.get('budget', 100):.0f}")

            except Exception as e:
                log.exception(f"Analytics review failed: {e}")

            # Sleep for 1 hour and check again
            time.sleep(3600)


if __name__ == "__main__":
    reviewer = AnalyticsReviewer()
    reviewer.run()
