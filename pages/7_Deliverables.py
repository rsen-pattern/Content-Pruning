"""Deliverables — download the seven outputs; optionally compare a prior snapshot."""
import json

import pandas as pd
import streamlit as st

from utils import exporters
from utils.ui import bifrost_sidebar, init_state, require

st.set_page_config(page_title="Deliverables", page_icon="📦", layout="wide")
init_state()
bifrost_sidebar()
ss = st.session_state

st.title("📦 Deliverables")
require("decided")
decided = ss["decided"]
cfg = ss["config"]
guides_loaded = ss.get("guides_loaded", False)
refresh_sugg = ss.get("refresh_suggestions", {})
repurpose_sugg = ss.get("repurpose_suggestions", {})

summary_md = exporters.executive_summary_md(decided, cfg, guides_loaded)

files = [
    ("1 · Decision Spreadsheet", "decision_spreadsheet.xlsx",
     exporters.decision_spreadsheet_xlsx(decided),
     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ("2 · Redirect Map", "redirect_map.csv",
     exporters.redirect_map_csv(decided), "text/csv"),
    ("3 · Refresh Queue", "refresh_queue.xlsx",
     exporters.refresh_queue_xlsx(decided, refresh_sugg),
     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ("4 · Consolidation Plan", "consolidation_plan.xlsx",
     exporters.consolidation_plan_xlsx(decided),
     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ("5 · Repurpose Backlog", "repurpose_backlog.xlsx",
     exporters.repurpose_backlog_xlsx(decided, repurpose_sugg),
     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ("6 · Snapshot JSON", "snapshot.json",
     exporters.snapshot_json(decided, cfg, {"guides_loaded": guides_loaded},
                             llm_overrides=ss.get("llm_overrides", {}),
                             manual_overrides=ss.get("manual_overrides", {}),
                             assignments=exporters.assignments_from_decided(decided)),
     "application/json"),
    ("7 · Executive Summary", "executive_summary.md",
     summary_md.encode("utf-8"), "text/markdown"),
]

st.subheader("Download")
cols = st.columns(2)
for i, (label, fname, data, mime) in enumerate(files):
    cols[i % 2].download_button(label, data=data, file_name=fname, mime=mime,
                                width='stretch')

st.divider()
st.subheader("Executive summary preview")
st.markdown(summary_md)

# --- Compare a prior snapshot ---------------------------------------------
st.divider()
st.subheader("Compare with a previous audit (optional)")
prior = st.file_uploader("Upload a prior snapshot.json", type=["json"])
if prior is not None:
    try:
        data = json.load(prior)
        prev_counts = data.get("counts", {})
        now_counts = exporters.action_counts(decided)
        keys = sorted(set(prev_counts) | set(now_counts))
        rows = [{"action": k, "previous": prev_counts.get(k, 0),
                 "current": now_counts.get(k, 0),
                 "delta": now_counts.get(k, 0) - prev_counts.get(k, 0)} for k in keys]
        st.dataframe(pd.DataFrame(rows), width='stretch')
        st.caption(f"Previous snapshot created {data.get('created_at', 'unknown')}.")
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not read snapshot: {exc}")
