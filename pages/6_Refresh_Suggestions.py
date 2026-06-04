"""Refresh Suggestions — ROI-ranked refresh queue + per-URL Bi Frost suggestions.
Also generates repurpose ideas for any LLM-flagged REPURPOSE URLs."""
import pandas as pd
import streamlit as st

from utils import llm
from utils import router as R
from utils.exporters import _roi_proxy
from utils.ui import (add_banner_if_fell_back, bifrost_sidebar, init_state,
                      require, show_llm_banners)

st.set_page_config(page_title="Refresh Suggestions", page_icon="✏️", layout="wide")
init_state()
client = bifrost_sidebar()
ss = st.session_state
cfg = ss["config"]

st.title("✏️ Refresh & Repurpose Suggestions")
require("decided")
show_llm_banners()
decided = ss["decided"]

ref = decided[decided["action"] == R.REFRESH].copy()
st.subheader(f"Refresh queue ({len(ref)} URLs)")
if ref.empty:
    st.info("No REFRESH-flagged URLs.")
else:
    ref["roi_proxy"] = ref.apply(_roi_proxy, axis=1)
    ref = ref.sort_values(["roi_proxy", "clicks_12mo"], ascending=False)
    cols = [c for c in ["url", "reason", "roi_proxy", "clicks_12mo", "avg_position",
                        "ctr", "top_query"] if c in ref.columns]
    st.dataframe(ref[cols], width='stretch')

    if client is None:
        st.info("Add a Bi Frost key to generate update suggestions.")
    elif st.button(f"Generate update suggestions for top {min(len(ref), 25)} URLs"):
        jm = cfg.get("judgment_model")
        prog = st.progress(0.0)
        top = ref.head(25)
        for i, (_, row) in enumerate(top.iterrows(), 1):
            sugg, used = llm.refresh_suggestions(client, jm, row)
            add_banner_if_fell_back(jm, used)
            ss["refresh_suggestions"][row["url"]] = sugg
            prog.progress(i / len(top))
        st.success("Suggestions generated. They are included in the Refresh Queue deliverable.")

    for url, sugg in ss.get("refresh_suggestions", {}).items():
        if sugg:
            with st.expander(url):
                for s in sugg:
                    st.markdown(f"- {s}")

# --- Repurpose -------------------------------------------------------------
rep = decided[decided["action"] == R.REPURPOSE].copy()
st.divider()
st.subheader(f"Repurpose backlog ({len(rep)} URLs)")
if rep.empty:
    st.caption("No REPURPOSE-flagged URLs. (REPURPOSE only comes from LLM judgment — "
               "run judgment on the Audit page.)")
else:
    st.dataframe(rep[[c for c in ["url", "reason", "intent", "topical_cluster", "note"]
                      if c in rep.columns]], width='stretch')
    if client is not None and st.button("Generate repurpose ideas"):
        jm = cfg.get("judgment_model")
        for _, row in rep.iterrows():
            sugg, used = llm.repurpose_suggestions(client, jm, row)
            add_banner_if_fell_back(jm, used)
            ss["repurpose_suggestions"][row["url"]] = sugg
        st.success("Repurpose ideas generated — included in the Repurpose Backlog deliverable.")
    for url, sugg in ss.get("repurpose_suggestions", {}).items():
        if sugg:
            with st.expander(url):
                for s in sugg:
                    st.markdown(f"- {s}")
