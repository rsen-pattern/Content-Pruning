"""Shared Streamlit UI helpers: Bi Frost sidebar, provenance chips, guards.

Keeps the pages thin and the Bi Frost key-loading consistent with the
bifrost-integration skill's sidebar pattern.
"""
from __future__ import annotations

from typing import Optional

import streamlit as st

from utils.bifrost import get_client, load_models, resolve_api_key
from utils.config import PROVENANCE_COLOURS, Config


def init_state() -> None:
    ss = st.session_state
    ss.setdefault("config", _fresh_config())
    ss.setdefault("loaders", {})
    ss.setdefault("merged", None)
    ss.setdefault("signals", None)
    ss.setdefault("decided", None)
    ss.setdefault("refresh_suggestions", {})
    ss.setdefault("repurpose_suggestions", {})
    ss.setdefault("llm_overrides", {})  # url -> {action, reason, source, confidence, note}
    ss.setdefault("llm_banners", [])
    ss.setdefault("guides_loaded", False)


def _fresh_config() -> Config:
    cfg = Config()
    cfg.apply_scenario("balanced")
    return cfg


def bifrost_sidebar() -> Optional[object]:
    """Render the Bi Frost connection control; return an OpenAI client or None."""
    with st.sidebar:
        st.markdown("### Bi Frost")
        existing_key, existing_source = resolve_api_key()
        user_key = st.text_input(
            "API key",
            type="password",
            key="bifrost_api_key",  # stable key so the paste persists across pages
            help="Loaded from BIFROST_API_KEY / st.secrets if blank. Never logged.",
            placeholder="••••••" if existing_key else "paste key or set secret",
        )
        key, source = resolve_api_key(user_key)
        if not key:
            st.caption("⚪ No key — LLM steps disabled; deterministic audit still works.")
            return None
        source_label = {"input": "sidebar input", "secrets": "st.secrets", "env": "environment"}
        st.caption(f"🟢 Key loaded from **{source_label.get(source, source)}**.")
        try:
            return get_client(key)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not build Bi Frost client: {exc}")
            return None


def model_picker(label: str, default: str, key: str) -> str:
    cat = load_models()
    ids = [m["id"] for m in cat.get("models", [])]
    if default not in ids:
        ids = [default] + ids
    idx = ids.index(default) if default in ids else 0
    return st.selectbox(label, ids, index=idx, key=key)


def provenance_chip(provenance: str) -> str:
    colour = PROVENANCE_COLOURS.get(provenance, "#9e9e9e")
    return (f"<span style='background:{colour};color:white;border-radius:4px;"
            f"padding:1px 6px;font-size:0.75em'>{provenance}</span>")


def show_llm_banners() -> None:
    for msg in st.session_state.get("llm_banners", []):
        st.info(msg)


def add_banner_if_fell_back(selected: str, used: str) -> None:
    if used != selected:
        msg = f"Fell back to **{used}** — {selected} was unavailable."
        if msg not in st.session_state["llm_banners"]:
            st.session_state["llm_banners"].append(msg)


def require(*state_keys: str) -> None:
    """Stop the page with a friendly message if a prerequisite is missing."""
    missing = [k for k in state_keys if st.session_state.get(k) is None]
    if missing:
        st.warning("Complete the earlier steps first: " + ", ".join(missing))
        st.stop()
