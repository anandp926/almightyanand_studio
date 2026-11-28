#!/usr/bin/env python3
"""
editor_ffmpeg.py — FFmpeg Video Assembly Agent

Assembly rules:
  1. PER-SHOT ASSETS: one generated image per shot card from MediaDispatcher,
     with the card's overlay and badge text composited on top.
  2. VOICE: one provider per video (Sarvam Bulbul, else Edge-TTS), spoken from
     the Devanagari voice track, with pace set by each scene's role and real
     silence inserted before the reveal.
  3. SCENE CUTS FOLLOW THE VOICE: each cut lands where its line starts.
  4. KEN BURNS PER SCENE: every scene gets its own slow zoom.
  5. HOST: the character is overlaid in the host zone, above the moving
     background, with its mouth following the voice when talk poses exist.
  6. SAFE-ZONE CAPTIONS: Roman Hinglish ASS subtitles, MarginV=360.
  7. AUDIO MIX: music ducked under the voice, a whoosh on cuts, a hit on the
     reveal, normalized to -16 LUFS.
  8. QA GATE, then the scratch directory is removed (KEEP_TMP=1 keeps it).

CLI Entrypoints:
    python -m agents.editor_ffmpeg [--dry-run]
"""

import argparse
import asyncio
import json
import logging
import math
import re
import shutil
import subprocess
import wave
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageOps

from agents import audio_design, host as host_module, voice as voice_module
from agents.host import HostTrack, SpeechSpan
from agents.media_dispatcher import MediaDispatcher, work_dir
from agents.playbook import load_playbook
from agents.qa_judge import QAJudge, QAJudgeReport
from agents.settings import STUDIO_ROOT, env_bool, ffmpeg_bin
from agents.voice import VoiceSegment

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [EDITOR-FFMPEG] %(levelname)s %(message)s",
)
log = logging.getLogger("editor_ffmpeg")

ASSETS_DIR = STUDIO_ROOT / "assets"
RENDERS_DIR = STUDIO_ROOT / "renders"
RENDERS_DIR.mkdir(parents=True, exist_ok=True)

FPS = 30
WIDTH, HEIGHT = 1080, 1920
TAIL_SECONDS = 0.5
ZOOM_AMOUNT = 0.06          # each scene drifts from 1.00x to 1.06x
ZOOM_SUPERSAMPLE = 1.5      # upscale before zoompan to avoid sub-pixel jitter
VOICE_SAMPLE_RATE = 24000

FONT_PATH = next(
    (p for p in [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ] if Path(p).exists()),
    None,
)


@dataclass
class AssemblyRenderOutput:
    production_id: str
    long_form_16x9_path: str
    shorts_9x16_paths: list
    reels_h265_paths: list
    audio_loudness_lufs: float
    qa_report: Optional[dict] = None
    rendered_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class VoiceTrack:
    duration: float                                   # seconds of voice.wav
    cues: List[Tuple[float, float, str]]              # caption cues (start, end, text)
    segments: List[VoiceSegment]
    speech_starts: List[float]                        # when each segment's speech begins
    speech_durations: List[float]
    provider: str = ""

    def scene_durations(self, target_duration: float) -> List[float]:
        """Each scene runs from its line's start to the next line's start, so cuts land on the voice."""
        starts = self.speech_starts
        durations = [starts[i + 1] - starts[i] for i in range(len(starts) - 1)]
        durations.append(max(0.1, target_duration - starts[-1]))
        return durations

    def spans(self, host_speakers: List[str]) -> List[SpeechSpan]:
        return [
            SpeechSpan(start, start + dur, seg.role, seg.speaker in host_speakers)
            for seg, start, dur in zip(self.segments, self.speech_starts, self.speech_durations)
        ]


def sanitize_text_for_render(text: str) -> str:
    """Eliminates tofu boxes (□) by replacing rupee symbols and stripping unrenderable glyphs."""
    t = text.replace("₹", "Rs. ")
    t = t.replace("’", "'").replace("“", '"').replace("”", '"').replace("—", " - ")
    t = re.sub(r"[^\x00-\x7F]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _load_font(size: int) -> ImageFont.ImageFont:
    if FONT_PATH:
        try:
            return ImageFont.truetype(FONT_PATH, size)
        except Exception as e:
            log.warning(f"Could not load font {FONT_PATH}: {e}")
    return ImageFont.load_default()


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def plan_scene_durations(segment_durations: List[float], num_scenes: int, target_duration: float) -> List[float]:
    """
    Seconds each scene stays on screen. When there is one narration segment per
    scene, each scene matches its segment and the last one absorbs the tail.
    Otherwise the video is split evenly.
    """
    num_scenes = max(1, num_scenes)
    if len(segment_durations) == num_scenes and all(d > 0 for d in segment_durations):
        durations = list(segment_durations)
        durations[-1] = max(durations[-1], target_duration - sum(durations[:-1]))
        return durations
    return [target_duration / num_scenes] * num_scenes


def _ffmpeg_filter_path(path: Path) -> str:
    """Escapes a path for use inside an FFmpeg filter argument."""
    p = str(path).replace("\\", "/")
    return p.replace(":", "\\:").replace("'", "\\'")


class EditorFFmpeg:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.ffmpeg = ffmpeg_bin()
        self.last_render_info: dict = {}
        self.dispatcher = MediaDispatcher(dry_run=dry_run)
        self.qa_judge = QAJudge(dry_run=dry_run)

    def assemble_dry_run(self, production_id: str = "prod_mock_2026") -> AssemblyRenderOutput:
        log.info(f"🎞 Running EditorFFmpeg in --dry-run mode for {production_id}")
        return AssemblyRenderOutput(
            production_id=production_id,
            long_form_16x9_path=str(RENDERS_DIR / f"{production_id}_16x9_longform.mp4"),
            shorts_9x16_paths=[str(RENDERS_DIR / f"{production_id}_short_01.mp4")],
            reels_h265_paths=[str(RENDERS_DIR / f"{production_id}_reel_01.mp4")],
            audio_loudness_lufs=-16.0,
        )

    # ── Voice & captions ──────────────────────────────────────────────

    async def _synthesize(self, segments: List[VoiceSegment], out_dir: Path, playbook: dict):
        """(provider name, [audio paths]). Isolated so tests can replace it."""
        return await voice_module.synthesize_all(segments, out_dir, playbook)

    def _to_wav(self, src: Path, dst: Path):
        subprocess.run(
            [self.ffmpeg, "-y", "-i", str(src), "-ar", str(VOICE_SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", str(dst)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
        )

    @staticmethod
    def _write_silence(dst: Path, seconds: float):
        with wave.open(str(dst), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(VOICE_SAMPLE_RATE)
            w.writeframes(b"\x00\x00" * int(round(seconds * VOICE_SAMPLE_RATE)))

    @staticmethod
    def _concat_wavs(parts: List[Path], dst: Path):
        with wave.open(str(dst), "wb") as out:
            for i, part in enumerate(parts):
                with wave.open(str(part), "rb") as w:
                    if i == 0:
                        out.setparams(w.getparams())
                    out.writeframes(w.readframes(w.getnframes()))

    async def generate_voice_and_subtitles(
        self,
        script_text: str,
        output_audio_path: Path,
        output_ass_path: Path,
        shot_cards: Optional[List[dict]] = None,
        category: str = "technology",
    ) -> VoiceTrack:
        """
        Synthesizes the narration and writes the caption file. Durations are
        measured from PCM, so captions, cuts and the host's mouth stay in sync.
        """
        playbook = load_playbook()
        segments = voice_module.build_segments(shot_cards or [], category, playbook)
        if not segments:
            default_speaker = playbook.get("pillars", {}).get(category, {}).get("default_speaker", "narrator")
            segments = voice_module.build_segments(
                [{"narration_cue": script_text, "speaker": default_speaker, "role": "build"}], category, playbook
            )
        for seg in segments:
            seg.caption = sanitize_text_for_render(seg.caption)

        chunks_dir = output_audio_path.parent / "voice_chunks"
        chunks_dir.mkdir(parents=True, exist_ok=True)
        provider, raw_paths = await self._synthesize(segments, chunks_dir, playbook)

        parts: List[Path] = []
        speech_starts: List[float] = []
        speech_durations: List[float] = []
        cues: List[Tuple[float, float, str]] = []
        cursor = 0.0

        for idx, (seg, raw) in enumerate(zip(segments, raw_paths)):
            if seg.pause_before > 0:
                silence = chunks_dir / f"pause_{idx:02d}.wav"
                self._write_silence(silence, seg.pause_before)
                parts.append(silence)
                cursor += _wav_duration(silence)

            chunk_wav = chunks_dir / f"seg_{idx:02d}.wav"
            self._to_wav(raw, chunk_wav)
            seg_dur = _wav_duration(chunk_wav)
            parts.append(chunk_wav)
            speech_starts.append(cursor)
            speech_durations.append(seg_dur)

            # 5-word caption cues spread over this segment
            words = seg.caption.replace("{", "(").replace("}", ")").split()
            sub_chunks = [words[i:i + 5] for i in range(0, len(words), 5)] or [words]
            per_chunk = seg_dur / len(sub_chunks)
            for j, c in enumerate(sub_chunks):
                cues.append((cursor + j * per_chunk, cursor + (j + 1) * per_chunk, " ".join(c)))
            cursor += seg_dur

        self._concat_wavs(parts, output_audio_path)

        ass_header = (
            "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nScaledBorderAndShadow: yes\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
            "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            "Style: Default,Arial,52,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,2,80,80,360,1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        )
        with open(output_ass_path, "w", encoding="utf-8") as f:
            f.write(ass_header)
            for start, end, text in cues:
                f.write(f"Dialogue: 0,{self._format_ass_timestamp(start)},{self._format_ass_timestamp(end)},Default,,0,0,0,,{text}\n")

        voice_duration = _wav_duration(output_audio_path)
        with_voice_track = sum(1 for seg in segments if seg.tts_text)
        log.info(
            f"✅ Voice via {provider}: {len(segments)} segments, {voice_duration:.2f}s, "
            f"{with_voice_track} spoken from the Devanagari track"
        )
        return VoiceTrack(voice_duration, cues, segments, speech_starts, speech_durations, provider)

    @staticmethod
    def _format_ass_timestamp(seconds: float) -> str:
        total_cs = int(round(seconds * 100))
        cs = total_cs % 100
        s = (total_cs // 100) % 60
        m = (total_cs // 6000) % 60
        h = total_cs // 360000
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

    # ── Frames ────────────────────────────────────────────────────────

    def build_scene_frames(
        self,
        shot_cards: List[dict],
        generated_scene_images: List[Path],
        tmp_dir: Path,
        durations: List[float],
    ) -> List[Tuple[Path, float]]:
        """Composites badge, handle and headline over each generated image. The host is added later, above the zoom."""
        log.info(f"🎨 Compositing {len(shot_cards)} scene cuts...")
        w, h = WIDTH, HEIGHT
        frame_entries = []

        font_header = _load_font(44)
        font_badge = _load_font(24)

        accent_colors = [
            (245, 158, 11),  # Amber Gold
            (99, 102, 241),  # Tech Indigo
            (16, 185, 129),  # Emerald Green
            (239, 68, 68),   # Alert Red
            (168, 85, 247),  # Vibrant Purple
            (59, 130, 246),  # Electric Blue
        ]

        for i, card in enumerate(shot_cards):
            accent = accent_colors[i % len(accent_colors)]
            overlay_text = sanitize_text_for_render(card.get("overlay_text", f"SCENE {i+1}"))
            badge_text = sanitize_text_for_render(card.get("badge_text", "UPDATE")).upper()

            # 1. Base image from generated media
            if i < len(generated_scene_images) and generated_scene_images[i].exists():
                raw_bg = Image.open(generated_scene_images[i]).convert("RGB")
                img = ImageOps.fit(raw_bg, (w, h), centering=(0.5, 0.35))
            else:
                raise RuntimeError(f"Scene {i+1} has no generated image; refusing to render a blank frame.")

            # 2. Dark bands for text legibility
            vignette = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            v_draw = ImageDraw.Draw(vignette)
            v_draw.rectangle([0, 0, w, 360], fill=(0, 0, 0, 160))
            v_draw.rectangle([0, 1380, w, 1680], fill=(0, 0, 0, 110))
            img = Image.alpha_composite(img.convert("RGBA"), vignette).convert("RGB")

            draw = ImageDraw.Draw(img)

            # 3. Top safe zone: badge & creator handle (y: 130 - 200)
            badge_width = min(420, max(200, len(badge_text) * 18 + 50))
            draw.rectangle([70, 130, 70 + badge_width, 195], fill=(20, 24, 38), outline=accent, width=2)
            draw.text((95, 145), badge_text, font=font_badge, fill=accent)
            draw.text((70 + badge_width + 30, 148), "@almightyanand", font=font_badge, fill=(226, 232, 240))

            # 4. Headline banner (y: 220 - 320)
            draw.text((72, 222), overlay_text, font=font_header, fill=(0, 0, 0))  # drop shadow
            draw.text((70, 220), overlay_text, font=font_header, fill=(255, 255, 255))

            frame_path = tmp_dir / f"scene_{i+1:02d}.jpg"
            img.save(frame_path, quality=92)
            frame_entries.append((frame_path, durations[i]))

        log.info(f"✅ Composited {len(frame_entries)} scene frames")
        return frame_entries

    # ── Render ────────────────────────────────────────────────────────

    def render_video(
        self,
        frame_entries: List[Tuple[Path, float]],
        voice_audio: Path,
        ass_file: Path,
        target_mp4: Path,
        target_duration: float,
        bgm_volume: float = 0.10,
        bgm_path: Optional[Path] = None,
        host_track: Optional[HostTrack] = None,
        sfx: Optional[dict] = None,
        scene_roles: Optional[List[str]] = None,
    ):
        """
        Video: each still becomes its own clip with a zoom from 1.00x to 1.06x
        over that clip's frames (so it restarts at every cut); the clips are
        concatenated, the host is overlaid, then captions are burned in.
        Audio: voice + ducked music + effects at the cut times.
        """
        cmd = [self.ffmpeg, "-y"]
        for path, _ in frame_entries:
            cmd.extend(["-i", str(path)])
        next_idx = len(frame_entries)

        voice_idx = next_idx
        cmd.extend(["-i", str(voice_audio)])
        next_idx += 1

        host_idx = None
        if host_track:
            host_idx = next_idx
            cmd.extend(host_track.input_args)
            next_idx += 1

        bgm_idx = None
        if bgm_path and Path(bgm_path).exists():
            bgm_idx = next_idx
            cmd.extend(["-stream_loop", "-1", "-i", str(bgm_path)])
            next_idx += 1

        sfx = sfx or {}
        whoosh_idx = hit_idx = None
        if sfx.get("whoosh"):
            whoosh_idx = next_idx
            cmd.extend(["-i", str(sfx["whoosh"])])
            next_idx += 1
        if sfx.get("hit"):
            hit_idx = next_idx
            cmd.extend(["-i", str(sfx["hit"])])
            next_idx += 1

        up_w = int(WIDTH * ZOOM_SUPERSAMPLE) // 2 * 2
        up_h = int(HEIGHT * ZOOM_SUPERSAMPLE) // 2 * 2

        chains = []
        scene_starts: List[float] = []
        frames_so_far = 0
        total_frames = math.ceil(target_duration * FPS)
        for i, (_, dur) in enumerate(frame_entries):
            if i == len(frame_entries) - 1:
                frames = max(1, total_frames - frames_so_far)  # last scene runs to the end
            else:
                frames = max(1, round(dur * FPS))
            scene_starts.append(frames_so_far / FPS)
            frames_so_far += frames
            chains.append(
                f"[{i}:v]scale={up_w}:{up_h}:flags=lanczos,"
                f"zoompan=z='1+{ZOOM_AMOUNT}*on/{frames}':"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                f"d={frames}:s={WIDTH}x{HEIGHT}:fps={FPS},setsar=1,format=yuv420p[v{i}]"
            )

        concat_inputs = "".join(f"[v{i}]" for i in range(len(frame_entries)))
        video_graph = ";".join(chains) + f";{concat_inputs}concat=n={len(frame_entries)}:v=1:a=0[bgv]"
        captions_in = "[bgv]"
        if host_track:
            video_graph += ";" + host_track.overlay_filter("[bgv]", f"[{host_idx}:v]", "[hosted]")
            captions_in = "[hosted]"
        video_graph += f";{captions_in}ass='{_ffmpeg_filter_path(ass_file)}'[v]"

        roles = scene_roles or ["build"] * len(frame_entries)
        whoosh_times, hit_times = audio_design.sfx_times(scene_starts, roles)
        audio_graph = audio_design.build_audio_graph(
            voice_idx,
            bgm_idx=bgm_idx,
            bgm_volume=bgm_volume,
            whoosh_idx=whoosh_idx,
            whoosh_times=whoosh_times,
            hit_idx=hit_idx,
            hit_times=hit_times,
        )

        cmd.extend([
            "-filter_complex", f"{video_graph};{audio_graph}",
            "-map", "[v]", "-map", "[a]",
            "-t", f"{target_duration:.2f}",
            "-r", str(FPS),
            "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart",
            str(target_mp4),
        ])

        log.info(
            f"🎬 Rendering {len(frame_entries)} scenes, {target_duration:.2f}s "
            f"(host: {'animated' if host_track and host_track.animated else 'still' if host_track else 'off'}, "
            f"music: {Path(bgm_path).name if bgm_idx is not None else 'none'}, "
            f"sfx: {len(whoosh_times) if whoosh_idx is not None else 0} whoosh / {len(hit_times) if hit_idx is not None else 0} hit)"
        )
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg render failed: {result.stderr[-1500:]}")
        log.info(f"🎉 RENDER COMPLETE: {target_mp4} ({target_mp4.stat().st_size / (1024*1024):.2f} MB)")

    def assemble_vertical_short(
        self,
        script_data: dict,
        production_id: str,
        output_filename: Optional[str] = None,
        attempt: int = 1,
    ) -> Tuple[Path, QAJudgeReport]:
        """
        Voice -> images -> frames -> render -> QA. `attempt` is the QA attempt
        number; it changes the image seeds so a retry is not a re-run of the
        frames that just failed.
        """
        target_mp4 = RENDERS_DIR / (output_filename or f"{production_id}_short_01.mp4")
        tmp_dir = work_dir(production_id)
        tmp_dir.mkdir(parents=True, exist_ok=True)

        try:
            voice_audio = tmp_dir / "voice.wav"
            ass_file = tmp_dir / "captions.ass"

            narration = script_data.get("narration_script_hinglish") or script_data.get("hook_hinglish", "")
            category = script_data.get("category", "technology")
            shot_cards = script_data.get("shot_cards", [])
            # Scenes and narration segments must stay 1-to-1, so a card without a
            # narration cue is dropped whenever cues are in use.
            if any(sanitize_text_for_render(c.get("narration_cue", "")) for c in shot_cards):
                shot_cards = [c for c in shot_cards if sanitize_text_for_render(c.get("narration_cue", ""))]
            if not shot_cards:
                raise RuntimeError("No shot cards to render.")

            # 1. Voice & captions
            track = asyncio.run(
                self.generate_voice_and_subtitles(
                    narration, voice_audio, ass_file, shot_cards=shot_cards, category=category,
                )
            )
            target_duration = round(track.duration + TAIL_SECONDS, 2)
            if len(track.segments) == len(shot_cards):
                durations = track.scene_durations(target_duration)
                roles = [seg.role for seg in track.segments]
            else:
                durations = plan_scene_durations([], len(shot_cards), target_duration)
                roles = ["build"] * len(shot_cards)
            log.info(f"⏱ Video duration: {target_duration}s (voice {track.duration:.2f}s + {TAIL_SECONDS}s)")

            # 2. Images (new seeds on every QA attempt)
            scene_images = self.dispatcher.dispatch_media(shot_cards, production_id, seed_salt=attempt - 1)
            scenes_dir = tmp_dir / "scenes"

            # 3. Composite, host, music, render
            frame_entries = self.build_scene_frames(shot_cards, scene_images, tmp_dir, durations)
            playbook = load_playbook()
            pillar = playbook.get("pillars", {}).get(category, {})
            host_track = host_module.build_host_track(
                voice_audio, track.spans(playbook.get("host_speakers") or [pillar.get("default_speaker", "narrator")]),
                target_duration, tmp_dir,
            )
            bgm_path = audio_design.pick_bgm(category, production_id, playbook)
            self.render_video(
                frame_entries, voice_audio, ass_file, target_mp4, target_duration,
                bgm_volume=float(pillar.get("bgm_volume", 0.10)),
                bgm_path=bgm_path,
                host_track=host_track,
                sfx=audio_design.ensure_sfx(self.ffmpeg),
                scene_roles=roles,
            )
            main_segment = track.segments[0]
            self.last_render_info = {
                "voice_provider": track.provider,
                "speaker": main_segment.speaker,
                "pace": main_segment.pace,
                "duration_s": target_duration,
                "music": bgm_path.name if bgm_path else "",
                "host": "animated" if host_track and host_track.animated else "still" if host_track else "off",
            }

            # 4. QA gate
            qa_report = self.qa_judge.audit(
                video_path=target_mp4,
                voice_path=voice_audio,
                ass_path=ass_file,
                scenes_dir=scenes_dir,
                topic=script_data.get("title", "Almighty Anand Short"),
            )

            if qa_report.status != "PASS":
                rejected_dir = RENDERS_DIR / "rejected"
                rejected_dir.mkdir(parents=True, exist_ok=True)
                rejected_path = rejected_dir / target_mp4.name
                target_mp4.rename(rejected_path)
                qa_report.video_path = str(rejected_path)
                log.error(f"🚫 QA FAILED — moved render to {rejected_path}")
                return rejected_path, qa_report

            return target_mp4, qa_report
        finally:
            if not env_bool("KEEP_TMP", False):
                shutil.rmtree(tmp_dir, ignore_errors=True)


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
