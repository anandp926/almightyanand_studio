#!/usr/bin/env python3
"""
services/omniroute_server.py — LLM Router & Gateway (port 8080)

A small in-repo gateway (not the upstream OmniRoute project) exposing
OpenAI-compatible /v1/models and /v1/chat/completions:

  - Fallback chain: Google Gemini -> Groq -> FreeLLMAPI.
  - Whitespace compression of prompt text.
  - Backoff on 429 responses.
  - VISION: `image_url` parts with base64 data URLs are forwarded to Gemini as
    inline images. Requests that contain images are NEVER sent to a text-only
    provider; if Gemini cannot take them the request fails with 503, so a
    caller can never get a verdict about images the model did not see.
"""

import asyncio
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from typing import List, Optional, Tuple

from fastapi import FastAPI, Request, HTTPException
import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [OMNIROUTE] %(levelname)s %(message)s",
)
log = logging.getLogger("omniroute")

app = FastAPI(title="OmniRoute Gateway", version="2.2.0")

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
FREELLM_UPSTREAM_URL = os.environ.get("FREELLM_UPSTREAM_URL", "http://localhost:8081")
GEMINI_MODEL = os.environ.get("OMNIROUTE_GEMINI_MODEL", "") or "gemini-flash-lite-latest"
GROQ_MODEL = os.environ.get("OMNIROUTE_GROQ_MODEL", "") or "openai/gpt-oss-120b"

# If keys are not set in the environment, attempt loading from .env
if not (GOOGLE_API_KEY and GROQ_API_KEY):
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                value = value.split(" #", 1)[0].strip()
                if value.startswith("#"):
                    value = ""
                if key.strip() == "GOOGLE_API_KEY" and not GOOGLE_API_KEY:
                    GOOGLE_API_KEY = value
                elif key.strip() == "GROQ_API_KEY" and not GROQ_API_KEY:
                    GROQ_API_KEY = value

_DATA_URL = re.compile(r"^data:(?P<mime>[\w.+-]+/[\w.+-]+);base64,(?P<data>.+)$", re.DOTALL)


class UnsupportedImageError(ValueError):
    """An image part that cannot be forwarded (e.g. a remote URL instead of a data URL)."""


def compress_tokens(text: str) -> str:
    """Strips redundant whitespace."""
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_content(content) -> Tuple[str, List[dict]]:
    """
    Splits one OpenAI-style message `content` into (text, images).
    Images are returned as Gemini `inline_data` dicts. Raises
    UnsupportedImageError for image parts that are not base64 data URLs.
    """
    if isinstance(content, str):
        return compress_tokens(content), []
    if not isinstance(content, list):
        return "", []

    texts: List[str] = []
    images: List[dict] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "text":
            texts.append(str(item.get("text", "")))
        elif kind == "image_url":
            image_url = item.get("image_url")
            url = image_url.get("url", "") if isinstance(image_url, dict) else str(image_url or "")
            match = _DATA_URL.match(url)
            if not match:
                raise UnsupportedImageError("image_url must be a base64 data URL")
            images.append({"mime_type": match.group("mime"), "data": match.group("data")})
    return compress_tokens(" ".join(texts)), images


def count_images(messages: list) -> int:
    return sum(len(split_content(m.get("content", ""))[1]) for m in messages)


def build_gemini_parts(messages: list) -> List[dict]:
    """Gemini `parts`: system text first, then each message's text and its images, in order."""
    system_texts: List[str] = []
    body_parts: List[dict] = []
    for m in messages:
        text, images = split_content(m.get("content", ""))
        if m.get("role") == "system":
            if text:
                system_texts.append(text)
            continue
        if text:
            body_parts.append({"text": f"{str(m.get('role', 'user')).upper()}: {text}"})
        for image in images:
            body_parts.append({"inline_data": image})
    parts = [{"text": "\n\n".join(system_texts)}] if system_texts else []
    return parts + body_parts


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "router": "OmniRoute",
        "token_compression": True,
        "vision": bool(GOOGLE_API_KEY),
        "active_fallbacks": [GEMINI_MODEL, GROQ_MODEL, "freellm-upstream"],
        "has_google_key": bool(GOOGLE_API_KEY),
        "has_groq_key": bool(GROQ_API_KEY),
    }


@app.get("/v1/models")
def list_models():
    """OpenAI-compatible model registry for the fallback chain."""
    models = [
        {
            "id": GEMINI_MODEL,
            "object": "model",
            "created": 1700000000,
            "owned_by": "google",
            "tier": "free",
            "priority": 1,
            "vision": True,
            "status": "active" if GOOGLE_API_KEY else "unconfigured",
        },
        {
            "id": GROQ_MODEL,
            "object": "model",
            "created": 1700000000,
            "owned_by": "groq",
            "tier": "free",
            "priority": 2,
            "vision": False,
            "status": "active" if GROQ_API_KEY else "unconfigured",
        },
    ]
    return {
        "object": "list",
        "data": models,
        "active_fallback_chains": {
            "tier_1_primary": f"{GEMINI_MODEL} (Google free tier, text + vision)",
            "tier_2_secondary": f"{GROQ_MODEL} (Groq free tier, text only)",
            "tier_3_upstream": f"FreeLLMAPI ({FREELLM_UPSTREAM_URL}, text only)",
        },
        "token_compression_active": True,
    }


def _post_json(url: str, payload: dict, headers: dict, timeout: int) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


async def _call_gemini(messages: list, response_format: Optional[dict] = None, temperature: float = 0.7) -> Optional[dict]:
    """Calls the Gemini REST endpoint, forwarding text and inline images."""
    if not GOOGLE_API_KEY:
        return None

    parts = build_gemini_parts(messages)
    images_forwarded = sum(1 for p in parts if "inline_data" in p)
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": 4096},
    }
    if response_format and response_format.get("type") == "json_object":
        payload["generationConfig"]["responseMimeType"] = "application/json"

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": GOOGLE_API_KEY}

    for attempt in range(1, 4):
        try:
            data = await asyncio.to_thread(_post_json, url, payload, headers, 30)
            content = data["candidates"][0]["content"]["parts"][0]["text"]
            log.info(f"🎯 Routed via Tier 1: {GEMINI_MODEL} ({images_forwarded} images)")
            return {
                "id": f"omniroute-gemini-{int(time.time())}",
                "object": "chat.completion",
                "model": GEMINI_MODEL,
                "images_forwarded": images_forwarded,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
            }
        except urllib.error.HTTPError as he:
            if he.code == 429 and attempt < 3:
                wait_time = (2 ** attempt) * 1.5
                log.warning(f"Gemini 429 Rate Limit hit. Backing off for {wait_time:.1f}s...")
                await asyncio.sleep(wait_time)
            else:
                log.warning(f"Gemini HTTP {he.code}: {he.reason}")
                break
        except Exception as e:
            log.warning(f"Gemini call attempt {attempt} failed: {e}")
            break
    return None


async def _call_groq(messages: list, response_format: Optional[dict] = None, temperature: float = 0.7) -> Optional[dict]:
    """Calls Groq's OpenAI-compatible endpoint. Text only."""
    if not GROQ_API_KEY:
        return None

    compressed_messages = [
        {"role": m.get("role", "user"), "content": split_content(m.get("content", ""))[0]}
        for m in messages
    ]
    payload = {"model": GROQ_MODEL, "messages": compressed_messages, "temperature": temperature}
    if response_format:
        payload["response_format"] = response_format
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
        "User-Agent": "OmniRoute/2.2",
    }

    for attempt in range(1, 4):
        try:
            data = await asyncio.to_thread(
                _post_json, "https://api.groq.com/openai/v1/chat/completions", payload, headers, 30
            )
            log.info(f"🎯 Routed via Tier 2: Groq ({GROQ_MODEL})")
            return data
        except urllib.error.HTTPError as he:
            if he.code == 429 and attempt < 3:
                wait_time = (2 ** attempt) * 1.5
                log.warning(f"Groq 429 Rate Limit hit. Backing off for {wait_time:.1f}s...")
                await asyncio.sleep(wait_time)
            else:
                log.warning(f"Groq HTTP {he.code}: {he.reason}")
                break
        except Exception as e:
            log.warning(f"Groq attempt {attempt} failed: {e}")
            break
    return None


async def _call_freellm_upstream(payload: dict) -> Optional[dict]:
    """Calls the secondary FreeLLMAPI aggregator. Text only."""
    try:
        data = await asyncio.to_thread(
            _post_json,
            f"{FREELLM_UPSTREAM_URL.rstrip('/')}/v1/chat/completions",
            payload,
            {"Content-Type": "application/json"},
            30,
        )
        log.info("🎯 Routed via Tier 3: upstream FreeLLMAPI")
        return data
    except Exception as e:
        log.warning(f"FreeLLMAPI upstream call failed: {e}")
        return None


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    response_format = body.get("response_format")
    temperature = float(body.get("temperature", 0.7))

    try:
        num_images = count_images(messages)
    except UnsupportedImageError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Tier 1: Gemini (text + vision)
    res = await _call_gemini(messages, response_format, temperature)
    if res:
        return res

    if num_images:
        # The remaining tiers are text-only. Answering without the images would
        # produce a confident verdict about pictures the model never saw.
        raise HTTPException(
            status_code=503,
            detail="Vision request could not be served: Gemini is unavailable and the fallback providers are text-only.",
        )

    # Tier 2: Groq (text)
    res = await _call_groq(messages, response_format, temperature)
    if res:
        return res

    # Tier 3: Upstream FreeLLMAPI (text)
    res = await _call_freellm_upstream(body)
    if res:
        return res

    raise HTTPException(status_code=503, detail="All free-tier LLM providers in fallback chain exhausted.")


def main():
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
