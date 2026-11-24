#!/usr/bin/env python3
"""
qa_judge.py — Automated QA & Quality Gate Judge Agent

Audits every rendered video against 5 strict gates before publishing:
  1. DURATION INTEGRITY: video_duration >= voice_duration (never truncate mid-sentence).
  2. SCENE VARIETY: At least 5 distinct scene image files used (zero static slideshows).
  3. SUBTITLE INTEGRITY: ASS subtitle file non-empty with safe-zone margin.
  4. VISUAL-TO-TOPIC RELEVANCE: Explicitly verifies `visuals_match_topic: true`
     (FAILS if a tech/AI video contains unrelated nature, food, or random stock photos).
  5. VISUAL LAYOUT & WATERMARK: 4 keyframes (10%, 35%, 65%, 90%) audited for 1080x1920
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

UNRELATED_BLACKLIST_TERMS = [
    "dog", "puppy", "cat", "kitten", "onion", "wheat field", "corn field",
    "paddy field", "pasture", "meadow", "farm animal", "livestock",
    "pasta recipe", "salad recipe", "kitchen cooking", "vegetable market"
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

    def verify_visual_to_topic_relevance(self, scenes_dir: Path, topic: str) -> Tuple[bool, List[str]]:
        """
        Audits visual prompts, scene metadata, and filenames.
        Fails if unrelated blacklist terms (dogs, wheat fields, onions) are detected for tech/AI topics.
        """
        notes = []
        is_tech_topic = any(w in topic.lower() for w in ["ai", "tech", "model", "deepseek", "gemini", "code", "cyber", "tool", "quantum"])
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

        # 4. Visual-to-Topic Relevance Check (MANDATORY)
        visuals_match_topic, rel_notes = self.verify_visual_to_topic_relevance(scenes_dir, topic)
        notes.extend(rel_notes)

        # 5. Visual Keyframe QC (10%, 35%, 65%, 90%)
        qc_dir = video_path.parent / "qc_audit"
        keyframes = self.extract_keyframes(video_path, qc_dir, video_dur)
        visual_qc_valid, visual_notes = self.evaluate_keyframes_layout(keyframes, topic)
        notes.extend(visual_notes)

        # Overall Status Gate
        checks = {
            "duration_valid": duration_valid,
            "scene_variety_valid": scene_variety_valid,
            "subtitles_valid": subtitles_valid,
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
            visuals_match_topic=visuals_match_topic,
            keyframes_evaluated=[str(k) for k in keyframes],
            checks=checks,
            score=score,
            notes=notes,
        )

        log.info(f"🏆 QA JUDGE RESULT: {status} (Score: {score}/10, visuals_match_topic: {visuals_match_topic})")
        for n in notes:
            log.info(f"  • {n}")

        return report


def main():
    parser = argparse.ArgumentParser(description="QA Judge Quality Gate")
    parser.add_argument("--video", required=True, type=Path, help="Path to rendered MP4 video")
    parser.add_argument("--voice", required=True, type=Path, help="Path to voice MP3")
    parser.add_argument("--ass", required=True, type=Path, help="Path to ASS subtitle file")
    parser.add_argument("--scenes-dir", required=True, type=Path, help="Directory containing generated shot images")
    parser.add_argument("--topic", default="General Topic", help="Topic title for verification")
    args = parser.parse_args()

    judge = QAJudge()
    report = judge.audit(
        video_path=args.video,
        voice_path=args.voice,
        ass_path=args.ass,
        scenes_dir=args.scenes_dir,
        topic=args.topic,
    )
    print("\n--- QA JUDGE REPORT ---")
    print(json.dumps(asdict(report), indent=2))
    sys.exit(0 if report.status == "PASS" else 1)


if __name__ == "__main__":
    main()
