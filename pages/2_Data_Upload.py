"""Data Upload — parse the four exports with graceful degradation."""
import io

import pandas as pd
import streamlit as st

from utils import loaders
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

LOADERS = {
    "Screaming Frog": ("frog", loaders.load_screaming_frog, "samples/screaming_frog_sample.csv"),
    "Google Search Console": ("gsc", loaders.load_gsc, "samples/gsc_sample.csv"),
    "GA4": ("ga4", loaders.load_ga4, "samples/ga4_sample.csv"),
    "Ahrefs / Semrush backlinks": ("backlinks", loaders.load_backlinks, "samples/backlinks_sample.csv"),
}

use_samples = st.toggle("Use bundled sample exports", value=False)

results = {}
for title, (key, fn, sample_path) in LOADERS.items():
    st.subheader(title)
    res = None
    if use_samples:
        try:
            with open(sample_path, "rb") as fh:
                res = _cached_load(fn, fh.read(), sample_path)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Sample load failed: {exc}")
    else:
        up = st.file_uploader(f"{title} export (CSV/XLSX)", type=["csv", "xlsx", "xls"], key=f"up_{key}")
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

st.subheader("Optional: trend / decline")
gsc_prev_file = None if use_samples else st.file_uploader(
    "GSC — previous period (same shape as your GSC export). Enables decline detection.",
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
