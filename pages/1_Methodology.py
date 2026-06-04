"""Methodology — render methodology.md plus a live view of the active rule order."""
from pathlib import Path

import streamlit as st

from utils.ui import bifrost_sidebar, init_state

st.set_page_config(page_title="Methodology", page_icon="📖", layout="wide")
init_state()
bifrost_sidebar()

st.title("📖 Methodology")

md = Path(__file__).resolve().parent.parent / "methodology.md"
if md.exists():
    st.markdown(md.read_text(encoding="utf-8"))
else:
    st.info("methodology.md not found.")
