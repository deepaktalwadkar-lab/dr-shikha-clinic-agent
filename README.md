# Dr. Shikha's Clinic - AI Receptionist

Flask app that lets patients chat with the clinic on **WhatsApp (Wati)** and **Instagram (Meta)**.
Gemini answers questions and books consultation tokens, which are stored in SQLite.

## Render settings
- Build command: `pip install -r requirements.txt`
- Start command: `python app.py` (serves with waitress on `$PORT`)

## Environment variables (Render -> Environment)
| Variable | Needed for | Notes |
|---|---|---|
| `GEMINI_API_KEY` | everything | from https://aistudio.google.com/apikey |
| `GEMINI_MODEL` | optional | default `gemini-3.8-flash` |
| `WATI_API_ENDPOINT` | WhatsApp | e.g. `https://live-mt-server.wati.io/12345` (Wati -> API Docs) |
| `WATI_ACCESS_TOKEN` | WhatsApp | Wati API token |
| `WATI_WEBHOOK_SECRET` | optional | if set, add `?secret=<value>` to the Wati webhook URL |
| `META_VERIFY_TOKEN` | Instagram | any password you choose; same value goes in the Meta dashboard |
| `META_ACCESS_TOKEN` | Instagram | Instagram/Page access token with messaging permission |
| `META_APP_SECRET` | optional | enables signature verification of Meta calls |
| `ADMIN_KEY` | `/view-bookings` | without it the endpoint stays locked |
| `CLINIC_TIMINGS`, `CLINIC_FEE`, `CLINIC_LOCATION`, `CLINIC_PAYMENT_LINK` | optional | override clinic details |

## Webhook URLs
- Wati (incoming message): `https://dr-shikha-clinic-agent.onrender.com/whatsapp-webhook`
- Meta / Instagram: `https://dr-shikha-clinic-agent.onrender.com/webhook` (verify token = `META_VERIFY_TOKEN`)

## Staff
`GET /view-bookings` with header `X-Admin-Key: <ADMIN_KEY>`.
