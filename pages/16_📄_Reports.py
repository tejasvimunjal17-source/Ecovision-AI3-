import streamlit as st
import pandas as pd
import io
from fpdf import FPDF
from backend.complaints import get_user_complaints, get_all_complaints
from backend import analytics
from utils.helpers import load_css, require_login

st.set_page_config(page_title="Reports | EcoVision AI", page_icon="📄", layout="wide")
require_login()
load_css()

user = st.session_state["user"]
st.markdown('<div class="eco-hero"><h1>📄 Reports</h1><p>Generate and download reports as PDF, Excel, or CSV.</p></div>', unsafe_allow_html=True)


def make_pdf(title, rows: list[dict]) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, title, ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.ln(4)
    if rows:
        cols = list(rows[0].keys())[:6]
        col_w = 190 / len(cols)
        pdf.set_font("Helvetica", "B", 8)
        for c in cols:
            pdf.cell(col_w, 8, str(c)[:18], border=1)
        pdf.ln()
        pdf.set_font("Helvetica", "", 8)
        for row in rows[:60]:
            for c in cols:
                pdf.cell(col_w, 8, str(row.get(c, ""))[:18], border=1)
            pdf.ln()
    return bytes(pdf.output())


if user["role"] == "citizen":
    st.subheader("My Complaint Report")
    data = get_user_complaints(user["id"])
else:
    st.subheader("Municipality-wide Complaint Report")
    data = get_all_complaints(limit=1000)

if not data:
    st.info("No data available yet to generate a report.")
    st.stop()

df = pd.DataFrame(data)
st.dataframe(df, use_container_width=True)

kpis = analytics.kpi_summary()
st.markdown('<div class="eco-section-title">📊 Summary KPIs</div>', unsafe_allow_html=True)
k1, k2, k3 = st.columns(3)
with k1:
    st.metric("Total Complaints", kpis["total_complaints"])
with k2:
    st.metric("Resolved", kpis["resolved"])
with k3:
    st.metric("Resolution Rate", f'{kpis["resolution_rate"]}%')

st.markdown('<div class="eco-section-title">⬇️ Download</div>', unsafe_allow_html=True)
d1, d2, d3 = st.columns(3)
with d1:
    st.download_button("📥 CSV", df.to_csv(index=False).encode(), "report.csv", "text/csv")
with d2:
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    st.download_button("📥 Excel", buf.getvalue(), "report.xlsx",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
with d3:
    pdf_bytes = make_pdf("EcoVision AI - Complaint Report", data)
    st.download_button("📥 PDF", pdf_bytes, "report.pdf", "application/pdf")
