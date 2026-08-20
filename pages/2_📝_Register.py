import streamlit as st
from backend.auth import register_user, validate_password_strength
from utils.validators import is_valid_email, is_valid_indian_phone
from utils.helpers import load_css, init_session_state

st.set_page_config(page_title="Register | EcoVision AI", page_icon="📝", layout="centered")
init_session_state()
load_css(show_prakriti=False)

st.markdown('<div class="eco-hero"><h1>📝 Create Your Account</h1><p>Join thousands of citizens making Indian cities cleaner and greener.</p></div>', unsafe_allow_html=True)

if st.session_state.get("user"):
    st.success("You're already logged in.")
    st.page_link("app.py", label="Go to Home", icon="🏠")
    st.stop()

with st.form("register_form"):
    role = st.selectbox("Register as", ["citizen", "officer"], format_func=lambda r: r.title(),
                         help="Admin accounts are created by existing administrators only.")
    c1, c2 = st.columns(2)
    with c1:
        full_name = st.text_input("Full Name *")
        email = st.text_input("Email *")
        phone = st.text_input("Phone Number *", placeholder="9876543210")
    with c2:
        ward = st.text_input("Ward / Sector", placeholder="e.g. Sector 45")
        address = st.text_input("Address")

    c3, c4 = st.columns(2)
    with c3:
        password = st.text_input("Password *", type="password")
    with c4:
        confirm = st.text_input("Confirm Password *", type="password")

    st.markdown("**Security Question** (used for password recovery)")
    security_question = st.selectbox("Choose a question", [
        "What is your favorite city?",
        "What was your first pet's name?",
        "What is your mother's maiden name?",
    ])
    security_answer = st.text_input("Your Answer *")

    agree = st.checkbox("I agree to the Terms of Service and Privacy Policy")
    submitted = st.form_submit_button("🚀 Create Account", type="primary", use_container_width=True)

if submitted:
    errors = []
    if not full_name.strip():
        errors.append("Full name is required.")
    if not is_valid_email(email):
        errors.append("Please enter a valid email address.")
    if not is_valid_indian_phone(phone):
        errors.append("Please enter a valid 10-digit Indian mobile number.")
    if password != confirm:
        errors.append("Passwords do not match.")
    else:
        ok, msg = validate_password_strength(password)
        if not ok:
            errors.append(msg)
    if not security_answer.strip():
        errors.append("Please answer the security question.")
    if not agree:
        errors.append("You must agree to the Terms of Service.")

    if errors:
        for e in errors:
            st.error(e)
    else:
        ok, result = register_user(
            full_name, email, phone, password, ward=ward, address=address,
            role=role, security_question=security_question, security_answer=security_answer,
        )
        if ok:
            st.success("🎉 Account created successfully! Please log in.")
            st.page_link("pages/1_🔐_Login.py", label="Go to Login", icon="🔐")
            st.balloons()
        else:
            st.error(result)

st.markdown("Already have an account?")
st.page_link("pages/1_🔐_Login.py", label="Login here", icon="🔐")
