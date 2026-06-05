"""Per-URL signal computation. Pure functions — no Streamlit, no I/O.

Takes the merged source frame, fills sensible defaults for absent data, and
derives the computed signals the router needs (is_orphan, is_stale,
has_year_in_url, is_html, cannibalisation count, expected CTR).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from utils.ctr_curves import expected_ctr

_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")

# Defaults applied when a source did not supply the column.
_ZERO_FILL = [
    "clicks_12mo", "impressions_12mo", "sessions_12mo", "conversions_12mo",
    "revenue_12mo", "non_organic_sessions_12mo", "referring_domains",
    "backlinks", "internal_links_in", "word_count",
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def has_year_in_url(url: str) -> bool:
    return bool(_YEAR_RE.search(str(url or "")))


def is_html_mime(mime: object) -> bool:
    if mime is None or (isinstance(mime, float) and pd.isna(mime)):
        return True  # assume HTML when unknown
    return "html" in str(mime).lower()


def compute_signals(
    df: pd.DataFrame,
    config,
    reference_date: Optional[datetime] = None,
) -> pd.DataFrame:
    """Return an enriched copy of df with all derived signals populated."""
    if df.empty:
        return df.copy()
    ref = reference_date or _now()
    out = df.copy()

    # Ensure every expected column exists.
    for col in _ZERO_FILL:
        if col not in out:
            out[col] = 0
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0)

    for col in ("avg_position", "ctr", "engagement_rate"):
        if col not in out:
            out[col] = pd.NA
    if "is_indexable" not in out:
        out["is_indexable"] = True
    out["is_indexable"] = out["is_indexable"].fillna(True).astype(bool)
    if "mime_type" not in out:
        out["mime_type"] = "text/html"
    if "last_modified" not in out:
        out["last_modified"] = pd.NaT
    if "status_code" not in out:
        out["status_code"] = pd.NA
    for col in ("title", "h1", "top_query"):
        if col not in out:
            out[col] = ""

    # Derived signals.
    out["is_orphan"] = out["internal_links_in"].fillna(0) == 0
    out["has_year_in_url"] = out["url"].map(has_year_in_url)
    out["is_html"] = out["mime_type"].map(is_html_mime)

    # LLM-assigned columns are filled later; ensure presence before staleness
    # (intent modulates how fast a page is considered stale).
    for col in ("topical_cluster", "intent"):
        if col not in out:
            out[col] = pd.NA

    last_mod = pd.to_datetime(out["last_modified"], errors="coerce", utc=True)
    days = (pd.Timestamp(ref) - last_mod).dt.days
    out["days_since_modified"] = days
    stale_days = config["stale_threshold_days"]
    factors = config.get("intent_stale_factors", {}) if hasattr(config, "get") else {}
    eff_stale = out["intent"].map(lambda i: stale_days * factors.get(str(i).lower(), 1.0)
                                  if i is not None and not pd.isna(i) else stale_days)
    out["effective_stale_days"] = eff_stale
    # Unknown last_modified is treated as NOT stale (avoid false refresh churn).
    out["is_stale"] = days.fillna(0) > eff_stale

    if "cannibalising_urls" not in out:
        out["cannibalising_urls"] = [[] for _ in range(len(out))]
    out["cannibalising_urls"] = out["cannibalising_urls"].apply(
        lambda v: v if isinstance(v, list) else []
    )
    out["cannibalising_count"] = out["cannibalising_urls"].map(len)

    curve = config.get("ctr_curve") if hasattr(config, "get") else None
    out["expected_ctr"] = out["avg_position"].map(
        lambda p: expected_ctr(p, curve) if pd.notna(p) else pd.NA
    )

    # Evidence score: fraction of uploaded sources that actually covered this URL.
    present_cols = [c for c in out.columns if c.startswith("present_")]
    if present_cols:
        out["evidence_score"] = out[present_cols].fillna(False).astype(bool).mean(axis=1).round(2)
    else:
        out["evidence_score"] = 1.0

    # Trend / decline (only when a previous-period GSC export was supplied).
    if "clicks_prev_12mo" in out:
        prev = pd.to_numeric(out["clicks_prev_12mo"], errors="coerce")
        cur = pd.to_numeric(out["clicks_12mo"], errors="coerce").fillna(0)
        change = (cur - prev) / prev.where(prev > 0)
        out["clicks_change_pct"] = change.round(3)
        decline_pct = config.get("trend_decline_pct", -0.2) if hasattr(config, "get") else -0.2
        out["is_declining"] = change.fillna(0) <= decline_pct
    else:
        out["clicks_change_pct"] = pd.NA
        out["is_declining"] = False
    return out


def suggest_detections(df: pd.DataFrame) -> dict:
    """Data-derived suggestions the Configuration page can surface as DETECTED
    (blue) provenance. Conservative — only what the data clearly supports."""
    suggestions: dict = {}
    if df.empty:
        return suggestions
    if "clicks_12mo" in df:
        clicks = pd.to_numeric(df["clicks_12mo"], errors="coerce").fillna(0)
        # Median of pages that get *any* clicks — a data-aware keep floor hint.
        earning = clicks[clicks > 0]
        if len(earning):
            suggestions["keep_threshold_hint"] = int(earning.median())
    return suggestions
