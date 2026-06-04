"""Bi Frost client — Pattern's unified LLM gateway (https://bifrost.pattern.com).

OpenAI-compatible. One client, one code path; provider swap is a string change.
Reconstructed from the bifrost-integration skill conventions:
  - client.chat.completions.create (NOT responses.create)
  - base_url normalised to end in /v1
  - model IDs come from config/models.json (never hard-coded inline)
  - every real call goes through call_with_fallback across providers
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from openai import OpenAI

BIFROST_BASE_URL = "https://bifrost.pattern.com"
_MODELS_PATH = Path(__file__).resolve().parent.parent / "config" / "models.json"


# --------------------------------------------------------------------------- #
# Credentials                                                                  #
# --------------------------------------------------------------------------- #
def get_api_key(user_value: Optional[str] = None) -> Optional[str]:
    """Three-tier precedence, highest first: user-entered, st.secrets, env.

    Accepts both BIFROST_API_KEY and BIFROST_KEY (older Pattern tools use the
    shorter name). Never logged.
    """
    if user_value:
        return user_value.strip()

    try:  # st.secrets only exists inside a Streamlit runtime
        import streamlit as st

        for name in ("BIFROST_API_KEY", "BIFROST_KEY"):
            if name in st.secrets:
                return str(st.secrets[name]).strip()
    except Exception:
        pass

    for name in ("BIFROST_API_KEY", "BIFROST_KEY"):
        val = os.environ.get(name)
        if val:
            return val.strip()
    return None


# --------------------------------------------------------------------------- #
# Client                                                                       #
# --------------------------------------------------------------------------- #
def get_client(api_key: str, base_url: str = BIFROST_BASE_URL) -> OpenAI:
    """Return an OpenAI client pointed at Bi Frost, base_url normalised to /v1."""
    if not base_url.rstrip("/").endswith("/v1"):
        base_url = base_url.rstrip("/") + "/v1"
    return OpenAI(api_key=api_key, base_url=base_url)


# --------------------------------------------------------------------------- #
# Model catalogue                                                              #
# --------------------------------------------------------------------------- #
def load_models(path: Path = _MODELS_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def fallback_chain_for(model: str, catalogue: Optional[dict] = None) -> list[str]:
    """Selected model first, then the configured chain (de-duplicated)."""
    cat = catalogue or load_models()
    chain = [model] + [m for m in cat.get("fallback_chain", []) if m != model]
    seen, ordered = set(), []
    for m in chain:
        if m not in seen:
            seen.add(m)
            ordered.append(m)
    return ordered


def model_meta(model: str, catalogue: Optional[dict] = None) -> dict:
    cat = catalogue or load_models()
    for m in cat.get("models", []):
        if m["id"] == model:
            return m
    return {}


# --------------------------------------------------------------------------- #
# Calls                                                                        #
# --------------------------------------------------------------------------- #
def call(
    client: OpenAI,
    model: str,
    system: str,
    user: str,
    max_tokens: int = 2000,
    temperature: float = 0.2,
) -> str:
    response = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return response.choices[0].message.content or ""


def call_with_fallback(
    client: OpenAI,
    model: str,
    system: str,
    user: str,
    max_tokens: int = 2000,
    temperature: float = 0.2,
    catalogue: Optional[dict] = None,
) -> tuple[str, str]:
    """Try the selected model, then each model in the fallback chain.

    Returns (result, used_model). Caller should surface a banner when
    used_model != the model they selected. Raises the last error if the whole
    chain fails.
    """
    last_err: Optional[Exception] = None
    for candidate in fallback_chain_for(model, catalogue):
        try:
            return call(client, candidate, system, user, max_tokens, temperature), candidate
        except Exception as exc:  # noqa: BLE001 — cascade to next provider
            last_err = exc
            continue
    raise RuntimeError(f"All Bi Frost models failed. Last error: {last_err}")


# --------------------------------------------------------------------------- #
# Cost estimation (for the pre-flight cap on the Audit page)                   #
# --------------------------------------------------------------------------- #
def estimate_chars_as_tokens(text: str) -> int:
    """Rough token estimate — ~4 chars/token. Good enough for a budget guard."""
    return max(1, len(text) // 4)


def estimate_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    catalogue: Optional[dict] = None,
) -> float:
    meta = model_meta(model, catalogue)
    in_rate = meta.get("usd_per_1k_input", 0.003)
    out_rate = meta.get("usd_per_1k_output", 0.015)
    return (input_tokens / 1000) * in_rate + (output_tokens / 1000) * out_rate
