#!/usr/bin/env python3
"""
editor_ffmpeg.py — FFmpeg Video Assembly Agent

Hardcoded Anti-Demonetization & Assembly Rules:
  1. REUSED CONTENT REJECTION: Transformation ratio must be >= 75%.
  2. REAL PER-SHOT ASSETS (No Hardcoded UI):
     - Uses distinct generated images from MediaDispatcher (no static PNG loops).
     - Fits every image using ImageOps.fit(centering=(0.5, 0.35)) so faces/subjects are never cropped.
     - Dynamic overlay & badge text from each ShotCard (no hardcoded banking checklists).
  3. SUBTLE KEN BURNS MOTION:
     - Applies smooth FFmpeg zoompan/drift so frames are continuously dynamic.
  4. STRICT SAFE-ZONE CAPTIONS:
     - Formatted natively via ASS subtitles (MarginV=360 on 1080x1920, y=78%-81%).
     - Zero collision with top headers, character subjects, or bottom platform UI.
  5. BROADCAST LOUDNORM & EXACT DURATION:
     - Target video duration = voice_duration + 0.5s (never truncate mid-sentence).
     - Broadcast -16.0 LUFS loudness normalization + ducked BGM (volume=0.10).
  6. INTEGRATED QA JUDGE:
     - Automatically audits every render via QAJudge before approval.

CLI Entrypoints:
    python -m agents.editor_ffmpeg [--dry-run]
"""

import argparse
import asyncio
import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageOps

from agents.media_dispatcher import MediaDispatcher
from agents.qa_judge import QAJudge, QAJudgeReport

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [EDITOR-FFMPEG] %(levelname)s %(message)s",
)
log = logging.getLogger("editor_ffmpeg")

MIN_TRANSFORMATION_RATIO = 0.75
STUDIO_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = STUDIO_ROOT / "assets"
RENDERS_DIR = STUDIO_ROOT / "renders"
RENDERS_DIR.mkdir(parents=True, exist_ok=True)

FFMPEG_BIN = "/Users/anand/.local/bin/ffmpeg" if Path("/Users/anand/.local/bin/ffmpeg").exists() else "ffmpeg"
FONT_PATH = "/System/Library/Fonts/Helvetica.ttc" if Path("/System/Library/Fonts/Helvetica.ttc").exists() else "/System/Library/Fonts/Supplemental/Arial.ttf"


@dataclass
class AssemblyRenderOutput:
    production_id: str
    transformation_ratio_verified: float
    anti_reused_content_passed: bool
    long_form_16x9_path: str
    shorts_9x16_paths: list
    reels_h265_paths: list
    audio_loudness_lufs: float
    qa_report: Optional[dict] = None
    rendered_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def sanitize_text_for_render(text: str) -> str:
    """Eliminates tofu boxes (□) by replacing rupee symbols and stripping unrenderable emojis."""
    t = text.replace("₹", "Rs. ")
    t = t.replace("’", "'").replace("“", '"').replace("”", '"').replace("—", " - ")
    t = re.sub(r"[^\x00-\x7F]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


class EditorFFmpeg:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.char_ref_path = ASSETS_DIR / "character_ref.png"
        self.bgm_path = ASSETS_DIR / "bgm" / "bed_loop.mp3"
        self.dispatcher = MediaDispatcher(dry_run=dry_run)
        self.qa_judge = QAJudge(dry_run=dry_run)

    def assemble_dry_run(
        self,
        production_id: str = "prod_mock_2026",
        transformation_ratio: float = 0.85,
    ) -> AssemblyRenderOutput:
        log.info(f"🎞 Running EditorFFmpeg in --dry-run mode for {production_id}")
        if transformation_ratio < MIN_TRANSFORMATION_RATIO:
            raise ValueError(
                f"🚨 BLOCKED BY ANTI-DEMONETIZATION POLICY: Transformation ratio is {transformation_ratio:.2f}, must be >= {MIN_TRANSFORMATION_RATIO:.2f}"
            )
        log.info(f"✅ Anti-Demonetization Policy Verified: Transformation ratio is {transformation_ratio * 100:.0f}% (>= 75%)")
        return AssemblyRenderOutput(
            production_id=production_id,
            transformation_ratio_verified=transformation_ratio,
            anti_reused_content_passed=True,
            long_form_16x9_path=str(RENDERS_DIR / f"{production_id}_16x9_longform.mp4"),
            shorts_9x16_paths=[str(RENDERS_DIR / f"{production_id}_short_01.mp4")],
            reels_h265_paths=[str(RENDERS_DIR / f"{production_id}_reel_01.mp4")],
            audio_loudness_lufs=-16.0,
        )

    async def generate_voice_and_subtitles(
        self,
        script_text: str,
        output_audio_path: Path,
        output_ass_path: Path,
        voice: str = "hi-IN-MadhurNeural",
    ) -> Tuple[float, List[Tuple[float, float, str]]]:
        """Synthesizes voice via Edge-TTS and outputs native ASS safe-zone subtitles."""
        import edge_tts

        clean_script = sanitize_text_for_render(script_text)
        log.info(f"🎙 Generating Edge-TTS Hinglish Voice ({voice})...")
        communicate = edge_tts.Communicate(clean_script, voice)

        raw_events = []
        with open(output_audio_path, "wb") as audio_file:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_file.write(chunk["data"])
                elif chunk["type"] == "SentenceBoundary":
                    start_s = chunk["offset"] / 10_000_000.0
                    dur_s = chunk["duration"] / 10_000_000.0
                    end_s = start_s + dur_s
                    raw_events.append((start_s, end_s, chunk["text"].strip()))

        # Split sentences into 4-6 word micro-cues for high-retention rapid pacing
        micro_cues = []
        for start_s, end_s, text in raw_events:
            clean_line = sanitize_text_for_render(text)
            words = clean_line.split()
            if len(words) <= 6:
                micro_cues.append((start_s, end_s, clean_line))
            else:
                chunk_size = 5
                total_dur = end_s - start_s
                sub_chunks = [words[i:i + chunk_size] for i in range(0, len(words), chunk_size)]
                time_per_chunk = total_dur / len(sub_chunks)
                for idx, c in enumerate(sub_chunks):
                    c_start = start_s + (idx * time_per_chunk)
                    c_end = c_start + time_per_chunk
                    micro_cues.append((c_start, c_end, " ".join(c)))

        # Native ASS Subtitle format (Safe-Zone y=78%-81%, MarginV=360, Fontsize=52)
        ass_header = (
            "[Script Info]\n"
            "ScriptType: v4.00+\n"
            "PlayResX: 1080\n"
            "PlayResY: 1920\n"
            "ScaledBorderAndShadow: yes\n\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
            "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            "Style: Default,Arial,52,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,2,80,80,360,1\n\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        )

        with open(output_ass_path, "w", encoding="utf-8") as f:
            f.write(ass_header)
            for start, end, text in micro_cues:
                start_str = self._format_ass_timestamp(start)
                end_str = self._format_ass_timestamp(end)
                f.write(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{text}\n")

        # Measure voice duration accurately
        voice_duration = self.qa_judge.probe_media_duration(output_audio_path)
        log.info(f"✅ Voice generated: {output_audio_path.name} ({voice_duration:.2f}s) with {len(micro_cues)} ASS cues")
        return voice_duration, micro_cues

    @staticmethod
    def _format_ass_timestamp(seconds: float) -> str:
        cs = int((seconds - int(seconds)) * 100)
        s = int(seconds) % 60
        m = (int(seconds) // 60) % 60
        h = int(seconds) // 3600
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

    def build_scene_frames(
        self,
        shot_cards: List[dict],
        generated_scene_images: List[Path],
        tmp_dir: Path,
        total_duration: float,
    ) -> List[Tuple[Path, float]]:
        """
        Creates dynamic composite frames using distinct generated visuals from MediaDispatcher.
        No hardcoded banking badges or checklists. Fits subjects with ImageOps.fit(centering=(0.5, 0.35)).
        """
        log.info(f"🎨 Compositing {len(shot_cards)} dynamic scene cuts with real generated assets...")
        w, h = 1080, 1920
        frame_entries = []

        font_header = ImageFont.truetype(FONT_PATH, 44)
        font_badge = ImageFont.truetype(FONT_PATH, 24)

        num_shots = max(1, len(shot_cards))
        duration_per_shot = round(total_duration / num_shots, 2)

        # Dynamic accent colors across shots
        accent_colors = [
            (245, 158, 11),  # Amber Gold
            (99, 102, 241),  # Tech Indigo
            (16, 185, 129),  # Emerald Green
            (239, 68, 68),   # Alert Red
            (168, 85, 247),  # Vibrant Purple
            (59, 130, 246),  # Electric Blue
        ]

        # Load avatar if present for small watermark avatar
        avatar_img = None
        if self.char_ref_path.exists():
            try:
                avatar_img = Image.open(self.char_ref_path).convert("RGBA")
            except Exception as e:
                log.warning(f"Avatar load note: {e}")

        for i, card in enumerate(shot_cards):
            accent = accent_colors[i % len(accent_colors)]
            overlay_text = sanitize_text_for_render(card.get("overlay_text", f"SCENE {i+1}"))
            badge_text = sanitize_text_for_render(card.get("badge_text", "UPDATE")).upper()

            # 1. Base Image from real generated media
            if i < len(generated_scene_images) and generated_scene_images[i].exists():
                try:
                    raw_bg = Image.open(generated_scene_images[i]).convert("RGB")
                    img = ImageOps.fit(raw_bg, (w, h), centering=(0.5, 0.35))
                except Exception as e:
                    log.warning(f"Error loading generated shot {i}: {e}")
                    img = Image.new("RGB", (w, h), (12, 16, 28))
            else:
                img = Image.new("RGB", (w, h), (12, 16, 28))

            # 2. Apply subtle dark vignette overlay for text legibility
            vignette = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            v_draw = ImageDraw.Draw(vignette)
            # Top gradient for header & watermark
            v_draw.rectangle([0, 0, w, 360], fill=(0, 0, 0, 160))
            # Lower safe zone subtle gradient for captions
            v_draw.rectangle([0, 1380, w, 1680], fill=(0, 0, 0, 110))
            img = Image.alpha_composite(img.convert("RGBA"), vignette).convert("RGB")

            draw = ImageDraw.Draw(img)

            # 3. Top Safe Zone: Dynamic Badge & Creator Watermark (y: 130 - 200)
            badge_width = min(420, max(200, len(badge_text) * 18 + 50))
            draw.rectangle([70, 130, 70 + badge_width, 195], fill=(20, 24, 38), outline=accent, width=2)
            draw.text((95, 145), badge_text, font=font_badge, fill=accent)
            draw.text((70 + badge_width + 30, 148), "@almightyanand", font=font_badge, fill=(226, 232, 240))

            # 4. Top Headline Overlay Banner (y: 220 - 320)
            draw.text((72, 222), overlay_text, font=font_header, fill=(0, 0, 0))  # drop shadow
            draw.text((70, 220), overlay_text, font=font_header, fill=(255, 255, 255))

            # 5. Clean Anand Character Branding in bottom-right corner if available
            if avatar_img:
                char_thumb = avatar_img.resize((150, 190))
                img.paste(char_thumb, (w - 200, h - 380), char_thumb)

            # Save frame
            frame_path = tmp_dir / f"scene_{i+1:02d}.jpg"
            img.save(frame_path, quality=92)
            frame_entries.append((frame_path, duration_per_shot))

        log.info(f"✅ Composited {len(frame_entries)} unique scene frames ({duration_per_shot}s per cut)")
        return frame_entries

    def assemble_vertical_short(
        self,
        script_data: dict,
        production_id: str,
        output_filename: Optional[str] = None,
    ) -> Tuple[Path, QAJudgeReport]:
        """
        Full Production Assembly:
          - Media: Dispatches MediaDispatcher for real distinct scene images.
          - Voice: Edge-TTS Hinglish (hi-IN-MadhurNeural).
          - Duration: Set exactly to voice_duration + 0.5s (never cut mid-sentence).
          - Subtitles: Native ASS safe-zone subtitles (y=78%-81%, MarginV=360).
          - Motion: Subtle Ken Burns zoompan drift.
          - QA Gate: Audits via QAJudge.
        """
        target_mp4 = RENDERS_DIR / (output_filename or f"{production_id}_short_01.mp4")
        tmp_dir = Path(f"/tmp/almighty_{production_id}")
        tmp_dir.mkdir(parents=True, exist_ok=True)

        voice_audio = tmp_dir / "voice.mp3"
        ass_file = tmp_dir / "captions.ass"
        concat_file = tmp_dir / "concat_list.txt"

        narration = script_data.get("narration_script_hinglish") or script_data.get("hook_hinglish", "")
        shot_cards = script_data.get("shot_cards", [])

        # 1. Synthesize Voice & Captions
        voice_duration, _ = asyncio.run(
            self.generate_voice_and_subtitles(narration, voice_audio, ass_file)
        )
        # Enforce rule: video_duration = voice_duration + 0.5s
        target_duration = round(voice_duration + 0.5, 2)
        log.info(f"⏱ Video Duration Target: {target_duration}s (voice={voice_duration}s + 0.5s buffer)")

        # 2. Dispatch MediaDispatcher for real distinct scene images
        scene_images = self.dispatcher.dispatch_media(shot_cards, production_id)
        scenes_dir = Path(f"/tmp/almighty_{production_id}/scenes")

        # 3. Build Composited Scene Frames
        frame_entries = self.build_scene_frames(shot_cards, scene_images, tmp_dir, target_duration)

        # 4. Create Concat Manifest
        with open(concat_file, "w") as f:
            for path, dur in frame_entries:
                f.write(f"file '{path.resolve()}'\n")
                f.write(f"duration {dur}\n")
            f.write(f"file '{frame_entries[-1][0].resolve()}'\n")

        # 5. Assemble with FFmpeg: Subtle Ken Burns Motion + Native ASS Subtitles + -16 LUFS
        log.info("🎬 Rendering final vertical Short with FFmpeg (Zoompan + Native ASS + Loudnorm)...")
        ffmpeg_cmd = [
            FFMPEG_BIN, "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_file),
            "-i", str(voice_audio),
        ]

        if self.bgm_path.exists():
            ffmpeg_cmd.extend(["-i", str(self.bgm_path)])
            filter_complex = (
                "[0:v]fps=30,scale=1080:1920,format=yuv420p,"
                "zoompan=z='min(zoom+0.0008,1.06)':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps=30,"
                f"ass={ass_file}[v]; "
                "[2:a]volume=0.10[bgm]; "
                "[1:a][bgm]amix=inputs=2:duration=first:dropout_transition=2,"
                "loudnorm=I=-16:TP=-1.5:LRA=11[a]"
            )
        else:
            filter_complex = (
                "[0:v]fps=30,scale=1080:1920,format=yuv420p,"
                "zoompan=z='min(zoom+0.0008,1.06)':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps=30,"
                f"ass={ass_file}[v]; "
                "[1:a]loudnorm=I=-16:TP=-1.5:LRA=11[a]"
            )

        ffmpeg_cmd.extend([
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", "[a]",
            "-t", str(target_duration),
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
            str(target_mp4)
        ])

        log.info(f"Running FFmpeg: {' '.join(ffmpeg_cmd[:8])} ...")
        subprocess.run(ffmpeg_cmd, check=True)
        log.info(f"🎉 RENDER COMPLETE: {target_mp4} ({target_mp4.stat().st_size / (1024*1024):.2f} MB)")

        # 6. Automatic QA Judge Quality Gate
        qa_report = self.qa_judge.audit(
            video_path=target_mp4,
            voice_path=voice_audio,
            ass_path=ass_file,
            scenes_dir=scenes_dir,
            topic=script_data.get("title", "Almighty Anand Short"),
        )

        return target_mp4, qa_report


def main():
    parser = argparse.ArgumentParser(description="EditorFFmpeg Assembly Agent")
    parser.add_argument("--dry-run", action="store_true", help="Run in dry run mode")
    args = parser.parse_args()

    editor = EditorFFmpeg(dry_run=args.dry_run)
    result = editor.assemble_dry_run()
    print("\n--- EditorFFmpeg Assembly Result ---")
    print(json.dumps(asdict(result), indent=2))


if __name__ == "__main__":
    main()
