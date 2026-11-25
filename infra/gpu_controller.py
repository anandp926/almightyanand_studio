#!/usr/bin/env python3
"""
gpu_controller.py — Spot GPU VM Lifecycle Manager

Monitors Redis job queue; wakes the g2-standard-4 Spot GPU VM when batched
jobs are queued, enforces a 4.5 hr/day hard cap, and auto-stops on idle.

Budget guardrail: ~$0.40/hr × 4.5 hr/day = ~$1.80/day = ~$54/month max GPU spend.
Actual target: ~$40/month (batch jobs into fewer, longer sessions).
"""

import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional

import redis
import subprocess

# ── Configuration ──────────────────────────────────────────────────

PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "")
ZONE = os.environ.get("GCP_ZONE", "us-central1-a")
GPU_VM_NAME = os.environ.get("GPU_VM_NAME", "almighty-gpu-worker")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/6")

# Hard limits
DAILY_GPU_HOURS_CAP = 4.5          # Maximum GPU hours per calendar day (UTC)
IDLE_TIMEOUT_SECONDS = 300         # 5 minutes idle → auto-stop
MIN_BATCH_SIZE = 3                 # Minimum queued jobs before waking GPU
POLL_INTERVAL_SECONDS = 30        # How often to check queue & VM status
STARTUP_GRACE_SECONDS = 180       # Wait for VM to boot before checking idle
COST_PER_HOUR = 0.40               # Approximate Spot cost for g2-standard-4

# Redis keys
QUEUE_KEY = "gpu:job_queue"
USAGE_KEY_PREFIX = "gpu:daily_usage"  # gpu:daily_usage:YYYY-MM-DD
STATE_KEY = "gpu:controller_state"
HEARTBEAT_KEY = "gpu:worker_heartbeat"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [GPU-CTRL] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("gpu_controller")


@dataclass
class ControllerState:
    """Tracks GPU session state across polls."""
    vm_running: bool = False
    session_start: Optional[datetime] = None
    last_activity: Optional[datetime] = None
    daily_seconds_used: float = 0.0
    today: str = ""

    def reset_if_new_day(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.today != today:
            log.info(f"New day detected ({self.today} → {today}). Resetting daily usage.")
            self.daily_seconds_used = 0.0
            self.today = today

    @property
    def daily_hours_used(self) -> float:
        return self.daily_seconds_used / 3600.0

    @property
    def daily_budget_remaining(self) -> float:
        return max(0, DAILY_GPU_HOURS_CAP - self.daily_hours_used)

    @property
    def estimated_daily_cost(self) -> float:
        return self.daily_hours_used * COST_PER_HOUR


class GPUController:
    """Manages GPU Spot VM lifecycle based on job queue and budget."""

    def __init__(self):
        self.rdb = redis.from_url(REDIS_URL, decode_responses=True)
        self.state = ControllerState()
        self._running = True
        self._load_state()
        try:
            if self._get_vm_status() == "RUNNING":
                self.state.vm_running = True
                if not self.state.session_start:
                    self.state.session_start = datetime.now(timezone.utc)
                if not self.state.last_activity:
                    self.state.last_activity = datetime.now(timezone.utc)
                log.info("Detected already-running GPU VM on startup; initialized safety timers.")
        except Exception as e:
            log.warning(f"Could not inspect initial GPU VM status: {e}")
        signal.signal(signal.SIGTERM, self._shutdown)
        signal.signal(signal.SIGINT, self._shutdown)

    def _shutdown(self, signum, frame):
        log.info("Shutdown signal received. Saving state...")
        self._save_state()
        self._running = False

    def _load_state(self):
        """Load persisted state from Redis."""
        raw = self.rdb.get(STATE_KEY)
        if raw:
            data = json.loads(raw)
            self.state.daily_seconds_used = data.get("daily_seconds_used", 0.0)
            self.state.today = data.get("today", "")
            log.info(f"Loaded state: {self.state.daily_hours_used:.2f}h used today")
        self.state.reset_if_new_day()

    def _save_state(self):
        """Persist state to Redis."""
        self.rdb.set(STATE_KEY, json.dumps({
            "daily_seconds_used": self.state.daily_seconds_used,
            "today": self.state.today,
            "last_save": datetime.now(timezone.utc).isoformat(),
        }))

    # ── GCP VM Control ─────────────────────────────────────────────

    def _gcloud(self, *args: str) -> subprocess.CompletedProcess:
        cmd = ["gcloud", "compute", "instances", *args,
               f"--zone={ZONE}", f"--project={PROJECT_ID}", "--quiet"]
        log.debug(f"Running: {' '.join(cmd)}")
        return subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    def _get_vm_status(self) -> str:
        """Returns VM status: RUNNING, TERMINATED, STOPPED, etc."""
        result = self._gcloud("describe", GPU_VM_NAME, "--format=get(status)")
        return result.stdout.strip().upper() if result.returncode == 0 else "UNKNOWN"

    def start_gpu_vm(self) -> bool:
        """Start the GPU Spot VM."""
        log.info("🚀 Starting GPU Spot VM...")
        result = self._gcloud("start", GPU_VM_NAME)
        if result.returncode == 0:
            self.state.vm_running = True
            self.state.session_start = datetime.now(timezone.utc)
            self.state.last_activity = datetime.now(timezone.utc)
            log.info("✅ GPU VM started successfully")
            return True
        else:
            log.error(f"❌ Failed to start GPU VM: {result.stderr}")
            return False

    def stop_gpu_vm(self, reason: str = "unknown") -> bool:
        """Stop the GPU Spot VM and record usage."""
        log.info(f"🛑 Stopping GPU VM (reason: {reason})...")
        if self.state.session_start:
            session_duration = (datetime.now(timezone.utc) - self.state.session_start).total_seconds()
            self.state.daily_seconds_used += session_duration
            log.info(f"Session lasted {session_duration/60:.1f} min. "
                     f"Daily total: {self.state.daily_hours_used:.2f}h / {DAILY_GPU_HOURS_CAP}h")

        result = self._gcloud("stop", GPU_VM_NAME)
        self.state.vm_running = False
        self.state.session_start = None
        self._save_state()

        if result.returncode == 0:
            log.info("✅ GPU VM stopped")
            return True
        else:
            log.warning(f"⚠ Stop command returned error (may already be stopped): {result.stderr}")
            return False

    # ── Queue & Activity Monitoring ────────────────────────────────

    def get_queue_length(self) -> int:
        return self.rdb.llen(QUEUE_KEY)

    def has_active_workers(self) -> bool:
        """Check if GPU workers have sent a heartbeat recently."""
        heartbeat = self.rdb.get(HEARTBEAT_KEY)
        if not heartbeat:
            return False
        last_beat = datetime.fromisoformat(heartbeat)
        return (datetime.now(timezone.utc) - last_beat).total_seconds() < 60

    def update_activity(self):
        """Mark activity if workers are alive."""
        if self.has_active_workers():
            self.state.last_activity = datetime.now(timezone.utc)

    # ── Main Control Loop ──────────────────────────────────────────

    def should_start(self) -> bool:
        """Decide whether to wake the GPU VM."""
        queue_len = self.get_queue_length()

        if queue_len < MIN_BATCH_SIZE:
            return False

        if self.state.daily_budget_remaining <= 0.25:
            log.warning(f"⚠ Daily GPU budget nearly exhausted "
                       f"({self.state.daily_hours_used:.2f}h / {DAILY_GPU_HOURS_CAP}h). "
                       f"Holding jobs until tomorrow.")
            return False

        log.info(f"📋 {queue_len} jobs queued, {self.state.daily_budget_remaining:.1f}h budget remaining. "
                 f"Starting GPU VM.")
        return True

    def should_stop(self) -> bool:
        """Decide whether to stop a running GPU VM."""
        # Hard cap reached
        if self.state.session_start:
            session_hours = (datetime.now(timezone.utc) - self.state.session_start).total_seconds() / 3600
            total_today = self.state.daily_hours_used + session_hours

            if total_today >= DAILY_GPU_HOURS_CAP:
                log.warning(f"🛑 HARD CAP: Daily GPU limit of {DAILY_GPU_HOURS_CAP}h reached!")
                return True

        # Still in startup grace period
        if self.state.session_start:
            uptime = (datetime.now(timezone.utc) - self.state.session_start).total_seconds()
            if uptime < STARTUP_GRACE_SECONDS:
                return False

        # Idle timeout
        if self.state.last_activity:
            idle_seconds = (datetime.now(timezone.utc) - self.state.last_activity).total_seconds()
            if idle_seconds > IDLE_TIMEOUT_SECONDS:
                queue_len = self.get_queue_length()
                if queue_len == 0:
                    log.info(f"💤 GPU idle for {idle_seconds:.0f}s with empty queue. Stopping.")
                    return True
                else:
                    log.info(f"💤 GPU idle for {idle_seconds:.0f}s but {queue_len} jobs in queue. "
                             f"Resetting idle timer.")
                    self.state.last_activity = datetime.now(timezone.utc)
                    return False

        return False

    def run(self):
        """Main control loop."""
        log.info("╔══════════════════════════════════════════════════╗")
        log.info("║   GPU Controller started                        ║")
        log.info(f"║   Daily cap: {DAILY_GPU_HOURS_CAP}h · "
                 f"Idle timeout: {IDLE_TIMEOUT_SECONDS}s          ║")
        log.info(f"║   Min batch: {MIN_BATCH_SIZE} jobs · "
                 f"Poll: {POLL_INTERVAL_SECONDS}s               ║")
        log.info("╚══════════════════════════════════════════════════╝")

        while self._running:
            try:
                self.state.reset_if_new_day()

                # Sync actual VM status
                actual_status = self._get_vm_status()
                self.state.vm_running = actual_status == "RUNNING"

                if self.state.vm_running:
                    self.update_activity()
                    if self.should_stop():
                        self.stop_gpu_vm(reason="idle_or_cap")
                else:
                    if self.should_start():
                        self.start_gpu_vm()

                # Periodic state save
                self._save_state()

                # Log status
                queue_len = self.get_queue_length()
                status_emoji = "🟢" if self.state.vm_running else "🔴"
                log.info(f"{status_emoji} VM={'ON' if self.state.vm_running else 'OFF'} | "
                         f"Queue={queue_len} | "
                         f"Today={self.state.daily_hours_used:.2f}h/{DAILY_GPU_HOURS_CAP}h | "
                         f"Cost≈${self.state.estimated_daily_cost:.2f}")

            except redis.ConnectionError:
                log.error("Redis connection lost. Retrying in 30s...")
            except Exception as e:
                log.exception(f"Unexpected error: {e}")

            time.sleep(POLL_INTERVAL_SECONDS)

        log.info("GPU Controller stopped.")


if __name__ == "__main__":
    if not PROJECT_ID:
        log.error("GCP_PROJECT_ID not set. Exiting.")
        sys.exit(1)

    controller = GPUController()
    controller.run()
