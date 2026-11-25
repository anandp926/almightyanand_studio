#!/usr/bin/env python3
"""
orchestrator.py — Master 24/7 Self-Healing Autonomous Production Daemon

Runs the end-to-end multi-agent pipeline and 24/7 scheduler:
  - 06:00 IST: Daily fresh trend scouting & CEO planning (memory deduplication).
  - 07:00 IST: Batch production, Spot GPU wake/stop, video rendering, QA Judge audit
               (auto-retries up to 2x on no_watermarks or visuals_match_topic failure).
  - 12:00 / 18:00 / 21:00 IST: Scheduled YouTube/Shorts publication with affiliate links.
  - 23:30 IST: Daily analytics audit, budget guardrails, and prompt weight updates.

CLI Entrypoints:
    python -m agents.orchestrator --run-once           # Run 1 full live cycle now
    python -m agents.orchestrator --daemon             # Start 24/7 self-healing scheduler
    python -m agents.orchestrator --dry-run            # Mock test
"""

#!/usr/bin/env python3
"""
orchestrator.py — 24/7 Self-Healing Autonomous Production & Publishing Daemon (IST)
"""

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from agents.memory_store import MemoryStore
from agents.trend_scout import TrendScout
from agents.script_director import ScriptDirector
from agents.editor_ffmpeg import EditorFFmpeg
from agents.publisher import Publisher
from agents.analytics_reviewer import AnalyticsReviewer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ORCHESTRATOR] %(levelname)s %(message)s",
)
log = logging.getLogger("orchestrator")

STUDIO_ROOT = Path(__file__).resolve().parent.parent
QUEUE_FILE = STUDIO_ROOT / "memory" / "publish_queue.json"


def _load_queue() -> list:
    if not QUEUE_FILE.exists():
        return []
    try:
        return json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_queue(items: list):
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_FILE.write_text(json.dumps(items, indent=2), encoding="utf-8")


def _get_active_style(memory: MemoryStore) -> str:
    with memory._get_connection() as conn:
        row = conn.execute(
            "SELECT chosen_style FROM daily_plans ORDER BY date DESC LIMIT 1"
        ).fetchone()
        last_style = row["chosen_style"] if row and row["chosen_style"] else "commentary_broll"
    pivot_needed, next_style, reason = memory.check_pivot_needed(last_style)
    if pivot_needed:
        log.warning(f"🔄 Autonomous style pivot: {reason}")
        return next_style
    return last_style


def execute_production_cycle(
    override_topic: Optional[str] = None,
    max_qa_retries: int = 2,
    publish_immediately: bool = False,
) -> dict:
    memory = MemoryStore()
    active_style = _get_active_style(memory)
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    log.info("=" * 70)
    log.info(f"👑 AUTONOMOUS PRODUCTION CYCLE | Style: {active_style}")
    log.info("=" * 70)

    # 1. Discover fresh topic
    scout = TrendScout(dry_run=False)
    candidate = scout.discover_live_candidate()
    if override_topic:
        candidate.title = override_topic
        candidate.category = "technology"
        candidate.source = "manual_override"

    # 2. Generate transformative Hinglish script
    director = ScriptDirector(dry_run=False)
    script_pkg = director.build_transformative_package(
        {
            "title": candidate.title,
            "category": candidate.category,
            "source": candidate.source,
            "source_url": candidate.source_url,
        },
        chosen_style=active_style,
    )

    # 3. Render + Vision QA Gate with auto-retry
    editor = EditorFFmpeg(dry_run=False)
    short_data = script_pkg.shorts_9x16_variants[0]
    render_path = None
    qa_report = None

    for attempt in range(1, max_qa_retries + 2):
        output_file = f"short_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.mp4"
        render_path, qa_report = editor.assemble_vertical_short(
            script_data=short_data,
            production_id=script_pkg.production_id,
            output_filename=output_file,
        )
        if qa_report.status == "PASS":
            log.info(f"🏆 QA PASS on attempt {attempt} ({qa_report.score}/10)")
            break
        log.warning(f"⚠️ QA FAIL on attempt {attempt}: {qa_report.notes}")
        if attempt > max_qa_retries:
            raise RuntimeError(f"Aborted after {attempt} failed QA attempts: {qa_report.notes}")

    # Build description with affiliate links
    aff_lines = "\n".join(f"🔗 {a['title']}: {a['url']}" for a in script_pkg.affiliate_links)
    description = (
        f"{short_data.get('hook_hinglish', '')}\n\n"
        f"{aff_lines}\n\n"
        "#Shorts #AI #TechIndia #Hinglish #AlmightyAnand"
    )

    job_payload = {
        "production_id": script_pkg.production_id,
        "topic": candidate.title,
        "video_path": str(render_path),
        "title": short_data.get("title", f"{candidate.title[:60]} #Shorts"),
        "description": description,
        "pinned_comment": script_pkg.pinned_comment,
        "style": active_style,
    }

    memory.record_daily_plan(
        plan_date=today_str,
        longform_topic=script_pkg.long_form_16x9.get("title", ""),
        shorts_topics=[job_payload["title"]],
        chosen_style=active_style,
    )

    published_record = None
    if publish_immediately:
        pub = Publisher(dry_run=False)
        published_record = asdict(
            pub.publish_youtube_short(
                video_path=Path(job_payload["video_path"]),
                title=job_payload["title"],
                description=job_payload["description"],
                pinned_comment=job_payload["pinned_comment"],
                slot_label="Immediate CLI Run",
            )
        )
    else:
        q = _load_queue()
        q.append(job_payload)
        _save_queue(q)
        log.info(f"📥 Queued approved Short for next IST publish slot (Queue size: {len(q)})")

    return {
        "production_id": script_pkg.production_id,
        "video_path": str(render_path),
        "qa_status": qa_report.status,
        "published": published_record,
    }


def publish_next_queued_slot(slot_label: str):
    q = _load_queue()
    if not q:
        log.info(f"📭 [{slot_label}] Publish queue empty—skipping.")
        return

    item = q.pop(0)
    _save_queue(q)
    try:
        pub = Publisher(dry_run=False)
        rec = pub.publish_youtube_short(
            video_path=Path(item["video_path"]),
            title=item["title"],
            description=item["description"],
            pinned_comment=item["pinned_comment"],
            slot_label=slot_label,
        )
        log.info(f"🚀 [{slot_label}] Published live: {rec.url}")
    except Exception as e:
        log.error(f"❌ [{slot_label}] Upload failed ({e}); re-queuing item.")
        q.insert(0, item)
        _save_queue(q)


def run_morning_batch(count: int = 2):
    log.info(f"🌅 Running 06:30 IST morning batch ({count} Shorts)...")
    for idx in range(1, count + 1):
        try:
            execute_production_cycle(publish_immediately=False)
        except Exception as e:
            log.error(f"❌ Morning batch item {idx} failed: {e}")


def run_nightly_analytics():
    log.info("🌙 Running 23:30 IST Analytics & Self-Learning Review...")
    try:
        report = AnalyticsReviewer(dry_run=False).run_daily_review()
        log.info(f"📊 Nightly Review Done: views={report.views}, AVD={report.avg_retention_percent}%")
    except Exception as e:
        log.error(f"❌ Nightly analytics failed: {e}")


def start_daemon():
    import schedule

    # Force IST timezone even on UTC GCP VMs
    os.environ["TZ"] = "Asia/Kolkata"
    if hasattr(time, "tzset"):
        time.tzset()

    log.info("🚀 Starting 24/7 Almighty Anand Autonomous Daemon (Timezone: Asia/Kolkata IST)")
    schedule.every().day.at("06:30").do(run_morning_batch, count=2)
    schedule.every().day.at("12:30").do(publish_next_queued_slot, slot_label="12:30 IST Lunch")
    schedule.every().day.at("18:30").do(publish_next_queued_slot, slot_label="18:30 IST Commute")
    schedule.every().day.at("21:00").do(publish_next_queued_slot, slot_label="21:00 IST Prime")
    schedule.every().day.at("23:30").do(run_nightly_analytics)

    while True:
        try:
            schedule.run_pending()
            time.sleep(30)
        except Exception as e:
            log.error(f"Daemon error: {e}. Self-healing in 60s...")
            time.sleep(60)


def main():
    parser = argparse.ArgumentParser(description="Master Autonomous Pipeline Orchestrator")
    parser.add_argument("--run-once", action="store_true", help="Render 1 Short and queue it")
    parser.add_argument("--publish", action="store_true", help="Upload immediately with --run-once")
    parser.add_argument("--daemon", action="store_true", help="Start 24/7 IST scheduler")
    parser.add_argument("--topic", type=str, default=None, help="Override topic title")
    args = parser.parse_args()

    if args.daemon:
        start_daemon()
    else:
        execute_production_cycle(override_topic=args.topic, publish_immediately=args.publish)


if __name__ == "__main__":
    main()