import streamlit as st
import pandas as pd
from backend.complaints import get_user_complaints, get_timeline
from utils.helpers import load_css, require_login, status_badge, priority_badge, format_datetime

st.set_page_config(page_title="Complaint History | EcoVision AI", page_icon="📜", layout="wide")
require_login(allowed_roles=["citizen"])
load_css()

user = st.session_state["user"]
st.markdown('<div class="eco-hero"><h1>📜 Complaint History & Tracking</h1><p>Track the status of every complaint you\'ve filed.</p></div>', unsafe_allow_html=True)

complaints = get_user_complaints(user["id"])
if not complaints:
    st.info("No complaints filed yet.")
    st.page_link("pages/4_📢_Report_Waste.py", label="Report your first waste issue", icon="📢")
    st.stop()

df = pd.DataFrame(complaints)
status_filter = st.selectbox("Filter by status", ["All"] + sorted(df["status"].unique().tolist()))
filtered = df if status_filter == "All" else df[df["status"] == status_filter]

for _, row in filtered.iterrows():
    with st.expander(f"#{row['id']} · {row['category']} · {row['status']}"):
        c1, c2 = st.columns([2, 1])
        with c1:
            st.write(f"**Description:** {row['description'] or '-'}")
            st.write(f"**Location:** {row['address_text'] or '-'} ({row['ward'] or 'N/A'})")
            st.write(f"**Filed on:** {format_datetime(row['created_at'])}")
            if row.get("image_path"):
                try:
                    st.image(row["image_path"], width=250)
                except Exception:
                    pass
        with c2:
            st.markdown(status_badge(row["status"]), unsafe_allow_html=True)
            st.markdown(priority_badge(row["priority"]), unsafe_allow_html=True)
            if row.get("assigned_worker"):
                st.caption(f"👷 Worker: {row['assigned_worker']}")

        st.markdown("**Timeline**")
        timeline = get_timeline(row["id"])
        for t in timeline:
            st.markdown(f"- `{format_datetime(t['created_at'])}` — **{t['status']}** {('· ' + t['note']) if t['note'] else ''}")
