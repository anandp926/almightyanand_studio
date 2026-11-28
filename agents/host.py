#!/usr/bin/env python3
"""
host.py — On-screen host (your character) in the reserved host zone.

The host is drawn on top of the moving background, so it stays steady while
the scene zooms. It works at three levels, depending on what is in
assets/character/:

  1. Nothing there: assets/character_ref.png is shown as a still, with a
     gentle bob.
  2. idle.png + talk.png (mouth closed / mouth open, transparent PNGs): the
     mouth opens with the loudness of the voice while a host speaker talks.
  3. Optional per-role poses: hook.png, reveal.png, cta.png (and
     hook_talk.png ...). The pose changes with the scene's role.

Render the poses from your 3D model with a transparent background, same
framing and size in every file. See assets/character/README.md.
"""

import array
import json
import logging
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image

from agents.settings import STUDIO_ROOT, env_bool

log = logging.getLogger("host")

CHARACTER_DIR = STUDIO_ROOT / "assets" / "character"
LEGACY_AVATAR = STUDIO_ROOT / "assets" / "character_ref.png"
STYLE_PATH = STUDIO_ROOT / "configs" / "visual_style.json"

MOUTH_WINDOW = 1.0 / 12.0       # seconds per mouth decision
DEFAULT_ZONE = {"enabled": True, "x": 40, "bottom": 1440, "height": 520}


@dataclass
class SpeechSpan:
    start: float
    end: float
    role: str
    host_is_speaking: bool


@dataclass
class HostTrack:
    input_args: List[str]       # FFmpeg input arguments for the host stream
    x: int
    y: int                      # top of the host box
    animated: bool

    def overlay_filter(self, background: str, host_label: str, out: str) -> str:
        """Overlay with a slow 5px bob so even a still host is not frozen."""
        return (
            f"{host_label}fps=30,format=rgba[hostv];"
            f"{background}[hostv]overlay=x={self.x}:y='{self.y}+5*sin(2*PI*t/2.6)':"
            f"eof_action=repeat:format=auto{out}"
        )


def load_host_zone() -> dict:
    try:
        zone = json.loads(STYLE_PATH.read_text(encoding="utf-8")).get("host_zone", {})
    except Exception:
        zone = {}
    return {**DEFAULT_ZONE, **{k: v for k, v in zone.items() if not k.startswith("_")}}


def find_poses(character_dir: Optional[Path] = None, legacy: Optional[Path] = None) -> Dict[str, Path]:
    """{pose name: png}. Always has 'idle' when any character art exists."""
    folder = character_dir or CHARACTER_DIR
    poses: Dict[str, Path] = {}
    if folder.is_dir():
        for path in sorted(folder.glob("*.png")):
            poses[path.stem.lower()] = path
    if "idle" not in poses:
        fallback = legacy or LEGACY_AVATAR
        if fallback.exists():
            poses["idle"] = fallback
    return poses


def mouth_envelope(voice_wav: Path, window: float = MOUTH_WINDOW) -> List[bool]:
    """True per window where the voice is loud enough for an open mouth."""
    with wave.open(str(voice_wav), "rb") as w:
        if w.getsampwidth() != 2:
            return []
        rate, channels = w.getframerate(), w.getnchannels()
        samples = array.array("h")
        samples.frombytes(w.readframes(w.getnframes()))
    if channels > 1:
        samples = samples[::channels]
    step = max(1, int(rate * window))
    levels = []
    for i in range(0, len(samples), step):
        chunk = samples[i:i + step]
        levels.append((sum(s * s for s in chunk) / max(1, len(chunk))) ** 0.5)
    if not levels:
        return []
    loud = sorted(levels)[int(len(levels) * 0.9)]
    threshold = max(250.0, loud * 0.30)
    return [level > threshold for level in levels]


def _pose_for(poses: Dict[str, Path], role: str, mouth_open: bool) -> str:
    """Best available pose name: role-specific first, then idle/talk."""
    if mouth_open:
        for name in (f"{role}_talk", "talk", role, "idle"):
            if name in poses:
                return name
    for name in (role, "idle"):
        if name in poses:
            return name
    return "idle"


def plan_frames(
    spans: List[SpeechSpan],
    envelope: List[bool],
    poses: Dict[str, Path],
    total_duration: float,
    window: float = MOUTH_WINDOW,
) -> List[Tuple[str, float]]:
    """[(pose name, seconds)] covering the whole video, with equal neighbours merged."""
    runs: List[Tuple[str, float]] = []
    steps = max(1, int(round(total_duration / window)))
    open_streak = 0
    for i in range(steps):
        t = (i + 0.5) * window
        span = next((s for s in spans if s.start <= t < s.end), None)
        role = span.role if span else "build"
        mouth_open = bool(span and span.host_is_speaking and i < len(envelope) and envelope[i])
        # Continuous speech keeps the level high; close the mouth for one window
        # after three open ones so it reads as talking, not as a held-open mouth.
        if mouth_open and open_streak >= 3:
            mouth_open = False
        open_streak = open_streak + 1 if mouth_open else 0
        pose = _pose_for(poses, role, mouth_open)
        if runs and runs[-1][0] == pose:
            runs[-1] = (pose, runs[-1][1] + window)
        else:
            runs.append((pose, window))
    return runs


def _prepare_frames(poses: Dict[str, Path], height: int, out_dir: Path) -> Tuple[Dict[str, Path], int]:
    """Scales every pose to the zone height on one shared transparent canvas."""
    scaled: Dict[str, Image.Image] = {}
    for name, path in poses.items():
        try:
            img = Image.open(path).convert("RGBA")
        except Exception as e:
            log.warning(f"Skipping unreadable pose {path.name}: {e}")
            continue
        width = max(2, round(img.width * height / img.height))
        scaled[name] = img.resize((width, height), Image.LANCZOS)
    if not scaled:
        return {}, 0
    canvas_w = max(img.width for img in scaled.values())
    canvas_w += canvas_w % 2
    canvas_h = height + height % 2
    out_dir.mkdir(parents=True, exist_ok=True)
    prepared: Dict[str, Path] = {}
    for name, img in scaled.items():
        canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
        canvas.paste(img, ((canvas_w - img.width) // 2, canvas_h - img.height), img)
        target = out_dir / f"host_{name}.png"
        canvas.save(target)
        prepared[name] = target
    return prepared, canvas_w


def build_host_track(
    voice_wav: Path,
    spans: List[SpeechSpan],
    total_duration: float,
    tmp_dir: Path,
    poses: Optional[Dict[str, Path]] = None,
) -> Optional[HostTrack]:
    """Host stream for one video, or None when the host is disabled or there is no art."""
    zone = load_host_zone()
    if not zone.get("enabled", True) or not env_bool("HOST_ENABLED", True):
        return None
    poses = poses if poses is not None else find_poses()
    if "idle" not in poses:
        return None

    height = int(zone["height"])
    frames, _ = _prepare_frames(poses, height, tmp_dir / "host")
    if "idle" not in frames:
        return None
    x, y = int(zone["x"]), int(zone["bottom"]) - height

    can_talk = any(name == "talk" or name.endswith("_talk") for name in frames)
    has_roles = any(name in frames for name in ("hook", "reveal", "cta"))
    if not can_talk and not has_roles:
        return HostTrack(["-loop", "1", "-framerate", "30", "-t", f"{total_duration:.2f}", "-i", str(frames["idle"])], x, y, False)

    envelope = mouth_envelope(voice_wav) if can_talk else []
    runs = plan_frames(spans, envelope, frames, total_duration)
    concat = tmp_dir / "host" / "host_frames.txt"
    lines = []
    for pose, seconds in runs:
        lines.append(f"file '{frames[pose].resolve()}'")
        lines.append(f"duration {seconds:.4f}")
    lines.append(f"file '{frames[runs[-1][0]].resolve()}'")     # concat demuxer needs the last file repeated
    concat.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return HostTrack(["-f", "concat", "-safe", "0", "-i", str(concat)], x, y, True)
