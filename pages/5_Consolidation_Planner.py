"""Consolidation Planner — cannibalisation clusters, winners, and redirect lists."""
import pandas as pd
import streamlit as st

from utils import router as R
from utils.ui import bifrost_sidebar, init_state, require

st.set_page_config(page_title="Consolidation Planner", page_icon="🧩", layout="wide")
init_state()
bifrost_sidebar()
ss = st.session_state

st.title("🧩 Consolidation Planner")
require("decided")
decided = ss["decided"]

con = decided[decided["action"] == R.CONSOLIDATE].copy()
if con.empty:
    st.info("No consolidation candidates — nothing is cannibalising in this inventory.")
    st.stop()

con["_authority"] = (
    pd.to_numeric(con.get("backlinks", 0), errors="coerce").fillna(0)
    + pd.to_numeric(con.get("clicks_12mo", 0), errors="coerce").fillna(0)
)
group_key = "topical_cluster" if con["topical_cluster"].notna().any() else "top_query"
if con["topical_cluster"].isna().all():
    st.caption("No LLM clusters assigned — grouping by shared top query. Run clustering on "
               "the Audit page for topic-level grouping.")
con[group_key] = con[group_key].fillna("(ungrouped)")

st.caption("Winner = highest combined backlinks + clicks. Losers redirect (301) to the winner.")
for key, grp in con.groupby(group_key):
    winner = grp.sort_values("_authority", ascending=False).iloc[0]
    with st.expander(f"**{key}** — {len(grp)} pages · winner: {winner['url']}"):
        show = grp[["url", "clicks_12mo", "backlinks", "avg_position", "_authority"]].copy()
        show["role"] = show["url"].map(lambda u: "WINNER" if u == winner["url"] else "merge → 301")
        st.dataframe(show, width='stretch')
        st.markdown(
            "**Merge notes:** fold unique sections from the losing pages into the winner, "
            "preserve the best-performing headings and internal links, then 301 the losers."
        )
