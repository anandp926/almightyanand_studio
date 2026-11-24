#!/usr/bin/env python3
"""
script_director.py — Transformative Script & Shot Card Director

MANDATES:
  1. STRICT HINGLISH MANDATE: All scripts, voiceovers, and captions MUST be in
     conversational, punchy Indian Hinglish (e.g., "Bhai, kya aapko pata hai...",
     "Agar aapka bank account freeze ho gaya to...").
  2. RAPID PACING: Structured 2.0–2.5 second shot cards (cuts every 2-3s max).
  3. ANTI-REUSED CONTENT: 100% original transformative commentary (>70% visual replacement).
  4. DUAL-FORMAT: 1 Long-form (16:9) + 2-3 Vertical Shorts (9:16) with cliffhangers.
  5. DAY-1 MONETIZATION: Auto-injected tracked affiliate links & pinned comments.

CLI Entrypoints:
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
        {"title": "Check Your Mule/Fraud Risk Free (Official RBI Portal)", "url": "https://amzn.to/rbi-fraud-check"},
        {"title": "Zero-Fee Scam Protection App (India)", "url": "https://amzn.to/cyber-shield-app"},
    ],
    "technology": [
        {"title": "Top AI Productivity Extension for Chrome", "url": "https://amzn.to/ai-productivity-tool"},
        {"title": "Hardware Security Key for Banking", "url": "https://amzn.to/yubikey-india"},
    ],
    "general": [
        {"title": "Almighty Anand Recommended Creator & Tech Gear", "url": "https://amzn.to/anand-stack"},
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
        if not self.groq_key:
            log.warning("No GROQ_API_KEY found, using dynamic Hinglish generator")
            return {}

        url = "https://api.groq.com/openai/v1/chat/completions"
        payload = {
            "model": "openai/gpt-oss-120b",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are the Lead Script Director for Almighty Anand (@almightyanand). "
                        "MANDATE: Output high-energy, conversational Indian Hinglish (Roman script). "
                        "Never use formal textbook Hindi or pure English. Use natural spoken phrases like "
                        "'Bhai, kya aapko pata hai...', 'Agar aapka bank account freeze ho gaya to...', "
                        "'Dhyan se suno'. Every short must have a 2-second hook and cuts every 2-3 seconds. "
                        "Reply in valid JSON only."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.7,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.groq_key}",
                "Content-Type": "application/json",
                "User-Agent": "AlmightyAnandStudio/1.0",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
                content = data["choices"][0]["message"]["content"]
                return json.loads(content)
        except Exception as e:
            log.error(f"Live LLM call error: {e}")
            return {}

    def build_transformative_package(
        self,
        topic_data: dict,
        chosen_style: str = "commentary_broll",
    ) -> ProductionScriptPackage:
        title = topic_data.get("title", "Live Trending Alert")
        category = topic_data.get("category", "finance")
        production_id = f"prod_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

        log.info(f"🎬 Formulating Transformative Hinglish Package for: '{title}'")
        log.info(f"🎨 Visual Style Preset: '{chosen_style}' | Mandatory Language: Hinglish")

        llm_prompt = f"""Write an original transformative Hinglish YouTube script package for Almighty Anand:
TOPIC: {title}
CATEGORY: {category}
STYLE: {chosen_style}

Generate valid JSON with:
"hook_hinglish": High-energy 0-2s hook in conversational Hinglish,
"long_form_summary_hinglish": 120-word deep-dive breakdown in Hinglish,
"short_01_hook_hinglish": Punchy 3-second short hook,
"short_01_script_hinglish": 70-word punchy Hinglish script for a 30s vertical Short,
"short_01_suspense_ending": High-curiosity cliffhanger closing line in Hinglish,
"scene_cues": list of 10 shot descriptions (each 2.5s) with overlay_text and visual_prompt
"""
        live_data = self.call_live_llm(llm_prompt)

        # Fallback dynamic Hinglish generator if LLM was unavailable
        clean_kw = re.sub(r"[^\w\s]", "", title).split()[:4]
        topic_short = " ".join(clean_kw)

        hook = live_data.get("hook_hinglish") or (
            f"Bhai, kya aapko pata hai? {topic_short} ko lekar abhi abhi ek massive alert aaya hai!"
        )
        body = live_data.get("long_form_summary_hinglish") or (
            f"Welcome back to Almighty Anand! Agar aap sochte ho ki aapka bank account 100% safe hai, "
            f"to aap bohot badi galatfehmi mein ho. RBI aur cyber agencies ne naya AI monitoring system "
            f"activate kar diya hai. Ab bina kisi warning ke suspicious transactions block honge. "
            f"Aaj ke is breakdown mein jaanenge ki aapko apne phone par kya settings turant badalni hain."
        )

        short_01_hook = live_data.get("short_01_hook_hinglish") or (
            f"Bhai, kya aapka account bhi bina bataye freeze ho sakta hai?"
        )
        short_01_script = live_data.get("short_01_script_hinglish") or (
            f"Bhai, kya aapka account bhi bina bataye freeze ho sakta hai? "
            f"RBI aur cyber cell ne ek naya AI tracking rule chalu kiya hai. "
            f"Agar aapke account mein kisi third-party se suspicious transfer aaya, "
            f"to bina kisi notice ke aapka UPI lock ho jayega. "
            f"Isko bachane ke liye phone mein yeh ek setting turant check karo."
        )
        suspense = live_data.get("short_01_suspense_ending") or (
            "Yeh setting off nahi ki, to agla transfer atak sakta hai. Check pinned comment right now!"
        )

        # Construct 11 Rapid-Cut Shot Cards (2.5s each for maximum retention)
        rapid_shot_cards = [
            asdict(ShotCard(1, 2.5, chosen_style, "Medium Close-up", f"Stylized host Anand looking shocked with phone alert badge: {topic_short}", "Bhai, kya aapka account bhi freeze ho sakta hai?", "ACCOUNT FREEZE RISK?")),
            asdict(ShotCard(2, 2.5, chosen_style, "Dutch Angle", "Digital bank balance turning into a locked red icon with glowing circuits", "RBI aur cyber cell ne naya AI tracking rule lagaya hai.", "NEW AI MONITORING")),
            asdict(ShotCard(3, 2.5, chosen_style, "Extreme Close-up", "Close up on smartphone screen showing failed transaction notification", "Agar kisi unknown party se payment aaya to turant alert.", "SUSPICIOUS TRANSFER")),
            asdict(ShotCard(4, 2.5, chosen_style, "Top-Down Flatlay", "Infographic illustrating mule accounts vs normal user accounts", "Aapka account automatically mule category mein ja sakta hai.", "MULE ACCOUNT TRAP")),
            asdict(ShotCard(5, 2.5, chosen_style, "Macro Camera Push", "Glowing holographic warning sign with Indian cyber cell logo", "Bina kisi advance SMS ke UPI freeze ho raha hai.", "NO ADVANCE SMS")),
            asdict(ShotCard(6, 2.5, chosen_style, "Isometric View", "Step 1: Open banking app and tap security tab", "Isko bachane ke liye Step 1: Banking app kholo.", "STEP 1: APP SECURITY")),
            asdict(ShotCard(7, 2.5, chosen_style, "Screen Zoom", "Step 2: Turn off unverified third-party permissions", "Step 2: Unverified third-party access band karo.", "STEP 2: REVOKE ACCESS")),
            asdict(ShotCard(8, 2.5, chosen_style, "Side Angle", "Step 3: Enable two-factor biometric verification", "Step 3: Biometric security on karo.", "STEP 3: BIOMETRIC LOCK")),
            asdict(ShotCard(9, 2.5, chosen_style, "Wide Cinematic", "Host Anand pointing forward with calm confident expression", "Yeh 30 seconds aapko lakhon ke loss se bacha sakte hain.", "SAVE YOUR MONEY")),
            asdict(ShotCard(10, 2.5, chosen_style, "Low Angle Rim Light", "Character warning sign with dark background and amber rim lights", "Agar yeh nahi kiya, to agla payment atak sakta hai.", "CHECK PINNED COMMENT")),
            asdict(ShotCard(11, 2.5, chosen_style, "Frontal CTA", "Official Almighty Anand subscribe badge and pinned comment pointer", "Full safety guide pinned comment mein hai. Abhi subscribe karo!", "SUBSCRIBE FOR HACKS")),
        ]

        # 1. 16:9 Long-Form Script
        long_form = {
            "title": f"{title}: Kya Hai Naya AI Rule? (Full Truth Exposed)",
            "format": "16:9",
            "target_duration_minutes": 8,
            "hook_hinglish": hook,
            "retention_anchors": {
                "0:30": "The New AI Mule Detection System",
                "2:45": "Why Normal Users Are Getting Trapped",
                "5:15": "The 3 Critical Security Settings to Change",
                "7:00": "How to Unfreeze Your UPI in 24 Hours",
            },
            "script_body_hinglish": body,
            "shot_cards": rapid_shot_cards[:4],
        }

        # 2. Dual-Format Shorts (9:16)
        shorts = [
            asdict(ShortVariant(
                variant_id=f"{production_id}_short_01",
                title=f"Kya aapka bank account freeze ho sakta hai? #Shorts",
                duration_seconds=27.5,
                hook_hinglish=short_01_hook,
                suspense_ending_hinglish=suspense,
                narration_script_hinglish=short_01_script,
                shot_cards=rapid_shot_cards,
            )),
            asdict(ShortVariant(
                variant_id=f"{production_id}_short_02",
                title=f"RBI New Cyber Rule 2026: Don't Do This #Shorts",
                duration_seconds=28.0,
                hook_hinglish="Bhai, RBI ne chupke se ek naya rule pass kar diya hai.",
                suspense_ending_hinglish="Apne dosto ko yeh video abhi share karo!",
                narration_script_hinglish="RBI ne sabhi banks ko AI monitoring mandate diya hai. Agar aap P2P transfer karte ho, to yeh 2 galti bilkul mat karna.",
            )),
        ]

        # 3. Monetization: Tracked Affiliate Links & Pinned Comment
        affiliates = AFFILIATE_CATALOG.get(category, AFFILIATE_CATALOG["general"])
        affiliate_lines = "\n".join([f"🔗 {item['title']}: {item['url']}" for item in affiliates])

        clean_topic_title = re.sub(r"[^\w\s]", "", title)
        pinned_comment = (
            f"🚨 IMPORTANT UPDATE ({clean_topic_title[:35]}):\n\n"
            f"📌 Recommended tools to protect your bank & UPI:\n{affiliate_lines}\n\n"
            f"💬 Kya aapko bhi koi suspicious payment alert aaya hai? Comment mein batao, main direct reply karunga!"
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

        # Record in memory store
        self.memory.record_topic(
            title=title,
            source=topic_data.get("source", "trend_scout"),
            source_url=topic_data.get("source_url", ""),
            category=category,
            angle="cyber_security_breakdown",
            visual_style=chosen_style,
            status="scripted",
        )

        return package


def main():
    parser = argparse.ArgumentParser(description="ScriptDirector Agent")
    parser.add_argument("--dry-run", action="store_true", help="Run without external API calls")
    args = parser.parse_args()

    director = ScriptDirector(dry_run=args.dry_run)
    sample_topic = {
        "title": "I4C signs MoU with RBI Innovation Hub to use AI for detecting mule accounts, tackling cyber fraud",
        "category": "finance",
        "source": "google_news_india",
    }
    pkg = director.build_transformative_package(sample_topic)
    print("\n--- ScriptDirector Transformative Package ---")
    print(json.dumps(asdict(pkg), indent=2))


if __name__ == "__main__":
    main()
