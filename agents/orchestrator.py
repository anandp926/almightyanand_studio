#!/usr/bin/env python3
"""
orchestrator.py — Production & Publishing (IST)

Two ways to run it:

  DAEMON (VM always on):  python -m agents.orchestrator --daemon
    06:30  Morning batch: SHORTS_PER_DAY Shorts (default 1), each scripted,
           fact-checked, rendered, QA-audited and queued.
    12:30 / 18:30 / 21:00  Upload the next queued Short.
    23:30  Analytics review and housekeeping.

  BURST (VM on for about an hour a day):  python -m agents.orchestrator --burst
    Analytics review, morning batch, then every queued Short is uploaded at
    once with a scheduled go-live time for today's slots. YouTube publishes
    them; the VM can shut down. See infra/burst_mode.md.

Safety behaviour:
  - A topic is only scripted from fetched source text, and the script must pass
    the fact-check. If nothing qualifies, the cycle is skipped.
  - A render is queued only after QA passes. Retries use new image seeds.
  - AUTO_PUBLISH=false keeps everything queued without uploading.
  - REVIEW_REQUIRED=true holds each Short until you approve it
    (python -m agents.review). In burst mode it uploads them as private
    instead, for you to publish from YouTube Studio.

CLI Entrypoints:
    python -m agents.orchestrator --run-once                 # Script, render and queue one Short
    python -m agents.orchestrator --run-once --publish       # ...and upload it now
    python -m agents.orchestrator --run-once --topic "..." --source-url https://...
    python -m agents.orchestrator --daemon
    python -m agents.orchestrator --burst
"""

import argparse
import json
import logging
import os
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

from agents.analytics_reviewer import AnalyticsReviewer
from agents.editor_ffmpeg import RENDERS_DIR, EditorFFmpeg
from agents.media_dispatcher import active_visual_style
from agents.memory_store import MemoryStore
from agents.publisher import Publisher
from agents.script_director import ScriptDirector, load_series
from agents.settings import STUDIO_ROOT, env_bool, env_int
from agents.topic_pipeline import NoPublishableTopicError, select_grounded_package
from agents.trend_scout import TrendScout

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ORCHESTRATOR] %(levelname)s %(message)s",
)
log = logging.getLogger("orchestrator")

QUEUE_FILE = STUDIO_ROOT / "memory" / "publish_queue.json"
IST = ZoneInfo("Asia/Kolkata")

BATCH_TIME = "06:30"
ANALYTICS_TIME = "23:30"
PUBLISH_SLOTS: List[Tuple[str, str]] = [
    ("12:30", "12:30 IST Lunch"),
    ("18:30", "18:30 IST Commute"),
    ("21:00", "21:00 IST Prime"),
]
SCHEDULE_LEAD_MINUTES = 20      # YouTube needs a scheduled time comfortably in the future


def _load_queue() -> list:
    if not QUEUE_FILE.exists():
        return []
    try:
        return json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_queue(items: list):
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = QUEUE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, QUEUE_FILE)


def _get_active_style(memory: MemoryStore) -> str:
    """
    The one fixed look from configs/visual_style.json. Rotating the look hurts
    recognition, so the 3-day style pivot only runs with STYLE_PIVOT_ENABLED=true.
    """
    if not env_bool("STYLE_PIVOT_ENABLED", False):
        return active_visual_style()[0]
    last_style = memory.get_latest_style()
    pivot_needed, next_style, reason = memory.check_pivot_needed(last_style)
    if pivot_needed:
        log.warning(f"🔄 Autonomous style pivot: {reason}")
        return next_style
    return last_style


def _is_publishable(item: dict) -> bool:
    return bool(item.get("approved")) or not env_bool("REVIEW_REQUIRED", False)


def _prune_missing(q: list, slot_label: str) -> list:
    """Drops items whose render no longer exists instead of failing on them forever."""
    kept = []
    for item in q:
        if Path(item["video_path"]).exists():
            kept.append(item)
        else:
            log.error(f"❌ [{slot_label}] Dropping queued item with missing render: {item['video_path']}")
    if len(kept) != len(q):
        _save_queue(kept)
    return kept


def _publish_item(
    item: dict,
    slot_label: str,
    memory: Optional[MemoryStore] = None,
    publish_at: Optional[datetime] = None,
    privacy: Optional[str] = None,
) -> dict:
    """Uploads one queued item and records it, with what it was made of, for per-video analytics."""
    pub = Publisher(dry_run=False)
    if privacy:
        pub.privacy_status = privacy
    rec = pub.publish_youtube_short(
        video_path=Path(item["video_path"]),
        title=item["title"],
        description=item["description"],
        top_comment=item.get("top_comment") or "",
        slot_label=slot_label,
        publish_at=publish_at,
    )
    went_live_at = publish_at if rec.scheduled_for else datetime.now(timezone.utc)
    (memory or MemoryStore()).record_published_video(
        video_id=rec.video_id,
        production_id=item.get("production_id", ""),
        title=item["title"],
        topic=item.get("topic", ""),
        category=item.get("category", "technology"),
        style=item.get("style", "commentary_broll"),
        hook=item.get("hook", ""),
        published_at=went_live_at.astimezone(timezone.utc).isoformat(),
        series=item.get("series", ""),
        format=item.get("format", ""),
        has_creator_take=bool(item.get("creator_take")),
        word_count=item.get("word_count"),
        voice_provider=item.get("voice_provider", ""),
        speaker=item.get("speaker", ""),
        pace=item.get("pace"),
        duration_s=item.get("duration_s"),
    )
    return asdict(rec)


def execute_production_cycle(
    override_topic: Optional[str] = None,
    source_url: Optional[str] = None,
    allow_ungrounded: bool = False,
    max_qa_retries: int = 2,
    publish_immediately: bool = False,
    prefer_category: Optional[str] = None,
) -> dict:
    """
    One full cycle: topic -> grounded script -> render -> QA -> queue (or upload).
    Raises NoPublishableTopicError when no topic can be grounded.
    """
    memory = MemoryStore()
    active_style = _get_active_style(memory)
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    log.info("=" * 70)
    log.info(f"👑 PRODUCTION CYCLE | Style: {active_style} | Preferred pillar: {prefer_category or 'any'}")
    log.info("=" * 70)

    # 1–2. Fresh topic with source text, and a script that passes the fact-check
    candidate, script_pkg = select_grounded_package(
        TrendScout(dry_run=False),
        ScriptDirector(dry_run=False),
        style=active_style,
        override_topic=override_topic,
        source_url=source_url,
        allow_ungrounded=allow_ungrounded,
        prefer_category=prefer_category,
    )

    # 3. Render + QA gate. Each retry uses new image seeds.
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
            attempt=attempt,
        )
        if qa_report.status == "PASS":
            log.info(f"🏆 QA PASS on attempt {attempt} ({qa_report.score}/10)")
            break
        log.warning(f"⚠️ QA FAIL on attempt {attempt}: {qa_report.notes}")
        if attempt > max_qa_retries:
            raise RuntimeError(f"Aborted after {attempt} failed QA attempts: {qa_report.notes}")

    render_info = getattr(editor, "last_render_info", {}) or {}
    job_payload = {
        "production_id": script_pkg.production_id,
        "topic": candidate.title,
        "category": script_pkg.category,
        "series": script_pkg.series_id,
        "format": script_pkg.format_id,
        "style": active_style,
        "hook": short_data.get("hook_hinglish", ""),
        "word_count": short_data.get("word_count"),
        "voice_provider": render_info.get("voice_provider", ""),
        "speaker": render_info.get("speaker", ""),
        "pace": render_info.get("pace"),
        "duration_s": render_info.get("duration_s"),
        "source_url": script_pkg.source_url,
        "grounded": script_pkg.grounded,
        "video_path": str(render_path),
        "title": short_data.get("title", f"{candidate.title[:60]} #Shorts"),
        "description": script_pkg.short_description,
        "top_comment": script_pkg.top_comment,
        "script_data": short_data,          # kept so a creator take can be added and the Short re-rendered
        "creator_take": "",
        "approved": False,
        "queued_at": datetime.now(timezone.utc).isoformat(),
    }

    memory.record_daily_plan(
        plan_date=today_str,
        longform_topic=script_pkg.long_form_16x9.get("title", ""),
        shorts_topics=[job_payload["title"]],
        chosen_style=active_style,
    )

    published_record = None
    if publish_immediately:
        published_record = _publish_item(job_payload, "Immediate CLI Run", memory)
    else:
        q = _load_queue()
        q.append(job_payload)
        _save_queue(q)
        log.info(f"📥 Queued QA-passed Short for the next IST publish slot (queue size: {len(q)})")

    return {
        "production_id": script_pkg.production_id,
        "video_path": str(render_path),
        "qa_status": qa_report.status,
        "published": published_record,
    }


def publish_next_queued_slot(slot_label: str):
    if not env_bool("AUTO_PUBLISH", True):
        log.info(f"⏸ [{slot_label}] AUTO_PUBLISH is off; leaving {len(_load_queue())} item(s) queued.")
        return

    q = _prune_missing(_load_queue(), slot_label)
    if not q:
        log.info(f"📭 [{slot_label}] Publish queue empty—skipping.")
        return

    index = next((i for i, item in enumerate(q) if _is_publishable(item)), None)
    if index is None:
        log.info(f"⏸ [{slot_label}] {len(q)} Short(s) waiting for review (python -m agents.review list).")
        return

    item = q.pop(index)
    _save_queue(q)
    try:
        rec = _publish_item(item, slot_label)
        log.info(f"🚀 [{slot_label}] Published: {rec['url']}")
    except Exception as e:
        log.error(f"❌ [{slot_label}] Upload failed ({e}); re-queuing item.")
        q = _load_queue()
        q.insert(index, item)
        _save_queue(q)


TAKE_VISUAL_PROMPT = "a single warm spotlight on a simple wooden desk, soft blurred background, calm and personal mood"


def apply_creator_take(position: int, take: str, tts_text: str = "", audio_path: str = "") -> dict:
    """
    Adds your own line to a queued Short (1-based position) and re-renders it.
    The line goes just before the closing question, with a "MY TAKE" badge. It
    is spoken by the host voice, or by you if `audio_path` is a recording.
    The new render must pass QA; if it does not, the queue item is left as it was.
    """
    take = (take or "").strip()
    if not take:
        raise ValueError("The take is empty.")
    q = _load_queue()
    index = position - 1
    if not 0 <= index < len(q):
        raise IndexError(f"No queue item {position}")
    item = q[index]
    script = item.get("script_data")
    if not script or not script.get("shot_cards"):
        raise RuntimeError("This item was queued before takes were supported; it cannot be re-rendered.")

    cards = [dict(c) for c in script["shot_cards"] if c.get("role") != "take"]
    host_speaker = cards[0].get("speaker", "narrator")
    take_card = {
        "narration_cue": take,
        "tts_text": tts_text.strip(),
        "audio_path": audio_path,
        "speaker": host_speaker,
        "role": "take",
        "overlay_text": "Meri Rai",
        "badge_text": "MY TAKE",
        "visual_prompt": TAKE_VISUAL_PROMPT,
    }
    insert_at = len(cards) - 1 if len(cards) > 1 else len(cards)       # before the closing question
    cards.insert(insert_at, take_card)
    new_script = {**script, "shot_cards": cards}

    editor = EditorFFmpeg(dry_run=False)
    output_file = f"short_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_take.mp4"
    render_path, qa_report = editor.assemble_vertical_short(
        script_data=new_script,
        production_id=f"{item.get('production_id', 'prod')}_take",
        output_filename=output_file,
    )
    if qa_report.status != "PASS":
        raise RuntimeError(f"Re-render failed QA, the original Short is unchanged: {qa_report.notes}")

    old_video = Path(item["video_path"])
    render_info = getattr(editor, "last_render_info", {}) or {}
    item.update({
        "script_data": new_script,
        "creator_take": take,
        "video_path": str(render_path),
        "duration_s": render_info.get("duration_s", item.get("duration_s")),
        "description": _description_with_take(item.get("description", ""), take),
    })
    q = _load_queue()
    q[index] = item
    _save_queue(q)
    if old_video.exists() and old_video != Path(render_path):
        old_video.unlink()
    log.info(f"✍️ Added your take to '{item.get('title', '')}' and re-rendered: {render_path}")
    return item


def _description_with_take(description: str, take: str) -> str:
    """Puts 'Meri rai: ...' after the hook line, replacing an earlier take if there was one."""
    blocks = [b for b in description.split("\n\n") if b and not b.startswith("Meri rai:")]
    blocks.insert(1 if blocks else 0, f"Meri rai: {take}")
    return "\n\n".join(blocks)


def _batch_pillars(count: int) -> List[Optional[str]]:
    """Pillar to prefer for each Short of the batch: the series list, repeated."""
    pillars = [s["pillar"] for s in load_series()]
    if not pillars:
        return [None] * count
    return [pillars[i % len(pillars)] for i in range(count)]


def run_morning_batch(count: Optional[int] = None):
    count = count or env_int("SHORTS_PER_DAY", 1)
    backlog = len(_load_queue())
    if backlog >= 2 * len(PUBLISH_SLOTS):
        log.warning(f"⏸ Queue already holds {backlog} Shorts; skipping today's batch.")
        return
    log.info(f"🌅 Running morning batch ({count} Shorts)...")
    for idx, pillar in enumerate(_batch_pillars(count), 1):
        try:
            execute_production_cycle(publish_immediately=False, prefer_category=pillar)
        except NoPublishableTopicError as e:
            log.warning(f"⏭ Batch item {idx}: no publishable topic ({e}). Stopping this batch.")
            break
        except Exception as e:
            log.error(f"❌ Morning batch item {idx} failed: {e}")


def upcoming_slot_times(now: Optional[datetime] = None, count: Optional[int] = None) -> List[Tuple[datetime, str]]:
    """The next `count` publish slots as timezone-aware IST datetimes, at least 20 minutes away."""
    now = (now or datetime.now(IST)).astimezone(IST)
    count = count or len(PUBLISH_SLOTS)
    earliest = now + timedelta(minutes=SCHEDULE_LEAD_MINUTES)
    result: List[Tuple[datetime, str]] = []
    day = now.date()
    while len(result) < count:
        for slot_time, label in PUBLISH_SLOTS:
            hour, minute = map(int, slot_time.split(":"))
            when = datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)
            if when >= earliest and len(result) < count:
                result.append((when, label))
        day += timedelta(days=1)
    return result


def schedule_queued_uploads() -> int:
    """
    Burst mode: uploads queued Shorts now, each with a scheduled go-live time
    for an upcoming slot. With REVIEW_REQUIRED=true they are uploaded as
    private with no schedule, for you to publish from YouTube Studio.
    """
    if not env_bool("AUTO_PUBLISH", True):
        log.info(f"⏸ AUTO_PUBLISH is off; leaving {len(_load_queue())} item(s) queued.")
        return 0

    review_first = env_bool("REVIEW_REQUIRED", False)
    q = _prune_missing(_load_queue(), "burst")
    slots = upcoming_slot_times(count=min(len(q), len(PUBLISH_SLOTS)))
    uploaded = 0
    for when, label in slots:
        q = _load_queue()
        if not q:
            break
        item = q.pop(0)
        _save_queue(q)
        try:
            if review_first and not item.get("approved"):
                rec = _publish_item(item, f"{label} (private, awaiting your review)", privacy="private")
                log.info(f"🔒 Uploaded as private for review: {rec['url']}")
            else:
                rec = _publish_item(item, label, publish_at=when)
                log.info(f"🗓 Scheduled for {when.strftime('%d %b %H:%M IST')}: {rec['url']}")
            uploaded += 1
        except Exception as e:
            log.error(f"❌ Upload failed ({e}); re-queuing item.")
            q = _load_queue()
            q.insert(0, item)
            _save_queue(q)
            break
    return uploaded


def prune_old_artifacts(max_age_days: Optional[int] = None) -> int:
    """Deletes old renders, rejected renders and QA keyframes. Queued renders are kept."""
    max_age_days = max_age_days or env_int("RENDER_RETENTION_DAYS", 7)
    cutoff = time.time() - max_age_days * 86400
    queued = {str(Path(item["video_path"]).resolve()) for item in _load_queue()}
    removed = 0
    for folder, pattern in [(RENDERS_DIR, "*.mp4"), (RENDERS_DIR / "rejected", "*.mp4"), (RENDERS_DIR / "qc_audit", "*.jpg")]:
        if not folder.exists():
            continue
        for path in folder.glob(pattern):
            try:
                if path.stat().st_mtime < cutoff and str(path.resolve()) not in queued:
                    path.unlink()
                    removed += 1
            except OSError as e:
                log.warning(f"Could not remove {path}: {e}")
    if removed:
        log.info(f"🧹 Pruned {removed} artifact(s) older than {max_age_days} days.")
    return removed


def run_nightly_analytics():
    log.info("🌙 Running analytics review...")
    try:
        report = AnalyticsReviewer(dry_run=False).run_daily_review()
        if report.data_available:
            log.info(
                f"📊 Review done: {report.videos_reviewed} videos, "
                f"views={report.views}, AVD={report.avg_retention_percent}%"
            )
        else:
            log.info(f"📊 Review done: no usable data ({'; '.join(report.notes)})")
    except Exception as e:
        log.error(f"❌ Analytics review failed: {e}")
    try:
        for row in MemoryStore().format_performance(env_int("ANALYTICS_MIN_VIEWS", 100)):
            log.info(f"🎭 Format '{row['format']}': {row['videos']} videos, {row['views']} views, AVD {row['avd_percent']}%")
    except Exception as e:
        log.warning(f"Format report skipped: {e}")
    try:
        prune_old_artifacts()
    except Exception as e:
        log.error(f"❌ Housekeeping failed: {e}")


def run_burst() -> int:
    """One self-contained daily run for a VM that is only on for the batch."""
    log.info("⚡ Burst run: analytics, batch, scheduled uploads.")
    run_nightly_analytics()
    run_morning_batch()
    uploaded = schedule_queued_uploads()
    log.info(f"⚡ Burst run finished: {uploaded} upload(s), {len(_load_queue())} left in the queue.")
    return uploaded


def start_daemon():
    import schedule

    # Force IST timezone even on UTC GCP VMs
    os.environ["TZ"] = "Asia/Kolkata"
    if hasattr(time, "tzset"):
        time.tzset()

    log.info("🚀 Starting Almighty Anand daemon (Timezone: Asia/Kolkata IST)")
    schedule.every().day.at(BATCH_TIME).do(run_morning_batch)
    for slot_time, slot_label in PUBLISH_SLOTS:
        schedule.every().day.at(slot_time).do(publish_next_queued_slot, slot_label=slot_label)
    schedule.every().day.at(ANALYTICS_TIME).do(run_nightly_analytics)

    while True:
        try:
            schedule.run_pending()
            time.sleep(30)
        except Exception as e:
            log.error(f"Daemon error: {e}. Retrying in 60s...")
            time.sleep(60)


def main():
    parser = argparse.ArgumentParser(description="Pipeline Orchestrator")
    parser.add_argument("--run-once", action="store_true", help="Render 1 Short and queue it")
    parser.add_argument("--publish", action="store_true", help="Upload immediately with --run-once")
    parser.add_argument("--daemon", action="store_true", help="Start the IST scheduler")
    parser.add_argument("--burst", action="store_true", help="Analytics, batch and scheduled uploads, then exit")
    parser.add_argument("--topic", type=str, default=None, help="Manual topic title (needs --source-url)")
    parser.add_argument("--source-url", type=str, default=None, help="Article to ground a manual --topic in")
    parser.add_argument(
        "--allow-ungrounded", action="store_true",
        help="Manual --topic only: script without a source (generic, figure-free)",
    )
    args = parser.parse_args()

    if args.daemon:
        start_daemon()
        return
    if args.burst:
        run_burst()
        return

    try:
        result = execute_production_cycle(
            override_topic=args.topic,
            source_url=args.source_url,
            allow_ungrounded=args.allow_ungrounded and bool(args.topic),
            publish_immediately=args.publish,
        )
        print(json.dumps(result, indent=2))
    except NoPublishableTopicError as e:
        log.warning(f"No Short produced: {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
