import streamlit as st
import pandas as pd
import plotly.express as px
from backend.complaints import (get_all_complaints, update_status, assign_officer,
                                 get_officers, get_wards)
from backend import analytics
from config import settings
from utils.helpers import load_css, require_login, status_badge, priority_badge, format_datetime

st.set_page_config(page_title="Officer Dashboard | EcoVision AI", page_icon="🧑‍💼", layout="wide")
require_login(allowed_roles=["officer", "admin"])
load_css()

user = st.session_state["user"]
head_l, head_r = st.columns([6, 1])
with head_l:
    st.markdown(f'<div class="eco-hero"><h1>🧑‍💼 Officer Dashboard</h1><p>Manage complaints, assign workers, and monitor ward performance.</p></div>', unsafe_allow_html=True)
with head_r:
    from frontend.notification_bell import render_notification_bell
    render_notification_bell(user)

kpis = analytics.kpi_summary()
k1, k2, k3, k4, k5 = st.columns(5)
for col, (val, label) in zip(
    [k1, k2, k3, k4, k5],
    [(kpis["total_complaints"], "Total Complaints"), (kpis["pending"], "Pending"),
     (kpis["resolved"], "Resolved"), (kpis["high_priority_open"], "High Priority Open"),
     (f'{kpis["avg_resolution_hours"]}h', "Avg Resolution Time")],
):
    with col:
        st.markdown(f'<div class="eco-stat"><div class="num">{val}</div><div class="label">{label}</div></div>', unsafe_allow_html=True)

tab_manage, tab_analytics = st.tabs(["📋 Complaint Management", "📊 Analytics"])

with tab_manage:
    fc1, fc2, fc3 = st.columns(3)
    with fc1:
        status_f = st.selectbox("Status", ["All"] + settings.COMPLAINT_STATUSES)
    with fc2:
        wards = get_wards()
        ward_f = st.selectbox("Ward", ["All"] + wards)
    with fc3:
        cat_f = st.selectbox("Category", ["All"] + settings.WASTE_CATEGORIES)

    complaints = get_all_complaints(status=status_f, ward=ward_f, category=cat_f)
    officers = get_officers()
    officer_map = {o["id"]: o["full_name"] for o in officers}

    if not complaints:
        st.info("No complaints match this filter.")
    for c in complaints:
        with st.expander(f"#{c['id']} · {c['category']} · {c['citizen_name']} · {c['status']}"):
            c1, c2 = st.columns([2, 1])
            with c1:
                st.write(f"**Description:** {c['description'] or '-'}")
                st.write(f"**Location:** {c['address_text'] or '-'} ({c['ward'] or 'N/A'})")
                st.write(f"**Filed:** {format_datetime(c['created_at'])}")
                if c.get("image_path"):
                    try:
                        st.image(c["image_path"], width=220)
                    except Exception:
                        pass
            with c2:
                st.markdown(status_badge(c["status"]), unsafe_allow_html=True)
                st.markdown(priority_badge(c["priority"]), unsafe_allow_html=True)

            st.markdown("---")
            ac1, ac2, ac3 = st.columns(3)
            with ac1:
                new_status = st.selectbox("Update status", settings.COMPLAINT_STATUSES,
                                           index=settings.COMPLAINT_STATUSES.index(c["status"]),
                                           key=f"status_{c['id']}")
                if st.button("Update", key=f"upd_{c['id']}"):
                    update_status(c["id"], new_status, user["id"], note="Updated by officer")
                    st.success("Status updated.")
                    st.rerun()
            with ac2:
                officer_choice = st.selectbox("Assign officer", ["-"] + list(officer_map.values()),
                                               key=f"officer_{c['id']}")
            with ac3:
                worker_name = st.text_input("Worker name", key=f"worker_{c['id']}")
                if st.button("Assign", key=f"assign_{c['id']}"):
                    officer_id = next((oid for oid, name in officer_map.items() if name == officer_choice), None)
                    if officer_id:
                        assign_officer(c["id"], officer_id, worker_name, changed_by=user["id"])
                        st.success("Assigned successfully.")
                        st.rerun()
                    else:
                        st.warning("Please select an officer first.")

with tab_analytics:
    cat_data = analytics.complaints_by_category()
    status_data = analytics.complaints_by_status()
    ward_data = analytics.complaints_by_ward()
    trend_data = analytics.complaints_daily_trend(30)

    r1c1, r1c2 = st.columns(2)
    with r1c1:
        if cat_data:
            df = pd.DataFrame(cat_data)
            fig = px.pie(df, names="category", values="count", title="Complaints by Category", hole=0.4)
            st.plotly_chart(fig, use_container_width=True)
    with r1c2:
        if status_data:
            df = pd.DataFrame(status_data)
            fig = px.bar(df, x="status", y="count", title="Complaints by Status", color="status")
            st.plotly_chart(fig, use_container_width=True)

    r2c1, r2c2 = st.columns(2)
    with r2c1:
        if ward_data:
            df = pd.DataFrame(ward_data)
            fig = px.bar(df, x="ward", y="count", title="Complaints by Ward", color="count")
            st.plotly_chart(fig, use_container_width=True)
    with r2c2:
        if trend_data:
            df = pd.DataFrame(trend_data)
            fig = px.line(df, x="day", y="count", title="Daily Complaint Trend (30 days)", markers=True)
            st.plotly_chart(fig, use_container_width=True)

    perf = analytics.officer_performance()
    if perf:
        st.markdown("**Officer Performance**")
        st.dataframe(pd.DataFrame(perf), use_container_width=True)
