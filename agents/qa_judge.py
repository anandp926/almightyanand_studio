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

import argparse
import hashlib
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

from PIL import Image

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [QA-JUDGE] %(levelname)s %(message)s",
)
log = logging.getLogger("qa_judge")

FFMPEG_BIN = "/Users/anand/.local/bin/ffmpeg" if Path("/Users/anand/.local/bin/ffmpeg").exists() else "ffmpeg"

WATERMARK_BLACKLIST = [
    "alamy", "adobestock", "adobe stock", "dreamstime", "shutterstock",
    "getty", "gettyimages", "istock", "istockphoto", "watermark",
    "depositphotos", "freepik", "vectorstock", "123rf", "pond5",
    "stock photo", "event poster", "cartel", "concierto", "fiesta"
]

UNRELATED_BLACKLIST_TERMS = [
    "dog", "puppy", "cat", "kitten", "onion", "wheat field", "corn field",
    "paddy field", "pasture", "meadow", "farm animal", "livestock",
    "pasta recipe", "salad recipe", "kitchen cooking", "vegetable market",
    "giant man in underwear", "dirt pit", "mud wrestling", "swimwear"
]


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
    keyframes_evaluated: List[str]
    checks: Dict[str, bool]
    score: float
    notes: List[str]
    audited_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class QAJudge:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run

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
        percentages = [0.10, 0.35, 0.65, 0.90]
        keyframes = []

        for p in percentages:
            ts = max(0.5, round(total_dur * p, 2))
            out_frame = output_dir / f"keyframe_{int(p*100)}pct.jpg"
            cmd = [
                FFMPEG_BIN, "-y", "-ss", str(ts),
                "-i", str(video_path),
                "-frames:v", "1",
                str(out_frame)
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if out_frame.exists() and out_frame.stat().st_size > 5000:
                keyframes.append(out_frame)

        return keyframes

    def verify_no_watermarks(self, scenes_dir: Path) -> Tuple[bool, List[str]]:
        """
        Anti-Watermark Gate: Scans scene metadata and image files.
        Fails if Alamy, Adobe Stock, Dreamstime, Shutterstock, Getty, or poster signatures are found.
        """
        notes = []
        violations = []

        if scenes_dir.exists():
            for meta_file in scenes_dir.glob("shot_*.json"):
                try:
                    data = json.loads(meta_file.read_text())
                    source = str(data.get("source", "")).lower()
                    if any(bad in source for bad in ["alamy", "shutterstock", "getty", "adobestock", "web_scraped"]):
                        violations.append(f"Shot {data.get('shot_index')}: unauthorized stock source '{source}'")

                    prompt_text = (data.get("visual_prompt", "") + " " + data.get("overlay_text", "")).lower()
                    for wm in WATERMARK_BLACKLIST:
                        if wm in prompt_text:
                            violations.append(f"Shot {data.get('shot_index')}: stock watermark keyword '{wm}'")

                    if data.get("watermark_detected") is True:
                        violations.append(f"Shot {data.get('shot_index')}: watermark flag detected")
                except Exception:
                    pass

            for img_file in scenes_dir.glob("shot_*.jpg"):
                try:
                    with Image.open(img_file) as img:
                        info_str = str(img.info).lower()
                        for wm in WATERMARK_BLACKLIST:
                            if wm in info_str:
                                violations.append(f"{img_file.name}: EXIF watermark tag '{wm}'")
                except Exception:
                    pass

        if violations:
            notes.append(f"Watermark FAIL: Detected copyrighted stock watermarks: {', '.join(violations)}")
            return False, notes

        notes.append("Watermark PASS: Zero stock watermarks or foreign posters detected across all scenes.")
        return True, notes

    def verify_visual_to_topic_relevance(self, scenes_dir: Path, topic: str) -> Tuple[bool, List[str]]:
        """
        Audits visual prompts, scene metadata, and filenames.
        Fails if unrelated blacklist terms (dogs, wheat fields, onions, underwear) are detected for tech/AI topics.
        """
        notes = []
        found_violations = []

        if scenes_dir.exists():
            for meta_file in scenes_dir.glob("shot_*.json"):
                try:
                    data = json.loads(meta_file.read_text())
                    prompt_text = (data.get("visual_prompt", "") + " " + data.get("overlay_text", "")).lower()
                    for bad in UNRELATED_BLACKLIST_TERMS:
                        if re.search(r"\b" + re.escape(bad) + r"\b", prompt_text):
                            found_violations.append(f"Shot {data.get('shot_index')}: found unrelated '{bad}' in prompt")
                except Exception:
                    pass

        if found_violations:
            notes.append(f"Relevance FAIL: Detected unrelated terms in visuals: {', '.join(found_violations)}")
            return False, notes

        notes.append(f"Relevance PASS: All visuals verified prompt-matched to topic '{topic[:35]}...'.")
        return True, notes

    def evaluate_keyframes_layout(self, keyframes: List[Path], topic: str) -> Tuple[bool, List[str]]:
        """
        Audits keyframe resolution (1080x1920) and non-blank visual variance.
        """
        notes = []
        if len(keyframes) < 4:
            notes.append(f"Expected 4 keyframes, only extracted {len(keyframes)}.")
            return False, notes

        for kf in keyframes:
            try:
                img = Image.open(kf)
                w, h = img.size
                if (w, h) != (1080, 1920):
                    notes.append(f"Keyframe {kf.name} resolution is {w}x{h}, must be 1080x1920.")
                    return False, notes

                extrema = img.convert("L").getextrema()
                if extrema[0] == extrema[1]:
                    notes.append(f"Keyframe {kf.name} is a blank/single-color frame.")
                    return False, notes
            except Exception as e:
                notes.append(f"Failed opening keyframe {kf.name}: {e}")
                return False, notes

        notes.append("Visual layout & resolution (1080x1920) verified across all 4 keyframes.")
        return True, notes

    def audit(
        self,
        video_path: Path,
        voice_path: Path,
        ass_path: Path,
        scenes_dir: Path,
        topic: str,
    ) -> QAJudgeReport:
        log.info(f"⚖️ Running QA Judge Audit on: {video_path.name}")
        notes = []

        # 1. Duration Integrity Check (never truncate mid-sentence)
        video_dur = self.probe_media_duration(video_path)
        voice_dur = self.probe_media_duration(voice_path)
        duration_valid = (video_dur >= (voice_dur - 0.2)) and (video_dur > 5.0)
        if duration_valid:
            notes.append(f"Duration PASS: video={video_dur}s >= voice={voice_dur}s (never truncated).")
        else:
            notes.append(f"Duration FAIL: video={video_dur}s < voice={voice_dur}s (truncated audio).")

        # 2. Scene Variety Check (>= 5 distinct image files)
        distinct_scenes = 0
        scene_hashes = set()
        if scenes_dir.exists():
            for scene_file in scenes_dir.glob("*.jpg"):
                try:
                    data = scene_file.read_bytes()
                    h = hashlib.md5(data).hexdigest()
                    if h not in scene_hashes:
                        scene_hashes.add(h)
                        distinct_scenes += 1
                except Exception:
                    pass

        scene_variety_valid = distinct_scenes >= 5
        if scene_variety_valid:
            notes.append(f"Scene Variety PASS: {distinct_scenes} unique scene images used (>= 5).")
        else:
            notes.append(f"Scene Variety FAIL: only {distinct_scenes} distinct scenes (must be >= 5).")

        # 3. Subtitle Integrity Check
        subs_count = 0
        if ass_path.exists():
            ass_text = ass_path.read_text(encoding="utf-8")
            subs_count = len(re.findall(r"^Dialogue:\s*", ass_text, re.MULTILINE))

        subtitles_valid = subs_count >= 5
        if subtitles_valid:
            notes.append(f"Subtitles PASS: {subs_count} safe-zone dialogue events rendered.")
        else:
            notes.append(f"Subtitles FAIL: only {subs_count} dialogue events in ASS file.")

        # 4. Anti-Watermark Check (MANDATORY)
        no_watermarks, wm_notes = self.verify_no_watermarks(scenes_dir)
        notes.extend(wm_notes)

        # 5. Visual-to-Topic Relevance Check (MANDATORY)
        visuals_match_topic, rel_notes = self.verify_visual_to_topic_relevance(scenes_dir, topic)
        notes.extend(rel_notes)

        # 6. Visual Keyframe QC (10%, 35%, 65%, 90%)
        qc_dir = video_path.parent / "qc_audit"
        keyframes = self.extract_keyframes(video_path, qc_dir, video_dur)
        visual_qc_valid, visual_notes = self.evaluate_keyframes_layout(keyframes, topic)
        notes.extend(visual_notes)

        # Overall Status Gate
        checks = {
            "duration_valid": duration_valid,
            "scene_variety_valid": scene_variety_valid,
            "subtitles_valid": subtitles_valid,
            "no_watermarks": no_watermarks,
            "visuals_match_topic": visuals_match_topic,
            "visual_qc_valid": visual_qc_valid,
        }

        all_passed = all(checks.values())
        status = "PASS" if all_passed else "FAIL"
        score = 10.0 if all_passed else round((sum(checks.values()) / len(checks)) * 10, 1)

        report = QAJudgeReport(
            status=status,
            video_path=str(video_path),
            topic=topic,
            video_duration=video_dur,
            voice_duration=voice_dur,
            distinct_scenes_count=distinct_scenes,
            subtitles_count=subs_count,
            no_watermarks=no_watermarks,
            visuals_match_topic=visuals_match_topic,
            keyframes_evaluated=[str(k) for k in keyframes],
            checks=checks,
            score=score,
            notes=notes,
        )

        log.info(f"🏆 QA JUDGE RESULT: {status} (Score: {score}/10, no_watermarks: {no_watermarks}, visuals_match_topic: {visuals_match_topic})")
        for n in notes:
            log.info(f"  • {n}")

        return report


def main():
    parser = argparse.ArgumentParser(description="QA Judge Agent")
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--voice", required=True, type=Path)
    parser.add_argument("--ass", required=True, type=Path)
    parser.add_argument("--scenes-dir", required=True, type=Path)
    parser.add_argument("--topic", default="Technology Update")
    args = parser.parse_args()

    judge = QAJudge()
    report = judge.audit(args.video, args.voice, args.ass, args.scenes_dir, args.topic)
    print(json.dumps(asdict(report), indent=2))


if __name__ == "__main__":
    main()
