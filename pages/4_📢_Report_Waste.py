import streamlit as st
from pathlib import Path
from datetime import datetime
from backend.complaints import create_complaint
from utils.ai_client import classify_waste_image, classify_waste_video, generate_complaint_description, predict_priority
from utils.helpers import load_css, require_login, toast
from config import settings

st.set_page_config(page_title="Report Waste | EcoVision AI", page_icon="📢", layout="wide")
require_login(allowed_roles=["citizen"])
load_css()

user = st.session_state["user"]
UPLOAD_DIR = Path(__file__).resolve().parent.parent / "assets" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _safe_html(html: str) -> str:
    """Collapse a multi-line HTML snippet to one line before rendering -- avoids
    Streamlit's Markdown parser mis-handling an indented/blank continuation line
    as a code block. Purely a rendering safeguard; output is visually identical."""
    return " ".join(line.strip() for line in html.strip().splitlines())


st.markdown('<div class="eco-hero"><h1>📢 Report a Waste Issue</h1><p>Upload a photo or video — our AI will classify the waste and help you write the complaint.</p></div>', unsafe_allow_html=True)

# ---------------------------------------------------------------------
# AI status banner — now specific about *why*, instead of a generic
# "demo mode" notice, and clearly distinct from a real classification
# result (this is part of the fix: never let "not configured" look like
# a low-confidence real prediction).
# ---------------------------------------------------------------------
if not settings.is_vision_configured():
    st.warning(f"⚠️ AI image/video classification is unavailable — {settings.ai_not_configured_reason()} "
               "Add a real `OPENROUTER_API_KEY` in Streamlit Secrets (or `.env` for local development) to enable live results.")

st.markdown('<div class="eco-section-title">Step 1 · Add a Photo or Video</div>', unsafe_allow_html=True)

tab_upload_img, tab_camera, tab_upload_video = st.tabs(["🖼 Upload Image", "📷 Capture Photo", "📹 Upload Video"])

media_bytes = None       # bytes to run AI classification on right now (a single image)
media_mime = "image/jpeg"
media_source = None      # "image" | "camera" | "video" -- what to save to disk on submit
media_name = None
video_bytes_for_save = None

with tab_upload_img:
    image_file = st.file_uploader(
        "Upload a clear photo of the waste",
        type=settings.ACCEPTED_IMAGE_EXTENSIONS,  # jpg, jpeg, png, webp
        key="image_uploader",
    )
    if image_file:
        st.image(image_file, caption="Uploaded photo", use_container_width=True)
        media_bytes = image_file.getvalue()
        media_mime = settings.IMAGE_MIME_TYPES.get(Path(image_file.name).suffix.lstrip(".").lower(), image_file.type or "image/jpeg")
        media_source, media_name = "image", image_file.name

with tab_camera:
    camera_file = st.camera_input("Take a photo of the waste", key="camera_input")
    if camera_file:
        media_bytes = camera_file.getvalue()
        media_mime = "image/jpeg"
        media_source, media_name = "camera", "camera_capture.jpg"

with tab_upload_video:
    video_file = st.file_uploader(
        "Upload a short video of the waste (a few seconds is enough)",
        type=settings.ACCEPTED_VIDEO_EXTENSIONS,  # mp4, mov, avi, mkv, webm
        key="video_uploader",
    )
    if video_file:
        st.video(video_file)
        video_bytes_for_save = video_file.getvalue()
        media_source, media_name = "video", video_file.name

st.markdown('<div class="eco-section-title">Step 2 · Run AI Classification</div>', unsafe_allow_html=True)

if st.button("🤖 Classify with AI", type="primary", use_container_width=True, disabled=not (media_bytes or video_bytes_for_save)):
    if video_bytes_for_save is not None:
        progress_bar = st.progress(0.0, text="Sampling video frames...")

        def _on_progress(current, total):
            progress_bar.progress(current / total, text=f"Analyzing frame {current} of {total}...")

        with st.spinner("Analyzing video with AI..."):
            ai_result = classify_waste_video(video_bytes_for_save, filename=media_name, max_frames=3, progress_callback=_on_progress)
        progress_bar.empty()
        st.session_state["_ai_result"] = ai_result
    elif media_bytes is not None:
        with st.spinner("Analyzing image with AI..."):
            ai_result = classify_waste_image(media_bytes, mime_type=media_mime)
        st.session_state["_ai_result"] = ai_result

ai_result = st.session_state.get("_ai_result")

if ai_result:
    if ai_result.get("not_configured"):
        st.warning(
            f"⚠️ **AI classification is not configured.**\n\n{ai_result.get('reason', '')}\n\n"
            "Add a real `OPENROUTER_API_KEY` in Streamlit Secrets (or `.env` locally) to get a real prediction here."
        )
    elif ai_result.get("error"):
        st.error(ai_result["error"])
    else:
        frames_note = ""
        if ai_result.get("frames_sampled"):
            frames_note = f'<br><span style="color:#64748b;font-size:0.8rem;">Based on the best of {ai_result["frames_sampled"]} sampled video frames.</span>'
        st.markdown(
            _safe_html(f"""<div class="eco-card">
                <b>Predicted Category:</b> {ai_result.get('category')}<br>
                <b>Confidence:</b> {ai_result.get('confidence')}%<br>
                <b>Recycling Method:</b> {ai_result.get('recycling_method')}<br>
                <b>Disposal Guide:</b> {ai_result.get('disposal_guide')}<br>
                <b>Environmental Impact:</b> {ai_result.get('environmental_impact')}
                {frames_note}
            </div>"""),
            unsafe_allow_html=True,
        )
else:
    st.caption("Add a photo/video above, then click \"Classify with AI\".")

st.markdown('<div class="eco-section-title">Step 3 · Complaint Details</div>', unsafe_allow_html=True)

with st.form("complaint_form"):
    ai_res_for_default = st.session_state.get("_ai_result") or {}
    default_category = ai_res_for_default.get("category") or "Mixed"
    idx = settings.WASTE_CATEGORIES.index(default_category) if default_category in settings.WASTE_CATEGORIES else 5
    category = st.selectbox("Waste Category", settings.WASTE_CATEGORIES, index=idx)
    ward = st.text_input("Ward / Sector", value=user.get("ward") or "")
    address_text = st.text_input("Landmark / Address", value=user.get("address") or "")
    raw_notes = st.text_area("Describe the issue (optional — AI can help write this)")

    gen_ai_desc = st.form_submit_button("✍️ Generate AI Description")
    submitted = st.form_submit_button("✅ Submit Complaint", type="primary", use_container_width=True)

if gen_ai_desc:
    with st.spinner("Writing a professional description..."):
        st.session_state["_ai_desc"] = generate_complaint_description(category, ward, raw_notes)

if st.session_state.get("_ai_desc"):
    st.info(f"**AI-generated description:**\n\n{st.session_state['_ai_desc']}")

if submitted:
    final_description = raw_notes.strip() or st.session_state.get("_ai_desc", "") or f"{category} waste reported at {ward or 'unspecified location'}."
    with st.spinner("Submitting complaint..."):
        priority = predict_priority(category, final_description)
        image_path = ""
        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")

        if media_source == "video" and video_bytes_for_save:
            ext = Path(media_name).suffix or ".mp4"
            image_path = str(UPLOAD_DIR / f"complaint_{user['id']}_{timestamp}{ext}")
            Path(image_path).write_bytes(video_bytes_for_save)
        elif media_bytes:
            ext = Path(media_name).suffix or ".jpg"
            image_path = str(UPLOAD_DIR / f"complaint_{user['id']}_{timestamp}{ext}")
            Path(image_path).write_bytes(media_bytes)

        ai_res = st.session_state.get("_ai_result") or {}
        complaint_id = create_complaint(
            user_id=user["id"],
            category=category,
            description=final_description,
            ai_description=st.session_state.get("_ai_desc", ""),
            ai_predicted_category=ai_res.get("category") or "",
            ai_confidence=ai_res.get("confidence"),
            priority=priority,
            image_path=image_path,
            ward=ward,
            address_text=address_text,
        )
    toast(f"Complaint #{complaint_id} submitted! +10 reward points 🎉")
    st.success(f"✅ Complaint #{complaint_id} submitted successfully with **{priority}** priority!")
    st.session_state.pop("_ai_result", None)
    st.session_state.pop("_ai_desc", None)
    st.balloons()
    st.page_link("pages/5_📜_Complaint_History.py", label="View Complaint History", icon="📜")
