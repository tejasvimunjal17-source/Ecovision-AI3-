# 🌿 EcoVision AI — Smart Waste Management & Recycling in Indian Cities

**AI-powered Smart City Platform for Sustainable Waste Management**

A production-ready Streamlit web application for Indian Municipal Corporations
(e.g. MCG Gurugram) that helps citizens report waste, learn recycling best
practices, chat with an AI sustainability assistant, and gives officers/admins
interactive dashboards to manage and analyze municipal waste data.

Supports **SDG 11** (Sustainable Cities), **SDG 12** (Responsible Consumption
& Production), and **SDG 13** (Climate Action).

---

<img src="https://user-images.githubusercontent.com/73097560/115834477-dbab4500-a447-11eb-908a-139a6edaec5c.gif" alt="" style="max-width: 100%; display: inline-block;" data-target="animated-image.originalImage">

Live At : https://smart-waste-management-and-recycling-in-indian-cities.streamlit.app/ 

<img src="https://user-images.githubusercontent.com/73097560/115834477-dbab4500-a447-11eb-908a-139a6edaec5c.gif" alt="" style="max-width: 100%; display: inline-block;" data-target="animated-image.originalImage">


## ✨ Features

| Module | What it does |
|---|---|
| 🔐 Auth | Register/Login/Forgot Password with PBKDF2-hashed passwords, rate limiting |
| 📢 Report Waste | Upload a photo → AI classifies category, writes description, predicts priority |
| 📜 Complaint Tracking | Full status timeline per complaint |
| 🌿 Prakriti AI Connect | Bilingual (English/Hindi) streaming AI chatbot, scoped to sustainability topics |
| 🧑‍💼 Officer Dashboard | Complaint management, worker assignment, ward analytics |
| 🛠️ Admin Dashboard | User/officer/category management, system-wide analytics |
| 📈 Dashboard Generator | Upload any CSV/Excel → auto KPIs, charts, AI insights, exports |
| ♻️ Recycling Guide | Category-wise disposal guidance + AI custom tips |
| 📍 Recycling Centres | Map + directory of authorized centres |
| 🌍 Carbon Calculator | Personal footprint estimator with AI reduction tips |
| 🎓 Certifications & 🌱 Green Jobs | Curated free-certification directory + live job-board search links |
| 📄 Reports | PDF / Excel / CSV exports |
| 🏆 Rewards | Points + city leaderboard |

## 🧱 Tech Stack

- **Frontend:** Streamlit, HTML/CSS (glassmorphism theme), minimal JS via components
- **Backend:** Python 3.10+
- **Database:** SQLite (file-based, zero external services)
- **AI:** OpenRouter Chat Completions API (text + vision)
- **Chatbot:** Prakriti AI Connect (custom system-prompt guardrails)
- **Deployment:** Streamlit Community Cloud

## 📂 Project Structure

```
ecovision-ai/
├── app.py                     # Landing page + router
├── pages/                     # All Streamlit pages (auto sidebar nav)
├── backend/                   # auth.py, complaints.py, analytics.py
├── chatbot/                   # prakriti.py — chatbot logic & guardrails
├── database/                  # db.py, schema.sql
├── utils/                     # ai_client.py, validators.py, helpers.py
├── config/                    # settings.py — loads .env
├── assets/                    # style.css, data/, uploads/
├── .env.example                # copy to .env and fill in secrets
├── requirements.txt
└── README.md
```

## 🚀 Quick Start (Local)

```bash
git clone <your-repo-url>
cd ecovision-ai
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # then edit .env with your real OpenRouter API key
streamlit run app.py
```

The app auto-creates the SQLite database and seeds default categories,
recycling centres, and a demo admin account on first run:

```
Email: admin@ecovision.local
Password: Admin@12345   (change immediately after first login)
```

## 🔑 Environment Variables

See `.env.example`. Required for full AI functionality:

```
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_MODEL=meta-llama/llama-3.2-11b-vision-instruct:free
OPENROUTER_TEXT_MODEL=meta-llama/llama-3.1-8b-instruct:free
```

Without a valid key, the app still runs fully — AI features degrade to
clearly-labeled demo/mock responses instead of crashing.

## 🔒 Security Notes

- Passwords: PBKDF2-HMAC-SHA256, 260k iterations, per-user random salt — never stored in plaintext.
- All SQL uses parameterized queries (no string-built SQL) — protected against SQL injection.
- Basic login rate limiting (5 failed attempts / 15 minutes).
- Secrets are only ever read from `.env` — never hardcoded, never logged.
- `.env` is git-ignored by default.

## 📊 Database Schema

See `database/schema.sql` for the full DDL (users, complaints, complaint_timeline,
rewards, chat_history, recycling_centres, carbon_records, categories,
login_attempts, audit_log).

## ☁️ Deployment

See `DEPLOYMENT.md` for the full Streamlit Community Cloud + GitHub guide.

## ⚠️ Known Simplifications (honest scope notes)

- **Job aggregation** (LinkedIn/Naukri/Indeed/etc.) uses generated deep-search
  links, not live scraping/APIs — those platforms require paid partnerships.
- **Certification search** uses a curated static directory linking to each
  provider's own course catalog, since 15+ live catalogs can't be reliably scraped.
- **SQL upload** in the Dashboard Generator is limited to CSV/Excel for security
  (arbitrary SQL file execution is a major injection risk).
- **Email** (forgot-password, contact form) uses a security-question flow
  instead of SMTP, since no mail server is configured out of the box — swap in
  a provider like SendGrid/SES if you need real email delivery.

## 📄 License

Built for educational/municipal-demo purposes. Adapt license as needed for your deployment.
