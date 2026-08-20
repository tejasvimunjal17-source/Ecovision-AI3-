import streamlit as st
from utils.helpers import load_css, require_login

st.set_page_config(page_title="Prakriti AI Connect | EcoVision AI", page_icon="🌿", layout="wide")

# Now gated like the other authenticated pages (Citizen/Officer/Admin
# Dashboard, Report Waste, etc.) — previously this page had no
# require_login() call and was reachable while logged out. Prakriti AI
# is now an authenticated-only feature (Step 3 requirement), and this
# full-page view is part of that same feature, so it gets the same gate.
require_login()

# show_prakriti=False: this page already renders the full chat panel
# below, so the floating circular button (which opens the same panel)
# would be redundant here — it still appears on every other
# authenticated page via load_css()'s default.
load_css(show_prakriti=False)

st.markdown(
    '<div class="eco-hero"><h1>🌿 Prakriti AI Connect</h1>'
    '<p>Your 24×7 bilingual AI Sustainability Assistant — ask about waste segregation, '
    'recycling, composting, e-waste, MCG guidelines, or your complaints.</p></div>',
    unsafe_allow_html=True,
)
st.caption("💬 This assistant is also available anywhere in the app via the floating 🌱 button, bottom-right.")

from frontend.prakriti_widget import render_chat_body
render_chat_body(key_prefix="prakriti_page", container_height=480)

st.markdown("---")
st.caption("Prakriti AI Connect only assists with waste, recycling, sustainability and civic topics related to this platform.")
