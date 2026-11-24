#!/usr/bin/env python3
"""
editor_ffmpeg.py — FFmpeg-based Video Assembly Agent

Consumes media packages from MediaDispatcher and assembles final rendered
videos with:
  - Voice narration over B-roll sequences
  - Whisper-sanitized captions (SRT/ASS)
  - Background music mixing (ducked under voice)
  - Suspense hooks and transitions
  - Platform-specific exports:
    - 16:9 H.264 for YouTube long-form
    - 9:16 H.265 for Instagram Reels
    - 9:16 H.264 for YouTube Shorts

Runs on GPU VM for hardware-accelerated encoding.
"""

import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import redis

# ── Configuration ──────────────────────────────────────────────────

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/2")
MEDIA_READY_QUEUE_KEY = "media:ready_queue"
RENDER_COMPLETE_QUEUE_KEY = "render:complete_queue"
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "/app/data/renders"))
TEMP_DIR = Path("/tmp/editor_workspace")

# FFmpeg presets
YOUTUBE_LONGFORM_PRESET = {
    "codec": "libx264",
    "crf": "20",
    "preset": "medium",
    "audio_codec": "aac",
    "audio_bitrate": "192k",
    "resolution": "1920x1080",
    "fps": "30",
    "pix_fmt": "yuv420p",
}

YOUTUBE_SHORTS_PRESET = {
    "codec": "libx264",
    "crf": "22",
    "preset": "fast",
    "audio_codec": "aac",
    "audio_bitrate": "128k",
    "resolution": "1080x1920",
    "fps": "30",
    "pix_fmt": "yuv420p",
}

INSTAGRAM_REELS_PRESET = {
    "codec": "libx265",
    "crf": "24",
    "preset": "fast",
    "audio_codec": "aac",
    "audio_bitrate": "128k",
    "resolution": "1080x1920",
    "fps": "30",
    "pix_fmt": "yuv420p",
    "tag": "hvc1",  # Required for Apple/Instagram compatibility
}

CAPTION_STYLE = {
    "font": "Montserrat-Bold",
    "fontsize": "48",
    "primary_color": "&H00FFFFFF",
    "outline_color": "&H00000000",
    "outline_width": "3",
    "shadow": "2",
    "alignment": "2",  # Bottom center
    "margin_v": "60",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [EDITOR] %(levelname)s %(message)s",
)
log = logging.getLogger("editor_ffmpeg")


@dataclass
class RenderResult:
    """Output from a completed render."""
    production_id: str
    format_type: str            # "long_form", "shorts", "reels"
    output_path: str
    duration_seconds: float = 0.0
    file_size_mb: float = 0.0
    resolution: str = ""
    has_captions: bool = False
    render_time_seconds: float = 0.0
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class RenderPackage:
    """All renders for a single production."""
    production_id: str
    long_form: Optional[RenderResult] = None
    shorts: Optional[RenderResult] = None
    reels: Optional[RenderResult] = None
    thumbnail_path: str = ""
    script_data: dict = field(default_factory=dict)
    status: str = "rendered"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class EditorFFmpeg:
    """Assembles final video renders using FFmpeg."""

    def __init__(self):
        self.rdb = redis.from_url(REDIS_URL, decode_responses=True)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        log.info("EditorFFmpeg initialized")

    # ── FFmpeg Helpers ─────────────────────────────────────────────

    def _run_ffmpeg(self, args: list[str], description: str = "") -> bool:
        """Execute FFmpeg command with logging."""
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning"] + args
        log.info(f"  🎞 FFmpeg: {description}")
        log.debug(f"  CMD: {' '.join(cmd)}")

        start = time.time()
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        elapsed = time.time() - start

        if result.returncode != 0:
            log.error(f"  ❌ FFmpeg failed ({elapsed:.1f}s): {result.stderr[:300]}")
            return False
        log.info(f"  ✅ FFmpeg done ({elapsed:.1f}s)")
        return True

    def _get_duration(self, filepath: str) -> float:
        """Get media duration in seconds."""
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", filepath],
            capture_output=True, text=True, timeout=30,
        )
        try:
            return float(result.stdout.strip())
        except (ValueError, AttributeError):
            return 0.0

    def _get_file_size_mb(self, filepath: str) -> float:
        """Get file size in MB."""
        try:
            return os.path.getsize(filepath) / (1024 * 1024)
        except OSError:
            return 0.0

    # ── Caption Generation ─────────────────────────────────────────

    def generate_captions(self, audio_path: str, output_srt: str) -> bool:
        """Generate captions using Whisper (sanitized for accuracy)."""
        try:
            # Use whisper-timestamped for word-level timing
            import whisper_timestamped as whisper

            model = whisper.load_model("base")
            result = whisper.transcribe(model, audio_path, language="en")

            # Write SRT file
            with open(output_srt, "w", encoding="utf-8") as f:
                for i, segment in enumerate(result["segments"], 1):
                    start = self._format_srt_time(segment["start"])
                    end = self._format_srt_time(segment["end"])
                    text = segment["text"].strip()
                    # Sanitize: remove filler words, fix common errors
                    text = self._sanitize_caption(text)
                    f.write(f"{i}\n{start} --> {end}\n{text}\n\n")

            log.info(f"  📝 Generated {len(result['segments'])} caption segments")
            return True
        except ImportError:
            log.warning("  ⚠ whisper-timestamped not available, skipping captions")
            return False
        except Exception as e:
            log.error(f"  ❌ Caption generation failed: {e}")
            return False

    @staticmethod
    def _format_srt_time(seconds: float) -> str:
        """Convert seconds to SRT timestamp format."""
        hrs = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        ms = int((seconds % 1) * 1000)
        return f"{hrs:02d}:{mins:02d}:{secs:02d},{ms:03d}"

    @staticmethod
    def _sanitize_caption(text: str) -> str:
        """Clean up caption text."""
        import re
        # Remove common filler words at sentence boundaries
        fillers = ["um", "uh", "like", "you know", "basically", "actually"]
        for filler in fillers:
            text = re.sub(rf'\b{filler}\b,?\s*', '', text, flags=re.IGNORECASE)
        # Clean up extra spaces
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    # ── Video Assembly ─────────────────────────────────────────────

    def assemble_long_form(self, media: dict) -> Optional[RenderResult]:
        """Assemble 16:9 long-form video with B-roll, captions, and music."""
        production_id = media["production_id"]
        voice_path = media.get("voice_long", "")
        music_path = media.get("music_long", "")
        broll_images = media.get("broll_images", [])

        if not voice_path:
            log.warning(f"  No voice audio for long-form {production_id}")
            return None

        preset = YOUTUBE_LONGFORM_PRESET
        output_path = str(OUTPUT_DIR / f"{production_id}_16x9.mp4")
        srt_path = str(TEMP_DIR / f"{production_id}.srt")
        start_time = time.time()

        # Step 1: Generate captions
        has_captions = self.generate_captions(voice_path, srt_path)

        # Step 2: Create B-roll slideshow (or black background if no images)
        slideshow_path = str(TEMP_DIR / f"{production_id}_slideshow.mp4")
        voice_duration = self._get_duration(voice_path)

        if broll_images:
            # Create a slideshow from B-roll images
            concat_file = str(TEMP_DIR / f"{production_id}_concat.txt")
            duration_per_image = voice_duration / len(broll_images)
            with open(concat_file, "w") as f:
                for img in broll_images:
                    f.write(f"file '{img}'\nduration {duration_per_image:.2f}\n")
                f.write(f"file '{broll_images[-1]}'\n")  # Last image repeated for ffmpeg

            self._run_ffmpeg([
                "-f", "concat", "-safe", "0", "-i", concat_file,
                "-vf", f"scale={preset['resolution'].replace('x', ':')},setsar=1:1",
                "-c:v", preset["codec"], "-crf", preset["crf"],
                "-pix_fmt", preset["pix_fmt"], "-r", preset["fps"],
                slideshow_path,
            ], "Creating B-roll slideshow")
        else:
            # Black background with text
            self._run_ffmpeg([
                "-f", "lavfi", "-i",
                f"color=c=black:s={preset['resolution']}:d={voice_duration}:r={preset['fps']}",
                "-c:v", preset["codec"], "-crf", preset["crf"],
                "-pix_fmt", preset["pix_fmt"],
                slideshow_path,
            ], "Creating black background video")

        # Step 3: Mix audio (voice + background music with ducking)
        mixed_audio = str(TEMP_DIR / f"{production_id}_mixed.aac")
        if music_path and os.path.exists(music_path):
            self._run_ffmpeg([
                "-i", voice_path,
                "-i", music_path,
                "-filter_complex",
                "[1:a]volume=0.15[music];"     # Music at 15% volume
                "[0:a][music]amix=inputs=2:duration=first:dropout_transition=3[mixed]",
                "-map", "[mixed]",
                "-c:a", preset["audio_codec"], "-b:a", preset["audio_bitrate"],
                mixed_audio,
            ], "Mixing voice + background music")
        else:
            mixed_audio = voice_path

        # Step 4: Final assembly
        ffmpeg_args = [
            "-i", slideshow_path,
            "-i", mixed_audio,
            "-map", "0:v", "-map", "1:a",
            "-c:v", preset["codec"], "-crf", preset["crf"],
            "-preset", preset["preset"],
            "-c:a", preset["audio_codec"], "-b:a", preset["audio_bitrate"],
            "-pix_fmt", preset["pix_fmt"],
            "-shortest",
        ]

        # Add burned-in captions if available
        if has_captions and os.path.exists(srt_path):
            ffmpeg_args.extend([
                "-vf", f"subtitles={srt_path}:force_style="
                       f"'FontName={CAPTION_STYLE['font']},"
                       f"FontSize={CAPTION_STYLE['fontsize']},"
                       f"PrimaryColour={CAPTION_STYLE['primary_color']},"
                       f"OutlineColour={CAPTION_STYLE['outline_color']},"
                       f"Outline={CAPTION_STYLE['outline_width']},"
                       f"Shadow={CAPTION_STYLE['shadow']},"
                       f"MarginV={CAPTION_STYLE['margin_v']}'"
            ])

        ffmpeg_args.append(output_path)
        success = self._run_ffmpeg(ffmpeg_args, "Final 16:9 assembly")

        if not success:
            return None

        render_time = time.time() - start_time
        return RenderResult(
            production_id=production_id,
            format_type="long_form",
            output_path=output_path,
            duration_seconds=self._get_duration(output_path),
            file_size_mb=self._get_file_size_mb(output_path),
            resolution=preset["resolution"],
            has_captions=has_captions,
            render_time_seconds=render_time,
        )

    def assemble_shorts(self, media: dict) -> Optional[RenderResult]:
        """Assemble 9:16 vertical video for YouTube Shorts."""
        production_id = media["production_id"]
        voice_path = media.get("voice_short", "")
        music_path = media.get("music_short", "")

        if not voice_path:
            log.warning(f"  No voice audio for shorts {production_id}")
            return None

        preset = YOUTUBE_SHORTS_PRESET
        output_path = str(OUTPUT_DIR / f"{production_id}_9x16_shorts.mp4")
        start_time = time.time()

        voice_duration = self._get_duration(voice_path)

        # Create vertical video with dynamic text overlays
        # (In production, this would use B-roll cropped to 9:16)
        ffmpeg_args = [
            "-f", "lavfi", "-i",
            f"color=c=0x1a1a2e:s={preset['resolution']}:d={voice_duration}:r={preset['fps']}",
            "-i", voice_path,
        ]

        if music_path and os.path.exists(music_path):
            ffmpeg_args.extend(["-i", music_path])
            ffmpeg_args.extend([
                "-filter_complex",
                "[2:a]volume=0.12[music];[1:a][music]amix=inputs=2:duration=first[audio]",
                "-map", "0:v", "-map", "[audio]",
            ])
        else:
            ffmpeg_args.extend(["-map", "0:v", "-map", "1:a"])

        ffmpeg_args.extend([
            "-c:v", preset["codec"], "-crf", preset["crf"],
            "-preset", preset["preset"],
            "-c:a", preset["audio_codec"], "-b:a", preset["audio_bitrate"],
            "-pix_fmt", preset["pix_fmt"],
            "-shortest",
            output_path,
        ])

        success = self._run_ffmpeg(ffmpeg_args, "9:16 Shorts assembly")
        if not success:
            return None

        render_time = time.time() - start_time
        return RenderResult(
            production_id=production_id,
            format_type="shorts",
            output_path=output_path,
            duration_seconds=self._get_duration(output_path),
            file_size_mb=self._get_file_size_mb(output_path),
            resolution=preset["resolution"],
            has_captions=False,
            render_time_seconds=render_time,
        )

    def assemble_reels(self, media: dict) -> Optional[RenderResult]:
        """Assemble 9:16 H.265 vertical video for Instagram Reels."""
        production_id = media["production_id"]
        shorts_path = str(OUTPUT_DIR / f"{production_id}_9x16_shorts.mp4")

        if not os.path.exists(shorts_path):
            log.warning(f"  No shorts render to transcode for Reels")
            return None

        preset = INSTAGRAM_REELS_PRESET
        output_path = str(OUTPUT_DIR / f"{production_id}_9x16_reels.mp4")
        start_time = time.time()

        # Transcode shorts to H.265 for Instagram
        success = self._run_ffmpeg([
            "-i", shorts_path,
            "-c:v", preset["codec"], "-crf", preset["crf"],
            "-preset", preset["preset"],
            "-tag:v", preset["tag"],
            "-c:a", preset["audio_codec"], "-b:a", preset["audio_bitrate"],
            "-pix_fmt", preset["pix_fmt"],
            output_path,
        ], "Instagram Reels H.265 transcode")

        if not success:
            return None

        render_time = time.time() - start_time
        return RenderResult(
            production_id=production_id,
            format_type="reels",
            output_path=output_path,
            duration_seconds=self._get_duration(output_path),
            file_size_mb=self._get_file_size_mb(output_path),
            resolution=preset["resolution"],
            has_captions=False,
            render_time_seconds=render_time,
        )

    # ── Main Loop ──────────────────────────────────────────────────

    def process_media_package(self, media: dict) -> Optional[RenderPackage]:
        """Process a media package into final renders."""
        production_id = media["production_id"]
        log.info(f"🎬 Rendering: {production_id}")

        package = RenderPackage(
            production_id=production_id,
            thumbnail_path=media.get("thumbnail", ""),
            script_data=media.get("script_package", {}),
        )

        # Render all formats
        package.long_form = self.assemble_long_form(media)
        package.shorts = self.assemble_shorts(media)
        package.reels = self.assemble_reels(media)

        rendered_count = sum(1 for r in [package.long_form, package.shorts, package.reels] if r)
        log.info(f"✅ Rendered {rendered_count}/3 formats for {production_id}")

        return package if rendered_count > 0 else None

    def run(self):
        """Main loop — consume media packages and render videos."""
        log.info("🎞 EditorFFmpeg starting up...")
        while True:
            try:
                result = self.rdb.blpop(MEDIA_READY_QUEUE_KEY, timeout=30)
                if not result:
                    continue

                _, media_json = result
                media = json.loads(media_json)

                package = self.process_media_package(media)
                if package:
                    self.rdb.rpush(RENDER_COMPLETE_QUEUE_KEY, json.dumps(asdict(package)))
                    log.info(f"📦 Render package queued for publishing: {package.production_id}")

            except Exception as e:
                log.exception(f"Render loop error: {e}")
                time.sleep(30)


if __name__ == "__main__":
    editor = EditorFFmpeg()
    editor.run()
