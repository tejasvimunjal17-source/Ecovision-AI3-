"""utils/helpers.py — shared UI/session helpers used across pages."""
import streamlit as st
from pathlib import Path
from datetime import datetime

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"


def load_css(show_sidebar: bool = True, show_prakriti: bool = True):
    """
    show_sidebar=True (default, unchanged behavior for every existing
    page): loads style.css and the collapsible drawer sidebar.

    show_sidebar=False (landing page only — see app.py): loads style.css
    but skips the drawer entirely and instead fully hides Streamlit's
    native sidebar (and its collapse arrow), so a logged-out visitor sees
    no sidebar affordance at all. The default Streamlit toolbar chrome
    (GitHub/deploy button cluster) is hidden either way.

    show_prakriti=True (default): renders the Prakriti AI Connect
    floating circular button (see frontend/prakriti_widget.py) IF
    st.session_state["user"] is set. This is the single place that
    decides authenticated-only visibility — a logged-out visitor never
    has "user" set, so the widget simply never renders for them,
    regardless of which page called load_css(). Pass False to opt a
    specific page out entirely (used by the Login/Register pages, so it
    never shows even in the edge case of an already-authenticated
    session lingering on those pages, and by the standalone Prakriti AI
    Connect page, which already shows the full chat panel itself).
    """
    css_path = ASSETS_DIR / "style.css"
    if css_path.exists():
        st.markdown(f"<style>{css_path.read_text()}</style>", unsafe_allow_html=True)

    if show_sidebar:
        # Collapsible left drawer sidebar (reused/rebranded from LearnMate
        # AI — see frontend/custom_sidebar.py). This only repositions/
        # animates Streamlit's own native, auto-generated page-nav sidebar
        # via CSS; it does not add, remove, or reorder any pages/routes.
        from frontend.custom_sidebar import render_custom_sidebar_controls
        render_custom_sidebar_controls()
    else:
        st.markdown(
            """
            <style>
            /* Landing page only: no sidebar, no collapse arrow, no drawer
            toggle — logged-out visitors get a clean page with nothing
            sidebar-related to open. */
            section[data-testid="stSidebar"],
            div[data-testid="stSidebarCollapseButton"],
            div[data-testid="collapsedControl"] {
                display: none !important;
            }
            /* Same toolbar cleanup as the logged-in drawer (GitHub/Share/
            deploy icon cluster) — kept independent of the sidebar so it
            applies on the landing page too. The "⋮" settings/rerun menu
            is intentionally left untouched. */
            div[data-testid="stToolbarActions"] {
                display: none !important;
            }
            .stAppDeployButton {
                display: none !important;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )

    if show_prakriti and st.session_state.get("user"):
        from frontend.prakriti_widget import render_floating_widget
        render_floating_widget()


def init_session_state():
    # --- DB safety net ---------------------------------------------------
    # Streamlit multipage apps only execute app.py's top-level code when the
    # user lands on the Home page. If someone opens /Register or any other
    # page directly (a fresh tab, a bookmark, a shared link, Streamlit
    # Cloud's cold start, etc.), app.py's init_db() call never runs and the
    # "users" table won't exist yet -> sqlite3.OperationalError on
    # registration/login. Every page calls init_session_state() (directly
    # or via require_login()), so initializing the DB here — guarded by a
    # session-state flag so it only runs once per session — guarantees the
    # schema exists no matter which page is opened first.
    if not st.session_state.get("_db_initialized"):
        from database.db_router import init_db
        init_db()
        st.session_state["_db_initialized"] = True

    defaults = {
        "user": None,
        "theme": "dark",
        "chat_history": [],
        "chat_session_id": datetime.utcnow().strftime("%Y%m%d%H%M%S"),
        "show_chat": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def require_login(allowed_roles=None):
    """Call at the top of a protected page. Stops rendering if unauthorized."""
    init_session_state()
    if not st.session_state.get("user"):
        st.warning("🔒 Please log in to access this page.")
        st.page_link("pages/1_🔐_Login.py", label="Go to Login", icon="🔐")
        st.stop()
    if allowed_roles and st.session_state["user"]["role"] not in allowed_roles:
        st.error("⛔ You don't have permission to view this page.")
        st.stop()


def logout():
    st.session_state["user"] = None
    st.session_state["chat_history"] = []


def status_badge(status: str) -> str:
    colors = {
        "Submitted": "#64748b", "Under Review": "#f59e0b", "Assigned": "#3b82f6",
        "In Progress": "#8b5cf6", "Resolved": "#10b981", "Rejected": "#ef4444",
    }
    color = colors.get(status, "#64748b")
    return f'<span style="background:{color}22;color:{color};padding:4px 12px;border-radius:20px;font-weight:600;font-size:0.85em;border:1px solid {color}55;">{status}</span>'


def priority_badge(priority: str) -> str:
    colors = {"Low": "#10b981", "Medium": "#f59e0b", "High": "#ef4444"}
    color = colors.get(priority, "#64748b")
    return f'<span style="background:{color}22;color:{color};padding:4px 12px;border-radius:20px;font-weight:600;font-size:0.85em;border:1px solid {color}55;">{priority}</span>'


def toast(message: str, icon: str = "✅"):
    st.toast(message, icon=icon)


def format_datetime(dt_str):
    if not dt_str:
        return "-"
    try:
        dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%d %b %Y, %I:%M %p")
    except Exception:
        return dt_str
