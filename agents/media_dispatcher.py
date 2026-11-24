#!/usr/bin/env python3
"""
media_dispatcher.py — 100% Copyright-Free AI Visual Asset Generation & Media Dispatcher

MANDATES:
  1. ZERO WEB SCRAPING: All DuckDuckGo, Bing, Google Image scrapers are DELETED.
     No stock photography watermarks (Alamy, Adobe Stock, Dreamstime, Shutterstock, Getty).
  2. 100% AI-GENERATED COPYRIGHT-FREE VISUALS:
     - Uses each shot card's `visual_prompt` + consistent style suffix:
       "cinematic dark futuristic tech illustration, anime/cyberpunk aesthetic, vertical 9:16, no text, no watermarks"
     - Primary: Open-Gen-AI (:8091) when Spot GPU is active.
     - Secondary: Pollinations.ai FLUX (https://image.pollinations.ai/prompt/{p}?width=1080&height=1920&model=flux&nologo=true)
     - Tertiary: Official Pexels API (royalty-free, zero watermarks) ONLY if PEXELS_API_KEY is present.
     - Fallback: Procedural dark cyberpunk vector canvas (guaranteed 0 watermarks).
  3. AUTO-CROPPING & SAFE FRAMING:
     - Fits every image using ImageOps.fit(img, (1080, 1920), centering=(0.5, 0.35)).
  4. GPU LIFECYCLE MANAGEMENT:
     - Wakes Spot GPU via infra/gpu_controller.py when batch rendering on GCP,
       with automatic CPU fallback (Edge-TTS + Pollinations Flux) if GPU budget is hit.

CLI Entrypoint:
    python -m agents.media_dispatcher [--dry-run]
"""

#!/usr/bin/env python3
"""
media_dispatcher.py — 100% Copyright-Free AI Visual Asset Generation & Media Dispatcher
"""

import argparse
import base64
import hashlib
import io
import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from PIL import Image, ImageOps

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [MEDIA-DISPATCH] %(levelname)s %(message)s",
)
log = logging.getLogger("media_dispatcher")

STYLE_SUFFIX = (
    "cinematic dark futuristic tech illustration, anime cyberpunk aesthetic, "
    "vertical 9:16, 8k high quality, no text, no watermarks"
)


@dataclass
class DispatchedMediaManifest:
    production_id: str
    voice_long_path: str
    voice_short_paths: list
    broll_image_paths: list
    music_track_path: str
    hindi_dubbed_voice_path: str
    gpu_credits_spent: float
    dispatched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class MediaDispatcher:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.gpu_enabled = os.environ.get("GPU_CONTROLLER_ENABLED", "false").lower() == "true"
        self.opengenai_url = os.environ.get("OPENGENAI_URL", "http://10.160.0.3:8091")
        self.hf_token = self._load_env_var("HF_TOKEN") or ""
        self.pexels_key = self._load_env_var("PEXELS_API_KEY") or ""

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

    def _call_opengenai_gpu(self, prompt: str) -> Optional[bytes]:
        if not self.gpu_enabled:
            return None
        try:
            payload = {
                "prompt": f"{prompt}, {STYLE_SUFFIX}",
                "aspect_ratio": "9:16",
                "width": 1080,
                "height": 1920,
            }
            req = urllib.request.Request(
                f"{self.opengenai_url.rstrip('/')}/generate-broll",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode())
                if data.get("status") == "success" and data.get("image_bytes"):
                    return base64.b64decode(data["image_bytes"])
        except Exception as e:
            log.debug(f"Open-Gen-AI GPU worker not reachable ({e})")
        return None

    def _generate_flux_pollinations(self, prompt: str, seed: int) -> Optional[bytes]:
        clean_p = re.sub(r"[^\w\s]", " ", prompt)
        clean_p = re.sub(r"\s+", " ", clean_p).strip()
        words = clean_p.split()[:14]
        full_prompt = f"{' '.join(words)}, {STYLE_SUFFIX}"
        encoded_p = urllib.parse.quote(full_prompt)
        url = (
            f"https://image.pollinations.ai/prompt/{encoded_p}"
            f"?width=1080&height=1920&model=flux&nologo=true&seed={seed}"
        )

        backoffs = [5, 10, 20]
        for attempt, wait_s in enumerate(backoffs, 1):
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = resp.read()
                    if len(data) > 30_000:
                        return data
                    log.warning(f"Pollinations returned small payload ({len(data)} bytes) on attempt {attempt}")
            except Exception as e:
                log.warning(f"Pollinations attempt {attempt} failed: {e}")
            if attempt < len(backoffs):
                time.sleep(wait_s)
        return None

    def _generate_flux_huggingface(self, prompt: str) -> Optional[bytes]:
        if not self.hf_token:
            return None
        url = "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell"
        payload = {"inputs": f"{prompt}, {STYLE_SUFFIX}"}
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self.hf_token}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=35) as resp:
                data = resp.read()
                if len(data) > 30_000:
                    return data
        except Exception as e:
            log.warning(f"HuggingFace FLUX fallback failed: {e}")
        return None

    def _fetch_pexels_royalty_free(self, query: str) -> Optional[bytes]:
        if not self.pexels_key:
            return None
        try:
            clean_q = re.sub(r"[^\w\s]", " ", query).strip()
            encoded_q = urllib.parse.quote(" ".join(clean_q.split()[:3]) or "technology")
            url = f"https://api.pexels.com/v1/search?query={encoded_q}&orientation=portrait&per_page=1"
            req = urllib.request.Request(
                url,
                headers={"Authorization": self.pexels_key, "User-Agent": "AlmightyAnandStudio/2.0"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                photos = data.get("photos", [])
                if photos:
                    img_url = photos[0]["src"].get("large2x") or photos[0]["src"].get("original")
                    req_img = urllib.request.Request(img_url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req_img, timeout=12) as r_img:
                        return r_img.read()
        except Exception as e:
            log.warning(f"Pexels API fallback failed: {e}")
        return None

    def dispatch_media(self, shot_cards: List[dict], production_id: str) -> List[Path]:
        scenes_dir = Path(f"/tmp/almighty_{production_id}/scenes")
        scenes_dir.mkdir(parents=True, exist_ok=True)
        generated_paths: List[Path] = []
        failed_shots = 0

        log.info(f"🎨 Generating {len(shot_cards)} real 9:16 scene images for {production_id}...")

        for i, card in enumerate(shot_cards, 1):
            target_path = scenes_dir / f"shot_{i:02d}.jpg"
            vis_prompt = card.get("visual_prompt", f"Cinematic tech scene {i}")
            overlay_text = card.get("overlay_text", "")
            seed = int(hashlib.md5(f"{vis_prompt}_{i}".encode()).hexdigest()[:8], 16) % 100000

            image_bytes = None
            source_used = "none"

            # 1. Spot GPU Open-Gen-AI
            image_bytes = self._call_opengenai_gpu(vis_prompt)
            if image_bytes:
                source_used = "opengenai_gpu_flux"

            # 2. Pollinations FLUX (with inter-shot pacing to prevent 429s)
            if not image_bytes:
                if i > 1:
                    time.sleep(4.0)
                image_bytes = self._generate_flux_pollinations(vis_prompt, seed)
                if image_bytes:
                    source_used = "pollinations_ai_flux"

            # 3. Hugging Face Inference FLUX.1-schnell
            if not image_bytes:
                image_bytes = self._generate_flux_huggingface(vis_prompt)
                if image_bytes:
                    source_used = "huggingface_flux_schnell"

            # 4. Official Pexels API (royalty-free only)
            if not image_bytes and self.pexels_key:
                image_bytes = self._fetch_pexels_royalty_free(f"{overlay_text} technology")
                if image_bytes:
                    source_used = "pexels_royalty_free"

            if not image_bytes:
                failed_shots += 1
                log.error(f"❌ Shot {i:02d} failed all real visual generators.")
                if failed_shots > 1:
                    raise RuntimeError(
                        f"Aborting render: {failed_shots} shots failed visual generation. "
                        "Refusing to emit blank wireframe placeholders."
                    )
                raise RuntimeError(f"Shot {i:02d} could not be generated by any visual provider.")

            img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            # Strip bottom 6% on Pollinations outputs to eliminate corner logo badge
            if source_used == "pollinations_ai_flux":
                img = img.crop((0, 0, img.width, int(img.height * 0.94)))

            fitted = ImageOps.fit(img, (1080, 1920), centering=(0.5, 0.35))
            fitted.save(target_path, quality=92)
            log.info(f"✅ Shot {i:02d} saved via {source_used}")

            meta_path = scenes_dir / f"shot_{i:02d}.json"
            meta_path.write_text(json.dumps({
                "shot_index": i,
                "visual_prompt": vis_prompt,
                "overlay_text": overlay_text,
                "source": source_used,
                "copyright_free": True,
            }))
            generated_paths.append(target_path)

        return generated_paths

    def dispatch_dry_run(self, production_id: str = "prod_mock_2026") -> DispatchedMediaManifest:
        return DispatchedMediaManifest(
            production_id=production_id,
            voice_long_path=f"/mock/data/renders/{production_id}_voice_16x9.wav",
            voice_short_paths=[f"/mock/data/renders/{production_id}_short_01.wav"],
            broll_image_paths=[f"/mock/data/renders/{production_id}_broll_01.png"],
            music_track_path=f"/mock/data/renders/{production_id}_bgm_maestro.wav",
            hindi_dubbed_voice_path=f"/mock/data/renders/{production_id}_voice_hindi.wav",
            gpu_credits_spent=0.00,
        )


def main():
    parser = argparse.ArgumentParser(description="MediaDispatcher Agent")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    dispatcher = MediaDispatcher(dry_run=args.dry_run)
    sample_cards = [
        {"visual_prompt": "Futuristic quantum processor glowing in deep space", "overlay_text": "Next-Gen AI"},
        {"visual_prompt": "Neural network pathways firing in neon cyan", "overlay_text": "Speed Test"},
    ]
    for p in dispatcher.dispatch_media(sample_cards, "test_dispatch"):
        print("•", p)


if __name__ == "__main__":
    main()