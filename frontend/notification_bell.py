"""
frontend/notification_bell.py
--------------------------------
🔔 notification bell for the authenticated dashboards (Step 4). Uses
Streamlit's built-in st.popover for the dropdown panel — no custom
fixed-position CSS overlay needed here (unlike the Prakriti floating
button, which has to float above arbitrary page content); a popover
already renders as a clean anchored dropdown under its trigger.

Visibility/authorization: render_notification_bell(user) is only ever
called with an already-authenticated `user` dict (see the three
dashboard pages), and every query it triggers goes through
backend.notifications, which scopes every read/write to that exact
user_id — see that module's docstring for the authorization note.
"""
import streamlit as st
from backend import notifications as notif_service

_TYPE_ICON = {
    "general": "🔔",
    "complaint_update": "📋",
    "announcement": "📣",
    "reward": "🏆",
    "account": "👤",
}


def render_notification_bell(user: dict, key_prefix: str = "notif") -> None:
    user_id = user["id"]
    unread = notif_service.get_unread_count(user_id)
    label = f"🔔 {unread}" if unread else "🔔"

    with st.popover(label, use_container_width=False):
        head_l, head_r = st.columns([2, 1])
        with head_l:
            st.markdown("**Notifications**")
        with head_r:
            if unread and st.button("✓ Mark all read", key=f"{key_prefix}_mark_all"):
                notif_service.mark_all_read(user_id)
                st.rerun()

        items = notif_service.get_user_notifications(user_id, limit=20)
        if not items:
            st.caption("You're all caught up — no notifications yet.")
            return

        for n in items:
            is_unread = n["read_at"] is None
            icon = _TYPE_ICON.get(n["type"], "🔔")
            row_l, row_r = st.columns([5, 1])
            with row_l:
                title_md = f"**{icon} {n['title']}**" if is_unread else f"{icon} {n['title']}"
                st.markdown(title_md)
                st.caption(n["message"])
            with row_r:
                if is_unread:
                    if st.button("Read", key=f"{key_prefix}_read_{n['id']}"):
                        notif_service.mark_read(n["id"], user_id)
                        st.rerun()
            st.divider()
