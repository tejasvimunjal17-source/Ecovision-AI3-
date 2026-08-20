# ☁️ Deployment Guide — EcoVision AI

## 1. Push to GitHub

```bash
cd ecovision-ai
git init
git add .
git commit -m "Initial commit — EcoVision AI Smart Waste Management Platform"
git branch -M main
git remote add origin https://github.com/<your-username>/ecovision-ai.git
git push -u origin main
```

> ⚠️ Double-check `.env` is **not** committed — it's already in `.gitignore`.
> Only `.env.example` (with placeholder values) should be in the repo.

## 2. Deploy on Streamlit Community Cloud

1. Go to https://share.streamlit.io and sign in with GitHub.
2. Click **"New app"** → select your `ecovision-ai` repository → branch `main`.
3. Set **Main file path** to `app.py`.
4. Click **"Advanced settings"** → **Secrets** and paste your environment
   variables in TOML format (Streamlit Cloud injects these as `st.secrets`,
   which `python-dotenv` + `os.getenv` will also pick up if you mirror them
   into environment variables — see note below):

```toml
OPENROUTER_API_KEY = "sk-or-your-real-key"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_MODEL = "meta-llama/llama-3.2-11b-vision-instruct:free"
OPENROUTER_TEXT_MODEL = "meta-llama/llama-3.1-8b-instruct:free"
APP_SECRET_KEY = "generate-a-long-random-string"
MUNICIPALITY_NAME = "Municipal Corporation of Gurugram (MCG)"
SUPPORT_EMAIL = "support@yourdomain.in"
SUPPORT_PHONE = "+91-XXXXXXXXXX"
```

5. Click **Deploy**. First boot will install `requirements.txt` and
   auto-create the SQLite database.

### Note on secrets vs `.env` on Streamlit Cloud

Streamlit Cloud secrets aren't automatically written to `os.environ` in older
Streamlit versions. If `config/settings.py` doesn't pick up your secrets,
add this snippet near the top of `app.py` (before `from config import settings`):

```python
import os, streamlit as st
for k, v in st.secrets.items():
    os.environ.setdefault(k, str(v))
```

## 3. Persistent Storage Note

Streamlit Community Cloud's filesystem is **ephemeral** — the SQLite file
and uploaded images reset on redeploys/restarts. For a real production
municipal deployment, swap `database/db.py` to point at a managed database
(e.g. PostgreSQL) and store uploaded images in object storage (e.g. AWS S3 /
IBM Cloud Object Storage) instead of the local `assets/uploads/` folder.

## 4. Custom Domain / IBM Cloud (optional)

To run this on IBM Cloud instead of Streamlit Cloud:
1. Containerize with a `Dockerfile` (`FROM python:3.11-slim`, copy repo,
   `pip install -r requirements.txt`, `CMD ["streamlit","run","app.py","--server.port=8080","--server.address=0.0.0.0"]`).
2. Push the image to IBM Cloud Container Registry.
3. Deploy via IBM Cloud Code Engine or Kubernetes Service, injecting the same
   environment variables as secrets/config maps.

## 5. Google OAuth Setup

The landing page's **Continue with Google** button uses Streamlit's
built-in `st.login()` (Authlib-based OIDC), configured entirely through
secrets — no client secret ever lives in the repo.

**Status:** implemented and wired up (`app.py`, `backend/auth.py`,
`config/settings.py::is_google_oauth_configured()`), but **not yet
tested end-to-end**, because no real Google OAuth credentials were
available in this environment. Until you complete the steps below, the
button correctly detects that state and shows an in-app message instead
of failing silently — it does not pretend to work.

1. **Google Cloud Console** → create (or reuse) a project → **APIs &
   Services → Credentials → Create Credentials → OAuth client ID**.
   - Application type: **Web application**.
   - Authorized redirect URI: `http://localhost:8501/oauth2callback` for
     local dev, and `https://<your-app>.streamlit.app/oauth2callback`
     (or your custom domain) for production. Streamlit's redirect path
     is always `/oauth2callback`.
2. Copy the generated **Client ID** and **Client Secret**.
3. Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml`
   (local) or paste the same TOML into **Streamlit Cloud → Settings →
   Secrets** (production), and fill in:
   ```toml
   [auth]
   redirect_uri = "https://<your-app>.streamlit.app/oauth2callback"
   cookie_secret = "<generate a long random string>"
   client_id = "<your client id>.apps.googleusercontent.com"
   client_secret = "<your client secret>"
   server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
   ```
4. Redeploy / restart the app. The Google button will now call
   `st.login()` and redirect to Google's consent screen; on return, a
   matching `users` row is looked up by email or created with
   `role='citizen'` and `auth_provider='google'` (see
   `backend/auth.py::get_or_create_google_user`).
5. **Test manually**: click Continue with Google → approve the Google
   consent screen → confirm you land on the Citizen Dashboard (or the
   correct role dashboard, if you manually promote the account to
   officer/admin afterward).

> ⚠️ Do not report Google OAuth as working in any deployment where this
> checklist wasn't actually completed and step 5 wasn't actually run.

## 6. Post-Deployment Checklist

- [ ] Change the default admin password (`admin@ecovision.local`) immediately.
- [ ] Confirm `OPENROUTER_API_KEY` is set and AI features respond live (not demo mode).
- [ ] Test registration, login, and password reset end-to-end.
- [ ] Complete the Google OAuth Setup section above and manually test sign-in — REQUIRES EXTERNAL CREDENTIALS, not yet tested in this environment.
- [ ] Verify file upload size limits match `.streamlit/config.toml` (`maxUploadSize`).
- [ ] Set up a real database + object storage before going into production with real citizen data.
