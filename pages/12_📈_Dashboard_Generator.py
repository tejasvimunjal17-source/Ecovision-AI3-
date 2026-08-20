import streamlit as st
import pandas as pd
import plotly.express as px
import io
from utils.ai_client import chat_completion
from utils.helpers import load_css, init_session_state
from config import settings

st.set_page_config(page_title="Dashboard Generator | EcoVision AI", page_icon="📈", layout="wide")
init_session_state()
load_css()

st.markdown('<div class="eco-hero"><h1>📈 Smart Analytics Dashboard Generator</h1><p>Upload any CSV or Excel file — get instant KPIs, auto-recommended charts, and AI insights.</p></div>', unsafe_allow_html=True)

MAX_UPLOAD_MB = 10
ALLOWED_EXTENSIONS = (".csv", ".xlsx", ".xls")

# NOTE: Streamlit's built-in `type=` filter relies on browser-reported MIME
# types, which are inconsistently reported for .xlsx across OS/browsers and
# was incorrectly rejecting valid Excel files on Streamlit Cloud. We accept
# any file here and validate the extension + size ourselves, so we can show
# a clear, accurate error message instead of a silent/generic rejection.
file = st.file_uploader("Upload a CSV or Excel file", type=None,
                         help=f"Accepted: CSV, XLSX, XLS — up to {MAX_UPLOAD_MB}MB")

if not file:
    st.info("👆 Upload a dataset to get started. Try any municipal, sales, or survey dataset.")
    st.stop()

file_ext = "." + file.name.rsplit(".", 1)[-1].lower() if "." in file.name else ""
file_size_mb = len(file.getvalue()) / (1024 * 1024)

if file_ext not in ALLOWED_EXTENSIONS:
    st.error(f"❌ Unsupported file type '{file_ext or 'unknown'}'. Please upload a CSV (.csv) or Excel (.xlsx / .xls) file.")
    st.stop()

if file_size_mb > MAX_UPLOAD_MB:
    st.error(f"❌ File is {file_size_mb:.1f}MB, which exceeds the {MAX_UPLOAD_MB}MB limit. Please upload a smaller file.")
    st.stop()

try:
    df = pd.read_csv(file) if file_ext == ".csv" else pd.read_excel(file)
except Exception as e:
    st.error(f"Could not read file: {e}")
    st.stop()

is_excel_input = file_ext in (".xlsx", ".xls")
original_sheet_name = "Data"

st.markdown('<div class="eco-section-title">🔍 Data Overview</div>', unsafe_allow_html=True)
k1, k2, k3, k4 = st.columns(4)
with k1:
    st.markdown(f'<div class="eco-stat"><div class="num">{df.shape[0]}</div><div class="label">Rows</div></div>', unsafe_allow_html=True)
with k2:
    st.markdown(f'<div class="eco-stat"><div class="num">{df.shape[1]}</div><div class="label">Columns</div></div>', unsafe_allow_html=True)
with k3:
    missing = int(df.isnull().sum().sum())
    st.markdown(f'<div class="eco-stat"><div class="num">{missing}</div><div class="label">Missing Values</div></div>', unsafe_allow_html=True)
with k4:
    dupes = int(df.duplicated().sum())
    st.markdown(f'<div class="eco-stat"><div class="num">{dupes}</div><div class="label">Duplicate Rows</div></div>', unsafe_allow_html=True)

with st.expander("📋 Column Types & Preview"):
    dtype_df = pd.DataFrame({"Column": df.columns, "Type": [str(t) for t in df.dtypes],
                              "Missing": df.isnull().sum().values})
    st.dataframe(dtype_df, use_container_width=True)
    st.dataframe(df.head(20), use_container_width=True)

numeric_cols = df.select_dtypes(include="number").columns.tolist()
categorical_cols = df.select_dtypes(include=["object", "category"]).columns.tolist()
datetime_cols = [c for c in df.columns if "date" in c.lower() or "time" in c.lower()]

st.markdown('<div class="eco-section-title">🎛️ Filters</div>', unsafe_allow_html=True)
filtered_df = df.copy()
if categorical_cols:
    filt_col = st.selectbox("Filter by column (optional)", ["None"] + categorical_cols)
    if filt_col != "None":
        options = ["All"] + sorted(df[filt_col].dropna().astype(str).unique().tolist())
        choice = st.selectbox(f"Value for {filt_col}", options)
        if choice != "All":
            filtered_df = filtered_df[filtered_df[filt_col].astype(str) == choice]

st.markdown('<div class="eco-section-title">📊 Auto-Generated Charts</div>', unsafe_allow_html=True)

charts_made = 0
chart_meta = []  # collects {title, type, x, y} for each generated chart -> used in Dashboard export sheet

if categorical_cols and numeric_cols:
    c1, c2 = st.columns(2)
    with c1:
        cat = categorical_cols[0]
        agg = filtered_df.groupby(cat)[numeric_cols[0]].sum().reset_index().head(15)
        fig = px.bar(agg, x=cat, y=numeric_cols[0], title=f"{numeric_cols[0]} by {cat}", color=cat)
        st.plotly_chart(fig, use_container_width=True)
        charts_made += 1
        chart_meta.append({"Chart Title": f"{numeric_cols[0]} by {cat}", "Type": "Bar", "X": cat, "Y": numeric_cols[0]})
    with c2:
        fig = px.pie(agg, names=cat, values=numeric_cols[0], title=f"{cat} Distribution", hole=0.4)
        st.plotly_chart(fig, use_container_width=True)
        charts_made += 1
        chart_meta.append({"Chart Title": f"{cat} Distribution", "Type": "Pie", "X": cat, "Y": numeric_cols[0]})

if len(numeric_cols) >= 1:
    c1, c2 = st.columns(2)
    with c1:
        fig = px.histogram(filtered_df, x=numeric_cols[0], title=f"Distribution of {numeric_cols[0]}", nbins=30)
        st.plotly_chart(fig, use_container_width=True)
        charts_made += 1
        chart_meta.append({"Chart Title": f"Distribution of {numeric_cols[0]}", "Type": "Histogram", "X": numeric_cols[0], "Y": "count"})
    with c2:
        if len(numeric_cols) >= 2:
            fig = px.scatter(filtered_df, x=numeric_cols[0], y=numeric_cols[1],
                              title=f"{numeric_cols[0]} vs {numeric_cols[1]}",
                              color=categorical_cols[0] if categorical_cols else None)
            st.plotly_chart(fig, use_container_width=True)
            charts_made += 1
            chart_meta.append({"Chart Title": f"{numeric_cols[0]} vs {numeric_cols[1]}", "Type": "Scatter", "X": numeric_cols[0], "Y": numeric_cols[1]})

if datetime_cols and numeric_cols:
    try:
        ts_df = filtered_df.copy()
        ts_df[datetime_cols[0]] = pd.to_datetime(ts_df[datetime_cols[0]], errors="coerce")
        ts_df = ts_df.dropna(subset=[datetime_cols[0]]).groupby(
            ts_df[datetime_cols[0]].dt.to_period("M"))[numeric_cols[0]].sum().reset_index()
        ts_df[datetime_cols[0]] = ts_df[datetime_cols[0]].astype(str)
        fig = px.line(ts_df, x=datetime_cols[0], y=numeric_cols[0], title="Time Series Trend", markers=True)
        st.plotly_chart(fig, use_container_width=True)
        charts_made += 1
        chart_meta.append({"Chart Title": "Time Series Trend", "Type": "Line", "X": datetime_cols[0], "Y": numeric_cols[0]})
    except Exception:
        pass

if len(numeric_cols) >= 2:
    corr = filtered_df[numeric_cols].corr()
    fig = px.imshow(corr, text_auto=".2f", title="Correlation Heatmap", color_continuous_scale="RdYlGn")
    st.plotly_chart(fig, use_container_width=True)
    charts_made += 1
    chart_meta.append({"Chart Title": "Correlation Heatmap", "Type": "Heatmap", "X": "numeric columns", "Y": "numeric columns"})

if charts_made == 0:
    st.info("Couldn't auto-detect chartable columns — try a dataset with at least one numeric column.")

st.markdown('<div class="eco-section-title">🤖 AI-Generated Insights</div>', unsafe_allow_html=True)
if st.button("Generate AI Insights", type="primary"):
    summary_stats = filtered_df.describe(include="all").to_string()[:3000]
    prompt = (
        f"Here is a statistical summary of an uploaded dataset with columns {list(df.columns)}:\n"
        f"{summary_stats}\n\n"
        "Give 4-5 concise, non-obvious data insights and 2 recommendations, as bullet points."
    )
    with st.spinner("Analyzing dataset..."):
        insights = chat_completion(
            [{"role": "system", "content": "You are a data analyst generating executive insights."},
             {"role": "user", "content": prompt}],
            temperature=0.4, max_tokens=500,
        )
    st.session_state["_dashboard_insights"] = insights
    st.markdown(f'<div class="eco-card">{insights}</div>', unsafe_allow_html=True)
elif st.session_state.get("_dashboard_insights"):
    st.markdown(f'<div class="eco-card">{st.session_state["_dashboard_insights"]}</div>', unsafe_allow_html=True)

def build_dashboard_summary_df():
    """KPI + insights summary written to the 'Dashboard' sheet."""
    summary_rows = [
        {"Metric": "Rows", "Value": df.shape[0]},
        {"Metric": "Columns", "Value": df.shape[1]},
        {"Metric": "Missing Values", "Value": missing},
        {"Metric": "Duplicate Rows", "Value": dupes},
        {"Metric": "Numeric Columns", "Value": ", ".join(numeric_cols) or "-"},
        {"Metric": "Categorical Columns", "Value": ", ".join(categorical_cols) or "-"},
    ]
    return pd.DataFrame(summary_rows)


def build_excel_workbook(data_df, sheet_name="Data"):
    """
    Builds a 2-sheet workbook: the original/cleaned data on `sheet_name`,
    plus a 'Dashboard' sheet with KPI summary + chart metadata + AI insights
    (each on its own labeled block within the sheet).
    """
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        # Sheet 1: preserve the original uploaded/cleaned data as-is
        data_df.to_excel(writer, index=False, sheet_name=sheet_name)

        # Sheet 2: "Dashboard" — KPI summary + chart metadata + AI insights
        start_row = 0
        build_dashboard_summary_df().to_excel(writer, index=False, sheet_name="Dashboard", startrow=start_row)
        start_row += len(build_dashboard_summary_df()) + 3

        if chart_meta:
            pd.DataFrame(chart_meta).to_excel(writer, index=False, sheet_name="Dashboard", startrow=start_row)
            start_row += len(chart_meta) + 3

        if st.session_state.get("_dashboard_insights"):
            insights_df = pd.DataFrame({"AI Insights": [st.session_state["_dashboard_insights"]]})
            insights_df.to_excel(writer, index=False, sheet_name="Dashboard", startrow=start_row)
    return buf.getvalue()


st.markdown('<div class="eco-section-title">⬇️ Download</div>', unsafe_allow_html=True)
d1, d2, d3 = st.columns(3)
with d1:
    st.download_button("📄 Cleaned CSV", filtered_df.to_csv(index=False).encode(),
                        "dashboard_data.csv", "text/csv")
with d2:
    workbook_bytes = build_excel_workbook(filtered_df, sheet_name=original_sheet_name if is_excel_input else "Data")
    label = "📊 Excel (Data + Dashboard sheets)" if is_excel_input else "📊 Dashboard Export (.xlsx, 2 sheets)"
    st.download_button(label, workbook_bytes, "dashboard_report.xlsx",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
with d3:
    html = filtered_df.to_html(index=False)
    st.download_button("🌐 HTML", html.encode(), "dashboard_data.html", "text/html")

if not is_excel_input:
    st.caption("ℹ️ CSV files can't hold multiple sheets, so 'Cleaned CSV' gives you the flat data and "
               "'Dashboard Export' gives you a 2-sheet .xlsx (original data + Dashboard summary).")
else:
    st.caption(f"ℹ️ The Excel download preserves your original data on '{original_sheet_name}' and adds "
               "a new 'Dashboard' sheet with KPIs, chart metadata, and AI insights.")
st.caption("Tip: use each chart's built-in camera icon (top-right of the chart) to download it as PNG.")
