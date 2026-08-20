import streamlit as st
from backend.auth import login_user, get_security_question, reset_password
from utils.helpers import load_css, init_session_state, toast

st.set_page_config(page_title="Login | EcoVision AI", page_icon="🔐", layout="centered")
init_session_state()
load_css(show_prakriti=False)

st.markdown('<div class="eco-hero"><h1>🔐 Welcome Back</h1><p>Log in to report waste, track complaints, and chat with Prakriti AI.</p></div>', unsafe_allow_html=True)

if st.session_state.get("user"):
    st.success(f"You're already logged in as {st.session_state['user']['full_name']}.")
    st.page_link("app.py", label="Go to Home", icon="🏠")
    st.stop()

tab_login, tab_forgot = st.tabs(["Login", "Forgot Password"])

with tab_login:
    with st.form("login_form"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login", type="primary", use_container_width=True)

    if submitted:
        if not email or not password:
            st.error("Please enter both email and password.")
        else:
            ok, result = login_user(email, password)
            if ok:
                st.session_state["user"] = result
                toast(f"Welcome back, {result['full_name']}!")
                st.success("Login successful! Redirecting...")
                target = {
                    "citizen": "pages/3_🏠_Citizen_Dashboard.py",
                    "officer": "pages/7_🧑‍💼_Officer_Dashboard.py",
                    "admin": "pages/8_🛠️_Admin_Dashboard.py",
                }[result["role"]]
                st.switch_page(target)
            else:
                st.error(result)

    st.info("**Demo admin account:** `admin@ecovision.local` / `Admin@12345` (change after first login).")
    st.markdown("Don't have an account?")
    st.page_link("pages/2_📝_Register.py", label="Register here", icon="📝")

with tab_forgot:
    st.write("Reset your password using your registered email and security answer.")
    fp_email = st.text_input("Registered Email", key="fp_email")
    if fp_email:
        q = get_security_question(fp_email)
        if q:
            st.write(f"**Security question:** {q}")
            with st.form("reset_form"):
                answer = st.text_input("Your Answer")
                new_pw = st.text_input("New Password", type="password")
                confirm_pw = st.text_input("Confirm New Password", type="password")
                reset_submit = st.form_submit_button("Reset Password", use_container_width=True)
            if reset_submit:
                if new_pw != confirm_pw:
                    st.error("Passwords do not match.")
                else:
                    ok, msg = reset_password(fp_email, answer, new_pw)
                    if ok:
                        st.success(msg)
                    else:
                        st.error(msg)
        elif fp_email:
            st.warning("No security question found for this email. Please contact support.")
