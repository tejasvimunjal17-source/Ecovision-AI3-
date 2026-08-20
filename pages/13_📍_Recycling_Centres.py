import streamlit as st
import pandas as pd
from database.db_router import fetch_all
from utils.helpers import load_css, init_session_state

st.set_page_config(page_title="Recycling Centres | EcoVision AI", page_icon="📍", layout="wide")
init_session_state()
load_css()

st.markdown('<div class="eco-hero"><h1>📍 Nearby Recycling Centres</h1><p>Find authorized recycling and e-waste collection centres near you.</p></div>', unsafe_allow_html=True)

centres = fetch_all("SELECT * FROM recycling_centres WHERE is_active=1")
if not centres:
    st.info("No recycling centres registered yet.")
    st.stop()

df = pd.DataFrame(centres)
type_filter = st.selectbox("Filter by type", ["All"] + sorted(df["type"].dropna().unique().tolist()))
filtered = df if type_filter == "All" else df[df["type"] == type_filter]

map_df = filtered.rename(columns={"latitude": "lat", "longitude": "lon"})[["lat", "lon"]].dropna()
if not map_df.empty:
    st.map(map_df, size=100)

for _, c in filtered.iterrows():
    st.markdown(
        f"""<div class="eco-card">
            <b>{c['name']}</b> · <span style="color:#34d399;">{c['type']}</span><br>
            📍 {c['address']}<br>
            📞 {c['contact']}<br>
            ♻️ Accepts: {c['materials_accepted']}
        </div>""",
        unsafe_allow_html=True,
    )
