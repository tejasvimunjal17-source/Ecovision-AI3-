import streamlit as st
from utils.helpers import load_css, init_session_state
from config import settings

st.set_page_config(page_title="About | EcoVision AI", page_icon="ℹ️", layout="wide")
init_session_state()
load_css()

st.markdown('<div class="eco-hero"><h1>ℹ️ About EcoVision AI</h1><p>AI-powered Smart City Platform for Sustainable Waste Management.</p></div>', unsafe_allow_html=True)

st.markdown(
    f"""
    <div class="eco-card">
    EcoVision AI was built to help Indian municipal corporations like {settings.MUNICIPALITY_NAME}
    modernize waste management through citizen participation and artificial intelligence. The
    platform connects citizens, sanitation officers, and administrators on one system — supporting
    <b>SDG 11 (Sustainable Cities)</b>, <b>SDG 12 (Responsible Consumption)</b>, and
    <b>SDG 13 (Climate Action)</b>.
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown('<div class="eco-section-title">📞 Contact</div>', unsafe_allow_html=True)
c1, c2, c3 = st.columns(3)
with c1:
    st.markdown(f'<div class="eco-card">📧 <b>Email</b><br>{settings.SUPPORT_EMAIL}</div>', unsafe_allow_html=True)
with c2:
    st.markdown(f'<div class="eco-card">📱 <b>Phone</b><br>{settings.SUPPORT_PHONE}</div>', unsafe_allow_html=True)
with c3:
    st.markdown(f'<div class="eco-card">🏢 <b>Municipality</b><br>{settings.MUNICIPALITY_NAME}</div>', unsafe_allow_html=True)

st.markdown('<div class="eco-section-title">✉️ Send us a message</div>', unsafe_allow_html=True)
with st.form("contact_form"):
    name = st.text_input("Name")
    email = st.text_input("Email")
    message = st.text_area("Message")
    sent = st.form_submit_button("Send", type="primary")
if sent:
    st.success("Thanks for reaching out! (This demo form doesn't send email — connect an SMTP/API service to enable delivery.)")

st.markdown(
    """
    <div class="eco-footer">
        🌿 <b>EcoVision AI</b> — Designed with ❤️ for Smart Sustainable Cities<br>
        Powered by Python · Streamlit · OpenRouter AI
    </div>
    """,
    unsafe_allow_html=True,
)
