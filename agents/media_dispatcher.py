#!/usr/bin/env python3
"""
media_dispatcher.py — AI Visual Asset Generation & Media Dispatcher

  1. NO WEB SCRAPING: no image-search scrapers, so no stock watermarks.
  2. GENERATED VISUALS from each shot card's `visual_prompt` + the suffix of the
     one fixed look in configs/visual_style.json (which also keeps the host's
     corner of the frame clear):
     - Open-Gen-AI on the Spot GPU, only when GPU_CONTROLLER_ENABLED=true AND the
       worker passes a health probe AND it actually returns image bytes.
     - Pollinations.ai FLUX.
     - Hugging Face FLUX.1-schnell (needs HF_TOKEN).
     - Pexels API (needs PEXELS_API_KEY).
  3. FRAMING: ImageOps.fit(img, (1080, 1920), centering=(0.5, 0.35)).
  4. RETRIES: `seed_salt` changes the image seeds, so a QA retry gets new
     visuals instead of regenerating the frames that just failed.

CLI Entrypoint:
    python -m agents.media_dispatcher
"""

import argparse
import base64
import hashlib
import io
import json
import logging
import re
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image, ImageOps

from agents.settings import STUDIO_ROOT, env, env_bool

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [MEDIA-DISPATCH] %(levelname)s %(message)s",
)
log = logging.getLogger("media_dispatcher")

DEFAULT_STYLE_SUFFIX = (
    "cinematic dark futuristic tech illustration, anime cyberpunk aesthetic, "
    "vertical 9:16, 8k high quality, no text, no watermarks"
)
VISUAL_STYLE_PATH = STUDIO_ROOT / "configs" / "visual_style.json"


def active_visual_style(path: Optional[Path] = None) -> Tuple[str, str]:
    """
    (style name, prompt suffix) of the one look used for every video, from
    configs/visual_style.json (VISUAL_STYLE in .env overrides `active`). When
    the host zone is on, the suffix also asks the generator to keep the host's
    corner clear.
    """
    try:
        cfg = json.loads((path or VISUAL_STYLE_PATH).read_text(encoding="utf-8"))
    except Exception:
        return "default", DEFAULT_STYLE_SUFFIX
    presets = cfg.get("presets", {})
    name = env("VISUAL_STYLE") or cfg.get("active") or ""
    if name not in presets:
        name = cfg.get("active") if cfg.get("active") in presets else next(iter(presets), "")
    suffix = presets.get(name, {}).get("suffix") or DEFAULT_STYLE_SUFFIX
    zone = cfg.get("host_zone", {})
    if zone.get("enabled", True) and env_bool("HOST_ENABLED", True) and zone.get("composition_prompt"):
        suffix = f"{zone['composition_prompt']}, {suffix}"
    return name or "default", suffix


def work_dir(production_id: str) -> Path:
    """Scratch directory for one production; removed by the editor after QA."""
    return Path(tempfile.gettempdir()) / f"almighty_{production_id}"


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
        self.gpu_enabled = env_bool("GPU_CONTROLLER_ENABLED", False)
        self.opengenai_url = env("OPENGENAI_URL", "http://10.128.0.3:8091")
        self.hf_token = env("HF_TOKEN", "")
        self.pollinations_key = env("POLLINATIONS_API_KEY", "")
        self.pexels_key = env("PEXELS_API_KEY", "")
        self._gpu_usable: Optional[bool] = None  # None = not probed yet
        self.style_name, self.style_suffix = active_visual_style()

    def _gpu_available(self) -> bool:
        """Probes the GPU worker once per dispatcher; a dead worker costs 3s, not 20s per shot."""
        if not self.gpu_enabled:
            return False
        if self._gpu_usable is None:
            try:
                with urllib.request.urlopen(f"{self.opengenai_url.rstrip('/')}/health", timeout=3) as resp:
                    self._gpu_usable = resp.status == 200
            except Exception as e:
                log.info(f"Open-Gen-AI GPU worker not reachable ({e}); using CPU-side generators.")
                self._gpu_usable = False
        return self._gpu_usable

    def _call_opengenai_gpu(self, prompt: str) -> Optional[bytes]:
        if not self._gpu_available():
            return None
        try:
            payload = {
                "prompt": f"{prompt}, {self.style_suffix}",
                "aspect_ratio": "9:16",
                "width": 1080,
                "height": 1920,
            }
            req = urllib.request.Request(
                f"{self.opengenai_url.rstrip('/')}/generate-broll",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode())
            if data.get("status") == "success" and data.get("image_bytes"):
                return base64.b64decode(data["image_bytes"])
            log.info("Open-Gen-AI answered without image bytes; disabling it for this run.")
        except Exception as e:
            log.info(f"Open-Gen-AI call failed ({e}); disabling it for this run.")
        self._gpu_usable = False
        return None

    def _generate_flux_pollinations(self, prompt: str, seed: int) -> Optional[bytes]:
        clean_p = re.sub(r"[^\w\s]", " ", prompt)
        clean_p = re.sub(r"\s+", " ", clean_p).strip()
        words = clean_p.split()[:14]
        full_prompt = f"{' '.join(words)}, {self.style_suffix}"
        encoded_p = urllib.parse.quote(full_prompt)

        endpoints = [
            f"https://gen.pollinations.ai/image/{encoded_p}?width=1080&height=1920&model=flux&nologo=true&seed={seed}",
            f"https://image.pollinations.ai/prompt/{encoded_p}?width=1080&height=1920&model=flux&nologo=true&seed={seed}",
        ]

        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
        if self.pollinations_key:
            headers["Authorization"] = f"Bearer {self.pollinations_key}"

        backoffs = [3, 7, 15]
        for url in endpoints:
            for attempt, wait_s in enumerate(backoffs, 1):
                try:
                    req = urllib.request.Request(url, headers=headers)
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        data = resp.read()
                        if len(data) > 30_000:
                            return data
                        log.warning(f"Pollinations returned small payload ({len(data)} bytes) on attempt {attempt}")
                except Exception as e:
                    log.warning(f"Pollinations attempt {attempt} failed on {url.split('?')[0][:80]}: {e}")
                if attempt < len(backoffs):
                    time.sleep(wait_s)
        return None

    def _generate_flux_huggingface(self, prompt: str, seed: int) -> Optional[bytes]:
        if not self.hf_token:
            return None
        try:
            from huggingface_hub import InferenceClient
            client = InferenceClient(api_key=self.hf_token)
            img = client.text_to_image(
                f"{prompt}, {self.style_suffix}", model="black-forest-labs/FLUX.1-schnell", seed=seed
            )
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG")
            return buf.getvalue()
        except Exception as e:
            log.warning(f"HuggingFace FLUX fallback failed: {e}")
        return None

    def _fetch_pexels_royalty_free(self, query: str, page: int = 1) -> Optional[bytes]:
        if not self.pexels_key:
            return None
        try:
            clean_q = re.sub(r"[^\w\s]", " ", query).strip()
            encoded_q = urllib.parse.quote(" ".join(clean_q.split()[:3]) or "technology")
            url = f"https://api.pexels.com/v1/search?query={encoded_q}&orientation=portrait&per_page=1&page={page}"
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

    def dispatch_media(self, shot_cards: List[dict], production_id: str, seed_salt: int = 0) -> List[Path]:
        """
        Generates one 1080x1920 image per shot card. `seed_salt` (the QA attempt
        number) changes every seed so a retry produces different images.
        """
        scenes_dir = work_dir(production_id) / "scenes"
        scenes_dir.mkdir(parents=True, exist_ok=True)
        for stale in scenes_dir.glob("shot_*"):
            stale.unlink()
        generated_paths: List[Path] = []

        log.info(f"🎨 Generating {len(shot_cards)} 9:16 scene images for {production_id} (seed salt {seed_salt})...")

        for i, card in enumerate(shot_cards, 1):
            target_path = scenes_dir / f"shot_{i:02d}.jpg"
            vis_prompt = card.get("visual_prompt", f"Cinematic tech scene {i}")
            overlay_text = card.get("overlay_text", "")
            seed = int(hashlib.md5(f"{vis_prompt}_{i}_{seed_salt}".encode()).hexdigest()[:8], 16) % 100000

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
                image_bytes = self._generate_flux_huggingface(vis_prompt, seed)
                if image_bytes:
                    source_used = "huggingface_flux_schnell"

            # 4. Official Pexels API (royalty-free only)
            if not image_bytes and self.pexels_key:
                image_bytes = self._fetch_pexels_royalty_free(f"{overlay_text} technology", page=1 + seed_salt)
                if image_bytes:
                    source_used = "pexels_royalty_free"

            if not image_bytes:
                raise RuntimeError(
                    f"Shot {i:02d} could not be generated by any visual provider. "
                    "Refusing to emit a blank placeholder."
                )

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
                "style": self.style_name,
                "seed": seed,
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
