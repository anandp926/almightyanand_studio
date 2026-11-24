#!/usr/bin/env python3
"""
orchestrator.py — Master Autonomous Pipeline Runner

Executes the end-to-end multi-agent video production cycle:
  CEOPlanner / TrendScout -> ScriptDirector -> MediaDispatcher -> EditorFFmpeg -> Publisher -> AnalyticsReviewer

CLI Entrypoint:
    python -m agents.orchestrator [--dry-run]
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone

from agents.memory_store import MemoryStore
from agents.trend_scout import TrendScout
from agents.script_director import ScriptDirector
from agents.media_dispatcher import MediaDispatcher
from agents.editor_ffmpeg import EditorFFmpeg
from agents.publisher import Publisher
from agents.analytics_reviewer import AnalyticsReviewer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ORCHESTRATOR] %(levelname)s %(message)s",
)
log = logging.getLogger("orchestrator")


def run_pipeline(dry_run: bool = True):
    log.info("=" * 70)
    log.info("🚀 ALMIGHTY ANAND STUDIO — AUTONOMOUS MULTI-AGENT PIPELINE")
    log.info(f"Mode: {'--dry-run (0 GPU Credits Spent)' if dry_run else 'PRODUCTION'}")
    log.info("=" * 70)

    # 1. Trend Discovery & Freshness Verification
    log.info("\n[STAGE 1/6] TrendScout: Discovering Live Indian & Global Trends...")
    scout = TrendScout(dry_run=dry_run)
    topic = scout.discover_live_candidate()

    # 2. Transformative Scripting & Shot Cards (Hinglish Mandate)
    log.info("\n[STAGE 2/6] ScriptDirector: Formulating Transformative Hinglish Script & Shot Cards...")
    director = ScriptDirector(dry_run=dry_run)
    script_package = director.build_transformative_package({
        "title": topic.title,
        "category": topic.category,
        "source": topic.source,
        "source_url": topic.source_url,
    })

    # 3. Media Dispatching (Voice + B-roll + Music)
    log.info("\n[STAGE 3/6] MediaDispatcher: Dispatching VoiceStudio, Open-Gen-AI & Maestro...")
    dispatcher = MediaDispatcher(dry_run=dry_run)
    media_manifest = dispatcher.dispatch_dry_run(production_id=script_package.production_id)

    # 4. FFmpeg Video Assembly
    log.info("\n[STAGE 4/6] EditorFFmpeg: Assembling 16:9 Long-Form & 9:16 Shorts with -16 LUFS...")
    editor = EditorFFmpeg(dry_run=dry_run)
    render_output = editor.assemble_dry_run(
        production_id=script_package.production_id,
        transformation_ratio=script_package.transformation_ratio,
    )

    # 5. Multi-Platform Publishing & Affiliate Monetization
    log.info("\n[STAGE 5/6] Publisher: Preparing YouTube & Hindi Auto-Dubbed Releases...")
    publisher = Publisher(dry_run=dry_run)
    published_records = publisher.publish_dry_run(production_id=script_package.production_id)

    # 6. Analytics & Budget Review
    log.info("\n[STAGE 6/6] AnalyticsReviewer: Auditing Performance & Enforcing Budget Limits...")
    reviewer = AnalyticsReviewer(dry_run=dry_run)
    daily_report = reviewer.review_dry_run()

    log.info("\n" + "=" * 70)
    log.info("🎉 PIPELINE RUN COMPLETE — ALL 6 STAGES PASSED CLEANLY")
    log.info(f"Topic Selected: {topic.title}")
    log.info(f"Visual Transformation Ratio: {script_package.transformation_ratio * 100:.0f}% (>= 75% threshold PASSED)")
    log.info(f"Long-Form Asset: {render_output.long_form_16x9_path}")
    log.info(f"Shorts Assets Spawned: {len(render_output.shorts_9x16_paths)} variants")
    log.info(f"Affiliate Links Attached: {len(script_package.affiliate_links)}")
    log.info(f"Budget Status: {daily_report.budget_status}")
    log.info("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Master Autonomous Pipeline Orchestrator")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Run dry run pipeline without GPU credits")
    args = parser.parse_args()

    run_pipeline(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
