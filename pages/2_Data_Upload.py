"""Data Upload — parse the four exports with graceful degradation."""
import io

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

if results:
    merged = loaders.merge_sources(
        results.get("frog"), results.get("gsc"), results.get("ga4"), results.get("backlinks")
    )
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

    st.dataframe(merged.head(50), width='stretch')

    # Surface data-derived suggestions for the Configuration page.
    hints = suggest_detections(merged)
    if hints.get("keep_threshold_hint"):
        st.info(f"💡 Median clicks among earning pages ≈ {hints['keep_threshold_hint']}. "
                "Consider this when setting the keep threshold on the Configuration page.")
        ss["detect_hints"] = hints
