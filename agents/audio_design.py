#!/usr/bin/env python3
"""
audio_design.py — Background music, sound effects and the final audio mix.

Music:
  Drop licensed loops into assets/bgm/<mood>/ (moods: urgent, curious, calm; a
  pillar's mood is `bgm_mood` in viral_playbook.json). Files directly in
  assets/bgm/ are used for any mood. One track is picked per video. With no
  files there is no music; nothing is generated silently.

Sound effects:
  A soft whoosh on scene cuts and a low hit on the reveal. Put your own at
  assets/sfx/whoosh.* and assets/sfx/hit.*; otherwise simple ones are
  synthesized once with FFmpeg into data/sfx_generated/. SFX_ENABLED=false
  turns them off.

Mix:
  Music is ducked under the voice with a sidechain compressor, effects are
  placed at cut times, and the result is normalized to -16 LUFS.

CLI:
    python -m agents.audio_design --make-placeholder-beds   # synthetic pads, until you add real music
    python -m agents.audio_design --list
"""

import argparse
import hashlib
import logging
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from agents.playbook import load_playbook
from agents.settings import STUDIO_ROOT, env_bool, env_float, ffmpeg_bin

log = logging.getLogger("audio_design")

BGM_DIR = STUDIO_ROOT / "assets" / "bgm"
SFX_DIR = STUDIO_ROOT / "assets" / "sfx"
GENERATED_SFX_DIR = STUDIO_ROOT / "data" / "sfx_generated"
AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".ogg", ".flac")
MOODS = ("urgent", "curious", "calm")
MAX_WHOOSHES = 10

# FFmpeg lavfi sources for the built-in effects.
_SFX_RECIPES = {
    "whoosh": (
        "anoisesrc=d=0.45:c=pink:a=0.7:r=48000,highpass=f=500,lowpass=f=7000,"
        "afade=t=in:st=0:d=0.28:curve=qsin,afade=t=out:st=0.28:d=0.17"
    ),
    "hit": (
        "sine=f=62:d=0.6:r=48000,volume=1.6,afade=t=in:st=0:d=0.008,afade=t=out:st=0.03:d=0.55:curve=exp"
    ),
}

# Root notes (Hz) for the optional placeholder pads.
_PAD_CHORDS = {
    "urgent": (110.0, 130.81, 164.81),     # A minor
    "curious": (130.81, 164.81, 196.0),    # C major
    "calm": (98.0, 123.47, 146.83),        # G major
}


def _audio_files(folder: Path) -> List[Path]:
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS)


def pick_bgm(category: str, key: str, playbook: Optional[dict] = None) -> Optional[Path]:
    """One track for this video: the pillar's mood folder first, then assets/bgm/ itself."""
    pb = playbook or load_playbook()
    mood = pb.get("pillars", {}).get(category, {}).get("bgm_mood", "curious")
    candidates = _audio_files(BGM_DIR / mood) or _audio_files(BGM_DIR)
    if not candidates:
        return None
    index = int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16) % len(candidates)
    return candidates[index]


def _user_sfx(name: str) -> Optional[Path]:
    for ext in AUDIO_EXTS:
        path = SFX_DIR / f"{name}{ext}"
        if path.exists():
            return path
    return None


def ensure_sfx(ffmpeg: Optional[str] = None) -> Dict[str, Path]:
    """Paths of the whoosh and hit effects, synthesizing the built-in ones if needed."""
    if not env_bool("SFX_ENABLED", True):
        return {}
    ffmpeg = ffmpeg or ffmpeg_bin()
    result: Dict[str, Path] = {}
    for name, recipe in _SFX_RECIPES.items():
        user_file = _user_sfx(name)
        if user_file:
            result[name] = user_file
            continue
        generated = GENERATED_SFX_DIR / f"{name}.wav"
        if not generated.exists():
            try:
                GENERATED_SFX_DIR.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    [ffmpeg, "-y", "-f", "lavfi", "-i", recipe, "-ac", "2", "-c:a", "pcm_s16le", str(generated)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
                )
            except Exception as e:
                log.warning(f"Could not synthesize '{name}' effect ({e}); continuing without it.")
                continue
        result[name] = generated
    return result


def build_audio_graph(
    voice_idx: int,
    bgm_idx: Optional[int] = None,
    bgm_volume: float = 0.10,
    whoosh_idx: Optional[int] = None,
    whoosh_times: Optional[List[float]] = None,
    hit_idx: Optional[int] = None,
    hit_times: Optional[List[float]] = None,
) -> str:
    """
    FFmpeg filter graph that ends in [a]. Inputs are referenced by index; the
    voice is always the first stream into the mix so it sets the duration.
    """
    whoosh_times = sorted(t for t in (whoosh_times or []) if t >= 0)[:MAX_WHOOSHES] if whoosh_idx is not None else []
    hit_times = sorted(t for t in (hit_times or []) if t >= 0)[:2] if hit_idx is not None else []
    whoosh_volume = env_float("SFX_WHOOSH_VOLUME", 0.22)
    hit_volume = env_float("SFX_HIT_VOLUME", 0.55)

    parts: List[str] = []
    mix_inputs: List[str] = ["[vox]"]

    if bgm_idx is not None:
        parts.append(f"[{voice_idx}:a]aresample=48000,aformat=channel_layouts=stereo,asplit=2[vox][sc]")
        parts.append(f"[{bgm_idx}:a]aresample=48000,aformat=channel_layouts=stereo,volume={bgm_volume:.3f}[bed]")
        parts.append("[bed][sc]sidechaincompress=threshold=0.03:ratio=6:attack=15:release=350[duck]")
        mix_inputs.append("[duck]")
    else:
        parts.append(f"[{voice_idx}:a]aresample=48000,aformat=channel_layouts=stereo[vox]")

    def place(idx: int, times: List[float], volume: float, tag: str):
        if not times:
            return
        if len(times) == 1:
            sources = [f"[{idx}:a]"]
        else:
            labels = "".join(f"[{tag}s{i}]" for i in range(len(times)))
            parts.append(f"[{idx}:a]asplit={len(times)}{labels}")
            sources = [f"[{tag}s{i}]" for i in range(len(times))]
        for i, (src, t) in enumerate(zip(sources, times)):
            parts.append(
                f"{src}aresample=48000,aformat=channel_layouts=stereo,"
                f"adelay={int(round(t * 1000))}:all=1,volume={volume:.3f}[{tag}{i}]"
            )
            mix_inputs.append(f"[{tag}{i}]")

    if whoosh_idx is not None:
        place(whoosh_idx, whoosh_times, whoosh_volume, "w")
    if hit_idx is not None:
        place(hit_idx, hit_times, hit_volume, "h")

    if len(mix_inputs) == 1:
        parts.append("[vox]loudnorm=I=-16:TP=-1.5:LRA=11[a]")
    else:
        parts.append(
            f"{''.join(mix_inputs)}amix=inputs={len(mix_inputs)}:duration=first:normalize=0:dropout_transition=0,"
            "loudnorm=I=-16:TP=-1.5:LRA=11[a]"
        )
    return ";".join(parts)


def sfx_times(scene_starts: List[float], roles: List[str]) -> Tuple[List[float], List[float]]:
    """(whoosh times, hit times): a whoosh just before every cut, a hit where the reveal starts."""
    whooshes = [max(0.0, t - 0.15) for t in scene_starts[1:]]
    hits = [t for t, role in zip(scene_starts, roles) if role == "reveal" and t > 0]
    return whooshes, hits


def make_placeholder_beds(ffmpeg: Optional[str] = None, seconds: int = 32) -> List[Path]:
    """Synthetic pad loops, one per mood, so there is some bed until real music is added."""
    ffmpeg = ffmpeg or ffmpeg_bin()
    created = []
    for mood, freqs in _PAD_CHORDS.items():
        folder = BGM_DIR / mood
        folder.mkdir(parents=True, exist_ok=True)
        out = folder / "placeholder_pad.wav"
        voices = "+".join(f"0.16*sin(2*PI*{f}*t)+0.07*sin(2*PI*{f * 2}*t)" for f in freqs)
        source = f"aevalsrc='({voices})*(0.75+0.25*sin(2*PI*0.12*t))':s=48000:d={seconds}"
        chain = f"{source},lowpass=f=1800,afade=t=in:st=0:d=2,afade=t=out:st={seconds - 2}:d=2"
        subprocess.run(
            [ffmpeg, "-y", "-f", "lavfi", "-i", chain, "-ac", "2", "-c:a", "pcm_s16le", str(out)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True,
        )
        created.append(out)
    return created


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [AUDIO] %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Music and sound-effect assets")
    parser.add_argument("--make-placeholder-beds", action="store_true", help="Synthesize a simple pad per mood into assets/bgm/")
    parser.add_argument("--list", action="store_true", help="Show which music and effects would be used")
    args = parser.parse_args()

    if args.make_placeholder_beds:
        for path in make_placeholder_beds():
            print("created", path)
    if args.list or not args.make_placeholder_beds:
        for mood in MOODS:
            print(f"bgm/{mood}: {[p.name for p in _audio_files(BGM_DIR / mood)] or 'none'}")
        print(f"bgm (any mood): {[p.name for p in _audio_files(BGM_DIR)] or 'none'}")
        for name, path in ensure_sfx().items():
            print(f"sfx/{name}: {path}")


if __name__ == "__main__":
    main()
