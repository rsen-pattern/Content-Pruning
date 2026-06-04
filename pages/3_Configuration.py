"""Configuration — thresholds, scenario, models. Provenance shown grey/blue/green."""
import streamlit as st

from utils.ctr_curves import parse_custom_curve
from utils.ui import bifrost_sidebar, init_state, model_picker, provenance_chip

st.set_page_config(page_title="Configuration", page_icon="⚙️", layout="wide")
init_state()
bifrost_sidebar()
cfg = st.session_state["config"]

st.title("⚙️ Configuration")
st.caption("Every threshold has a default (grey), can be detected from data (blue), "
           "and can be overridden (green). Overrides always win over the scenario.")

# --- Scenario --------------------------------------------------------------
scenario = st.radio(
    "Scenario",
    ["conservative", "balanced", "aggressive"],
    index=["conservative", "balanced", "aggressive"].index(cfg.get("scenario", "balanced")),
    horizontal=True,
    help="Conservative deletes fewer / refreshes more; aggressive deletes more.",
)
if scenario != cfg.get("scenario"):
    cfg.apply_scenario(scenario)
    st.rerun()


def threshold(key: str, label: str, *, kind="int", step=1, minv=0.0, maxv=None, fmt=None):
    chip = provenance_chip(cfg.provenance.get(key, "defaulted"))
    st.markdown(f"**{label}** &nbsp; {chip}", unsafe_allow_html=True)
    current = cfg[key]
    if kind == "int":
        val = st.number_input(label, value=int(current), step=int(step), min_value=int(minv),
                              label_visibility="collapsed", key=f"in_{key}")
        val = int(val)
    else:
        val = st.number_input(label, value=float(current), step=float(step), min_value=float(minv),
                              max_value=maxv, format=fmt, label_visibility="collapsed", key=f"in_{key}")
    if val != current:
        cfg.override(key, val)


col1, col2 = st.columns(2)
with col1:
    threshold("keep_threshold", "Keep threshold (clicks/yr)")
    if st.session_state.get("detect_hints", {}).get("keep_threshold_hint"):
        if st.button("Use detected median ({})".format(
                st.session_state["detect_hints"]["keep_threshold_hint"])):
            cfg.detect("keep_threshold", st.session_state["detect_hints"]["keep_threshold_hint"])
            st.rerun()
    threshold("stale_threshold_days", "Stale threshold (days)")
    threshold("sweet_spot_position_low", "Sweet-spot position low")
    threshold("sweet_spot_position_high", "Sweet-spot position high")
    threshold("sweet_spot_imp_threshold", "Sweet-spot impressions floor")
with col2:
    threshold("ctr_underperform_ratio", "CTR underperform ratio", kind="float", step=0.05, maxv=1.0, fmt="%.2f")
    threshold("ctr_min_impressions", "CTR rule impressions floor")
    threshold("non_organic_threshold", "Non-organic sessions threshold")
    threshold("delete_410_age_days", "Delete-410 age (days)")
    threshold("thin_word_count", "Thin-page word count")

preserve = st.checkbox("Preserve useful-but-unindexed pages as KEEP (instead of NOINDEX)",
                       value=bool(cfg.get("preserve_non_organic_as_keep")))
if preserve != cfg.get("preserve_non_organic_as_keep"):
    cfg.override("preserve_non_organic_as_keep", preserve)

# --- CTR curve -------------------------------------------------------------
st.subheader("Industry CTR curve")
st.caption("Preset: Advanced Web Ranking 2025. Upload a custom (position, ctr) CSV to override.")
curve_file = st.file_uploader("Custom CTR curve CSV (columns: position, ctr)", type=["csv"], key="ctr_up")
if curve_file is not None:
    import pandas as pd
    try:
        cdf = pd.read_csv(curve_file)
        rows = list(zip(cdf.iloc[:, 0].astype(int), cdf.iloc[:, 1].astype(float)))
        cfg.override("ctr_curve", parse_custom_curve(rows))
        st.success(f"Custom CTR curve loaded ({len(rows)} positions).")
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not parse curve: {exc}")

# --- LLM settings ----------------------------------------------------------
st.subheader("LLM (Bi Frost)")
mode = st.radio("Use LLM judgment for",
                ["ambiguous_only", "all", "none"],
                index=["ambiguous_only", "all", "none"].index(cfg.get("llm_judgment_for")),
                horizontal=True)
if mode != cfg.get("llm_judgment_for"):
    cfg.override("llm_judgment_for", mode)

c1, c2, c3 = st.columns(3)
with c1:
    bm = model_picker("Batch model (clustering + intent)", cfg.get("batch_model"), "pick_batch")
    if bm != cfg.get("batch_model"):
        cfg.override("batch_model", bm)
with c2:
    jm = model_picker("Judgment model (ambiguous)", cfg.get("judgment_model"), "pick_judge")
    if jm != cfg.get("judgment_model"):
        cfg.override("judgment_model", jm)
with c3:
    threshold("ambiguous_batch_size", "Ambiguous URLs per LLM call")

threshold("max_llm_cost_usd", "Hard LLM cost cap (USD)", kind="float", step=0.5, fmt="%.2f")

st.divider()
st.caption("Active configuration")
st.json(cfg.values)
