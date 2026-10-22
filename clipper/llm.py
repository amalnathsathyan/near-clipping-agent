"""Tiny JSON-only LLM client: Gemini first, Groq as fallback. No SDKs, just HTTPS."""

from __future__ import annotations

import json
import logging
import re
import time

import requests

from .config import Settings

log = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"


class LLMUnavailable(RuntimeError):
    pass


def providers(settings: Settings) -> list[str]:
    p = settings.llm_provider
    if p == "heuristic":
        return []
    order = []
    if p in ("auto", "gemini") and settings.gemini_api_key:
        order.append("gemini")
    if p in ("auto", "groq") and settings.groq_api_key:
        order.append("groq")
    return order


def complete_json(system: str, user: str, settings: Settings, max_tokens: int = 4096) -> tuple[dict, str]:
    """Returns (parsed JSON object, provider label). Raises LLMUnavailable if every provider fails."""
    errors = []
    for name in providers(settings):
        try:
            if name == "gemini":
                text = _gemini(system, user, settings, max_tokens)
                label = f"gemini:{settings.gemini_model}"
            else:
                text = _groq(system, user, settings, max_tokens)
                label = f"groq:{settings.groq_llm_model}"
            return parse_json(text), label
        except Exception as e:
            log.warning("%s failed: %s", name, e)
            errors.append(f"{name}: {e}")
    raise LLMUnavailable("; ".join(errors) or "no LLM key configured")


def parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def _post_with_retry(url: str, **kw) -> requests.Response:
    for attempt in range(4):
        r = requests.post(url, timeout=180, **kw)
        if r.status_code in (429, 500, 502, 503, 504):
            wait = float(r.headers.get("retry-after") or 2 ** (attempt + 2))
            time.sleep(min(wait, 60))
            continue
        return r
    return r


def _gemini(system: str, user: str, settings: Settings, max_tokens: int) -> str:
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.4,
                             "maxOutputTokens": max_tokens},
    }
    r = _post_with_retry(GEMINI_URL.format(model=settings.gemini_model),
                         headers={"x-goog-api-key": settings.gemini_api_key}, json=body)
    if not r.ok:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    parts = data["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def _groq(system: str, user: str, settings: Settings, max_tokens: int) -> str:
    body = {
        "model": settings.groq_llm_model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
        "temperature": 0.4,
        "max_completion_tokens": max_tokens,
    }
    r = _post_with_retry(GROQ_CHAT_URL, headers={"Authorization": f"Bearer {settings.groq_api_key}"}, json=body)
    if not r.ok:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    return r.json()["choices"][0]["message"]["content"]
