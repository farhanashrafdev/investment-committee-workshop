"""Streamlit entry point:  streamlit run app.py"""

from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="Investment Committee · Amazon Bedrock", page_icon="🏛️", layout="wide")

from ui import architecture_page, ask_page, committee_page, security_page  # noqa: E402
from ui.common import apply_style  # noqa: E402

apply_style()
pages = [
    st.Page(committee_page.render, title="Investment Committee", icon="🏛️", default=True),
    st.Page(ask_page.render, title="Ask the Committee", icon="💬", url_path="ask"),
    st.Page(security_page.render, title="Security Lab", icon="🛡️", url_path="security"),
    st.Page(architecture_page.render, title="Architecture", icon="🧭", url_path="architecture"),
]
st.navigation(pages).run()
