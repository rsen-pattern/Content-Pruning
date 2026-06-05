"""The five Bi Frost call sites, plus content fetch and a cost pre-flight.

  1. assign_clusters      — batched topical clustering (cheap model)
  2. classify_intent      — batched intent classification (cheap model)
  3. judge_ambiguous      — batched judgment for AMBIGUOUS URLs (judgment model)
  4. refresh_suggestions  — per-URL refresh recommendations
  5. repurpose_suggestions— per-URL repurpose ideas

Cost control (the chosen "batch + cache" strategy): the large static context
(reference guides) is sent once per batch as the system prompt — gateway-side
prompt caching reuses it across batches — and ambiguous URLs are grouped
(config['ambiguous_batch_size']) so the variable per-URL content is the only
thing that scales. estimate_ambiguous_cost() drives the hard pre-flight cap.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import pandas as pd
from pydantic import BaseModel, ValidationError, field_validator

from utils.bifrost import call_with_fallback, estimate_chars_as_tokens, estimate_cost_usd
from utils.prompts import load_prompt, render
from utils.router import ACTIONS, AMBIGUOUS

_REFERENCES_DIR = Path(__file__).resolve().parent.parent / "references"
_SYSTEM = "You are a precise SEO analyst. Follow the output format exactly and return only what is asked."
USER_AGENT = "ContentAuditEngine/1.0 (+https://pattern.com; SEO content audit bot)"
_VALID_ACTIONS = set(ACTIONS)


def normalize_action(raw: object) -> str:
    """Map a model's free-form action to a known action; unknown -> AMBIGUOUS."""
    act = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    return act if act in _VALID_ACTIONS else AMBIGUOUS


class JudgmentItem(BaseModel):
    """Validated shape of one ambiguous-judgment result."""
    url: str
    action: str = AMBIGUOUS
    rationale: str = ""
    confidence: float = 0.0

    @field_validator("action", mode="before")
    @classmethod
    def _norm_action(cls, v):
        return normalize_action(v)

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_conf(cls, v):
        try:
            return min(1.0, max(0.0, float(v)))
        except (TypeError, ValueError):
            return 0.0


def parse_judgment_items(parsed) -> dict[str, dict]:
    """Validate a parsed JSON array into {url: {action, rationale, confidence}}.
    Malformed items are skipped rather than crashing the run."""
    out: dict[str, dict] = {}
    if not isinstance(parsed, list):
        return out
    for item in parsed:
        if not isinstance(item, dict) or not item.get("url"):
            continue
        try:
            v = JudgmentItem(**item)
        except ValidationError:
            continue
        out[v.url] = {"action": v.action, "rationale": v.rationale, "confidence": v.confidence}
    return out


# --------------------------------------------------------------------------- #
# Reference guides (judgment context)                                          #
# --------------------------------------------------------------------------- #
def load_guides() -> str:
    """Concatenate the reference guides as judgment context.

    Excludes README.md (it's folder documentation, not a source guide). When no
    guides are present, returns a stub and the judgment rationale is flagged
    UNVERIFIED against the literature."""
    mds = sorted(p for p in _REFERENCES_DIR.glob("*.md") if p.stem.lower() != "readme")
    if not mds:
        return ("(No reference guides are loaded. Apply general SEO content-audit "
                "best practice and be conservative about deletion.)")
    return "\n\n".join(f"### {p.stem}\n{p.read_text(encoding='utf-8')}" for p in mds)


# --------------------------------------------------------------------------- #
# JSON parsing                                                                 #
# --------------------------------------------------------------------------- #
def _extract_json(text: str):
    """Pull the first JSON object/array out of a model response."""
    if not text:
        return None
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


# --------------------------------------------------------------------------- #
# Content fetch                                                                #
# --------------------------------------------------------------------------- #
_robots_cache: dict[str, Optional[RobotFileParser]] = {}


def _robots_allows(url: str, timeout: int = 5) -> bool:
    """Check robots.txt for the URL's host (cached per host). Allows on error
    so a missing/unreachable robots.txt doesn't block the audit."""
    try:
        import requests
    except Exception:
        return True
    parts = urlsplit(url)
    host = (parts.scheme or "https", parts.netloc)
    key = f"{host[0]}://{host[1]}"
    if key not in _robots_cache:
        rp = RobotFileParser()
        try:
            robots_url = urlunsplit((host[0], host[1], "/robots.txt", "", ""))
            r = requests.get(robots_url, timeout=timeout, headers={"User-Agent": USER_AGENT})
            if r.status_code >= 400:
                _robots_cache[key] = None  # no robots => allow all
            else:
                rp.parse(r.text.splitlines())
                _robots_cache[key] = rp
        except Exception:
            _robots_cache[key] = None
    rp = _robots_cache[key]
    if rp is None:
        return True
    try:
        return rp.can_fetch(USER_AGENT, url)
    except Exception:
        return True


def fetch_excerpt(url: str, max_chars: int = 3000, timeout: int = 10) -> str:
    """Best-effort visible-text excerpt for a URL. Respects robots.txt. Never raises."""
    try:
        import requests
        from bs4 import BeautifulSoup
    except Exception:
        return ""
    if not _robots_allows(url):
        return ""
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
        if resp.status_code >= 400 or "html" not in resp.headers.get("content-type", "html"):
            return ""
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = " ".join(soup.get_text(separator=" ").split())
        return text[:max_chars]
    except Exception:
        return ""


def _chunks(items: list, size: int):
    for i in range(0, len(items), max(1, size)):
        yield items[i:i + size]


# --------------------------------------------------------------------------- #
# 1 & 2: batched clustering / intent                                           #
# --------------------------------------------------------------------------- #
def _pages_block(df: pd.DataFrame, with_queries: bool) -> str:
    lines = []
    for _, r in df.iterrows():
        line = f"- {r['url']} | title: {str(r.get('title') or '')[:120]} | h1: {str(r.get('h1') or '')[:120]}"
        if with_queries and r.get("top_query"):
            line += f" | top query: {r['top_query']}"
        lines.append(line)
    return "\n".join(lines)


def assign_clusters(client, model, df: pd.DataFrame, batch_size: int = 40):
    result: dict[str, str] = {}
    used = model
    for chunk in _chunks(list(range(len(df))), batch_size):
        sub = df.iloc[chunk]
        user = render("topical_cluster", pages_block=_pages_block(sub, with_queries=True))
        text, used = call_with_fallback(client, model, _SYSTEM, user, max_tokens=1500)
        parsed = _extract_json(text) or {}
        if isinstance(parsed, dict):
            result.update({str(k): str(v) for k, v in parsed.items()})
    return result, used


def classify_intent(client, model, df: pd.DataFrame, batch_size: int = 40):
    result: dict[str, str] = {}
    used = model
    for chunk in _chunks(list(range(len(df))), batch_size):
        sub = df.iloc[chunk]
        user = render("intent_classify", pages_block=_pages_block(sub, with_queries=False))
        text, used = call_with_fallback(client, model, _SYSTEM, user, max_tokens=1500)
        parsed = _extract_json(text) or {}
        if isinstance(parsed, dict):
            result.update({str(k): str(v) for k, v in parsed.items()})
    return result, used


# --------------------------------------------------------------------------- #
# 3: batched ambiguous judgment                                                #
# --------------------------------------------------------------------------- #
_SIGNAL_COLS = ["clicks_12mo", "impressions_12mo", "avg_position", "ctr",
                "sessions_12mo", "non_organic_sessions_12mo", "referring_domains",
                "backlinks", "internal_links_in", "word_count", "days_since_modified",
                "intent", "topical_cluster"]


def _ambiguous_block(df: pd.DataFrame, fetch: bool) -> str:
    blocks = []
    for _, r in df.iterrows():
        sig = {c: (None if pd.isna(r.get(c)) else r.get(c)) for c in _SIGNAL_COLS if c in r}
        excerpt = fetch_excerpt(r["url"]) if fetch else ""
        block = (f"URL: {r['url']}\nTitle: {str(r.get('title') or '')[:160]}\n"
                 f"Signals: {json.dumps(sig, default=str)}\n"
                 f"Excerpt: {excerpt[:1500] if excerpt else '(not fetched)'}")
        blocks.append(block)
    return "\n\n---\n\n".join(blocks)


def estimate_ambiguous_cost(df: pd.DataFrame, model: str, batch_size: int,
                            guides_context: str, fetch: bool) -> dict:
    """Pre-flight estimate WITHOUT fetching (excerpts dominate variable cost;
    we approximate them by an assumed 1500-char excerpt per URL)."""
    n = len(df)
    if n == 0:
        return {"calls": 0, "input_tokens": 0, "output_tokens": 0, "usd": 0.0}
    calls = (n + batch_size - 1) // batch_size
    template = load_prompt("ambiguous_judgment")
    guides_tok = estimate_chars_as_tokens(guides_context + template)
    # Signals (~300 chars) + an excerpt only when fetching (~1500 chars).
    per_url_chars = 300 + (1500 if fetch else 15)
    per_url_tok = estimate_chars_as_tokens("x" * per_url_chars)
    input_tokens = calls * guides_tok + n * per_url_tok
    output_tokens = n * 120  # ~one short JSON object per URL
    usd = estimate_cost_usd(model, input_tokens, output_tokens)
    return {"calls": calls, "input_tokens": input_tokens,
            "output_tokens": output_tokens, "usd": round(usd, 4)}


def judge_ambiguous(client, model, df: pd.DataFrame, guides_context: str,
                    batch_size: int = 5, fetch: bool = True):
    """Return {url: {action, rationale, confidence}} and the used model."""
    out: dict[str, dict] = {}
    used = model
    for chunk in _chunks(list(range(len(df))), batch_size):
        sub = df.iloc[chunk]
        user = render("ambiguous_judgment", guides_context=guides_context,
                      pages_block=_ambiguous_block(sub, fetch))
        text, used = call_with_fallback(client, model, _SYSTEM, user, max_tokens=2000)
        out.update(parse_judgment_items(_extract_json(text)))
    return out, used


# --------------------------------------------------------------------------- #
# 4 & 5: per-URL suggestions                                                   #
# --------------------------------------------------------------------------- #
def _suggestions(client, model, prompt_name: str, row, fetch: bool):
    sig = {c: (None if pd.isna(row.get(c)) else row.get(c)) for c in _SIGNAL_COLS if c in row}
    excerpt = fetch_excerpt(row["url"]) if fetch else ""
    user = render(prompt_name, url=row["url"], title=str(row.get("title") or ""),
                  top_query=str(row.get("top_query") or ""),
                  signals=json.dumps(sig, default=str),
                  content_excerpt=excerpt[:1500] if excerpt else "(not fetched)")
    text, used = call_with_fallback(client, model, _SYSTEM, user, max_tokens=1200)
    parsed = _extract_json(text)
    suggestions = parsed if isinstance(parsed, list) else [text.strip()] if text else []
    return [str(s) for s in suggestions], used


def refresh_suggestions(client, model, row, fetch: bool = True):
    return _suggestions(client, model, "refresh_recommendations", row, fetch)


def repurpose_suggestions(client, model, row, fetch: bool = True):
    return _suggestions(client, model, "repurpose_ideas", row, fetch)
