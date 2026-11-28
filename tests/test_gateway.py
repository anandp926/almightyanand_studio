import asyncio
import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services"))

PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
VISION_MESSAGES = [{
    "role": "user",
    "content": [
        {"type": "text", "text": "Judge these frames."},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{PNG_B64}"}},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{PNG_B64}"}},
    ],
}]


@pytest.fixture
def omniroute():
    return importlib.import_module("omniroute_server")


@pytest.fixture
def freellm():
    return importlib.import_module("freellm_server")


def test_images_are_forwarded_to_gemini_as_inline_data(omniroute):
    parts = omniroute.build_gemini_parts(VISION_MESSAGES)
    images = [p["inline_data"] for p in parts if "inline_data" in p]
    assert [i["mime_type"] for i in images] == ["image/png", "image/jpeg"]
    assert images[0]["data"] == PNG_B64
    assert omniroute.count_images(VISION_MESSAGES) == 2


def test_gemini_payload_actually_contains_the_images(omniroute, monkeypatch):
    sent = {}

    def fake_post(url, payload, headers, timeout):
        sent.update(payload=payload, headers=headers, url=url)
        return {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}

    monkeypatch.setattr(omniroute, "GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(omniroute, "_post_json", fake_post)
    result = asyncio.run(omniroute._call_gemini(VISION_MESSAGES, {"type": "json_object"}))
    parts = sent["payload"]["contents"][0]["parts"]
    assert sum(1 for p in parts if "inline_data" in p) == 2
    assert result["images_forwarded"] == 2
    assert sent["headers"]["x-goog-api-key"] == "test-key" and "key=" not in sent["url"]


def test_vision_request_never_falls_back_to_text_only_providers(omniroute, monkeypatch):
    async def gemini_down(*a, **kw):
        return None

    async def must_not_be_called(*a, **kw):
        raise AssertionError("text-only provider was asked to judge images")

    monkeypatch.setattr(omniroute, "_call_gemini", gemini_down)
    monkeypatch.setattr(omniroute, "_call_groq", must_not_be_called)
    monkeypatch.setattr(omniroute, "_call_freellm_upstream", must_not_be_called)
    resp = TestClient(omniroute.app).post("/v1/chat/completions", json={"messages": VISION_MESSAGES})
    assert resp.status_code == 503


def test_text_request_still_falls_back_to_groq(omniroute, monkeypatch):
    async def gemini_down(*a, **kw):
        return None

    async def groq_ok(messages, response_format=None, temperature=0.7):
        return {"choices": [{"message": {"role": "assistant", "content": "hi"}}]}

    monkeypatch.setattr(omniroute, "_call_gemini", gemini_down)
    monkeypatch.setattr(omniroute, "_call_groq", groq_ok)
    resp = TestClient(omniroute.app).post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hello"}]})
    assert resp.status_code == 200 and resp.json()["choices"][0]["message"]["content"] == "hi"


def test_remote_image_urls_are_rejected(omniroute):
    messages = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://x.test/a.jpg"}}]}]
    resp = TestClient(omniroute.app).post("/v1/chat/completions", json={"messages": messages})
    assert resp.status_code == 400


def test_freellm_rejects_images(freellm):
    resp = TestClient(freellm.app).post("/v1/chat/completions", json={"messages": VISION_MESSAGES})
    assert resp.status_code == 400
