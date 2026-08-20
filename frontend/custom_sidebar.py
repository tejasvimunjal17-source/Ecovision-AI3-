"""
frontend/custom_sidebar.py
-----------------------------
A Gmail/Drive-style collapsible sidebar "drawer" for EcoVision AI.

Ported from LearnMate AI's frontend/custom_sidebar.py. Only the
mechanism (how the drawer opens/closes) was reused as-is; branding was
swapped to EcoVision AI (🌎 toggle icon, emerald glow instead of violet)
and internal widget keys were renamed from the `lm_` prefix to `eco_` so
nothing in this codebase references LearnMate naming.

This does NOT touch EcoVision's routing or backend logic in any way: it
only repositions/animates Streamlit's own native sidebar (the one
Streamlit already auto-builds from the files in pages/) via CSS. No
pages were added, removed, or reordered, and no navigation link here is
hand-built — the drawer just slides the existing, unmodified nav in and
out of view.

How it works (read before touching this file)
------------------------------------------------
Streamlit provides no public API to resize, hide, or animate its own
sidebar - so a custom collapsible sidebar necessarily has to reach it via
CSS targeting Streamlit's own DOM. This file does exactly that, and ONLY
that: no JavaScript, no click simulation, no reading/writing Streamlit's
internal JS state, no iframe.

Why `position: fixed` + `transform`, not a width animation
-------------------------------------------------------------
An earlier version of this file animated the sidebar's `width` between
0 and its normal value. That produced a partially-visible "sliver" bug:
Streamlit's actual sidebar/main layout isn't guaranteed to be sized
purely by that one CSS property (it may involve an inner content wrapper
with its own intrinsic width, or a CSS Grid track sized independently of
the section's own `width`) - so shrinking `width` alone didn't fully
match what the layout engine reserved space for.

`position: fixed` sidesteps that entirely: once an element is taken out
of the normal document flow, no grid/flexbox sizing algorithm affects it
anymore - it becomes an independent floating layer, and `transform:
translateX()` slides that whole layer (identical width at all times, so
nothing inside it "shrinks" or "clips") fully on/off screen. This is a
layout-independent technique, not dependent on which internal layout
model this particular Streamlit version uses.

Because the sidebar is no longer part of the flex/grid flow, main
content no longer reflows into its space automatically - so on desktop
this file also sets an explicit `margin-left` on the main content
container, toggled in sync with the same transition. On mobile, the
drawer instead overlays on top of the content (no margin shift), with a
tap-to-close backdrop - matching how the Gmail/Drive Android drawer
behaves.

The only Streamlit-internal selectors touched are:

    section[data-testid="stSidebar"]         - the sidebar, repositioned
                                                fixed + slid via transform
    section[data-testid="stMain"], .main     - main content, margin-left
                                                animated on desktop only
    [data-testid="collapsedControl"],
    [data-testid="stSidebarCollapseButton"]  - Streamlit's own native
                                                collapse arrow, hidden
                                                (display:none) since our
                                                🌎 button replaces it
    .block-container                         - given a small top-padding
                                                reservation so the fixed
                                                toggle button (top-left)
                                                never overlaps EcoVision's
                                                existing page content in
                                                the closed state

EcoVision's sidebar content (Streamlit's own auto-generated page nav
list, built from the files in pages/) is completely untouched - nothing
is moved, rebuilt, or hand-authored; it's the same native sidebar,
merely repositioned/animated.

State
------
st.session_state["sidebar_open"] is the single source of truth (default
True). No JavaScript state, no browser storage - a plain Python boolean,
recomputed into CSS on every rerun.
"""

from __future__ import annotations

import streamlit as st

_DRAWER_WIDTH = "21rem"
_DRAWER_WIDTH_MOBILE = "min(21rem, 85vw)"
_TRANSITION_MS = 300

# EcoVision AI brand glow (emerald, #10b981) — replaces LearnMate's violet
# (rgba(124,92,255,...)) so the toggle button matches the rest of the app's
# existing button/hover glow already defined in assets/style.css.
_GLOW = "rgba(16,185,129,0.30)"
_GLOW_HOVER = "rgba(16,185,129,0.45)"


def render_custom_sidebar_controls() -> None:
    """Render the 🌎 drawer toggle, the mobile tap-to-close backdrop, and
    apply the resulting open/closed CSS.

    Call this once, early on every page (it's wired into
    utils.helpers.load_css(), which every page already calls) - both the
    toggle button and the backdrop are independent, fixed-position
    elements, so they don't need to live inside `st.sidebar` to work, and
    nothing about EcoVision's existing sidebar content is touched.
    """
    st.session_state.setdefault("sidebar_open", True)
    is_open = st.session_state["sidebar_open"]

    # ---- Toggle button: always visible, always in the same spot. ----
    with st.container(key="eco_drawer_toggle"):
        toggle_clicked = st.button(
            "🌎", key="eco_drawer_toggle_btn", help="Open / Close Navigation"
        )

    # ---- Mobile tap-to-close backdrop: a real (always-rendered) button,
    # shown only via a CSS media query on small screens and only while the
    # drawer is open. Clicking it closes the drawer, same as tapping
    # outside a Gmail/Drive Android drawer. ----
    with st.container(key="eco_drawer_backdrop"):
        backdrop_clicked = st.button(
            "", key="eco_drawer_backdrop_btn", help="Close navigation"
        )

    if toggle_clicked or (backdrop_clicked and is_open):
        st.session_state["sidebar_open"] = not st.session_state["sidebar_open"]
        is_open = st.session_state["sidebar_open"]

    transform = "translateX(0)" if is_open else "translateX(-100%)"
    backdrop_display = "block" if is_open else "none"
    main_margin = _DRAWER_WIDTH if is_open else "0"

    st.markdown(
        f"""
        <style>
        /* ---- Fixed toggle button: always visible, always in the same
        spot, regardless of the drawer's open/closed state. ---- */
        div[class*="st-key-eco_drawer_toggle"] {{
            position: fixed;
            top: 14px;
            left: 14px;
            z-index: 1000000;
        }}
        div[class*="st-key-eco_drawer_toggle_btn"] button {{
            width: 44px;
            height: 44px;
            border-radius: 14px;
            padding: 0;
            font-size: 1.2rem;
            box-shadow: 0 6px 18px {_GLOW};
            transition: transform 280ms ease, box-shadow 280ms ease;
        }}
        div[class*="st-key-eco_drawer_toggle_btn"] button:hover {{
            transform: translateY(-2px) scale(1.05);
            box-shadow: 0 10px 24px {_GLOW_HOVER};
        }}

        /* ---- The drawer itself: taken out of document flow so no
        grid/flex sizing algorithm can partially-clip it - always full
        width, purely slid on/off screen via transform. ---- */
        section[data-testid="stSidebar"] {{
            position: fixed !important;
            top: 0 !important;
            left: 0 !important;
            height: 100vh !important;
            width: {_DRAWER_WIDTH} !important;
            min-width: {_DRAWER_WIDTH} !important;
            max-width: {_DRAWER_WIDTH} !important;
            z-index: 999998;
            overflow-y: auto !important;
            transform: {transform};
            transition: transform {_TRANSITION_MS}ms ease;
        }}
        @media (max-width: 640px) {{
            section[data-testid="stSidebar"] {{
                width: {_DRAWER_WIDTH_MOBILE} !important;
                min-width: {_DRAWER_WIDTH_MOBILE} !important;
                max-width: {_DRAWER_WIDTH_MOBILE} !important;
            }}
        }}

        /* ---- Desktop only: main content margin AND width both shift in
        sync with the drawer. THIS IS THE FIX for the horizontal
        layout-shift/right-edge-clipping bug: the previous version only
        set margin-left here, with no matching width change. Since the
        sidebar is position:fixed (out of flex flow — see module
        docstring), stMain is the sole remaining flex child of its
        parent and therefore already stretches to 100% of the viewport
        width on its own; adding margin-left on top of an already
        full-width box pushes its right edge that same distance PAST the
        viewport's right edge, which is exactly the overflow/clipping
        seen in the reported screenshots. Explicitly shrinking width by
        the same amount the content is shifted keeps the right edge
        anchored at the viewport edge instead of sliding off it, so
        content actually resizes to fill the remaining space rather than
        merely being shifted while staying full-width. On mobile the
        drawer overlays instead (no margin/width shift — see the
        backdrop below). ---- */
        @media (min-width: 641px) {{
            section[data-testid="stMain"], .main {{
                margin-left: {main_margin} !important;
                width: calc(100% - {main_margin}) !important;
                max-width: calc(100% - {main_margin}) !important;
                box-sizing: border-box !important;
                transition: margin-left {_TRANSITION_MS}ms ease, width {_TRANSITION_MS}ms ease;
            }}
        }}

        /* ---- Defensive safety net: prevent any transient horizontal
        scrollbar/1px rounding artifact during the open/close transition
        (belt-and-braces alongside the calc() fix above — does not
        change any visual sizing on its own). ---- */
        html, body, .stApp, div[data-testid="stAppViewContainer"] {{
            overflow-x: hidden;
        }}

        /* ---- Mobile tap-to-close backdrop: invisible/inert on desktop,
        a dim full-screen tap target on mobile while the drawer is open. ---- */
        div[class*="st-key-eco_drawer_backdrop"] {{
            display: none;
        }}
        @media (max-width: 640px) {{
            div[class*="st-key-eco_drawer_backdrop"] {{
                display: {backdrop_display};
                position: fixed;
                inset: 0;
                z-index: 999997;
            }}
            div[class*="st-key-eco_drawer_backdrop_btn"] button {{
                width: 100%;
                height: 100%;
                background: rgba(0,0,0,0.45) !important;
                border: none !important;
                box-shadow: none !important;
                cursor: pointer;
            }}
        }}

        /* ---- Hide the GitHub / Share / star / fork icon cluster in
        Streamlit's toolbar (top-right). Multiple selectors are targeted
        since the exact data-testid has varied across Streamlit
        versions; this is purely cosmetic (display:none) and does not
        remove any app functionality — the "⋮" settings/rerun menu
        (#MainMenu) is intentionally left untouched. ---- */
        div[data-testid="stToolbarActions"] {{
            display: none !important;
        }}
        .stAppDeployButton {{
            display: none !important;
        }}

        /* ---- Hide Streamlit's own native collapse control - fully
        replaced by our 🌎 button above. Presentational display:none only,
        not a click or a state read. ---- */
        div[data-testid="stSidebarCollapseButton"],
        div[data-testid="collapsedControl"] {{
            display: none !important;
        }}

        /* ---- Reserve top-left clearance so the fixed 🌎 toggle button
        never overlaps EcoVision's existing page content (e.g. the "🌿
        EcoVision AI" header row on the Home page) when the drawer is
        closed. This is the ONLY new spacing rule added for integration -
        every other existing style in assets/style.css is untouched. ---- */
        .block-container {{
            padding-top: 4.5rem !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
