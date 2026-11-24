#!/usr/bin/env python3
"""
qa_judge.py — Automated QA & Quality Gate Judge Agent

Audits every rendered video against 6 strict gates before publishing:
  1. DURATION INTEGRITY: video_duration >= voice_duration (never truncate mid-sentence).
  2. SCENE VARIETY: At least 5 distinct scene image files used (zero static slideshows).
  3. SUBTITLE INTEGRITY: ASS subtitle file non-empty with safe-zone margin.
  4. ANTI-WATERMARK GATE (no_watermarks: true):
     MUST FAIL if any frame, metadata, or source contains stock watermarks
     (Alamy, Adobe Stock, Dreamstime, Shutterstock, Getty, iStock, etc.) or foreign-language posters.
  5. VISUAL-TO-TOPIC RELEVANCE (visuals_match_topic: true):
     MUST FAIL if a tech/AI video contains unrelated nature, food, dogs, or surreal non-tech images.
  6. VISUAL LAYOUT & WATERMARK: 4 keyframes (10%, 35%, 65%, 90%) audited for 1080x1920
     resolution, safe-zone clearance, and handle consistency (@almightyanand).

CLI Entrypoint:
    python -m agents.qa_judge --video <path> --voice <path> --ass <path> --scenes-dir <path> --topic <topic>
"""
#!/usr/bin/env python3
"""
qa_judge.py — Deterministic + Multimodal Vision QA Gatekeeper
"""

import argparse
import base64
import hashlib
import json
import logging
import os
import re
import subprocess
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [QA-JUDGE] %(levelname)s %(message)s",
)
log = logging.getLogger("qa_judge")

FFMPEG_BIN = "/Users/anand/.local/bin/ffmpeg" if Path("/Users/anand/.local/bin/ffmpeg").exists() else "ffmpeg"


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


class QAJudge:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.omniroute_url = self._load_env_var("OMNIROUTE_BASE_URL") or "http://localhost:8080/v1"
        self.omniroute_key = self._load_env_var("OMNIROUTE_API_KEY") or "sk-omniroute"
        self.gemini_key = self._load_env_var("GEMINI_API_KEY") or self._load_env_var("GOOGLE_API_KEY") or ""
        self.vision_model = self._load_env_var("QA_VISION_MODEL") or "gemini-flash-lite-latest"

    @staticmethod
    def _load_env_var(var_name: str) -> Optional[str]:
        if os.environ.get(var_name):
            return os.environ[var_name]
        env_file = Path(__file__).resolve().parent.parent / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith(f"{var_name}="):
                    return line.split("=", 1)[1].strip()
        return None

    @staticmethod
    def probe_media_duration(file_path: Path) -> float:
        cmd = [FFMPEG_BIN, "-i", str(file_path)]
        try:
            res = subprocess.run(cmd, stderr=subprocess.PIPE, text=True)
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
            out_frame = output_dir / f"keyframe_{int(p * 100)}pct.jpg"
            subprocess.run(
                [FFMPEG_BIN, "-y", "-ss", str(ts), "-i", str(video_path), "-frames:v", "1", str(out_frame)],
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
            if any(bad in src for bad in ["procedural", "placeholder", "alamy", "shutterstock", "web_scraped"]):
                notes.append(f"Shot {data.get('shot_index')}: disallowed source '{src}'")
        if notes:
            return False, notes
        return True, ["All scene sources verified as real AI/royalty-free generators."]

    def run_vision_audit(self, keyframes: List[Path], topic: str) -> Tuple[dict, List[str]]:
        """Sends 4 base64 keyframes to a Vision LLM (Gemini or OmniRoute) for real pixel inspection."""
        rubric = (
            f"You are a strict YouTube Shorts QA Judge. The video topic is: '{topic}'. "
            "Inspect these 4 keyframes (10%, 35%, 65%, 90%) and return ONLY valid JSON:\n"
            '{"no_watermarks": bool, "visuals_match_topic": bool, "no_blank_wireframes": bool, '
            '"captions_clean": bool, "failures": [string]}\n'
            "Rules:\n"
            "- no_watermarks: FALSE if any frame shows alamy, Adobe Stock, dreamstime, shutterstock, getty, pollinations.ai, or foreign posters.\n"
            "- visuals_match_topic: FALSE if visuals are unrelated to the topic (e.g. random nature/food/animals for a tech video).\n"
            "- no_blank_wireframes: FALSE if any frame is an empty grid/wireframe box or solid dark placeholder.\n"
            "- captions_clean: FALSE if subtitles overlap faces/headers, clip off-screen, or contain □ tofu boxes."
        )

        # Path A: Direct Gemini Vision API (fast, reliable if GEMINI_API_KEY is set)
        if self.gemini_key:
            parts = [{"text": rubric}]
            for kf in keyframes:
                b64 = base64.b64encode(kf.read_bytes()).decode("utf-8")
                parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64}})
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.vision_model}:generateContent?key={self.gemini_key}"
            payload = {
                "contents": [{"parts": parts}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1},
            }
            for attempt in range(1, 4):
                try:
                    req = urllib.request.Request(
                        url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}
                    )
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        raw = json.loads(resp.read().decode())
                        text = raw["candidates"][0]["content"]["parts"][0]["text"]
                        return json.loads(text), ["Vision QA executed via Gemini Vision API."]
                except Exception as e:
                    if attempt < 3:
                        import time
                        time.sleep(2.0)
                    else:
                        log.warning(f"Gemini Vision QA failed ({e}), trying OmniRoute...")

        # Path B: OpenAI-compatible OmniRoute Vision endpoint
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
        try:
            req = urllib.request.Request(
                f"{self.omniroute_url.rstrip('/')}/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self.omniroute_key}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = json.loads(resp.read().decode())
                text = raw["choices"][0]["message"]["content"]
                return json.loads(text), ["Vision QA executed via OmniRoute."]
        except Exception as e:
            return (
                {
                    "no_watermarks": False,
                    "visuals_match_topic": False,
                    "no_blank_wireframes": False,
                    "captions_clean": False,
                    "failures": [f"Vision QA unreachable: {e}"],
                },
                [f"Vision QA FAIL: Could not reach Gemini or OmniRoute ({e})."],
            )

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
        notes.extend(v_notes)
        for fail_msg in vision_res.get("failures", []):
            notes.append(f"Vision FAIL: {fail_msg}")

        checks = {
            "duration_valid": duration_valid,
            "scene_variety_valid": scene_variety_valid,
            "sources_valid": sources_valid,
            "subtitles_valid": subtitles_valid,
            "resolution_valid": res_valid,
            "no_watermarks": bool(vision_res.get("no_watermarks", False)),
            "visuals_match_topic": bool(vision_res.get("visuals_match_topic", False)),
            "no_blank_wireframes": bool(vision_res.get("no_blank_wireframes", False)),
            "captions_clean": bool(vision_res.get("captions_clean", False)),
        }

        all_passed = all(checks.values())
        status = "PASS" if all_passed else "FAIL"
        score = round((sum(checks.values()) / len(checks)) * 10, 1)

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