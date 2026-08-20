"""
frontend/prakriti_widget.py
------------------------------
Prakriti AI Connect as a circular floating button, fixed bottom-right,
that expands into the chat panel on click (IBM SkillsBuild-style
interaction) instead of living only on its own dedicated page.

This file adds NO new AI/translation/history logic of its own — every
call into chatbot/prakriti.py (stream_reply, save_message, load_history,
clear_history) is exactly what pages/9_🤖_Prakriti_AI_Connect.py already
used before this change. render_chat_body() below is that same page's
chat-rendering code, extracted into a function so both the floating
widget and the full standalone page call the identical logic instead of
maintaining two copies.

Visibility: render_floating_widget() is called from
utils.helpers.load_css() only when st.session_state["user"] is set (see
that file), which is how "authenticated users only" is enforced — this
module itself has no auth logic, it's simply not invoked for a
logged-out visitor.

Positioning technique: same "wrap in st.container(key=...), then target
the resulting `st-key-<key>` class with CSS" approach already used for
the drawer toggle in frontend/custom_sidebar.py — kept consistent with
the rest of the codebase rather than introducing a new mechanism.
"""
from __future__ import annotations

import streamlit as st
from chatbot.prakriti import stream_reply, save_message, load_history, clear_history
from config import settings

_GLOW = "rgba(16,185,129,0.35)"
_GLOW_HOVER = "rgba(16,185,129,0.55)"


def render_chat_body(key_prefix: str, container_height: int = 420) -> None:
    """
    The actual Prakriti AI conversation UI: language toggle, clear
    button, message history, streaming reply. Identical behavior to the
    pre-existing pages/9 implementation — only extracted into a function
    (parameterized by key_prefix so widget keys stay unique when this
    runs inside the floating panel vs. the full standalone page) and by
    container_height (smaller inside the floating panel than on the
    full page).
    """
    user = st.session_state.get("user")
    user_id = user["id"] if user else 0
    session_id = st.session_state["chat_session_id"]

    if not settings.is_ai_configured():
        st.warning("⚠️ Running in demo mode — add a real `OPENROUTER_API_KEY` to `.env` for live AI responses.")

    top1, top2 = st.columns([2, 1])
    with top1:
        language = st.radio(
            "Language / भाषा", ["English", "हिंदी (Hindi)"], horizontal=True,
            key=f"{key_prefix}_lang", label_visibility="collapsed",
        )
    with top2:
        if st.button("🗑️ Clear", key=f"{key_prefix}_clear", use_container_width=True):
            if user:
                clear_history(user_id, session_id)
            st.session_state["chat_history"] = []
            st.rerun()

    if user and not st.session_state.get("chat_history"):
        st.session_state["chat_history"] = load_history(user_id, session_id)

    if not st.session_state["chat_history"]:
        st.session_state["chat_history"] = [{
            "role": "assistant",
            "content": "🌿 Namaste! I'm Prakriti AI Connect. Ask me about waste segregation, "
                       "recycling, composting, e-waste disposal, or MCG guidelines — in English or Hindi!"
        }]

    chat_container = st.container(height=container_height)
    with chat_container:
        for msg in st.session_state["chat_history"]:
            css_class = "chat-bubble-user" if msg["role"] == "user" else "chat-bubble-ai"
            icon = "🧑" if msg["role"] == "user" else "🌿"
            st.markdown(f'<div class="{css_class}">{icon} {msg["content"]}</div>', unsafe_allow_html=True)

    user_input = st.chat_input("Ask Prakriti AI Connect anything...", key=f"{key_prefix}_input")

    if user_input:
        st.session_state["chat_history"].append({"role": "user", "content": user_input})
        if user:
            save_message(user_id, session_id, "user", user_input)

        with chat_container:
            st.markdown(f'<div class="chat-bubble-user">🧑 {user_input}</div>', unsafe_allow_html=True)
            placeholder = st.empty()
            full_response = ""
            for chunk in stream_reply(st.session_state["chat_history"][:-1], user_input, language):
                full_response += chunk
                placeholder.markdown(f'<div class="chat-bubble-ai">🌿 {full_response}▌</div>', unsafe_allow_html=True)
            placeholder.markdown(f'<div class="chat-bubble-ai">🌿 {full_response}</div>', unsafe_allow_html=True)

        st.session_state["chat_history"].append({"role": "assistant", "content": full_response})
        if user:
            save_message(user_id, session_id, "assistant", full_response)
        st.rerun()


def render_floating_widget() -> None:
    """
    Collapsed state: a circular 🌱 button, fixed bottom-right.
    Expanded state (st.session_state['show_chat'] == True): the button
    is replaced by a floating chat panel with a header (title + ✕ close)
    containing render_chat_body(). Closing returns to the circular
    button — same st.session_state['show_chat'] flag drives both, so
    there's a single source of truth, no separate "which view" state.
    """
    st.session_state.setdefault("show_chat", False)
    is_open = st.session_state["show_chat"]

    if not is_open:
        with st.container(key="prakriti_fab"):
            if st.button("🌱", key="prakriti_fab_btn", help="Chat with Prakriti AI"):
                st.session_state["show_chat"] = True
                st.rerun()
    else:
        with st.container(key="prakriti_panel"):
            header_l, header_r = st.columns([5, 1])
            with header_l:
                st.markdown("**🌿 Prakriti AI**")
            with header_r:
                if st.button("✕", key="prakriti_close_btn", help="Minimize"):
                    st.session_state["show_chat"] = False
                    st.rerun()
            render_chat_body(key_prefix="prakriti_widget", container_height=300)

    fab_display = "none" if is_open else "flex"
    panel_display = "block" if is_open else "none"

    st.markdown(
        f"""
        <style>
        /* ---- Collapsed: circular floating button, bottom-right ---- */
        div[class*="st-key-prakriti_fab"] {{
            position: fixed;
            bottom: 22px;
            right: 22px;
            z-index: 1000000;
            display: {fab_display};
        }}
        div[class*="st-key-prakriti_fab_btn"] button {{
            width: 60px;
            height: 60px;
            border-radius: 50% !important;
            padding: 0 !important;
            font-size: 1.6rem;
            line-height: 1;
            box-shadow: 0 8px 24px {_GLOW};
            transition: transform 220ms ease, box-shadow 220ms ease;
        }}
        div[class*="st-key-prakriti_fab_btn"] button:hover {{
            transform: translateY(-3px) scale(1.06);
            box-shadow: 0 12px 30px {_GLOW_HOVER};
        }}

        /* ---- Expanded: floating chat panel, bottom-right, anchored
        where the button was. Rectangular panel is expected/fine here —
        only the COLLAPSED state must be circular (already handled
        above); this is the "chat window" the requirements explicitly
        allow to be rectangular. ---- */
        div[class*="st-key-prakriti_panel"] {{
            position: fixed;
            bottom: 22px;
            right: 22px;
            z-index: 1000000;
            display: {panel_display};
            width: 360px;
            max-width: calc(100vw - 24px);
            background: #0f172a;
            border: 1px solid var(--glass-border, rgba(255,255,255,0.14));
            border-radius: 18px;
            box-shadow: 0 16px 48px rgba(0,0,0,0.45);
            padding: 14px 14px 10px 14px;
        }}
        div[class*="st-key-prakriti_close_btn"] button {{
            padding: 0 !important;
            width: 30px;
            height: 30px;
            border-radius: 8px !important;
        }}

        @media (max-width: 640px) {{
            div[class*="st-key-prakriti_fab"] {{
                bottom: 16px;
                right: 16px;
            }}
            div[class*="st-key-prakriti_panel"] {{
                bottom: 0;
                right: 0;
                left: 0;
                width: 100vw;
                max-width: 100vw;
                border-radius: 18px 18px 0 0;
                max-height: 82vh;
            }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
