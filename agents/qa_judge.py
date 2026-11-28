#!/usr/bin/env python3
"""
qa_judge.py — Deterministic + Multimodal Vision QA Gatekeeper

Audits every rendered video before it can be queued for publishing:
  1. DURATION: video_duration >= voice_duration (never truncate mid-sentence).
  2. SCENE VARIETY: at least 5 distinct scene images.
  3. SCENE SOURCES: no placeholder / scraped sources in the scene metadata.
  4. SUBTITLES: ASS file has at least 5 dialogue lines.
  5. RESOLUTION: 4 keyframes (10%, 35%, 65%, 90%) are 1080x1920.
  6. VISION AUDIT: the 4 keyframes are sent to a vision model, which must return
     explicit `true` for no_watermarks, visuals_match_topic, no_blank_wireframes
     and captions_clean.

The gate fails closed: if the keyframes cannot be extracted, the vision model
is unreachable, or its answer is missing a field, the render is rejected.

CLI Entrypoint:
    python -m agents.qa_judge --video <path> --voice <path> --ass <path> --scenes-dir <path> --topic <topic>
"""

import argparse
import base64
import hashlib
import json
import logging
import re
import subprocess
import time
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

from PIL import Image

from agents.settings import env, ffmpeg_bin

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [QA-JUDGE] %(levelname)s %(message)s",
)
log = logging.getLogger("qa_judge")

VISION_KEYS = ("no_watermarks", "visuals_match_topic", "no_blank_wireframes", "captions_clean")


@dataclass
class QAJudgeReport:
    status: str  # "PASS" or "FAIL"
    video_path: str
    topic: str
    video_duration: float
    voice_duration: float
    distinct_scenes_count: int
    subtitles_count: int
    no_watermarks: bool
    visuals_match_topic: bool
    no_blank_wireframes: bool
    captions_clean: bool
    keyframes_evaluated: List[str]
    checks: Dict[str, bool]
    score: float
    notes: List[str]
    audited_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def _all_false(reason: str) -> dict:
    return {**{k: False for k in VISION_KEYS}, "failures": [reason]}


class QAJudge:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.ffmpeg = ffmpeg_bin()
        self.omniroute_url = env("OMNIROUTE_BASE_URL", "http://localhost:8080/v1")
        self.omniroute_key = env("OMNIROUTE_API_KEY", "sk-omniroute")
        self.gemini_key = env("GEMINI_API_KEY") or env("GOOGLE_API_KEY") or ""
        self.vision_model = env("QA_VISION_MODEL", "gemini-flash-lite-latest")

    def probe_media_duration(self, file_path: Path) -> float:
        try:
            res = subprocess.run([self.ffmpeg, "-i", str(file_path)], stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True)
            match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", res.stderr)
            if match:
                h, m, s = match.groups()
                return round(float(h) * 3600 + float(m) * 60 + float(s), 2)
        except Exception as e:
            log.warning(f"Failed to probe duration for {file_path}: {e}")
        return 0.0

    def extract_keyframes(self, video_path: Path, output_dir: Path, total_dur: float) -> List[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        keyframes = []
        for p in [0.10, 0.35, 0.65, 0.90]:
            ts = max(0.5, round(total_dur * p, 2))
            out_frame = output_dir / f"{video_path.stem}_keyframe_{int(p * 100)}pct.jpg"
            subprocess.run(
                [self.ffmpeg, "-y", "-ss", str(ts), "-i", str(video_path), "-frames:v", "1", str(out_frame)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if out_frame.exists() and out_frame.stat().st_size > 5000:
                keyframes.append(out_frame)
        return keyframes

    def check_scene_sources(self, scenes_dir: Path) -> Tuple[bool, List[str]]:
        notes = []
        if not scenes_dir.exists():
            return False, ["Scenes directory missing."]
        for meta_file in scenes_dir.glob("shot_*.json"):
            data = json.loads(meta_file.read_text())
            src = str(data.get("source", "")).lower()
            if not src or src == "none" or any(bad in src for bad in ["procedural", "placeholder", "alamy", "shutterstock", "web_scraped"]):
                notes.append(f"Shot {data.get('shot_index')}: disallowed source '{src}'")
        if notes:
            return False, notes
        return True, ["All scene sources verified as AI/royalty-free generators."]

    @staticmethod
    def _rubric(topic: str) -> str:
        return (
            f"You are a strict YouTube Shorts QA Judge. The video topic is: '{topic}'. "
            "Inspect these 4 keyframes (10%, 35%, 65%, 90%) and return ONLY valid JSON:\n"
            '{"no_watermarks": bool, "visuals_match_topic": bool, "no_blank_wireframes": bool, '
            '"captions_clean": bool, "failures": [string]}\n'
            "Rules:\n"
            "- If you cannot see 4 images, set every field to false and say so in failures.\n"
            "- no_watermarks: FALSE if any frame shows alamy, Adobe Stock, dreamstime, shutterstock, getty, pollinations.ai, or foreign posters.\n"
            "- visuals_match_topic: FALSE if visuals are unrelated to the topic (e.g. random nature/food/animals for a tech video).\n"
            "- no_blank_wireframes: FALSE if any frame is an empty grid/wireframe box or solid dark placeholder.\n"
            "- captions_clean: FALSE if subtitles overlap faces/headers, clip off-screen, or contain □ tofu boxes."
        )

    def _vision_via_gemini(self, keyframes: List[Path], rubric: str) -> dict:
        parts = [{"text": rubric}]
        for kf in keyframes:
            b64 = base64.b64encode(kf.read_bytes()).decode("utf-8")
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64}})
        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1},
        }
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.vision_model}:generateContent",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-goog-api-key": self.gemini_key},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = json.loads(resp.read().decode())
        return json.loads(raw["candidates"][0]["content"]["parts"][0]["text"])

    def _vision_via_omniroute(self, keyframes: List[Path], rubric: str) -> dict:
        content = [{"type": "text", "text": rubric}]
        for kf in keyframes:
            b64 = base64.b64encode(kf.read_bytes()).decode("utf-8")
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
        payload = {
            "model": self.vision_model,
            "messages": [{"role": "user", "content": content}],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        }
        req = urllib.request.Request(
            f"{self.omniroute_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.omniroute_key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            raw = json.loads(resp.read().decode())
        # OmniRoute reports how many images it actually forwarded to a vision model.
        forwarded = raw.get("images_forwarded")
        if forwarded is not None and forwarded != len(keyframes):
            raise RuntimeError(f"router forwarded {forwarded} of {len(keyframes)} images")
        return json.loads(raw["choices"][0]["message"]["content"])

    def run_vision_audit(self, keyframes: List[Path], topic: str) -> Tuple[dict, List[str]]:
        """Sends the keyframes to a vision model. Any failure returns all-false."""
        if len(keyframes) < 4:
            reason = f"Only {len(keyframes)} of 4 keyframes could be extracted."
            return _all_false(reason), [f"Vision QA FAIL: {reason}"]

        rubric = self._rubric(topic)
        errors = []

        if self.gemini_key:
            for attempt in range(1, 4):
                try:
                    return self._vision_via_gemini(keyframes, rubric), ["Vision QA executed via Gemini Vision API."]
                except Exception as e:
                    errors.append(f"gemini: {e}")
                    if attempt < 3:
                        time.sleep(2.0)
            log.warning(f"Gemini Vision QA failed ({errors[-1]}), trying OmniRoute...")

        try:
            return self._vision_via_omniroute(keyframes, rubric), ["Vision QA executed via OmniRoute."]
        except Exception as e:
            errors.append(f"omniroute: {e}")

        reason = f"Vision QA unreachable: {errors[-1]}"
        return _all_false(reason), [f"Vision QA FAIL: could not get a vision verdict ({'; '.join(errors[-2:])})."]

    def audit(
        self,
        video_path: Path,
        voice_path: Path,
        ass_path: Path,
        scenes_dir: Path,
        topic: str,
    ) -> QAJudgeReport:
        log.info(f"⚖️ Running QA Judge Audit on: {video_path.name}")
        notes: List[str] = []

        video_dur = self.probe_media_duration(video_path)
        voice_dur = self.probe_media_duration(voice_path)
        duration_valid = (video_dur >= (voice_dur - 0.2)) and (video_dur > 5.0)
        notes.append(f"Duration {'PASS' if duration_valid else 'FAIL'}: video={video_dur}s, voice={voice_dur}s")

        scene_hashes = set()
        if scenes_dir.exists():
            for f in scenes_dir.glob("*.jpg"):
                scene_hashes.add(hashlib.md5(f.read_bytes()).hexdigest())
        distinct_scenes = len(scene_hashes)
        scene_variety_valid = distinct_scenes >= 5

        sources_valid, src_notes = self.check_scene_sources(scenes_dir)
        notes.extend(src_notes)

        subs_count = 0
        if ass_path.exists():
            subs_count = len(re.findall(r"^Dialogue:\s*", ass_path.read_text(encoding="utf-8"), re.MULTILINE))
        subtitles_valid = subs_count >= 5

        keyframes = self.extract_keyframes(video_path, video_path.parent / "qc_audit", video_dur)
        res_valid = len(keyframes) == 4 and all(Image.open(kf).size == (1080, 1920) for kf in keyframes)

        vision_res, v_notes = self.run_vision_audit(keyframes, topic)
        if not isinstance(vision_res, dict):
            vision_res = _all_false("Vision model did not return a JSON object.")
        notes.extend(v_notes)
        for fail_msg in vision_res.get("failures") or []:
            notes.append(f"Vision FAIL: {fail_msg}")

        checks = {
            "duration_valid": duration_valid,
            "scene_variety_valid": scene_variety_valid,
            "sources_valid": sources_valid,
            "subtitles_valid": subtitles_valid,
            "resolution_valid": res_valid,
        }
        # Only an explicit JSON `true` passes; a missing or non-boolean field fails.
        for key in VISION_KEYS:
            checks[key] = vision_res.get(key) is True

        all_passed = all(checks.values())
        status = "PASS" if all_passed else "FAIL"
        score = round((sum(checks.values()) / len(checks)) * 10, 1)
        failed = [name for name, ok in checks.items() if not ok]
        if failed:
            notes.append(f"Failed checks: {', '.join(failed)}")

        report = QAJudgeReport(
            status=status,
            video_path=str(video_path),
            topic=topic,
            video_duration=video_dur,
            voice_duration=voice_dur,
            distinct_scenes_count=distinct_scenes,
            subtitles_count=subs_count,
            no_watermarks=checks["no_watermarks"],
            visuals_match_topic=checks["visuals_match_topic"],
            no_blank_wireframes=checks["no_blank_wireframes"],
            captions_clean=checks["captions_clean"],
            keyframes_evaluated=[str(k) for k in keyframes],
            checks=checks,
            score=score,
            notes=notes,
        )

        log.info(f"🏆 QA JUDGE RESULT: {status} (Score: {score}/10)")
        return report


def main():
    parser = argparse.ArgumentParser(description="QA Judge Agent")
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--voice", required=True, type=Path)
    parser.add_argument("--ass", required=True, type=Path)
    parser.add_argument("--scenes-dir", required=True, type=Path)
    parser.add_argument("--topic", default="Technology Update")
    args = parser.parse_args()

    report = QAJudge().audit(args.video, args.voice, args.ass, args.scenes_dir, args.topic)
    print(json.dumps(asdict(report), indent=2))


if __name__ == "__main__":
    main()
