"""
Shared AI provider layer: Groq (primary) -> Google Gemini (fallback)
=====================================================================

Used by context.py, email_sender.py and whatapp_sender.py so the provider /
model logic lives in exactly one place. Put this file in the SAME folder as
those scripts.

Both providers are called through the `openai` SDK (each exposes an
OpenAI-compatible endpoint), so the only dependencies are:
    pip install openai python-dotenv

.env variables:
    GROQ_API_KEY=...                       (primary)
    GOOGLE_API_KEY=...                     (fallback; from aistudio.google.com)

    # Optional overrides. Leave these OUT of .env unless you want to change
    # the defaults below. If you have old GROQ_MODEL / GOOGLE_MODEL / AI_MODEL
    # lines pointing at llama-3.3-70b-versatile or gemini-2.0-flash, DELETE
    # them -- both models have been shut down.
    GROQ_MODEL=openai/gpt-oss-120b
    GROQ_REASONING_EFFORT=low              (low | medium | high; gpt-oss only)
    GOOGLE_MODEL=gemini-3.5-flash-lite

Model notes (checked Oct 2026):
    - Groq retired llama-3.3-70b-versatile on Aug 16, 2026. Their recommended
      replacement is openai/gpt-oss-120b (production tier).
    - Google shut down gemini-2.0-flash / 2.0-flash-lite. gemini-3.5-flash-lite
      is the cheap, fast GA model, a good fit for short outreach messages.
    - Gemini 3+ models deprecate temperature/top_p/top_k, so no sampling
      params are sent to Google.
"""

import os
import re

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_REASONING_EFFORT = os.getenv("GROQ_REASONING_EFFORT", "low")
GOOGLE_MODEL = os.getenv("GOOGLE_MODEL", "gemini-3.5-flash-lite")

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GOOGLE_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

_clients: dict[str, OpenAI | None] = {}


def _get_client(provider: str) -> OpenAI | None:
    """Lazily build one reused client per provider. None if no key is set."""
    if provider in _clients:
        return _clients[provider]

    if provider == "groq":
        key, base_url = os.getenv("GROQ_API_KEY"), GROQ_BASE_URL
    else:
        key, base_url = os.getenv("GOOGLE_API_KEY"), GOOGLE_BASE_URL

    _clients[provider] = (
        OpenAI(api_key=key, base_url=base_url, timeout=30.0, max_retries=2)
        if key else None
    )
    return _clients[provider]


def _clean(text: str | None) -> str:
    """Strip stray reasoning tags and wrapping quotes from model output."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()
    return text.strip('"').strip("'").strip()


def _call(provider: str, prompt: str, temperature: float | None) -> str | None:
    client = _get_client(provider)
    if client is None:
        return None

    if provider == "groq":
        kwargs = {"model": GROQ_MODEL}
        if temperature is not None:
            kwargs["temperature"] = temperature
        # gpt-oss models are reasoning models; "low" keeps short outreach
        # messages fast and cheap. Sent via extra_body so it works on any
        # version of the openai SDK.
        if "gpt-oss" in GROQ_MODEL.lower() and GROQ_REASONING_EFFORT:
            kwargs["extra_body"] = {"reasoning_effort": GROQ_REASONING_EFFORT}
    else:
        # Gemini 3+: no temperature / top_p / top_k.
        kwargs = {"model": GOOGLE_MODEL}

    response = client.chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        **kwargs,
    )
    return _clean(response.choices[0].message.content) or None


def generate_text(prompt: str, temperature: float | None = None) -> tuple[str | None, str | None]:
    """
    Try Groq first, then Google Gemini.

    Returns (text, provider) where provider is "groq" or "google".
    Returns (None, None) if both fail, so the caller can use its own
    hardcoded/generic fallback. Never raises.
    """
    for provider, label in (("groq", "Groq"), ("google", "Google Gemini")):
        if _get_client(provider) is None:
            print(f"  [{label}] Skipped, no API key set in .env")
            continue
        try:
            text = _call(provider, prompt, temperature)
            if text:
                return text, provider
            print(f"  [{label}] Returned an empty response.")
        except Exception as error:
            print(f"  [{label}] Failed: {error}")

        if provider == "groq":
            print("  -> Falling back to Google Gemini...")

    return None, None


def provider_summary() -> str:
    """One-line description of the active setup, for startup banners."""
    groq = f"Groq ({GROQ_MODEL})" if os.getenv("GROQ_API_KEY") else "Groq (NO KEY)"
    google = f"Google ({GOOGLE_MODEL})" if os.getenv("GOOGLE_API_KEY") else "Google (NO KEY)"
    return f"{groq} -> {google}"