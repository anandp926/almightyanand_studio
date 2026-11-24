#!/usr/bin/env python3
"""
media_dispatcher.py — Media Production Dispatcher

Consumes script packages from ScriptDirector and dispatches three parallel
production jobs to the GPU VM services:
  1. VoiceStudio — TTS voice cloning (Almighty Anand's voice)
  2. Open-Gen-AI — B-roll and thumbnail image generation
  3. Maestro — Background music generation

Coordinates with gpu_controller.py to wake the GPU VM when jobs are queued.
Waits for all three jobs to complete before pushing to EditorFFmpeg.
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

import redis
import httpx

# ── Configuration ──────────────────────────────────────────────────

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/2")
GPU_VM_IP = os.environ.get("GPU_VM_IP", "10.128.0.3")
VOICESTUDIO_URL = f"http://{GPU_VM_IP}:8090"
OPENGENAI_URL = f"http://{GPU_VM_IP}:8091"
MAESTRO_URL = f"http://{GPU_VM_IP}:8092"

SCRIPT_QUEUE_KEY = "scripts:ready_queue"
GPU_JOB_QUEUE_KEY = "gpu:job_queue"
MEDIA_READY_QUEUE_KEY = "media:ready_queue"
GPU_HEARTBEAT_KEY = "gpu:worker_heartbeat"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [MEDIA-DISPATCH] %(levelname)s %(message)s",
)
log = logging.getLogger("media_dispatcher")


@dataclass
class MediaJob:
    """A single media production job dispatched to a GPU service."""
    job_id: str
    production_id: str
    job_type: str               # "voice", "broll", "music"
    service_url: str
    payload: dict = field(default_factory=dict)
    status: str = "queued"      # queued, processing, completed, failed
    result_path: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: str = ""
    error: str = ""


@dataclass
class MediaPackage:
    """All media assets for a single production."""
    production_id: str
    voice_long: str = ""         # Path to long-form voice audio
    voice_short: str = ""        # Path to shorts voice audio
    broll_images: list = field(default_factory=list)   # Paths to B-roll images
    thumbnail: str = ""          # Path to thumbnail image
    music_long: str = ""         # Path to long-form background music
    music_short: str = ""        # Path to shorts background music
    script_package: dict = field(default_factory=dict)  # Original script data
    status: str = "pending"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class MediaDispatcher:
    """Dispatches and coordinates GPU-bound media production jobs."""

    def __init__(self):
        self.rdb = redis.from_url(REDIS_URL, decode_responses=True)
        self.http = httpx.Client(timeout=300)  # 5-min timeout for GPU jobs
        log.info("MediaDispatcher initialized")

    # ── Job Dispatch ───────────────────────────────────────────────

    def dispatch_voice(self, production_id: str, script: dict, format_type: str) -> MediaJob:
        """Dispatch voice cloning job to VoiceStudio."""
        job = MediaJob(
            job_id=f"{production_id}_voice_{format_type}",
            production_id=production_id,
            job_type="voice",
            service_url=VOICESTUDIO_URL,
            payload={
                "text": script.get("script", ""),
                "voice_profile": "almighty_anand_v2",
                "format": format_type,  # "long" or "short"
                "output_format": "wav",
                "speed": 1.0 if format_type == "long" else 1.05,  # Slightly faster for shorts
                "emotion": "energetic",
            },
        )
        self._enqueue_gpu_job(job)
        return job

    def dispatch_broll(self, production_id: str, script: dict) -> MediaJob:
        """Dispatch B-roll and thumbnail generation to Open-Gen-AI."""
        # Extract B-roll cues from script
        import re
        broll_cues = re.findall(r'\[B-ROLL:\s*(.+?)\]', script.get("script", ""))
        shot_list = script.get("shot_list", [])

        job = MediaJob(
            job_id=f"{production_id}_broll",
            production_id=production_id,
            job_type="broll",
            service_url=OPENGENAI_URL,
            payload={
                "broll_prompts": broll_cues[:10],  # Cap at 10 images
                "shot_list": shot_list[:8],
                "thumbnail_text": script.get("thumbnail_text", ""),
                "style": "cinematic_youtube",
                "resolution": {"width": 1920, "height": 1080},
                "thumbnail_resolution": {"width": 1280, "height": 720},
                "batch_size": min(len(broll_cues) + 1, 11),  # +1 for thumbnail
            },
        )
        self._enqueue_gpu_job(job)
        return job

    def dispatch_music(self, production_id: str, script: dict, format_type: str) -> MediaJob:
        """Dispatch background music generation to Maestro."""
        # Extract music mood from script
        import re
        music_cues = re.findall(r'\[MUSIC:\s*(.+?)\]', script.get("script", ""))
        primary_mood = music_cues[0] if music_cues else "upbeat motivational"

        duration = script.get("estimated_duration_minutes", 10) * 60 if format_type == "long" else 60

        job = MediaJob(
            job_id=f"{production_id}_music_{format_type}",
            production_id=production_id,
            job_type="music",
            service_url=MAESTRO_URL,
            payload={
                "mood": primary_mood,
                "duration_seconds": duration,
                "genre": "cinematic_ambient",
                "tempo": "medium" if format_type == "long" else "fast",
                "output_format": "wav",
                "loopable": format_type == "long",  # Long-form music should loop cleanly
            },
        )
        self._enqueue_gpu_job(job)
        return job

    def _enqueue_gpu_job(self, job: MediaJob):
        """Add job to GPU queue (triggers gpu_controller to wake VM)."""
        self.rdb.rpush(GPU_JOB_QUEUE_KEY, json.dumps(asdict(job)))
        log.info(f"  📤 Queued GPU job: {job.job_id} ({job.job_type})")

    # ── Job Execution ──────────────────────────────────────────────

    def execute_job(self, job: MediaJob) -> MediaJob:
        """Send job to GPU service and wait for result."""
        try:
            log.info(f"  ⚡ Executing: {job.job_id}")
            response = self.http.post(
                f"{job.service_url}/generate",
                json=job.payload,
            )
            response.raise_for_status()
            result = response.json()

            job.status = "completed"
            job.result_path = result.get("output_path", "")
            job.completed_at = datetime.now(timezone.utc).isoformat()
            log.info(f"  ✅ Completed: {job.job_id} → {job.result_path}")

        except Exception as e:
            job.status = "failed"
            job.error = str(e)[:200]
            log.error(f"  ❌ Failed: {job.job_id} — {e}")

        return job

    def wait_for_gpu_vm(self, timeout: int = 300) -> bool:
        """Wait for GPU VM to be ready (heartbeat appearing in Redis)."""
        log.info("  ⏳ Waiting for GPU VM to boot...")
        start = time.time()
        while time.time() - start < timeout:
            heartbeat = self.rdb.get(GPU_HEARTBEAT_KEY)
            if heartbeat:
                log.info("  🟢 GPU VM is ready")
                return True
            time.sleep(10)
        log.error("  ❌ GPU VM did not start within timeout")
        return False

    # ── Orchestration ──────────────────────────────────────────────

    def process_script_package(self, script_data: dict) -> Optional[MediaPackage]:
        """Process a full script package into a media package."""
        production_id = script_data.get("production_id", "unknown")
        log.info(f"🎬 Processing media for: {production_id}")

        long_form = script_data.get("long_form_script", {})
        shorts = script_data.get("shorts_script", {})

        # Dispatch all jobs (they queue up for the GPU)
        jobs = []
        if long_form:
            jobs.append(self.dispatch_voice(production_id, long_form, "long"))
            jobs.append(self.dispatch_broll(production_id, long_form))
            jobs.append(self.dispatch_music(production_id, long_form, "long"))

        if shorts:
            jobs.append(self.dispatch_voice(production_id, shorts, "short"))
            jobs.append(self.dispatch_music(production_id, shorts, "short"))

        if not jobs:
            log.warning(f"No scripts found in package {production_id}")
            return None

        # Wait for GPU VM to come online
        if not self.wait_for_gpu_vm():
            return None

        # Execute all jobs
        media = MediaPackage(production_id=production_id, script_package=script_data)
        for job in jobs:
            job = self.execute_job(job)
            # Update heartbeat so gpu_controller knows we're active
            self.rdb.set(GPU_HEARTBEAT_KEY, datetime.now(timezone.utc).isoformat())

            if job.status == "completed":
                if job.job_type == "voice" and "long" in job.job_id:
                    media.voice_long = job.result_path
                elif job.job_type == "voice" and "short" in job.job_id:
                    media.voice_short = job.result_path
                elif job.job_type == "broll":
                    media.broll_images = job.result_path.split(",") if job.result_path else []
                    if media.broll_images:
                        media.thumbnail = media.broll_images[-1]  # Last image is thumbnail
                elif job.job_type == "music" and "long" in job.job_id:
                    media.music_long = job.result_path
                elif job.job_type == "music" and "short" in job.job_id:
                    media.music_short = job.result_path

        media.status = "ready"
        return media

    # ── Main Loop ──────────────────────────────────────────────────

    def run(self):
        """Main loop — consume script packages and dispatch media jobs."""
        log.info("🎬 MediaDispatcher starting up...")
        while True:
            try:
                # Block-pop from script queue (wait up to 30s)
                result = self.rdb.blpop(SCRIPT_QUEUE_KEY, timeout=30)
                if not result:
                    continue

                _, script_json = result
                script_data = json.loads(script_json)

                media = self.process_script_package(script_data)
                if media:
                    self.rdb.rpush(MEDIA_READY_QUEUE_KEY, json.dumps(asdict(media)))
                    log.info(f"✅ Media package ready: {media.production_id}")

            except Exception as e:
                log.exception(f"Dispatch loop error: {e}")
                time.sleep(30)


if __name__ == "__main__":
    dispatcher = MediaDispatcher()
    dispatcher.run()
