"""Prompt loader — prompts are data, not code.

Each prompt lives in prompts/<name>.txt with {placeholders}. Load it, then
.format(**kwargs). Iterating on prompts needs no deploy and shows up cleanly
in diffs.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


@lru_cache(maxsize=None)
def _read(name: str) -> str:
    path = _PROMPTS_DIR / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"Prompt not found: {path}")
    return path.read_text(encoding="utf-8")


def load_prompt(name: str) -> str:
    """Return the raw template text for prompts/<name>.txt."""
    return _read(name)


def render(name: str, **kwargs) -> str:
    """Load prompts/<name>.txt and substitute {placeholders}."""
    return _read(name).format(**kwargs)
