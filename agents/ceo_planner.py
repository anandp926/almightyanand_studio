#!/usr/bin/env python3
"""
ceo_planner.py — Master Autonomous CEO & Daily Self-Planning Agent

North Star Goal:
  Maximize views, subscriber growth, average view duration (AVD > 75% on Shorts, > 45% on long-form),
  and affiliate monetization within the $250 / 2.5-month GCP budget ($100/mo cap).

Autonomous Control Loop:
  1. Gathers live Indian & global trends (Google Trends India, Google News, Cyber/AI alerts).
  2. Queries SQLite MemoryStore (`memory/published_topics.db`) to GUARANTEE 100% fresh topics (zero copying).
  3. Audits past 3-day performance to autonomously pivot visual styles (Anime, Pixel Art, Origami, B-roll).
  4. Decides daily content plan (1 16:9 Long-Form + 2-3 9:16 Shorts with cliffhangers).
  5. Formulates high-retention Hinglish scripts and structured shot cards.
  6. Dispatches FFmpeg assembly for immediate verified video output.

CLI Entrypoints:
    python -m agents.ceo_planner --run-today     # Plan and output today's fresh content package
    python -m agents.ceo_planner --render-short  # Plan + render today's Short with FFmpeg
"""

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from agents.memory_store import MemoryStore
from agents.trend_scout import TrendScout
from agents.script_director import ScriptDirector
from agents.editor_ffmpeg import EditorFFmpeg
from agents.media_dispatcher import active_visual_style
from agents.settings import env_bool
from agents.topic_pipeline import NoPublishableTopicError, select_grounded_package

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [CEO-PLANNER] %(levelname)s %(message)s",
)
log = logging.getLogger("ceo_planner")

AVAILABLE_STYLES = ["commentary_broll", "origami_breakdown", "retro_pixel_art", "anime_epic"]


@dataclass
class DailyCEODecision:
    date: str
    north_star_goal: str
    budget_status: str
    active_visual_style: str
    pivot_triggered: bool
    pivot_reason: str
    primary_topic: str
    topic_source: str
    composite_virality_score: float
    deduplication_verified: bool
    long_form_16x9_title: str
    shorts_variants: List[dict]
    affiliate_cta: str
    rendered_video_path: Optional[str] = None
    qa_report: Optional[dict] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class CEOPlanner:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.memory = MemoryStore()
        self.scout = TrendScout(dry_run=dry_run)
        self.director = ScriptDirector(dry_run=dry_run)
        self.editor = EditorFFmpeg(dry_run=dry_run)

    def plan_today(
        self,
        render_short: bool = False,
        override_topic: Optional[str] = None,
        source_url: Optional[str] = None,
        allow_ungrounded: bool = False,
    ) -> DailyCEODecision:
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log.info("=" * 70)
        log.info(f"👑 CEO PLANNER: INITIATING AUTONOMOUS CONTENT PLAN FOR {today_str}")
        log.info("🎯 North Star: AVD > 75% on Shorts, > 45% Long-form, YPP Monetization under $100/mo")
        log.info("=" * 70)

        # 1. One fixed look; the 3-day style pivot only runs with STYLE_PIVOT_ENABLED=true
        pivot_needed, pivot_reason = False, "Fixed visual style."
        if env_bool("STYLE_PIVOT_ENABLED", False):
            current_style = self.memory.get_latest_style()
            pivot_needed, active_style, pivot_reason = self.memory.check_pivot_needed(current_style)
            if pivot_needed:
                log.warning(f"🔄 AUTONOMOUS PIVOT TRIGGERED: {pivot_reason}")
        else:
            active_style = active_visual_style()[0]
        log.info(f"🎨 Visual style: '{active_style}'")

        # 2–3. Fresh topic with source text, and a script that passes the fact-check.
        # Raises NoPublishableTopicError when nothing qualifies; no topic is invented.
        candidate, script_pkg = select_grounded_package(
            self.scout,
            self.director,
            style=active_style,
            override_topic=override_topic,
            source_url=source_url,
            allow_ungrounded=allow_ungrounded,
        )
        log.info(f"✨ Fresh Candidate Locked: '{candidate.title}' (Source: {candidate.source})")

        # 4. Optional Immediate FFmpeg Video Render
        rendered_path = None
        qa_dict = None
        if render_short and script_pkg.shorts_9x16_variants:
            log.info("🎥 Triggering Immediate FFmpeg Render for Short #1...")
            short_data = script_pkg.shorts_9x16_variants[0]
            output_file = f"short_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.mp4"
            target_mp4, qa_report = self.editor.assemble_vertical_short(
                script_data=short_data,
                production_id=script_pkg.production_id,
                output_filename=output_file,
            )
            rendered_path = str(target_mp4)
            qa_dict = asdict(qa_report)
            log.info(f"🎉 Short Video Render Complete: {rendered_path} (QA Status: {qa_report.status})")

        # 5. Record daily plan in persistent SQLite
        shorts_titles = [s.get("title", "") for s in script_pkg.shorts_9x16_variants]
        self.memory.record_daily_plan(
            plan_date=today_str,
            longform_topic=script_pkg.long_form_16x9.get("title", ""),
            shorts_topics=shorts_titles,
            chosen_style=active_style,
            pivot_reason=pivot_reason if pivot_needed else "None",
        )

        decision = DailyCEODecision(
            date=today_str,
            north_star_goal="YPP Monetization ($250 Budget / $100/mo cap, AVD > 75% Shorts)",
            budget_status="NOT_TRACKED (see the GCP billing budget alerts)",
            active_visual_style=active_style,
            pivot_triggered=pivot_needed,
            pivot_reason=pivot_reason,
            primary_topic=candidate.title,
            topic_source=candidate.source,
            composite_virality_score=candidate.composite_score,
            deduplication_verified=candidate.source != "manual_override",
            long_form_16x9_title=script_pkg.long_form_16x9.get("title", ""),
            shorts_variants=script_pkg.shorts_9x16_variants,
            affiliate_cta=script_pkg.short_description,
            rendered_video_path=rendered_path,
            qa_report=qa_dict,
        )

        log.info("=" * 70)
        log.info(f"✅ DAILY DECISION LOCKED: '{candidate.title}'")
        log.info(f"Visual Style: {active_style} | Shorts Spawned: {len(script_pkg.shorts_9x16_variants)}")
        if rendered_path:
            log.info(f"Rendered Short: {rendered_path}")
        log.info("=" * 70)

        return decision


def main():
    parser = argparse.ArgumentParser(description="Master CEO Planner Agent")
    parser.add_argument("--run-today", action="store_true", help="Plan today's content package")
    parser.add_argument("--render-short", action="store_true", help="Plan and render Short video with FFmpeg")
    parser.add_argument("--topic", type=str, default=None, help="Manual topic title (needs --source-url)")
    parser.add_argument("--source-url", type=str, default=None, help="Article to ground a manual --topic in")
    parser.add_argument("--allow-ungrounded", action="store_true", help="Manual --topic only: script without a source")
    parser.add_argument("--dry-run", action="store_true", help="Skip Redis; LLM and feeds are still called")
    args = parser.parse_args()

    planner = CEOPlanner(dry_run=args.dry_run)
    try:
        decision = planner.plan_today(
            render_short=args.render_short,
            override_topic=args.topic,
            source_url=args.source_url,
            allow_ungrounded=args.allow_ungrounded and bool(args.topic),
        )
    except NoPublishableTopicError as e:
        log.warning(f"No plan today: {e}")
        raise SystemExit(1)

    print("\n--- CEO PLANNER DAILY DECISION ---")
    print(json.dumps(asdict(decision), indent=2))


if __name__ == "__main__":
    main()
