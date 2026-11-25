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
#!/usr/bin/env python3
"""
script_director.py — Transformative Script & Shot Card Director
"""
#!/usr/bin/env python3
"""
script_director.py — Transformative Script & Shot Card Director
"""

import argparse
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from agents.memory_store import MemoryStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SCRIPT-DIR] %(levelname)s %(message)s",
)
log = logging.getLogger("script_director")

MIN_VISUAL_TRANSFORMATION_RATIO = 0.80
STUDIO_ROOT = Path(__file__).resolve().parent.parent

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
    speaker: str = "narrator"
    is_original_generated: bool = True


@dataclass
class ShortVariant:
    variant_id: str
    title: str
    duration_seconds: float
    category: str = "technology"
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
        env_file = STUDIO_ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith(f"{var_name}="):
                    return line.split("=", 1)[1].strip()
        return None

    @staticmethod
    def _load_playbook() -> dict:
        pb_path = STUDIO_ROOT / "configs" / "viral_playbook.json"
        if pb_path.exists():
            try:
                return json.loads(pb_path.read_text(encoding="utf-8"))
            except Exception as e:
                log.warning(f"Failed to load viral_playbook.json: {e}")
        return {"pillars": {}, "character_voices": {}}

    def call_live_llm(self, prompt: str) -> dict:
        system_content = (
            "You are the Lead Script Director for Almighty Anand (@almightyanand).\n"
            "CRITICAL MANDATES:\n"
            "1. Output high-energy, conversational Indian Hinglish (Roman script).\n"
            "2. SCROLL-STOPPING 0-2s HOOK (STRICT BAN): NEVER start with 'Namaste dosto', 'Welcome back', 'Hello guys', "
            "or flat textbook statements like 'AI ab har startup ki backbone ban gaya hai'. "
            "Line 1 MUST create an instant curiosity gap, shock, or direct challenge to the viewer "
            "(e.g. 'India mein ek naya AI update aaya hai... jo 5 ghante ka kaam 2 minute mein karta hai, par kaise?', "
            "'Agar aap UPI ya AI tools use karte ho, to 30 second ruko...').\n"
            "3. ZERO PR / NO SPOKESPERSON NAMES: Never mention executive names, company spokespeople, or interview sources. "
            "Make every script sound 100% creator-native and focused strictly on how the trend impacts the viewer.\n"
            "4. STRICT FACT-CHECK MANDATE: State ONLY verified facts from the source topic. "
            "NEVER hallucinate or invent technical specs, context window sizes, parameter counts, pricing, or fake benchmarks.\n"
            "5. HUMANIZED SPOKEN-WORD PACING (CRITICAL FOR VOICEOVER):\n"
            "   - Write for the EAR, not for reading. Keep every sentence short (6 to 10 words max).\n"
            "   - Use natural Indian spoken connectors: 'Suno...', 'Dekho,', 'Matlab,', 'Wait...', 'Aur sabse badi baat?'.\n"
            "   - Use '...' for dramatic 0.5s pauses before a reveal, and '?' / '!' for vocal inflection.\n"
            "   - Break ideas into punchy Q&A rhythm (e.g., 'Iska fayda kya hai? Simple. Aapka 2 ghante ka kaam... 2 minute mein.').\n"
            "   - Avoid dense English jargon back-to-back; weave simple spoken Hindi words between tech terms.\n"
            "6. CLEAN VISUAL PROMPTS (NO AI GIBBERISH TEXT): In every 'visual_prompt', describe ONLY physical objects, "
            "scenes, characters, lighting, and abstract symbols. NEVER request text, signs, flowcharts, documents, dashboards, or UI screens.\n"
            "7. Generate valid JSON only. Provide 7 to 8 distinct 'scene_cues' ordered strictly 1-to-1 with the narration sentences."
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

        # 1. Try OmniRoute first
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
                    log.info("✅ LLM response received via OmniRoute!")
                    return json.loads(content)
            except urllib.error.HTTPError as he:
                if he.code == 429:
                    wait_time = (2 ** attempt) * 1.5
                    log.warning(f"OmniRoute 429 Rate Limit. Backing off {wait_time:.1f}s...")
                    time.sleep(wait_time)
                else:
                    log.info(f"OmniRoute returned HTTP {he.code}: {he.reason}, falling back...")
                    break
            except Exception as e:
                log.info(f"OmniRoute attempt {attempt} failed ({e}), falling back...")
                break

        # 2. Upstream fallback to Groq
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
                    log.info("✅ LLM response received via Groq free tier!")
                    return json.loads(content)
            except urllib.error.HTTPError as he:
                if he.code == 429:
                    wait_time = (2 ** attempt) * 1.5
                    log.warning(f"Groq 429 Rate Limit. Backing off {wait_time:.1f}s...")
                    time.sleep(wait_time)
                else:
                    log.warning(f"Groq API call attempt {attempt} failed: {he}")
                    if attempt == 3:
                        raise RuntimeError(f"All LLM routing failed: {he}")
            except Exception as e:
                log.warning(f"Groq API call attempt {attempt} failed: {e}")
                if attempt == 3:
                    raise RuntimeError(f"All LLM routing failed: {e}")
                time.sleep(1.5)

        raise RuntimeError("All LLM routing attempts exhausted.")

    def build_transformative_package(
        self,
        topic_data: dict,
        chosen_style: str = "commentary_broll",
    ) -> ProductionScriptPackage:
        title = topic_data.get("title", "")
        category = topic_data.get("category", "technology")
        if not title:
            raise ValueError("Cannot formulate script: topic title is empty.")

        # Load viral playbook priors (hooks + multi-character speaker voices)
        pb = self._load_playbook()
        pillar_cfg = pb.get("pillars", {}).get(category, pb.get("pillars", {}).get("technology", {}))
        default_speaker = pillar_cfg.get("default_speaker", "narrator")
        top_hooks = [
            h["pattern"]
            for h in sorted(pillar_cfg.get("hook_priors", []), key=lambda x: x.get("weight", 1.0), reverse=True)[:3]
        ]
        allowed_speakers = list(pb.get("character_voices", {}).keys()) or ["narrator"]
        hook_examples_str = "\n".join(f"  - {h}" for h in top_hooks) if top_hooks else "  - Wait... kya aapne yeh naya update dekha?"

        production_id = f"prod_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        log.info(f"🎬 Formulating Transformative Hinglish Package for: '{title}'")
        log.info(f"🎨 Visual Style Preset: '{chosen_style}' | Category: '{category}' | Default Speaker: '{default_speaker}'")

        llm_prompt = f"""Write an original transformative YouTube script package for Almighty Anand:
TOPIC: {title}
CATEGORY: {category}
STYLE: {chosen_style}

PROVEN HIGH-RETENTION HOOK STRUCTURES FOR THIS PILLAR (adapt the style, don't copy verbatim):
{hook_examples_str}

ALLOWED CHARACTER SPEAKERS: {allowed_speakers}
DEFAULT SPEAKER: {default_speaker}

STRICT RULES:
- ONLY state verified facts about {title}. Never invent specs or numbers.
- NEVER mention company spokespeople, CEO/executive names, or PR/interview sources.
- Start short_01_script_hinglish with a punchy curiosity-gap question or shock hook (NOT a flat statement).
- Keep sentences 6-10 words max with '...' pauses for natural spoken-word TTS delivery.
- End with a strong curiosity CTA/question for comments.
- Order scene_cues strictly 1-to-1 with the narration flow. Never request text, logos, signs, or UI screens in visual_prompt.
- For each scene_cue, assign 'speaker' from {allowed_speakers}. Switch speakers (e.g., 'skeptic_friend', 'tech_expert', 'mythic_hero', 'villain_heavy') whenever there is dialogue or a contrasting question; otherwise use '{default_speaker}'.

Generate valid JSON with:
"long_form_title": Clickable 16:9 title (Hinglish/English),
"hook_hinglish": 0-2s high-energy curiosity hook,
"long_form_summary_hinglish": 100-word deep-dive breakdown in punchy Hinglish,
"short_01_title": Clickable vertical Short title with #Shorts,
"short_01_hook_hinglish": 0-2s scroll-stopping vertical hook,
"short_01_script_hinglish": 70-85 word humanized Hinglish narration script (starts with short_01_hook_hinglish),
"short_01_suspense_ending": High-curiosity closing CTA line in Hinglish,
"scene_cues": [
  {{
    "overlay_text": "2-4 word punchy title banner",
    "badge_text": "1-2 word contextual badge (e.g. AI SHIFT, ALERT, BREAKTHROUGH)",
    "speaker": "Speaker ID from {allowed_speakers}",
    "visual_prompt": "Cinematic physical scene, objects, lighting, and characters ONLY (no text, no signs, no charts)",
    "narration_cue": "Exact sentence from short_01_script_hinglish corresponding to this cut"
  }}
] (Provide 7 to 8 scene cues ordered 1-to-1 with the script)
"""

        live_data = self.call_live_llm(llm_prompt)

        raw_scene_cues = live_data.get("scene_cues")
        if not raw_scene_cues or not isinstance(raw_scene_cues, list) or len(raw_scene_cues) < 5:
            raise RuntimeError(
                f"🚨 LLM returned invalid or empty scene_cues for '{title}'. Aborting."
            )

        shot_cards = []
        for i, cue in enumerate(raw_scene_cues, 1):
            overlay = str(cue.get("overlay_text") or f"SHOT {i}").strip()
            badge = str(cue.get("badge_text") or "UPDATE").strip().upper()
            vis_prompt = str(cue.get("visual_prompt") or f"Cinematic futuristic scene for {title}").strip()
            narr_cue = str(cue.get("narration_cue") or "").strip()
            speaker = str(cue.get("speaker") or default_speaker).strip()
            if speaker not in allowed_speakers:
                speaker = default_speaker

            shot_cards.append(asdict(ShotCard(
                shot_index=i,
                duration_seconds=3.5,
                style_preset=chosen_style,
                camera_angle=f"Angle {i}",
                visual_prompt=vis_prompt,
                narration_cue=narr_cue,
                overlay_text=overlay,
                badge_text=badge,
                speaker=speaker,
            )))

        long_title = live_data.get("long_form_title") or f"{title}: Real Impact Explained"
        long_body = live_data.get("long_form_summary_hinglish") or live_data.get("hook_hinglish", "")

        short_title = live_data.get("short_01_title") or f"{title[:45]} #Shorts"
        short_hook = live_data.get("short_01_hook_hinglish") or shot_cards[0]["narration_cue"]
        short_script = live_data.get("short_01_script_hinglish")
        if not short_script:
            raise RuntimeError(f"🚨 LLM failed to generate 'short_01_script_hinglish' for '{title}'.")

        banned_openings = ["namaste", "welcome back", "hello guys", "hey guys", "hello friends"]
        for b in banned_openings:
            if short_script.lower().startswith(b):
                short_script = re.sub(r"^" + re.escape(b) + r"[\s,\.\!]*", "", short_script, flags=re.IGNORECASE).strip()
                if short_script:
                    short_script = short_script[0].upper() + short_script[1:]

        suspense = live_data.get("short_01_suspense_ending") or "Aapka kya opinion hai? Comment mein batao!"

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
                duration_seconds=round(len(shot_cards) * 3.5, 1),
                category=category,
                hook_hinglish=short_hook,
                suspense_ending_hinglish=suspense,
                narration_script_hinglish=short_script,
                shot_cards=shot_cards,
            ))
        ]

        affiliates = AFFILIATE_CATALOG.get(category, AFFILIATE_CATALOG["general"])
        affiliate_lines = "\n".join([f"🔗 {item['title']}: {item['url']}" for item in affiliates])
        clean_title_snippet = re.sub(r"[^\w\s]", "", title)[:40]

        pinned_comment = (
            f"⚡ UPDATE: {clean_title_snippet}\n\n"
            f"📌 Recommended tools & resources:\n{affiliate_lines}\n\n"
            f"💬 Is topic par aapka kya opinion hai? Comment mein batao!"
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
        "title": "Google Gemini Autonomous Coding Agent Update",
        "category": "technology",
        "source": "google_news_india",
    }
    pkg = director.build_transformative_package(sample_topic)
    print("\n--- ScriptDirector Transformative Package ---")
    print(json.dumps(asdict(pkg), indent=2))


if __name__ == "__main__":
    main()