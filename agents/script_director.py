#!/usr/bin/env python3
"""
script_director.py — Transformative Script & Shot Card Director

MANDATES:
  1. STRICT HINGLISH MANDATE: All scripts, voiceovers, and captions MUST be in
     conversational, punchy Indian Hinglish.
  2. STRICT BAN ON GENERIC OPENINGS: Banned phrases like "Namaste dosto", "Welcome back",
     "Hello guys". Line 1 MUST be a 0–2s curiosity/shock hook.
  3. DYNAMIC SCENE CUES: Parses live_data["scene_cues"] into ShotCard objects.
     If LLM fails or returns empty scene_cues, raises an exception (NO hardcoded banking fallbacks).
  4. NO HARDCODED BANKING TEXT: All titles, hooks, anchors, and cards are dynamically generated.
  5. ROUTED VIA OMNIROUTE: Reads OMNIROUTE_BASE_URL (default http://localhost:8080/v1) with
     Groq (api.groq.com) as upstream fallback.

CLI Entrypoint:
    python -m agents.script_director [--dry-run]
"""

import argparse
import json
import logging
import os
import re
import sys
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from agents.memory_store import MemoryStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SCRIPT-DIR] %(levelname)s %(message)s",
)
log = logging.getLogger("script_director")

MIN_VISUAL_TRANSFORMATION_RATIO = 0.80

AFFILIATE_CATALOG = {
    "finance": [
        {"title": "Zero-Fee Scam & Subscription Tracker (India)", "url": "https://amzn.to/fin-tracker-india"},
        {"title": "Zero-Fee Index Investing Portal", "url": "https://amzn.to/invest-zero-fee"},
    ],
    "technology": [
        {"title": "Top AI Productivity Extension for Chrome", "url": "https://amzn.to/ai-productivity-tool"},
        {"title": "Best Creator & Developer Productivity Gear", "url": "https://amzn.to/dev-creator-gear"},
    ],
    "general": [
        {"title": "Almighty Anand Recommended Creator & Tech Stack", "url": "https://amzn.to/anand-stack"},
    ],
}


@dataclass
class ShotCard:
    shot_index: int
    duration_seconds: float
    style_preset: str
    camera_angle: str
    visual_prompt: str
    narration_cue: str
    overlay_text: str
    badge_text: str = "TRENDING"
    is_original_generated: bool = True


@dataclass
class ShortVariant:
    variant_id: str
    title: str
    duration_seconds: float
    aspect_ratio: str = "9:16"
    hook_hinglish: str = ""
    suspense_ending_hinglish: str = ""
    narration_script_hinglish: str = ""
    shot_cards: List[dict] = field(default_factory=list)


@dataclass
class ProductionScriptPackage:
    production_id: str
    source_topic: str
    category: str
    transformation_ratio: float
    visual_style: str
    long_form_16x9: dict
    shorts_9x16_variants: List[dict]
    affiliate_links: List[dict]
    pinned_comment: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ScriptDirector:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.memory = MemoryStore()
        self.omniroute_url = os.environ.get("OMNIROUTE_BASE_URL", "http://localhost:8080/v1")
        self.groq_key = self._load_env_var("GROQ_API_KEY")

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

    def call_live_llm(self, prompt: str) -> dict:
        """
        Attempts OmniRoute (default http://localhost:8080/v1) first.
        Falls back to Groq free-tier (openai/gpt-oss-120b) with sub-second JSON response.
        """
        system_content = (
            "You are the Lead Script Director for Almighty Anand (@almightyanand).\n"
            "CRITICAL MANDATES:\n"
            "1. Output high-energy, conversational Indian Hinglish (Roman script).\n"
            "2. STRICT BAN: NEVER start scripts with 'Namaste dosto', 'Welcome back', 'Hello guys', or 'Hey friends'. "
            "Line 1 MUST be an immediate 0-2 second shock/curiosity hook (e.g. 'Bhai, is ek feature ne sab kuch badal diya...', "
            "'Agar aap yeh tool use karte ho to suno...').\n"
            "3. STRICT FACT-CHECK MANDATE: You MUST only state verified, true facts from the source topic. "
            "NEVER hallucinate or invent technical specifications, context window sizes, parameter counts, pricing, or benchmarks. "
            "(For example, NEVER invent fictional claims like '1 TB context window' or impossible specs). "
            "If specific numbers are not verified in the topic data, speak strictly to verified architectural features or general capabilities without fabricating numbers.\n"
            "4. HUMANIZED SPOKEN-WORD PACING (CRITICAL FOR VOICEOVER):\n"
            "   - Write for the EAR, not for reading. Keep every sentence short (6 to 10 words max).\n"
            "   - Use natural Indian spoken connectors: 'Suno...', 'Dekho,', 'Matlab,', 'Wait...', 'Aur sabse badi baat?'.\n"
            "   - Use '...' for dramatic 0.5s pauses before a reveal, and '?' / '!' for vocal inflection.\n"
            "   - Break complex ideas into punchy Q&A rhythm (e.g., 'Iska fayda kya hai? Simple. Aapka 2 ghante ka kaam... 2 minute mein.').\n"
            "   - Avoid tongue-twister English jargon back-to-back; mix simple spoken Hindi words between tech terms.\n"
            "5. CLEAN VISUAL PROMPTS (NO AI GIBBERISH TEXT): In every 'visual_prompt', describe ONLY physical objects, scenes, characters, lighting, and abstract symbols. "
            "NEVER request text, signs, flowcharts, documents, dashboards, or UI screens.\n"
            "6. Generate valid JSON only. You MUST provide at least 6-8 distinct 'scene_cues' ordered strictly 1-to-1 with the narration sentences."
        )

        payload = {
            "model": "openai/gpt-oss-120b",
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.7,
        }

        # 1. Try OmniRoute first with exponential backoff on 429/transient errors
        for attempt in range(1, 4):
            try:
                log.info(f"Connecting to OmniRoute at {self.omniroute_url} (attempt {attempt})...")
                req = urllib.request.Request(
                    f"{self.omniroute_url.rstrip('/')}/chat/completions",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "AlmightyAnandStudio/1.0",
                    },
                )
                with urllib.request.urlopen(req, timeout=12) as resp:
                    data = json.loads(resp.read().decode())
                    content = data["choices"][0]["message"]["content"]
                    log.info("✅ LLM response successfully received via OmniRoute!")
                    return json.loads(content)
            except urllib.error.HTTPError as he:
                if he.code == 429:
                    wait_time = (2 ** attempt) * 1.5
                    log.warning(f"OmniRoute 429 Rate Limit hit. Backing off {wait_time:.1f}s...")
                    import time
                    time.sleep(wait_time)
                else:
                    log.info(f"OmniRoute returned HTTP {he.code}: {he.reason}, attempting fallback...")
                    break
            except Exception as e:
                log.info(f"OmniRoute attempt {attempt} failed ({e}), falling back...")
                break

        # 2. Upstream fallback to Groq with automatic retry & backoff
        if not self.groq_key:
            raise RuntimeError("GROQ_API_KEY is not configured and OmniRoute is unreachable.")

        url = "https://api.groq.com/openai/v1/chat/completions"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.groq_key}",
                "Content-Type": "application/json",
                "User-Agent": "AlmightyAnandStudio/1.0",
            },
        )
        for attempt in range(1, 4):
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode())
                    content = data["choices"][0]["message"]["content"]
                    log.info("✅ LLM response successfully received via Groq free tier!")
                    return json.loads(content)
            except urllib.error.HTTPError as he:
                if he.code == 429:
                    wait_time = (2 ** attempt) * 1.5
                    log.warning(f"Groq 429 Rate Limit. Backing off {wait_time:.1f}s...")
                    import time
                    time.sleep(wait_time)
                else:
                    log.warning(f"Groq API call attempt {attempt} failed: {he}")
                    if attempt == 3:
                        raise RuntimeError(f"All LLM routing failed: {he}")
            except Exception as e:
                log.warning(f"Groq API call attempt {attempt} failed: {e}")
                if attempt == 3:
                    raise RuntimeError(f"All LLM routing failed: {e}")
                import time
                time.sleep(1.5)

    def build_transformative_package(
        self,
        topic_data: dict,
        chosen_style: str = "commentary_broll",
    ) -> ProductionScriptPackage:
        title = topic_data.get("title", "")
        category = topic_data.get("category", "technology")
        if not title:
            raise ValueError("Cannot formulate script: topic title is empty.")

        production_id = f"prod_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        log.info(f"🎬 Formulating Transformative Hinglish Package for: '{title}'")
        log.info(f"🎨 Visual Style Preset: '{chosen_style}' | Category: '{category}'")

        llm_prompt = f"""Write an original transformative YouTube script package for Almighty Anand:
TOPIC: {title}
CATEGORY: {category}
STYLE: {chosen_style}

CRITICAL FACT-CHECK MANDATE:
- ONLY state verified facts about {title}.
- NEVER hallucinate or invent technical specifications, context window sizes (e.g. do NOT invent '1 TB context window'), parameter counts, or fake benchmarks.
- If specific numbers are not verified, frame capabilities accurately in general terms.

Generate valid JSON with:
"long_form_title": Viral, clickable 16:9 title for this topic (Hinglish/English),
"hook_hinglish": 0-2s high-energy shock hook (BANNED: 'Namaste', 'Welcome back', 'Hello'),
"long_form_summary_hinglish": 100-word deep-dive breakdown in punchy Hinglish,
"short_01_title": Clickable vertical Short title with #Shorts,
"short_01_hook_hinglish": 0-2s immediate vertical hook,
"short_01_script_hinglish": 65-80 word high-retention narration script in Hinglish,
"short_01_suspense_ending": High-curiosity cliffhanger closing line in Hinglish,
"scene_cues": [
  {{
    "overlay_text": "2-4 word high-contrast title banner",
    "badge_text": "1-2 word contextual badge (e.g. AI RELEASE, NEW HACK, WARNING)",
    "visual_prompt": "Detailed cinematic prompt for AI diffusion or stock B-roll camera angle",
    "narration_cue": "Spoken sentence corresponding to this scene"
  }}
] (Provide exactly 7 to 10 scene cues tailored specifically to {title})
"""

        live_data = self.call_live_llm(llm_prompt)

        # STRICT VERIFICATION: Verify scene_cues exist
        raw_scene_cues = live_data.get("scene_cues")
        if not raw_scene_cues or not isinstance(raw_scene_cues, list) or len(raw_scene_cues) < 4:
            raise RuntimeError(
                f"🚨 LLM returned invalid or empty scene_cues for '{title}'. "
                "Hardcoded banking fallbacks are banned. Aborting."
            )

        # Build dynamic ShotCard objects from live_data["scene_cues"]
        shot_cards = []
        for i, cue in enumerate(raw_scene_cues, 1):
            overlay = str(cue.get("overlay_text") or f"SHOT {i}").strip()
            badge = str(cue.get("badge_text") or "ALERT").strip().upper()
            vis_prompt = str(cue.get("visual_prompt") or f"Cinematic visual representing {title}").strip()
            narr_cue = str(cue.get("narration_cue") or "").strip()

            shot_cards.append(asdict(ShotCard(
                shot_index=i,
                duration_seconds=3.0,
                style_preset=chosen_style,
                camera_angle=f"Angle {i}",
                visual_prompt=vis_prompt,
                narration_cue=narr_cue,
                overlay_text=overlay,
                badge_text=badge,
            )))

        # Dynamic Script Content
        long_title = live_data.get("long_form_title") or f"{title}: The Real Truth Exposed"
        long_body = live_data.get("long_form_summary_hinglish") or live_data.get("hook_hinglish", "")
        
        short_title = live_data.get("short_01_title") or f"{title[:45]} #Shorts"
        short_hook = live_data.get("short_01_hook_hinglish") or shot_cards[0]["narration_cue"]
        short_script = live_data.get("short_01_script_hinglish")
        if not short_script:
            raise RuntimeError(f"🚨 LLM failed to generate 'short_01_script_hinglish' for '{title}'.")

        # Enforce Ban on generic openings
        banned_openings = ["namaste", "welcome back", "hello guys", "hey guys", "hello friends"]
        for b in banned_openings:
            if short_script.lower().startswith(b):
                short_script = re.sub(r"^" + re.escape(b) + r"[\s,\.\!]*", "", short_script, flags=re.IGNORECASE).strip()
                short_script = short_script[0].upper() + short_script[1:]

        suspense = live_data.get("short_01_suspense_ending") or "Check the pinned comment right now before it's too late!"

        long_form = {
            "title": long_title,
            "format": "16:9",
            "target_duration_minutes": 8,
            "hook_hinglish": short_hook,
            "script_body_hinglish": long_body,
            "shot_cards": shot_cards[:4],
        }

        shorts = [
            asdict(ShortVariant(
                variant_id=f"{production_id}_short_01",
                title=short_title,
                duration_seconds=round(len(shot_cards) * 3.0, 1),
                hook_hinglish=short_hook,
                suspense_ending_hinglish=suspense,
                narration_script_hinglish=short_script,
                shot_cards=shot_cards,
            ))
        ]

        # Monetization: Tracked Affiliate Links & Pinned Comment
        affiliates = AFFILIATE_CATALOG.get(category, AFFILIATE_CATALOG["general"])
        affiliate_lines = "\n".join([f"🔗 {item['title']}: {item['url']}" for item in affiliates])
        clean_title_snippet = re.sub(r"[^\w\s]", "", title)[:40]

        pinned_comment = (
            f"⚡ UPDATE: {clean_title_snippet}\n\n"
            f"📌 Recommended tools & resources:\n{affiliate_lines}\n\n"
            f"💬 Is topic par aapka kya opinion hai? Comment mein batao, let's discuss!"
        )

        package = ProductionScriptPackage(
            production_id=production_id,
            source_topic=title,
            category=category,
            transformation_ratio=MIN_VISUAL_TRANSFORMATION_RATIO,
            visual_style=chosen_style,
            long_form_16x9=long_form,
            shorts_9x16_variants=shorts,
            affiliate_links=affiliates,
            pinned_comment=pinned_comment,
        )

        # Record topic in SQLite memory store
        self.memory.record_topic(
            title=title,
            source=topic_data.get("source", "trend_scout"),
            source_url=topic_data.get("source_url", ""),
            category=category,
            angle="transformative_commentary",
            visual_style=chosen_style,
            status="scripted",
        )

        return package


def main():
    parser = argparse.ArgumentParser(description="ScriptDirector Agent")
    parser.add_argument("--dry-run", action="store_true", help="Run in dry run mode")
    args = parser.parse_args()

    director = ScriptDirector(dry_run=args.dry_run)
    sample_topic = {
        "title": "Microsoft Unveils Code Red AI Infrastructure Upgrade 2026",
        "category": "technology",
        "source": "google_trends_india",
    }
    pkg = director.build_transformative_package(sample_topic)
    print("\n--- ScriptDirector Transformative Package ---")
    print(json.dumps(asdict(pkg), indent=2))


if __name__ == "__main__":
    main()
