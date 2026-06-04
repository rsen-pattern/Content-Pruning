"""Content Audit Engine — a unified per-URL decision router for SEO content audits.

Entry point. Each run stands alone (no DB); download a Snapshot JSON at the end
to compare a future audit against this one.
"""
import streamlit as st

from utils.ui import bifrost_sidebar, init_state, show_llm_banners

st.set_page_config(page_title="Content Audit Engine", page_icon="🧭", layout="wide")
init_state()
bifrost_sidebar()

st.title("🧭 Content Audit Engine")
st.markdown(
    "Route every URL to one of eight actions — **keep · refresh · repurpose · "
    "consolidate · schedule-update · noindex · delete-301 · delete-410** — from your "
    "crawl, performance, link and analytics exports. Most URLs route by deterministic "
    "rules; Bi Frost is used only for the ambiguous ones."
)
show_llm_banners()

ss = st.session_state
done = {
    "Data uploaded": ss.get("merged") is not None,
    "Audit run": ss.get("decided") is not None,
}
cols = st.columns(len(done))
for col, (label, ok) in zip(cols, done.items()):
    col.metric(label, "✓" if ok else "—")

st.markdown(
    """
### How to use it
1. **Data Upload** — drop in Screaming Frog, GSC, GA4 and Ahrefs/Semrush exports (any subset works).
2. **Configuration** — pick a scenario and tune thresholds; defaults are shown with provenance.
3. **Audit** — run the router; optionally let Bi Frost judge the ambiguous URLs.
4. **Consolidation / Refresh** — review clusters and generate update suggestions.
5. **Deliverables** — download the decision spreadsheet, redirect map, queues and snapshot.

Use the sidebar pages to move through the workflow. Nothing is persisted between
sessions — the Snapshot JSON on the Deliverables page is your record.
"""
)

if not ss.get("guides_loaded"):
    st.warning(
        "Reference guides not loaded (`references/*.md` is empty). The deterministic "
        "router is unaffected, but LLM judgment rationales and timeline citations are "
        "**unverified against the source literature**. See the Methodology page."
    )
