#!/usr/bin/env python3
"""
services/freellm_server.py — Free-tier LLM Aggregator (port 8081)

A small in-repo aggregator (not the upstream freellmapi project) exposing an
OpenAI-compatible /v1/chat/completions over Google Gemini and Groq free tiers.

Text only. A request containing image parts is rejected with 400 rather than
answered without the images.
"""

import json
import logging
import os
import urllib.request

from fastapi import FastAPI, Request, HTTPException
import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [FREELLMAPI] %(levelname)s %(message)s",
)
log = logging.getLogger("freellmapi")

app = FastAPI(title="FreeLLMAPI Aggregator", version="2.1.0")

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GEMINI_MODEL = os.environ.get("FREELLM_GEMINI_MODEL", "") or "gemini-2.0-flash"
GROQ_MODEL = os.environ.get("FREELLM_GROQ_MODEL", "") or "openai/gpt-oss-120b"


def flatten_text(content) -> str:
    """Message content as plain text. Raises ValueError if it carries images."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                texts.append(str(item.get("text", "")))
            elif item.get("type") in ("image_url", "image", "input_image"):
                raise ValueError("this aggregator is text-only and cannot process images")
        return " ".join(texts)
    return ""


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "service": "freellmapi",
        "vision": False,
        "has_google_key": bool(GOOGLE_API_KEY),
        "has_groq_key": bool(GROQ_API_KEY),
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    temperature = float(body.get("temperature", 0.7))

    try:
        text_messages = [
            {"role": m.get("role", "user"), "content": flatten_text(m.get("content", ""))}
            for m in messages
        ]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    system_prompt = "\n\n".join(m["content"] for m in text_messages if m["role"] == "system")
    user_prompt = "\n\n".join(m["content"] for m in text_messages if m["role"] != "system")
    combined_prompt = f"{system_prompt}\n\n{user_prompt}".strip()

    # 1. Google Gemini free tier
    if GOOGLE_API_KEY:
        try:
            payload = {
                "contents": [{"parts": [{"text": combined_prompt}]}],
                "generationConfig": {"temperature": temperature, "maxOutputTokens": 2048},
            }
            if (body.get("response_format") or {}).get("type") == "json_object":
                payload["generationConfig"]["responseMimeType"] = "application/json"

            req = urllib.request.Request(
                f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json", "x-goog-api-key": GOOGLE_API_KEY},
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode())
                text_content = data["candidates"][0]["content"]["parts"][0]["text"]
                return {
                    "id": "freellm-gemini",
                    "object": "chat.completion",
                    "model": GEMINI_MODEL,
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": text_content}}],
                }
        except Exception as e:
            log.warning(f"Gemini call failed ({e}), falling back to Groq...")

    # 2. Groq free tier
    if GROQ_API_KEY:
        try:
            payload = {"model": GROQ_MODEL, "messages": text_messages, "temperature": temperature}
            if body.get("response_format"):
                payload["response_format"] = body.get("response_format")

            req = urllib.request.Request(
                "https://api.groq.com/openai/v1/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json",
                    "User-Agent": "FreeLLMAPI/2.1",
                },
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            log.warning(f"Groq call failed: {e}")

    raise HTTPException(status_code=503, detail="All free-tier LLM providers failed or unconfigured")


def main():
    port = int(os.environ.get("PORT", "8081"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
