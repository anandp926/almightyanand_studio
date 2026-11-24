#!/usr/bin/env python3
"""
services/freellm_server.py — FreeLLMAPI Aggregator Service

Exposes an OpenAI-compatible /v1/chat/completions endpoint aggregating
free-tier LLMs (Google Gemini free tier, Groq free tier).
Runs on port 8081.
"""

import json
import logging
import os
import urllib.parse
import urllib.request
from typing import Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [FREELLMAPI] %(levelname)s %(message)s",
)
log = logging.getLogger("freellmapi")

app = FastAPI(title="FreeLLMAPI Aggregator", version="2.0.0")

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "service": "freellmapi",
        "has_google_key": bool(GOOGLE_API_KEY),
        "has_groq_key": bool(GROQ_API_KEY),
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    model = body.get("model", "auto")

    # Extract prompt
    user_prompt = ""
    system_prompt = ""
    for m in messages:
        if m.get("role") == "system":
            system_prompt = m.get("content", "")
        elif m.get("role") == "user":
            user_prompt = m.get("content", "")

    combined_prompt = f"{system_prompt}\n\n{user_prompt}".strip()

    # 1. Try Google Gemini free tier
    if GOOGLE_API_KEY:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GOOGLE_API_KEY}"
            payload = {
                "contents": [{"parts": [{"text": combined_prompt}]}],
                "generationConfig": {"temperature": 0.7, "maxOutputTokens": 2048},
            }
            if body.get("response_format", {}).get("type") == "json_object":
                payload["generationConfig"]["responseMimeType"] = "application/json"

            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode())
                text_content = data["candidates"][0]["content"]["parts"][0]["text"]
                return {
                    "id": "freellm-gemini",
                    "object": "chat.completion",
                    "model": "gemini-2.0-flash",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": text_content}}],
                }
        except Exception as e:
            log.warning(f"FreeLLMAPI Gemini call failed ({e}), falling back to Groq...")

    # 2. Try Groq free tier
    if GROQ_API_KEY:
        try:
            url = "https://api.groq.com/openai/v1/chat/completions"
            payload = {
                "model": "openai/gpt-oss-120b",
                "messages": messages,
                "temperature": 0.7,
            }
            if body.get("response_format"):
                payload["response_format"] = body.get("response_format")

            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode())
                return data
        except Exception as e:
            log.warning(f"FreeLLMAPI Groq call failed: {e}")

    raise HTTPException(status_code=503, detail="All free-tier LLM providers failed or unconfigured")


def main():
    port = int(os.environ.get("PORT", "8081"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
