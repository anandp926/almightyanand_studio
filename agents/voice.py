#!/usr/bin/env python3
"""
voice.py — Speech synthesis for narration segments.

Two text tracks per line:
  - `caption` : Roman Hinglish, shown on screen.
  - `tts_text`: the same line with Hindi words in Devanagari and English words
                in Latin script. TTS engines pronounce this far more reliably
                than Romanised Hindi.

Providers:
  - sarvam : Sarvam Bulbul (needs SARVAM_API_KEY). Built for Hinglish code-mixing.
  - edge   : Edge-TTS. Free, no key. Used as the fallback.

TTS_PROVIDER=auto (default) uses Sarvam when a key is set, otherwise Edge. A
video is voiced by ONE provider: if any Sarvam segment fails, the whole video
is redone with Edge instead of mixing two voices (unless TTS_PROVIDER=sarvam,
which fails instead).

CLI (listen to a line before spending a render on it):
    python -m agents.voice --text "आज RBI ने नया rule जारी किया है" --speaker narrator --out /tmp/test.wav
"""

import argparse
import asyncio
import base64
import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from agents.playbook import load_playbook
from agents.settings import STUDIO_ROOT, env

log = logging.getLogger("voice")

SARVAM_URL = "https://api.sarvam.ai/text-to-speech"
SARVAM_MAX_CHARS = 2500
PRONUNCIATION_PATH = STUDIO_ROOT / "configs" / "pronunciation.json"

DEFAULT_VOICE = {
    "voice": "hi-IN-MadhurNeural", "rate": "+8%", "pitch": "+2Hz",
    "sarvam_speaker": "shubh", "pace": 1.05,
}
DEFAULT_DELIVERY = {
    "hook": {"pace": 1.06, "pause_before": 0.0},
    "build": {"pace": 1.0, "pause_before": 0.08},
    "reveal": {"pace": 0.93, "pause_before": 0.35},
    "cta": {"pace": 1.0, "pause_before": 0.15},
    "take": {"pace": 0.97, "pause_before": 0.30},      # the creator's own line, added at review
}
ROLES = tuple(DEFAULT_DELIVERY.keys())

_DEVANAGARI = re.compile(r"[\u0900-\u097F]")
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


class VoiceError(RuntimeError):
    """A provider could not synthesize a segment."""


@dataclass
class VoiceSegment:
    speaker: str
    caption: str            # Roman Hinglish, ASCII
    tts_text: str           # Devanagari + Latin, or "" when only the caption is available
    role: str = "build"
    pace: float = 1.0       # resolved: speaker base pace x role multiplier
    pause_before: float = 0.0
    audio_path: str = ""    # a recording to use as-is instead of synthesizing this line


def has_devanagari(text: str) -> bool:
    return bool(_DEVANAGARI.search(text or ""))


def normalize_digits(text: str) -> str:
    """Devanagari digits to ASCII, so figures can be compared across scripts."""
    return (text or "").translate(_DEVANAGARI_DIGITS)


def infer_role(index: int, total: int) -> str:
    """Role by position when the script did not label the scene."""
    if index == 0:
        return "hook"
    if index == total - 1:
        return "cta"
    if total >= 4 and index == total - 2:
        return "reveal"
    return "build"


def load_pronunciations(path: Optional[Path] = None) -> Dict[str, str]:
    try:
        data = json.loads((path or PRONUNCIATION_PATH).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.warning(f"Could not read pronunciation.json: {e}")
        return {}
    return {k: v for k, v in data.items() if not k.startswith("_") and isinstance(v, str) and v}


def apply_pronunciations(text: str, lexicon: Dict[str, str]) -> str:
    """Whole-word, case-sensitive replacement. Longer keys first so 'CERT-In' beats 'In'."""
    for word in sorted(lexicon, key=len, reverse=True):
        text = re.sub(rf"(?<![\w-]){re.escape(word)}(?![\w-])", lexicon[word], text)
    return text


def build_segments(shot_cards: List[dict], category: str, playbook: Optional[dict] = None) -> List[VoiceSegment]:
    """One VoiceSegment per shot card that has a narration cue, with pace and pause resolved."""
    pb = playbook or load_playbook()
    voices = pb.get("character_voices", {})
    delivery = {**DEFAULT_DELIVERY, **{k: v for k, v in pb.get("delivery", {}).items() if isinstance(v, dict)}}
    default_speaker = pb.get("pillars", {}).get(category, {}).get("default_speaker", "narrator")

    cards = [c for c in shot_cards if (c.get("narration_cue") or "").strip()]
    segments = []
    for i, card in enumerate(cards):
        speaker = card.get("speaker") or default_speaker
        role = card.get("role") if card.get("role") in ROLES else infer_role(i, len(cards))
        cfg = {**DEFAULT_VOICE, **voices.get(speaker, {})}
        role_cfg = {**DEFAULT_DELIVERY["build"], **delivery.get(role, {})}
        pace = max(0.7, min(1.4, float(cfg.get("pace", 1.0)) * float(role_cfg.get("pace", 1.0))))
        segments.append(VoiceSegment(
            speaker=speaker,
            caption=card["narration_cue"].strip(),
            tts_text=(card.get("tts_text") or "").strip(),
            role=role,
            pace=round(pace, 3),
            pause_before=0.0 if i == 0 else max(0.0, float(role_cfg.get("pause_before", 0.0))),
            audio_path=str(card.get("audio_path") or ""),
        ))
    return segments


# ── Providers ─────────────────────────────────────────────────────────

class SarvamProvider:
    name = "sarvam"

    def __init__(self, api_key: str, lexicon: Optional[Dict[str, str]] = None):
        self.api_key = api_key
        self.model = env("SARVAM_TTS_MODEL", "bulbul:v3")
        self.lexicon = lexicon if lexicon is not None else load_pronunciations()

    def text_for(self, segment: VoiceSegment) -> str:
        text = segment.tts_text or segment.caption
        text = apply_pronunciations(text, self.lexicon)
        return re.sub(r"\s+", " ", text).strip()[:SARVAM_MAX_CHARS]

    def _request(self, payload: dict) -> bytes:
        req = urllib.request.Request(
            SARVAM_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"api-subscription-key": self.api_key, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
        audios = data.get("audios") or []
        if not audios:
            raise VoiceError("Sarvam returned no audio")
        return base64.b64decode("".join(audios))

    def synthesize_sync(self, segment: VoiceSegment, voice_cfg: dict, out_path: Path) -> Path:
        payload = {
            "text": self.text_for(segment),
            "language_code": "hi-IN",
            "model": self.model,
            "speaker": voice_cfg.get("sarvam_speaker") or DEFAULT_VOICE["sarvam_speaker"],
            "pace": max(0.5, min(2.0, segment.pace)),
            "speech_sample_rate": 24000,
            "enable_preprocessing": True,
        }
        try:
            audio = self._request(payload)
        except urllib.error.HTTPError as he:
            detail = he.read().decode("utf-8", errors="replace")[:300]
            raise VoiceError(f"Sarvam HTTP {he.code}: {detail}") from he
        except VoiceError:
            raise
        except Exception as e:
            raise VoiceError(f"Sarvam request failed: {e}") from e
        out = out_path.with_suffix(".wav")
        out.write_bytes(audio)
        return out

    async def synthesize(self, segment: VoiceSegment, voice_cfg: dict, out_path: Path) -> Path:
        return await asyncio.to_thread(self.synthesize_sync, segment, voice_cfg, out_path)


class EdgeProvider:
    name = "edge"

    def __init__(self, lexicon: Optional[Dict[str, str]] = None):
        self.lexicon = lexicon if lexicon is not None else load_pronunciations()

    def text_for(self, segment: VoiceSegment, voice_cfg: dict) -> str:
        hindi_voice = str(voice_cfg.get("voice", "")).lower().startswith("hi-")
        if hindi_voice and has_devanagari(segment.tts_text):
            text = apply_pronunciations(segment.tts_text, self.lexicon)
        else:
            # English-locale voices cannot read Devanagari; give them the Roman line.
            text = re.sub(r"\bhi\b", "hee", segment.caption, flags=re.IGNORECASE)  # "hi" is read as "high"
        text = re.sub(r"\s*-\s*", ", ", text)
        return text.replace("...", ",")       # a breath, not a 0.8s gap; real pauses are inserted as silence

    @staticmethod
    def rate_for(segment: VoiceSegment, voice_cfg: dict, base_pace: float) -> str:
        """Edge rate string: the speaker's configured rate, shifted by the role multiplier."""
        try:
            base = int(str(voice_cfg.get("rate", "+8%")).replace("%", "").replace("+", ""))
        except ValueError:
            base = 8
        role_shift = round(((segment.pace / base_pace) - 1.0) * 100) if base_pace else 0
        total = max(-30, min(40, base + role_shift))
        return f"+{total}%" if total >= 0 else f"{total}%"

    async def synthesize(self, segment: VoiceSegment, voice_cfg: dict, out_path: Path) -> Path:
        import edge_tts

        out = out_path.with_suffix(".mp3")
        base_pace = float(voice_cfg.get("pace", 1.0)) or 1.0
        try:
            communicate = edge_tts.Communicate(
                self.text_for(segment, voice_cfg),
                voice=voice_cfg["voice"],
                rate=self.rate_for(segment, voice_cfg, base_pace),
                pitch=voice_cfg["pitch"],
            )
            with open(out, "wb") as f:
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        f.write(chunk["data"])
        except Exception as e:
            raise VoiceError(f"Edge-TTS failed: {e}") from e
        if not out.exists() or out.stat().st_size < 500:
            raise VoiceError("Edge-TTS returned no audio")
        return out


def get_providers() -> list:
    """Providers to try, in order, for a whole video."""
    choice = (env("TTS_PROVIDER", "auto") or "auto").lower()
    key = env("SARVAM_API_KEY")
    if choice == "edge":
        return [EdgeProvider()]
    if choice == "sarvam":
        if not key:
            raise VoiceError("TTS_PROVIDER=sarvam but SARVAM_API_KEY is not set")
        return [SarvamProvider(key)]
    return [SarvamProvider(key), EdgeProvider()] if key else [EdgeProvider()]


async def synthesize_all(segments: List[VoiceSegment], out_dir: Path, playbook: Optional[dict] = None, providers: Optional[list] = None):
    """
    Synthesizes every segment with a single provider. Returns (provider name,
    [audio paths in segment order]). Falls through to the next provider only
    as a whole, never per segment.
    """
    pb = playbook or load_playbook()
    voices = pb.get("character_voices", {})
    out_dir.mkdir(parents=True, exist_ok=True)
    errors = []
    for provider in providers or get_providers():
        paths: List[Path] = []
        try:
            for idx, segment in enumerate(segments):
                if segment.audio_path and Path(segment.audio_path).exists():
                    paths.append(Path(segment.audio_path))      # your own recording, used as-is
                    continue
                cfg = {**DEFAULT_VOICE, **voices.get(segment.speaker, {})}
                paths.append(await provider.synthesize(segment, cfg, out_dir / f"{provider.name}_seg_{idx:02d}"))
            return provider.name, paths
        except VoiceError as e:
            errors.append(f"{provider.name}: {e}")
            log.warning(f"Voice provider '{provider.name}' failed ({e}); trying the next provider for the whole video.")
    raise VoiceError("All voice providers failed: " + " | ".join(errors))


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [VOICE] %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Synthesize one line to hear how a voice handles it")
    parser.add_argument("--text", required=True, help="Line to speak (Devanagari + English works best)")
    parser.add_argument("--caption", default="", help="Roman version, used by English-locale Edge voices")
    parser.add_argument("--speaker", default="narrator", help="Character id from viral_playbook.json")
    parser.add_argument("--role", default="build", choices=ROLES)
    parser.add_argument("--out", default="voice_test.wav")
    args = parser.parse_args()

    card = {"narration_cue": args.caption or args.text, "tts_text": args.text, "speaker": args.speaker, "role": args.role}
    segments = build_segments([card], "technology")
    out = Path(args.out)
    provider, paths = asyncio.run(synthesize_all(segments, out.parent / "voice_test_parts"))
    paths[0].replace(out.with_suffix(paths[0].suffix))
    print(f"{provider}: {out.with_suffix(paths[0].suffix)}  (pace {segments[0].pace})")


if __name__ == "__main__":
    main()
