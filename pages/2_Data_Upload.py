"""Data Upload — parse the four exports with graceful degradation."""
import io

import pandas as pd
import streamlit as st

from utils import loaders
from utils.config import DEFAULTS
from utils.signals import suggest_detections
from utils.ui import bifrost_sidebar, init_state


@st.cache_data(show_spinner=False)
def _cached_load(_loader, data: bytes, filename: str):
    """Parse cached by file content + name, so reruns don't re-parse big CSVs.
    `_loader` is underscore-prefixed so Streamlit skips hashing the function."""
    return _loader(io.BytesIO(data), filename)

st.set_page_config(page_title="Data Upload", page_icon="📤", layout="wide")
init_state()
bifrost_sidebar()
ss = st.session_state

st.title("📤 Data Upload")
st.caption("Any subset works — missing data degrades gracefully (warnings, not crashes). "
           "You can also load the bundled samples to try the tool.")

# --------------------------------------------------------------------------- #
# Export-window selector                                                        #
# --------------------------------------------------------------------------- #
st.subheader("Export window")
st.caption(
    "Match this to the date range of your traffic exports. "
    "Thresholds that are measured per year (clicks, sessions, stale age) "
    "are auto-scaled so decisions stay calibrated when you have less than 12 months of data."
)

col_w, col_info = st.columns([1, 3])
with col_w:
    export_window = st.number_input(
        "Months covered by your GSC / GA4 exports",
        min_value=1, max_value=24, value=int(ss.get("export_window_months", 12)), step=1,
        label_visibility="collapsed",
    )

_SCALE_KEYS = ("keep_threshold", "non_organic_threshold", "stale_threshold_days",
               "delete_410_age_days")

if export_window != ss.get("export_window_months", 12):
    ss["export_window_months"] = export_window
    cfg = ss["config"]
    factor = export_window / 12.0
    for k in _SCALE_KEYS:
        cfg.detect(k, max(1, round(DEFAULTS[k] * factor)))

with col_info:
    if export_window == 12:
        st.info("12 months — using standard annual thresholds.", icon="📅")
    else:
        cfg = ss["config"]
        factor = export_window / 12.0
        scaled = {k: max(1, round(DEFAULTS[k] * factor)) for k in _SCALE_KEYS}
        st.info(
            f"**{export_window}-month window detected.** "
            f"Keep threshold → {scaled['keep_threshold']} clicks "
            f"(from {DEFAULTS['keep_threshold']}), "
            f"non-organic threshold → {scaled['non_organic_threshold']} sessions, "
            f"stale age → {scaled['stale_threshold_days']} days, "
            f"delete-410 age → {scaled['delete_410_age_days']} days. "
            "Shown as **detected** (blue) on the Configuration page — you can still override.",
            icon="⚡",
        )

# --------------------------------------------------------------------------- #
# File uploaders                                                                #
# --------------------------------------------------------------------------- #
LOADERS = {
    "Screaming Frog": {
        "key": "frog",
        "fn": loaders.load_screaming_frog,
        "sample": "samples/screaming_frog_sample.csv",
        "help": (
            "**Fresh crawl at audit time** — ideally within the last 7 days so the crawl "
            "reflects your live site. Use *All* crawl mode in Screaming Frog. "
            "Enable 'List mode' if you want to crawl a specific URL set."
        ),
    },
    "Google Search Console": {
        "key": "gsc",
        "fn": loaders.load_gsc,
        "sample": "samples/gsc_sample.csv",
        "help": (
            "**Trailing 12 months of organic data** (or your chosen export window above). "
            "In GSC: Performance → Pages, set date to *Last 12 months*, exclude the "
            "last 2–3 days (GSC has a 2–3 day data lag). "
            "Export all pages — the default 1,000-row UI cap misses most of your site; "
            "use the API, Looker Studio, or a third-party connector for the full export."
        ),
    },
    "GA4": {
        "key": "ga4",
        "fn": loaders.load_ga4,
        "sample": "samples/ga4_sample.csv",
        "help": (
            "**Same trailing 12 months, aligned with your GSC export.** "
            "Export from *Pages and screens* with the *Session default channel group* "
            "dimension so the tool can separate organic from non-organic sessions. "
            "Include sessions, conversions, and revenue. "
            "Tip: set GA4 data retention to **14 months** (Admin → Data Retention → "
            "Event data retention) to avoid gaps on your next audit."
        ),
    },
    "Ahrefs / Semrush backlinks": {
        "key": "backlinks",
        "fn": loaders.load_backlinks,
        "sample": "samples/backlinks_sample.csv",
        "help": (
            "**Current snapshot — point-in-time, not a trend.** "
            "The audit uses referring domains as a quality signal to protect pages with "
            "external links from being deleted. "
            "Ahrefs: Site Explorer → Referring Domains export. "
            "Semrush: Backlink Analytics → Referring Domains."
        ),
    },
}

use_samples = st.toggle("Use bundled sample exports", value=False)

results = {}
for title, spec in LOADERS.items():
    key, fn, sample_path = spec["key"], spec["fn"], spec["sample"]
    st.subheader(title)
    st.caption(spec["help"])
    res = None
    if use_samples:
        try:
            with open(sample_path, "rb") as fh:
                res = _cached_load(fn, fh.read(), sample_path)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Sample load failed: {exc}")
    else:
        up = st.file_uploader(f"{title} export (CSV/XLSX)", type=["csv", "xlsx", "xls"],
                               key=f"up_{key}")
        if up is not None:
            try:
                res = _cached_load(fn, up.getvalue(), up.name)
            except Exception as exc:  # noqa: BLE001
                st.error(f"Could not parse: {exc}")
    if res is not None:
        results[key] = res
        c1, c2 = st.columns([1, 2])
        c1.metric("Rows", res.rows)
        c2.write("**Columns found:** " + (", ".join(res.found.keys()) or "none"))
        for w in res.warnings:
            st.warning(w)

# --------------------------------------------------------------------------- #
# Previous-period GSC for trend / decline detection                            #
# --------------------------------------------------------------------------- #
st.subheader("Optional: trend / decline")
st.caption(
    "Upload a **previous-period GSC export** of the same length as your main export "
    "(e.g. if your main export is the last 6 months, use the 6 months before that). "
    "This enables the router's decline-detection rule: high-traffic pages trending "
    "down route to REFRESH instead of KEEP."
)
gsc_prev_file = None if use_samples else st.file_uploader(
    "GSC — previous period (same shape as your GSC export)",
    type=["csv", "xlsx", "xls"], key="up_gsc_prev")

if results:
    merged = loaders.merge_sources(
        results.get("frog"), results.get("gsc"), results.get("ga4"), results.get("backlinks")
    )
    if gsc_prev_file is not None:
        try:
            prev_res = _cached_load(loaders.load_gsc, gsc_prev_file.getvalue(), gsc_prev_file.name)
            merged = loaders.attach_previous_clicks(merged, prev_res)
            st.caption("📉 Previous-period GSC attached — declining pages will be flagged for refresh.")
        except Exception as exc:  # noqa: BLE001
            st.warning(f"Could not attach previous-period GSC: {exc}")
    ss["loaders"] = results
    ss["merged"] = merged
    ss["signals"] = None
    ss["decided"] = None
    # Which sources are present drives the router's data-availability guards.
    ss["availability"] = {k: (results.get(k) is not None and results[k].ok)
                          for k in ("gsc", "ga4", "backlinks", "frog")}
    st.success(f"Merged inventory: **{len(merged)} URLs**.")

    # --- Coverage & join diagnostics --------------------------------------
    st.subheader("Coverage & join diagnostics")
    avail = ss["availability"]
    missing = [k.upper() for k, v in avail.items() if not v]
    if missing:
        st.warning(
            f"Not uploaded: **{', '.join(missing)}**. The audit will *not* infer deletions "
            "from signals it can't see — affected URLs are escalated for review instead of "
            "being treated as zero-traffic."
        )
    report = loaders.join_report(results, merged)
    st.dataframe(report, width='stretch')
    low = report[report["match_rate"].str.rstrip("%").apply(lambda x: x.isdigit() and int(x) < 60)]
    if not low.empty:
        st.error(
            "Low join rate on: " + ", ".join(low["source"]) +
            ". URLs likely don't match across exports (protocol, www, trailing slash, "
            "or path-only). Check those exports' URL format."
        )

    # Per-signal coverage: % of the inventory that actually has each signal.
    signal_cols = [c for c in ["clicks_12mo", "impressions_12mo", "avg_position",
                               "sessions_12mo", "non_organic_sessions_12mo", "conversions_12mo",
                               "revenue_12mo", "referring_domains", "word_count", "last_modified"]
                   if c in merged.columns]
    if signal_cols and len(merged):
        cov = pd.DataFrame({
            "signal": signal_cols,
            "coverage": [f"{100.0 * merged[c].notna().mean():.0f}%" for c in signal_cols],
        })
        st.caption("Signal coverage across the merged inventory:")
        st.dataframe(cov, width='stretch')

    st.dataframe(merged.head(50), width='stretch')

    # Surface data-derived suggestions for the Configuration page.
    hints = suggest_detections(merged)
    if hints.get("keep_threshold_hint"):
        st.info(f"💡 Median clicks among earning pages ≈ {hints['keep_threshold_hint']}. "
                "Consider this when setting the keep threshold on the Configuration page.")
        ss["detect_hints"] = hints
