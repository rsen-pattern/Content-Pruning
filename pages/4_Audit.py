"""Audit — run loaders→signals→router, then optionally enrich with Bi Frost."""
import pandas as pd
import streamlit as st

from utils import llm, router, signals
from utils.ui import (add_banner_if_fell_back, bifrost_sidebar, init_state,
                      require, show_llm_banners)

st.set_page_config(page_title="Audit", page_icon="🧭", layout="wide")
init_state()
client = bifrost_sidebar()
ss = st.session_state
cfg = ss["config"]

st.title("🧭 Audit")
require("merged")
show_llm_banners()


def run_deterministic():
    sig = signals.compute_signals(ss["merged"], cfg)
    ss["signals"] = sig
    ss["decided"] = router.run_router(sig, cfg)


if ss.get("decided") is None:
    run_deterministic()

if st.button("↻ Re-run deterministic audit (after config changes)"):
    run_deterministic()
    st.success("Router re-run with current configuration.")

decided = ss["decided"]

# --- Counts ---------------------------------------------------------------
counts = decided["action"].value_counts().to_dict()
st.subheader("Decisions")
metric_cols = st.columns(len(router.ACTIONS))
for col, action in zip(metric_cols, router.ACTIONS):
    col.metric(action.replace("_", "-"), int(counts.get(action, 0)))

# --- Bi Frost enrichment --------------------------------------------------
st.subheader("Bi Frost enrichment")
mode = cfg.get("llm_judgment_for")
if client is None:
    st.info("Add a Bi Frost key in the sidebar to enable clustering, intent and judgment.")
elif mode == "none":
    st.info("LLM judgment is set to **none** on the Configuration page.")
else:
    c1, c2 = st.columns(2)

    with c1:
        st.markdown("**Clustering + intent** (batched, cheap model)")
        if st.button("Assign topical clusters & intent"):
            with st.spinner("Calling Bi Frost…"):
                bm = cfg.get("batch_model")
                clusters, used_c = llm.assign_clusters(client, bm, ss["signals"])
                intents, used_i = llm.classify_intent(client, bm, ss["signals"])
                add_banner_if_fell_back(bm, used_c)
                add_banner_if_fell_back(bm, used_i)
                ss["signals"]["topical_cluster"] = ss["signals"]["url"].map(clusters)
                ss["signals"]["intent"] = ss["signals"]["url"].map(intents)
                ss["decided"] = router.run_router(ss["signals"], cfg)
            st.success(f"Clusters + intent assigned to {len(clusters)} URLs. Router re-run.")
            st.rerun()

    with c2:
        st.markdown("**Judgment** for ambiguous URLs")
        target = decided if mode == "all" else decided[decided["action"] == router.AMBIGUOUS]
        guides = llm.load_guides()
        ss["guides_loaded"] = "(No reference guides" not in guides
        fetch = st.checkbox("Fetch page content for judgment", value=True,
                            help="Slower but higher quality. Non-HTML URLs are skipped.")
        est = llm.estimate_ambiguous_cost(target, cfg.get("judgment_model"),
                                          cfg.get("ambiguous_batch_size"), guides, fetch)
        cap = cfg.get("max_llm_cost_usd")
        st.caption(f"{len(target)} URLs · {est['calls']} calls · "
                   f"~{est['input_tokens']:,} in / {est['output_tokens']:,} out tokens · "
                   f"**est. ${est['usd']:.3f}** (cap ${cap:.2f})")
        over = est["usd"] > cap
        if over:
            st.error("Estimated cost exceeds the cap — raise it on the Configuration page or reduce scope.")
        if st.button("Run Bi Frost judgment", disabled=over or target.empty):
            with st.spinner(f"Judging {len(target)} URLs…"):
                jm = cfg.get("judgment_model")
                results, used = llm.judge_ambiguous(
                    client, jm, target, guides,
                    batch_size=cfg.get("ambiguous_batch_size"), fetch=fetch)
                add_banner_if_fell_back(jm, used)
                d = ss["decided"]
                for url, res in results.items():
                    m = d["url"] == url
                    d.loc[m, "action"] = res["action"]
                    d.loc[m, "reason"] = "llm-judgment"
                    d.loc[m, "source"] = "llm"
                    d.loc[m, "confidence"] = res["confidence"]
                    d.loc[m, "note"] = res["rationale"]
                ss["decided"] = router.assign_redirect_targets(d)
            st.success(f"Judged {len(results)} URLs.")
            st.rerun()

# --- Table ----------------------------------------------------------------
st.subheader("All URLs")
action_filter = st.multiselect("Filter by action", router.ACTIONS, default=[])
view = decided if not action_filter else decided[decided["action"].isin(action_filter)]
cols = [c for c in ["url", "action", "reason", "source", "confidence", "destination_url",
                    "clicks_12mo", "impressions_12mo", "avg_position", "referring_domains",
                    "topical_cluster", "intent", "note"] if c in view.columns]
st.dataframe(view[cols], width='stretch', height=480)
