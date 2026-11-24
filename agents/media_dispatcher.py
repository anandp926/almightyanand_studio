#!/usr/bin/env python3
"""
media_dispatcher.py — Media Dispatcher Agent

Dispatches parallel tasks to GPU services:
  1. VoiceStudio (port 8090): Voice cloning with Anand's profile + Hindi dubbing
  2. Open-Gen-AI (port 8091): Visual shot-card & B-roll generation
  3. Maestro (port 8092): Background music & scoring generation

CLI Entrypoint:
    python -m agents.media_dispatcher [--dry-run]
"""

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [MEDIA-DISPATCH] %(levelname)s %(message)s",
)
log = logging.getLogger("media_dispatcher")


@dataclass
class DispatchedMediaManifest:
    production_id: str
    voice_long_path: str
    voice_short_paths: list
    broll_image_paths: list
    music_track_path: str
    hindi_dubbed_voice_path: str
    gpu_credits_spent: float
    dispatched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class MediaDispatcher:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run

    def dispatch_dry_run(self, production_id: str = "prod_mock_2026") -> DispatchedMediaManifest:
        log.info(f"🧪 Running MediaDispatcher in --dry-run mode for {production_id}")
        log.info(f"🎙 [VoiceStudio :8090] Mocking voice clone with Anand's XTTS profile (0 GPU credits)")
        log.info(f"🌐 [VoiceStudio :8090] Mocking Hindi auto-dubbing audio generation (0 GPU credits)")
        log.info(f"🎨 [Open-Gen-AI :8091] Mocking visual shot-card diffusion render for 4 B-roll cards")
        log.info(f"🎵 [Maestro :8092] Mocking background score composition (genre: cinematic tech)")

        manifest = DispatchedMediaManifest(
            production_id=production_id,
            voice_long_path=f"/mock/data/renders/{production_id}_voice_16x9.wav",
            voice_short_paths=[
                f"/mock/data/renders/{production_id}_short_01.wav",
                f"/mock/data/renders/{production_id}_short_02.wav",
                f"/mock/data/renders/{production_id}_short_03.wav",
            ],
            broll_image_paths=[
                f"/mock/data/renders/{production_id}_broll_01.png",
                f"/mock/data/renders/{production_id}_broll_02.png",
                f"/mock/data/renders/{production_id}_broll_03.png",
                f"/mock/data/renders/{production_id}_broll_04.png",
            ],
            music_track_path=f"/mock/data/renders/{production_id}_bgm_maestro.wav",
            hindi_dubbed_voice_path=f"/mock/data/renders/{production_id}_voice_hindi.wav",
            gpu_credits_spent=0.00,
        )
        log.info(f"✅ Media assets successfully prepared (GPU Credits Spent: $0.00)")
        return manifest


def main():
    parser = argparse.ArgumentParser(description="MediaDispatcher Agent")
    parser.add_argument("--dry-run", action="store_true", help="Run in dry-run mode without GPU VM")
    args = parser.parse_args()

    dispatcher = MediaDispatcher(dry_run=args.dry_run)
    manifest = dispatcher.dispatch_dry_run()
    print("\n--- MediaDispatcher Result ---")
    print(json.dumps(asdict(manifest), indent=2))


if __name__ == "__main__":
    main()
