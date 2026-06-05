"""Deterministic decision router — the rules engine.

First match wins. The ordering below DEVIATES from the build spec in five
documented ways (see methodology.md "Router deviations"):

  1. Useful-but-unindexed (non-organic) is checked BEFORE the link-equity 301,
     so a page another channel actively lands on is never redirected away.
  2. The CTR-underperformance refresh rule gains an impressions floor
     (ctr_min_impressions) so low-impression noise is not flagged.
  3. Cannibalisation CONSOLIDATE is evaluated BEFORE the sweet-spot / CTR
     refresh rules, so we never refresh one half of a split.
  4. The opening "already-deindexed" branch no longer mislabels as KEEP: a
     noindexed page with link equity becomes DELETE_301; otherwise NO_ACTION.
  5. A thin-page catch routes thin, link-less, click-less pages deterministically
     to NOINDEX instead of letting thousands of them escalate to the LLM.

REPURPOSE is intentionally not produced here — it only emerges from LLM
judgment (call site #3). Pure functions; no Streamlit, no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlsplit

import pandas as pd

# Action vocabulary (8 spec actions + two routing/terminal states).
KEEP = "keep"
REFRESH = "refresh"
REPURPOSE = "repurpose"          # LLM-only
CONSOLIDATE = "consolidate"
SCHEDULE_UPDATE = "schedule_update"
NOINDEX = "noindex"
DELETE_301 = "delete_301"
DELETE_410 = "delete_410"
NO_ACTION = "no_action"          # already excluded; nothing to gain
AMBIGUOUS = "ambiguous"          # escalate to Bi Frost

ACTIONS = [
    KEEP, REFRESH, REPURPOSE, CONSOLIDATE, SCHEDULE_UPDATE,
    NOINDEX, DELETE_301, DELETE_410, NO_ACTION, AMBIGUOUS,
]


@dataclass
class Decision:
    action: str
    reason: str
    source: str = "rule"          # rule | llm | override
    destination_url: Optional[str] = None
    note: str = ""
    confidence: Optional[float] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "reason": self.reason,
            "source": self.source,
            "destination_url": self.destination_url,
            "note": self.note,
            "confidence": self.confidence,
        }


def _g(rec: Any, key: str, default=0):
    """Safe getter for dict or pandas Series; NaN -> default."""
    val = rec.get(key, default) if hasattr(rec, "get") else default
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return default
    return val


def _path_stem(url: str) -> str:
    path = urlsplit(str(url)).path
    if "." in path.rsplit("/", 1)[-1]:
        path = path.rsplit(".", 1)[0]
    return path.rstrip("/")


def _basename_stem(url: str) -> str:
    return _path_stem(url).rsplit("/", 1)[-1]


def find_html_equivalent(url: str, html_urls: set[str]) -> Optional[str]:
    """A non-HTML asset's HTML twin shares the path stem (e.g. /guide.pdf -> /guide).

    Prefers a full-path-stem match, then falls back to a basename match so
    /files/whitepaper.pdf can still resolve to /whitepaper."""
    stem = _path_stem(url)
    for h in html_urls:
        if h != url and _path_stem(h) == stem:
            return h
    base = _basename_stem(url)
    if base:
        for h in html_urls:
            if h != url and _basename_stem(h) == base:
                return h
    return None


# --------------------------------------------------------------------------- #
# Per-URL routing                                                              #
# --------------------------------------------------------------------------- #
_ALL_AVAILABLE = {"gsc": True, "ga4": True, "backlinks": True, "frog": True}
# Only destructive actions remove/redirect the URL and would break a conversion
# path. NOINDEX keeps the page live (email/direct still work), so it isn't guarded.
_DESTRUCTIVE_ACTIONS = {DELETE_301, DELETE_410}


def route(rec: Any, config, html_urls: Optional[set[str]] = None,
          avail: Optional[dict] = None) -> Decision:
    """Route one URL. `avail` records which sources were uploaded; traffic- and
    link-based DELETIONS are suppressed (escalated to AMBIGUOUS) when the source
    that would justify them is absent — so missing data never reads as 'zero'."""
    avail = avail or _ALL_AVAILABLE
    clicks_known = avail.get("gsc", True)     # clicks / impressions / position / ctr
    traffic_known = avail.get("ga4", True)    # sessions / non-organic / conversions / revenue
    links_known = avail.get("backlinks", True)  # referring domains / backlinks

    decision = _route_core(rec, config, html_urls, clicks_known, traffic_known, links_known)
    return _apply_guardrails(decision, rec, config, traffic_known)


def _route_core(rec, config, html_urls, clicks_known, traffic_known, links_known) -> Decision:
    clicks = _g(rec, "clicks_12mo", 0)
    impressions = _g(rec, "impressions_12mo", 0)
    position = rec.get("avg_position") if hasattr(rec, "get") else None
    ctr = rec.get("ctr") if hasattr(rec, "get") else None
    ref_domains = _g(rec, "referring_domains", 0)
    non_org = _g(rec, "non_organic_sessions_12mo", 0)
    word_count = _g(rec, "word_count", 0)
    days_old = _g(rec, "days_since_modified", 0)
    is_indexable = bool(_g(rec, "is_indexable", True))
    is_stale = bool(_g(rec, "is_stale", False))
    is_html = bool(_g(rec, "is_html", True))
    has_year = bool(_g(rec, "has_year_in_url", False))
    cannibal = _g(rec, "cannibalising_count", 0)
    expected = rec.get("expected_ctr") if hasattr(rec, "get") else None
    declining = bool(_g(rec, "is_declining", False))

    keep_t = config["keep_threshold"]
    sw_lo = config["sweet_spot_position_low"]
    sw_hi = config["sweet_spot_position_high"]
    sw_imp = config["sweet_spot_imp_threshold"]
    ctr_ratio = config["ctr_underperform_ratio"]
    ctr_min_imp = config["ctr_min_impressions"]
    non_org_t = config["non_organic_threshold"]
    age_410 = config["delete_410_age_days"]
    thin_wc = config["thin_word_count"]
    preserve = config["preserve_non_organic_as_keep"]

    # Shorthands: "no organic clicks" is only assertable when GSC was uploaded.
    no_clicks = clicks_known and clicks == 0

    # --- (4) Already excluded from index -----------------------------------
    if not is_indexable and no_clicks:
        if links_known and ref_domains >= 1:
            return Decision(DELETE_301, "already-deindexed-with-equity",
                            note="Noindex page carries link equity — redirect to preserve it.")
        return Decision(NO_ACTION, "already-deindexed",
                        note="Already excluded from index and earning nothing — leave as-is.")

    # --- Non-HTML simplified path ------------------------------------------
    if not is_html:
        if links_known and ref_domains >= 1:
            twin = find_html_equivalent(_g(rec, "url", ""), html_urls or set())
            if twin:
                return Decision(DELETE_301, "non-html-redirect-to-equivalent",
                                destination_url=twin,
                                note="Linked non-HTML asset has an HTML equivalent — redirect/canonicalise.")
            return Decision(KEEP, "non-html-linked-asset",
                            note="Non-HTML asset earns external links and has no HTML twin — keep.")
        if links_known and no_clicks and (not traffic_known or non_org <= non_org_t):
            return Decision(DELETE_410, "non-html-unused",
                            note="Non-HTML asset with no links and no traffic.")
        return Decision(KEEP, "non-html-default")

    # --- Refresh: declining earner (needs previous-period trend data) ------
    if clicks_known and clicks >= keep_t and declining:
        return Decision(REFRESH, "refresh-declining",
                        note="Still high-traffic but clicks are trending down — refresh before it slips.")

    # --- Strong keep --------------------------------------------------------
    if clicks_known and clicks >= keep_t and not is_stale:
        return Decision(KEEP, "strong-keep")

    # --- Refresh: stale but earning ----------------------------------------
    if clicks_known and clicks >= keep_t and is_stale:
        return Decision(REFRESH, "refresh-stale-earner",
                        note="High traffic but content is stale.")

    # --- (3) Consolidate: cannibalisation (moved ahead of refresh) ---------
    if clicks_known and cannibal > 0:
        return Decision(CONSOLIDATE, "cannibalisation",
                        note="Competes with sibling URL(s) for its top query — group and pick a winner.")

    # --- Refresh: sweet-spot position --------------------------------------
    if clicks_known and position is not None and pd.notna(position) and sw_lo <= position <= sw_hi and impressions >= sw_imp:
        return Decision(REFRESH, "refresh-sweet-spot",
                        note="Page-2/striking-distance position with real impressions.")

    # --- (2) Refresh: CTR underperformance (with impressions floor) --------
    if (clicks_known and position is not None and pd.notna(position) and position <= 10
            and impressions >= ctr_min_imp
            and expected is not None and pd.notna(expected)
            and (ctr if ctr is not None and pd.notna(ctr) else 0) < expected * ctr_ratio):
        return Decision(REFRESH, "refresh-title-meta",
                        note="Ranks top-10 but CTR is below the position baseline — title/meta opportunity.")

    # --- Schedule update: known obsolescence (data-independent) ------------
    if has_year:
        return Decision(SCHEDULE_UPDATE, "scheduled-obsolescence",
                        note="Dated URL — set a recurring update cadence.")

    # --- (1) Useful but unindexed (checked BEFORE the 301) -----------------
    if traffic_known and no_clicks and non_org > non_org_t:
        if preserve:
            return Decision(KEEP, "non-organic-keep",
                            note="No organic clicks but meaningful non-organic traffic — preserve visibility.")
        return Decision(NOINDEX, "non-organic-noindex",
                        note="Earns its keep off non-organic channels — noindex rather than redirect away.")

    # --- Preserve link equity ----------------------------------------------
    if links_known and no_clicks and ref_domains >= 1:
        return Decision(DELETE_301, "preserve-link-equity",
                        note="No organic value but external links — redirect to closest topical match.")

    # --- Hard delete --------------------------------------------------------
    if links_known and no_clicks and ref_domains == 0 and days_old > age_410:
        return Decision(DELETE_410, "hard-delete",
                        note="No clicks, no links, and old — safe to 410.")

    # --- (5) Thin-page catch (keeps the LLM bill sane) ---------------------
    if links_known and no_clicks and ref_domains == 0 and 0 < word_count < thin_wc:
        return Decision(NOINDEX, "thin-no-value",
                        note="Thin, link-less, click-less page — noindex deterministically.")

    # --- Escalate -----------------------------------------------------------
    return Decision(AMBIGUOUS, "needs-judgment")


def _apply_guardrails(decision: Decision, rec, config, traffic_known: bool) -> Decision:
    """Safety net: never auto-delete a page that converts or earns revenue,
    even with zero organic clicks (e.g. paid-landing / email pages)."""
    if (decision.action in _DESTRUCTIVE_ACTIONS and traffic_known
            and config.get("protect_conversions", True)):
        conv = _g(rec, "conversions_12mo", 0)
        rev = _g(rec, "revenue_12mo", 0)
        if conv >= config.get("protect_conversions_floor", 1) or rev >= config.get("protect_revenue_floor", 1.0):
            return Decision(KEEP, "protected-converter",
                            note=(f"Has {conv:g} conversions / {rev:g} revenue — protected from "
                                  f"deletion despite no organic clicks (would have been {decision.action})."))
    return decision


# --------------------------------------------------------------------------- #
# Whole-inventory pass                                                          #
# --------------------------------------------------------------------------- #
def run_router(signals_df: pd.DataFrame, config, availability: Optional[dict] = None) -> pd.DataFrame:
    """Apply route() to every URL, then resolve 301 destinations.

    `availability` maps source -> bool (gsc/ga4/backlinks/frog). When omitted,
    all sources are assumed present (back-compatible)."""
    if signals_df.empty:
        return signals_df.copy()
    html_urls = set(
        signals_df.loc[signals_df.get("is_html", True) == True, "url"]  # noqa: E712
    ) if "is_html" in signals_df else set(signals_df["url"])

    decisions = [route(row, config, html_urls, availability) for _, row in signals_df.iterrows()]
    dec_df = pd.DataFrame([d.as_dict() for d in decisions])
    out = pd.concat([signals_df.reset_index(drop=True), dec_df], axis=1)
    out = assign_redirect_targets(out)
    # Orphan remediation: a valuable page with no internal links in needs them.
    if "is_orphan" in out:
        keeper = out["action"].isin([KEEP, REFRESH, SCHEDULE_UPDATE, CONSOLIDATE])
        out["needs_internal_links"] = out["is_orphan"].fillna(False).astype(bool) & keeper
    return out


def apply_overrides(df: pd.DataFrame, overrides: dict) -> pd.DataFrame:
    """Re-apply saved LLM (or manual) decisions onto a freshly-routed frame.

    Lets the deterministic router be re-run after config changes without losing
    judgment work. `overrides` maps url -> {action, reason, source, confidence,
    note, destination_url?}."""
    if df.empty or not overrides:
        return df
    df = df.copy()
    for url, ov in overrides.items():
        m = df["url"] == url
        if not m.any():
            continue
        for field in ("action", "reason", "source", "confidence", "note", "destination_url"):
            if field in ov and ov[field] is not None:
                df.loc[m, field] = ov[field]
    return df


def assign_redirect_targets(df: pd.DataFrame) -> pd.DataFrame:
    """Fill destination_url for DELETE_301 rows that don't have one yet,
    choosing the strongest same-cluster KEEP/REFRESH page (else closest path)."""
    if df.empty or "action" not in df:
        return df
    keepers = df[df["action"].isin([KEEP, REFRESH, SCHEDULE_UPDATE])].copy()
    if "clicks_12mo" in keepers and "referring_domains" in keepers:
        keepers["_authority"] = (
            pd.to_numeric(keepers["clicks_12mo"], errors="coerce").fillna(0)
            + pd.to_numeric(keepers["referring_domains"], errors="coerce").fillna(0)
        )
    else:
        keepers["_authority"] = 0

    def best_target(row) -> Optional[str]:
        if row["action"] != DELETE_301 or row.get("destination_url"):
            return row.get("destination_url")
        cluster = row.get("topical_cluster")
        pool = keepers
        if cluster is not None and pd.notna(cluster) and "topical_cluster" in keepers:
            same = keepers[keepers["topical_cluster"] == cluster]
            if not same.empty:
                pool = same
        if pool.empty:
            return None
        return pool.sort_values("_authority", ascending=False).iloc[0]["url"]

    df = df.copy()
    df["destination_url"] = df.apply(best_target, axis=1)
    return df
