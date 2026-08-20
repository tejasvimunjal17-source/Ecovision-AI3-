import streamlit as st
import pandas as pd
from backend.complaints import get_user_complaints, get_user_rewards
from utils.helpers import load_css, require_login, status_badge, priority_badge, format_datetime

st.set_page_config(page_title="Citizen Dashboard | EcoVision AI", page_icon="🏠", layout="wide")
require_login(allowed_roles=["citizen"])
load_css()

user = st.session_state["user"]
head_l, head_r = st.columns([6, 1])
with head_l:
    st.markdown(f'<div class="eco-hero"><h1>🏠 Welcome, {user["full_name"].split()[0]}!</h1><p>Your personal sustainability dashboard.</p></div>', unsafe_allow_html=True)
with head_r:
    from frontend.notification_bell import render_notification_bell
    render_notification_bell(user)

complaints = get_user_complaints(user["id"])
df = pd.DataFrame(complaints) if complaints else pd.DataFrame()

k1, k2, k3, k4 = st.columns(4)
with k1:
    st.markdown(f'<div class="eco-stat"><div class="num">{len(df)}</div><div class="label">Total Complaints</div></div>', unsafe_allow_html=True)
with k2:
    resolved = int((df["status"] == "Resolved").sum()) if not df.empty else 0
    st.markdown(f'<div class="eco-stat"><div class="num">{resolved}</div><div class="label">Resolved</div></div>', unsafe_allow_html=True)
with k3:
    pending = len(df) - resolved
    st.markdown(f'<div class="eco-stat"><div class="num">{pending}</div><div class="label">Pending</div></div>', unsafe_allow_html=True)
with k4:
    st.markdown(f'<div class="eco-stat"><div class="num">{user["reward_points"]}</div><div class="label">🏆 Reward Points</div></div>', unsafe_allow_html=True)

st.markdown("---")
qc1, qc2, qc3, qc4 = st.columns(4)
with qc1:
    st.page_link("pages/4_📢_Report_Waste.py", label="📢 Report Waste", use_container_width=True)
with qc2:
    st.page_link("pages/5_📜_Complaint_History.py", label="📜 Complaint History", use_container_width=True)
with qc3:
    st.page_link("pages/6_🏆_Rewards.py", label="🏆 Rewards & Leaderboard", use_container_width=True)
with qc4:
    st.page_link("pages/11_🌍_Carbon_Calculator.py", label="🌍 Carbon Calculator", use_container_width=True)

st.markdown('<div class="eco-section-title">📋 Recent Complaints</div>', unsafe_allow_html=True)
if df.empty:
    st.info("You haven't reported any waste issues yet. Click 'Report Waste' to get started!")
else:
    for _, row in df.head(5).iterrows():
        with st.container():
            c1, c2, c3 = st.columns([3, 1, 1])
            with c1:
                st.markdown(f"**#{row['id']} · {row['category']}** — {row['description'][:80] if row['description'] else 'No description'}...")
                st.caption(f"📍 {row['ward'] or 'N/A'} · 🕒 {format_datetime(row['created_at'])}")
            with c2:
                st.markdown(status_badge(row["status"]), unsafe_allow_html=True)
            with c3:
                st.markdown(priority_badge(row["priority"]), unsafe_allow_html=True)
            st.divider()
