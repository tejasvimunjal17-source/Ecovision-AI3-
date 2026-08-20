"""
utils/ai_client.py
---------------------
Thin wrapper around AI providers used across EcoVision AI. Centralizes:
  - plain chat completion (Prakriti AI Connect chatbot) — OpenRouter
  - streaming chat completion — OpenRouter
  - vision-based waste image classification — OpenRouter (primary),
    automatic fallback to IBM watsonx.ai if OpenRouter fails and
    watsonx is configured
  - vision-based waste VIDEO classification (frame sampling)
  - structured text generation (priority, complaint text, insights)

No API key is ever hardcoded — read from config.settings, which reads
st.secrets first and .env second (see config/settings.py docstring).

THREE POSSIBLE OUTCOMES of classify_waste_image() / classify_waste_video()
(callers — the Report Waste page — must branch on these rather than
assuming `category` is always present; this is what replaced the old
bug where an unconfigured/broken AI silently showed a fake
"Predicted Category: Mixed / Confidence: 0%"):
  1. Success        -> {"category": "...", "confidence": 87, ..., "not_configured": False, "error": None}
  2. Not configured  -> {"category": None, ..., "not_configured": True, "error": None, "reason": "..."}
  3. Provider error  -> {"category": None, ..., "not_configured": False, "error": "❌ AI image classification failed...\n\nReason:\n...\n\nPossible causes:\n...\n\nSuggested Fix:\n..."}
"""
import io
import time
import json
import base64
import logging
import requests
from PIL import Image

from config import settings

logger = logging.getLogger("ecovision.ai")

TIMEOUT = 60


# =======================================================================
# Shared error formatting
# =======================================================================
def _format_provider_error(context_label: str, reason: str, causes: list, fix: str) -> str:
    """Builds the required structured error message:
    ❌ <context>. / Reason: / Possible causes (bulleted) / Suggested Fix
    """
    causes_block = "\n".join(f"• {c}" for c in causes)
    return (
        f"❌ {context_label}\n\n"
        f"Reason:\n{reason}\n\n"
        f"Possible causes:\n{causes_block}\n\n"
        f"Suggested Fix:\n{fix}"
    )


def _classification_error_from_exception(e, resp=None, model_hint=None, provider="OpenRouter"):
    """
    Turns a request exception into the structured error format above,
    extracting the provider's own error message from the response body
    when available (instead of the generic HTTP status line the
    original bug report showed — a bare "404 Client Error").
    """
    status_code = getattr(resp, "status_code", None)
    api_message = None
    if resp is not None:
        try:
            body = resp.json()
            err = body.get("error")
            api_message = (err.get("message") if isinstance(err, dict) else err) or body.get("message")
        except Exception:
            api_message = (getattr(resp, "text", "") or "").strip()[:300] or None

    reason = api_message or str(e)

    if status_code == 404:
        causes = ["Invalid or retired Vision model", "Incorrect API base URL"]
        fix = (
            f"The model '{model_hint}' was not found on {provider}. "
            "Set OPENROUTER_VISION_MODEL to 'openrouter/free' (auto-selects a currently working "
            "vision model) or choose a current one from https://openrouter.ai/models."
        )
    elif status_code in (401, 403):
        causes = ["Invalid API key", "Missing Streamlit Secret", "API key lacks permission"]
        fix = "Verify the API key is correct and set in Streamlit Secrets (Settings → Secrets on Streamlit Cloud) or .env for local development."
    elif status_code == 429:
        causes = ["Rate limit exceeded (common on free models — typically 20 req/min, 200/day)"]
        fix = "Wait a minute and try again, or switch OPENROUTER_VISION_MODEL to a different model."
    elif isinstance(e, requests.exceptions.Timeout):
        causes = ["Network error", "Request timed out"]
        fix = "Check your network connection and try again."
    elif isinstance(e, requests.exceptions.ConnectionError):
        causes = ["Network error", "Could not reach the API host"]
        fix = "Check your network/firewall/proxy settings and that the base URL is correct."
    else:
        causes = ["Invalid API key", "Invalid Vision model", "Missing Streamlit Secret", "Network error"]
        fix = "Check the API key, base URL, and model name in Streamlit Secrets (or .env locally)."

    return _format_provider_error("AI image classification failed.", reason, causes, fix)


# =======================================================================
# Performance: shrink/re-encode images before they're base64'd and sent
# over the network. Phone photos are commonly 3-8MB at 4000px+ on a
# side; no vision model needs that much resolution to identify a waste
# category, and a smaller payload means a faster upload, faster API
# round-trip, and less chance of hitting a provider payload-size limit.
# =======================================================================
def _prepare_image_for_upload(image_bytes: bytes, max_dimension: int = 1024, jpeg_quality: int = 85):
    """
    Returns (bytes, mime_type). Falls back to the original bytes
    untouched (mime_type=None, meaning "keep caller's mime_type") if
    Pillow can't decode the image for any reason — an optimization
    failing must never block a classification attempt.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > max_dimension:
            scale = max_dimension / max(w, h)
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
        return buf.getvalue(), "image/jpeg"
    except Exception as e:
        logger.warning("Image optimization skipped, using original bytes: %s", e)
        return image_bytes, None


# =======================================================================
# OpenRouter — chat (Prakriti AI Connect)
# =======================================================================
def _headers():
    return {
        "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": settings.OPENROUTER_SITE_URL,
        "X-Title": settings.OPENROUTER_APP_NAME,
    }


def _endpoint():
    return f"{settings.OPENROUTER_BASE_URL.rstrip('/')}/chat/completions"


def chat_completion(messages, model=None, temperature=0.4, max_tokens=800, json_mode=False):
    """Non-streaming completion. `messages`: [{"role": ..., "content": ...}]"""
    if not settings.is_ai_configured():
        return _mock_response(messages)

    chosen_model = model or settings.OPENROUTER_CHAT_MODEL
    payload = {"model": chosen_model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    resp = None
    try:
        resp = requests.post(_endpoint(), headers=_headers(), json=payload, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except requests.exceptions.RequestException as e:
        logger.error("OpenRouter chat request failed: %s", e)
        return "⚠️ " + _classification_error_from_exception(e, resp, chosen_model).replace("AI image classification failed.", "AI chat request failed.")
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        logger.error("Unexpected OpenRouter response: %s", e)
        return "⚠️ AI service returned an unexpected response. Please try again."


def stream_chat_completion(messages, model=None, temperature=0.5, max_tokens=800):
    """Generator yielding text chunks — used for the streaming chatbot UI."""
    if not settings.is_ai_configured():
        yield _mock_response(messages)
        return

    chosen_model = model or settings.OPENROUTER_CHAT_MODEL
    payload = {"model": chosen_model, "messages": messages, "temperature": temperature,
               "max_tokens": max_tokens, "stream": True}
    resp = None
    try:
        with requests.post(_endpoint(), headers=_headers(), json=payload, timeout=TIMEOUT, stream=True) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                line = line.decode("utf-8").strip()
                if not line.startswith("data:"):
                    continue
                data_str = line[len("data:"):].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                    delta = chunk["choices"][0]["delta"].get("content")
                    if delta:
                        yield delta
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
    except requests.exceptions.RequestException as e:
        logger.error("OpenRouter streaming failed: %s", e)
        yield "\n\n⚠️ " + _classification_error_from_exception(e, resp, chosen_model).replace("AI image classification failed.", "AI chat request failed.")


# =======================================================================
# Vision classification — OpenRouter (primary) + IBM watsonx.ai (fallback)
# =======================================================================
def _not_configured_result():
    return {
        "category": None, "confidence": None, "recycling_method": None,
        "disposal_guide": None, "environmental_impact": None,
        "not_configured": True, "error": None,
        "reason": settings.ai_not_configured_reason(),
    }


def _error_result(message):
    return {
        "category": None, "confidence": None, "recycling_method": None,
        "disposal_guide": None, "environmental_impact": None,
        "not_configured": False, "error": message,
    }


_VISION_SYSTEM_PROMPT_TEMPLATE = (
    "You are an expert municipal waste-classification AI for Indian cities. "
    "Classify the uploaded image into exactly one of these categories: {categories}. "
    "Respond ONLY with a valid JSON object with keys: "
    "category (string, one of the allowed categories), "
    "confidence (number 0-100), "
    "recycling_method (short string), "
    "disposal_guide (2-3 sentences, India-specific, mention MCG guidelines where relevant), "
    "environmental_impact (1-2 sentences). No markdown, no extra text."
)


def _parse_classification_json(raw_content: str):
    content = raw_content.strip().strip("`")
    if content.lower().startswith("json"):
        content = content[4:].strip()
    parsed = json.loads(content)
    return {
        "category": parsed.get("category") or "Mixed",
        "confidence": parsed.get("confidence", 0),
        "recycling_method": parsed.get("recycling_method") or "-",
        "disposal_guide": parsed.get("disposal_guide") or "-",
        "environmental_impact": parsed.get("environmental_impact") or "-",
        "not_configured": False,
        "error": None,
    }


def _classify_via_openrouter(image_bytes: bytes, mime_type: str):
    categories = ", ".join(settings.WASTE_CATEGORIES)
    data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode()}"
    payload = {
        "model": settings.OPENROUTER_VISION_MODEL,
        "messages": [
            {"role": "system", "content": _VISION_SYSTEM_PROMPT_TEMPLATE.format(categories=categories)},
            {"role": "user", "content": [
                {"type": "text", "text": "Classify this waste image."},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]},
        ],
        "temperature": 0.2,
        "max_tokens": 400,
    }
    resp = None
    try:
        resp = requests.post(_endpoint(), headers=_headers(), json=payload, timeout=TIMEOUT)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return _parse_classification_json(content)
    except requests.exceptions.RequestException as e:
        logger.error("OpenRouter vision classification failed: %s", e)
        return _error_result(_classification_error_from_exception(e, resp, settings.OPENROUTER_VISION_MODEL, provider="OpenRouter"))
    except (KeyError, IndexError, json.JSONDecodeError, ValueError) as e:
        logger.error("OpenRouter vision classification returned unexpected data: %s", e)
        return _error_result(_format_provider_error(
            "AI image classification failed.",
            f"The model's response wasn't valid JSON ({e}).",
            ["The selected Vision model returned a non-JSON or malformed reply"],
            "This can happen with some free models under load — try again, or set "
            "OPENROUTER_VISION_MODEL to 'openrouter/free' or another reliable vision model.",
        ))


# ---- IBM watsonx.ai fallback ------------------------------------------
_watsonx_token_cache = {"token": None, "expires_at": 0}


def _get_watsonx_token() -> str:
    """
    Exchanges WATSONX_API_KEY for a short-lived IAM bearer token (the
    watsonx.ai inference API requires a bearer token, not the raw API
    key — this is IBM Cloud's standard IAM flow). Cached in-process
    until shortly before expiry so we're not re-authenticating on every
    single classification call.
    """
    now = time.time()
    if _watsonx_token_cache["token"] and now < _watsonx_token_cache["expires_at"]:
        return _watsonx_token_cache["token"]

    resp = requests.post(
        "https://iam.cloud.ibm.com/identity/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "urn:ibm:params:oauth:grant-type:apikey", "apikey": settings.WATSONX_API_KEY},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data["access_token"]
    _watsonx_token_cache["token"] = token
    _watsonx_token_cache["expires_at"] = now + data.get("expires_in", 3600) - 60
    return token


def _classify_via_watsonx(image_bytes: bytes, mime_type: str):
    """
    IBM watsonx.ai chat API (ml/v1/text/chat) — official IBM-documented
    schema: https://dataplatform.cloud.ibm.com (Adding generative chat
    function to your applications with the chat API). Requires
    WATSONX_MODEL_ID to be a vision-capable model (e.g.
    'meta-llama/llama-3-2-11b-vision-instruct') — IBM's own text-only
    Granite instruct models (like the 'ibm/granite-3-8b-instruct'
    default) do NOT accept image input and will fail here; this is
    surfaced as a normal provider error, not a crash.
    """
    try:
        token = _get_watsonx_token()
    except requests.exceptions.RequestException as e:
        return _error_result(_format_provider_error(
            "AI image classification failed.",
            f"Could not authenticate with IBM Cloud IAM: {e}",
            ["Invalid WATSONX_API_KEY", "IBM Cloud IAM service unreachable"],
            "Verify WATSONX_API_KEY is a valid, active IBM Cloud API key from https://cloud.ibm.com/iam/apikeys.",
        ))
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        return _error_result(_format_provider_error(
            "AI image classification failed.",
            f"IBM Cloud IAM returned an unexpected response ({e}).",
            ["IBM Cloud IAM API changed or is temporarily degraded"],
            "Verify WATSONX_API_KEY and try again shortly.",
        ))

    categories = ", ".join(settings.WASTE_CATEGORIES)
    data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode()}"
    payload = {
        "model_id": settings.WATSONX_MODEL_ID,
        "project_id": settings.WATSONX_PROJECT_ID,
        "messages": [
            {"role": "system", "content": [{"type": "text", "text": _VISION_SYSTEM_PROMPT_TEMPLATE.format(categories=categories)}]},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": "Classify this waste image."},
            ]},
        ],
        "max_tokens": 400,
        "time_limit": 30000,
    }
    resp = None
    try:
        resp = requests.post(
            f"{settings.WATSONX_URL.rstrip('/')}/ml/v1/text/chat?version=2024-10-09",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"},
            json=payload, timeout=TIMEOUT,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        result = _parse_classification_json(content)
        result["source"] = "IBM watsonx.ai"
        return result
    except requests.exceptions.RequestException as e:
        logger.error("watsonx.ai vision classification failed: %s", e)
        return _error_result(_classification_error_from_exception(e, resp, settings.WATSONX_MODEL_ID, provider="IBM watsonx.ai"))
    except (KeyError, IndexError, json.JSONDecodeError, ValueError) as e:
        logger.error("watsonx.ai vision classification returned unexpected data: %s", e)
        return _error_result(_format_provider_error(
            "AI image classification failed.",
            f"watsonx.ai's response wasn't valid JSON ({e}).",
            ["WATSONX_MODEL_ID may not be a vision-capable model"],
            "Set WATSONX_MODEL_ID to a vision-capable model such as 'meta-llama/llama-3-2-11b-vision-instruct'.",
        ))


def classify_waste_image(image_bytes: bytes, mime_type: str = "image/jpeg"):
    """
    Main entry point. Prefers OpenRouter; automatically falls back to
    IBM watsonx.ai if OpenRouter fails AND watsonx is configured;
    otherwise returns a clear, structured error. Never raises — see
    module docstring for the 3 possible return shapes.
    """
    if not settings.is_vision_configured() and not settings.is_watsonx_configured():
        return _not_configured_result()

    # Performance: normalize/shrink once, benefits every path below
    # (OpenRouter, watsonx, and every video frame that flows through here).
    image_bytes, optimized_mime = _prepare_image_for_upload(image_bytes)
    if optimized_mime:
        mime_type = optimized_mime

    if settings.is_vision_configured():
        result = _classify_via_openrouter(image_bytes, mime_type)
        if not result.get("error"):
            return result
        if settings.is_watsonx_configured():
            logger.warning("OpenRouter vision failed, falling back to IBM watsonx.ai: %s", result["error"])
            fallback = _classify_via_watsonx(image_bytes, mime_type)
            if not fallback.get("error"):
                return fallback
            # both providers failed — surface the primary (OpenRouter) error,
            # note that the fallback was attempted too, never crash
            combined = result["error"] + f"\n\n(IBM watsonx.ai fallback was also attempted and failed.)"
            return _error_result(combined)
        return result  # OpenRouter failed, no watsonx configured to fall back to
    else:
        # OpenRouter not configured at all, but watsonx is -> use it directly
        return _classify_via_watsonx(image_bytes, mime_type)


def classify_waste_video(video_bytes: bytes, filename: str = "video.mp4", max_frames: int = 3, progress_callback=None):
    """
    Samples up to `max_frames` evenly-spaced frames from an uploaded
    waste video, classifies each with classify_waste_image() (which
    already includes the OpenRouter->watsonx fallback and image
    optimization), and returns the highest-confidence result. The full
    per-frame breakdown is included as "frame_results" for transparency.

    progress_callback(current, total), if provided, drives a
    st.progress() bar on the Report Waste page while processing.
    """
    import tempfile
    import os as _os

    try:
        import cv2
    except ImportError:
        return {
            **_error_result(_format_provider_error(
                "AI video classification failed.",
                "The 'opencv-python-headless' package required for video frame extraction is not installed.",
                ["Missing Python dependency"],
                "Add opencv-python-headless to requirements.txt and redeploy.",
            )),
            "frame_results": [],
        }

    if not settings.is_vision_configured() and not settings.is_watsonx_configured():
        return {**_not_configured_result(), "frame_results": []}

    suffix = "." + (filename.rsplit(".", 1)[-1].lower() if "." in filename else "mp4")
    tmp_path = None
    frame_results = []
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(video_bytes)
            tmp_path = tmp.name

        cap = cv2.VideoCapture(tmp_path)
        if not cap.isOpened():
            return {
                **_error_result(_format_provider_error(
                    "AI video classification failed.",
                    "The uploaded video could not be decoded (unsupported codec/container in this environment).",
                    ["Unsupported video codec", "Corrupted video file"],
                    "Re-export as MP4 (H.264), or upload a still image instead.",
                )),
                "frame_results": [],
            }

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        if total_frames <= 0:
            cap.release()
            return {
                **_error_result(_format_provider_error(
                    "AI video classification failed.",
                    "The video appears to have no readable frames.",
                    ["Corrupted or empty video file"],
                    "Re-export the video and try again.",
                )),
                "frame_results": [],
            }

        n = max(1, min(max_frames, total_frames))
        frame_indices = [int(total_frames * (i + 1) / (n + 1)) for i in range(n)]

        for i, idx in enumerate(frame_indices):
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = cap.read()
            if ok:
                success, buf = cv2.imencode(".jpg", frame)
                if success:
                    frame_results.append(classify_waste_image(buf.tobytes(), mime_type="image/jpeg"))
            if progress_callback:
                progress_callback(i + 1, n)
        cap.release()
    finally:
        if tmp_path and _os.path.exists(tmp_path):
            _os.remove(tmp_path)

    valid = [r for r in frame_results if r.get("category") and not r.get("error") and not r.get("not_configured")]
    if not valid:
        first_error = next((r for r in frame_results if r.get("error")), None)
        if first_error:
            return {**first_error, "frame_results": frame_results}
        return {
            **_error_result(_format_provider_error(
                "AI video classification failed.",
                "Could not classify any sampled frame.",
                ["Video content unclear or too dark/blurry"],
                "Try a clearer video, or upload a still image instead.",
            )),
            "frame_results": frame_results,
        }

    best = max(valid, key=lambda r: r.get("confidence") or 0)
    return {**best, "frame_results": frame_results, "frames_sampled": len(frame_results)}


# =======================================================================
# Text generation helpers (unchanged behavior, use the chat model)
# =======================================================================
def generate_complaint_description(category, ward, raw_notes):
    prompt = (
        f"Write a concise, professional municipal waste complaint description "
        f"(3-4 sentences) for a citizen report in India. "
        f"Category: {category}. Ward: {ward or 'not specified'}. "
        f"Citizen's raw notes: '{raw_notes or 'none provided'}'. "
        "Be factual, polite, and actionable — suitable for a municipal officer to read."
    )
    return chat_completion(
        [{"role": "system", "content": "You write clear municipal complaint descriptions."},
         {"role": "user", "content": prompt}],
        temperature=0.4, max_tokens=200,
    )


def predict_priority(category, description):
    prompt = (
        f"Given this municipal waste complaint, classify its urgency as exactly one word: "
        f"High, Medium, or Low.\nCategory: {category}\nDescription: {description}\n"
        "Consider: Biomedical and E-Waste hazards, large accumulation, health risk, or road/drain "
        "blockage = High. Routine uncollected household waste = Medium. Minor/cosmetic issues = Low. "
        "Respond with ONLY the single word."
    )
    result = chat_completion(
        [{"role": "system", "content": "You triage municipal complaints by urgency."},
         {"role": "user", "content": prompt}],
        temperature=0.1, max_tokens=5,
    )
    for level in settings.PRIORITY_LEVELS:
        if level.lower() in result.lower():
            return level
    return "Medium"


def generate_awareness_content(topic, content_type="eco tips"):
    prompt = (
        f"Generate {content_type} about '{topic}' for an Indian municipal sustainability "
        "awareness campaign. Keep it practical, locally relevant, and easy to share on social "
        "media or a poster. Use short bullet points."
    )
    return chat_completion(
        [{"role": "system", "content": "You are a creative sustainability communications expert for Indian cities."},
         {"role": "user", "content": prompt}],
        temperature=0.7, max_tokens=500,
    )


def generate_resume_content(kind, profile_details):
    prompts = {
        "resume": "Write a clean, ATS-friendly resume in Markdown based on these details:",
        "cover_letter": "Write a professional cover letter (max 300 words) based on these details:",
        "portfolio": "Write a short professional portfolio 'About Me' summary based on these details:",
        "linkedin": "Write a compelling LinkedIn 'About' summary (max 150 words) based on these details:",
    }
    prompt = f"{prompts.get(kind, prompts['resume'])}\n\n{profile_details}"
    return chat_completion(
        [{"role": "system", "content": "You are an expert career coach and resume writer."},
         {"role": "user", "content": prompt}],
        temperature=0.5, max_tokens=900,
    )


def _mock_response(messages):
    return (f"🌿 Prakriti AI Connect is running in demo mode — {settings.ai_not_configured_reason()} "
            "Add a real OPENROUTER_API_KEY in Streamlit Secrets (or .env for local dev) to enable live AI responses.")
