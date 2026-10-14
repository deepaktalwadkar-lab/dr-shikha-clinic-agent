"""Dr. Shikha's Clinic - AI receptionist.

Patients message the clinic on WhatsApp (via Wati) or Instagram (via Meta).
Gemini answers, collects name / phone / preferred time, and books a token.

All secrets and clinic details come from environment variables (see README.md).
Never hard-code tokens in this file: the GitHub repository is public.
"""

import hashlib
import hmac
import logging
import os
import re
import sqlite3
import threading
from collections import OrderedDict
from contextlib import closing
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from flask import Flask, jsonify, request
from google import genai
from google.genai import types

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("clinic-agent")

DB_PATH = os.environ.get("DB_PATH", "clinic.db")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")

# WhatsApp via Wati
WATI_API_ENDPOINT = os.environ.get("WATI_API_ENDPOINT", "").rstrip("/")  # e.g. https://live-mt-server.wati.io/12345
WATI_ACCESS_TOKEN = os.environ.get("WATI_ACCESS_TOKEN", "")
WATI_WEBHOOK_SECRET = os.environ.get("WATI_WEBHOOK_SECRET", "")  # optional: add ?secret=... to the Wati webhook URL

# Instagram via Meta
META_VERIFY_TOKEN = os.environ.get("META_VERIFY_TOKEN", "")
META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN", "")
META_APP_SECRET = os.environ.get("META_APP_SECRET", "")  # optional: enables signature checks
META_GRAPH_URL = os.environ.get("META_GRAPH_URL", "https://graph.facebook.com/v23.0").rstrip("/")

# Protects /view-bookings (it contains patient names and phone numbers)
ADMIN_KEY = os.environ.get("ADMIN_KEY", "")

# Clinic details (override on Render without touching code)
CLINIC_NAME = os.environ.get("CLINIC_NAME", "Dr. Shikha's Clinic")
CLINIC_LOCATION = os.environ.get("CLINIC_LOCATION", "Bidar near JJ Tower")
CLINIC_TIMINGS = os.environ.get("CLINIC_TIMINGS", "10:00 AM to 12:00 AM")
CLINIC_FEE = os.environ.get("CLINIC_FEE", "₹200")
CLINIC_PAYMENT_LINK = os.environ.get("CLINIC_PAYMENT_LINK", "https://upi.link/pay-200-dr-shikha")
TIMEZONE = ZoneInfo(os.environ.get("TIMEZONE", "Asia/Kolkata"))

HISTORY_TURNS = 16  # messages of context remembered per patient

app = Flask(__name__)


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------
def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with closing(db()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_name TEXT NOT NULL,
                phone_number TEXT NOT NULL,
                preferred_time TEXT NOT NULL,
                channel TEXT,
                sender_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel TEXT NOT NULL,
                sender_id TEXT NOT NULL,
                role TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # Older deployments created `bookings` without these columns.
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(bookings)")}
        for column in ("channel", "sender_id"):
            if column not in existing:
                conn.execute(f"ALTER TABLE bookings ADD COLUMN {column} TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_sender ON messages (channel, sender_id, id)")
        conn.commit()


init_db()


def load_history(channel, sender_id):
    with closing(db()) as conn:
        rows = conn.execute(
            "SELECT role, text FROM messages WHERE channel = ? AND sender_id = ? ORDER BY id DESC LIMIT ?",
            (channel, sender_id, HISTORY_TURNS),
        ).fetchall()
    rows = list(reversed(rows))
    while rows and rows[0]["role"] != "user":  # Gemini history must start with a user turn
        rows.pop(0)
    return [types.Content(role=r["role"], parts=[types.Part(text=r["text"])]) for r in rows]


def save_turn(channel, sender_id, role, text):
    with closing(db()) as conn:
        conn.execute(
            "INSERT INTO messages (channel, sender_id, role, text) VALUES (?, ?, ?, ?)",
            (channel, sender_id, role, text),
        )
        conn.commit()


# --------------------------------------------------------------------------
# Gemini tool: book a token
# --------------------------------------------------------------------------
def make_booking_tool(channel, sender_id):
    """Build a booking tool bound to this conversation (so we know who booked)."""

    def book_clinic_token(patient_name: str, phone_number: str, preferred_time: str) -> dict:
        """Book a consultation token for a patient and save it to the clinic database.

        Only call this once the patient has given their name, phone number and
        preferred date/time.

        Args:
            patient_name: Full name of the patient.
            phone_number: Contact phone number of the patient (10 digits for India).
            preferred_time: Preferred date and time for the consultation.
        """
        name = (patient_name or "").strip()
        time_pref = (preferred_time or "").strip()
        digits = re.sub(r"\D", "", phone_number or "")
        if len(digits) > 10 and digits.startswith("91"):
            digits = digits[2:]
        if not name or not time_pref:
            return {"status": "error", "message": "Patient name and preferred time are required."}
        if len(digits) != 10:
            return {"status": "error", "message": "Phone number looks invalid. Ask the patient for a 10-digit number."}

        with closing(db()) as conn:
            # Avoid double-booking if the model calls the tool twice for one request.
            dup = conn.execute(
                """SELECT id FROM bookings
                   WHERE sender_id = ? AND phone_number = ? AND preferred_time = ?
                   AND created_at > datetime('now', '-10 minutes')""",
                (sender_id, digits, time_pref),
            ).fetchone()
            if dup:
                booking_id = dup["id"]
            else:
                cur = conn.execute(
                    "INSERT INTO bookings (patient_name, phone_number, preferred_time, channel, sender_id) VALUES (?, ?, ?, ?, ?)",
                    (name, digits, time_pref, channel, sender_id),
                )
                conn.commit()
                booking_id = cur.lastrowid

        token_number = f"TOKEN-#{booking_id + 100}"
        log.info("Booked %s for %s via %s", token_number, name, channel)
        return {
            "status": "success",
            "token_number": token_number,
            "consultation_fee": CLINIC_FEE,
            "payment_link": CLINIC_PAYMENT_LINK,
            "clinic_location": CLINIC_LOCATION,
            "timings": CLINIC_TIMINGS,
        }

    return book_clinic_token


def system_prompt():
    now = datetime.now(TIMEZONE).strftime("%A, %d %B %Y, %I:%M %p")
    return (
        f"You are the AI receptionist for {CLINIC_NAME}, located at {CLINIC_LOCATION}. "
        f"Operating hours: {CLINIC_TIMINGS}. Consultation fee: {CLINIC_FEE}. "
        f"Current date and time (India): {now}.\n"
        "Your job: answer questions about timings, location and fee, and book consultation tokens.\n"
        "- Reply in the same language the patient writes in (English, Hindi, Kannada, Telugu, Urdu, Marathi, etc.).\n"
        "- Keep replies short and friendly, like a chat message. No markdown formatting.\n"
        "- To book, collect: patient name, 10-digit phone number, and preferred date/time. "
        "Ask for whatever is missing, then call book_clinic_token. Never invent a token number.\n"
        "- After booking, tell the patient their token number, fee, payment link, location and timings.\n"
        "- Never give medical diagnoses or prescribe medicine. For emergencies, tell the patient to go to the "
        "nearest hospital or call 112 immediately.\n"
        "- If asked something unrelated to the clinic, politely say you can only help with clinic enquiries and bookings."
    )


# --------------------------------------------------------------------------
# AI reply
# --------------------------------------------------------------------------
_client = None


def gemini():
    global _client
    if _client is None:
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is not set")
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


FALLBACK_REPLY = (
    "Sorry, I'm having a little trouble right now. Please try again in a few minutes, "
    f"or visit {CLINIC_NAME} at {CLINIC_LOCATION} ({CLINIC_TIMINGS})."
)


def generate_reply(channel, sender_id, message_text):
    contents = load_history(channel, sender_id)
    contents.append(types.Content(role="user", parts=[types.Part(text=message_text)]))
    try:
        response = gemini().models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt(),
                tools=[make_booking_tool(channel, sender_id)],  # automatic function calling is on by default
                temperature=0.3,
            ),
        )
        reply = (response.text or "").strip()
    except Exception:
        log.exception("Gemini call failed")
        return FALLBACK_REPLY
    if not reply:
        return FALLBACK_REPLY
    save_turn(channel, sender_id, "user", message_text)
    save_turn(channel, sender_id, "model", reply)
    return reply


# --------------------------------------------------------------------------
# Sending replies
# --------------------------------------------------------------------------
def send_whatsapp_message(wa_id, text):
    if not (WATI_API_ENDPOINT and WATI_ACCESS_TOKEN):
        log.error("WATI_API_ENDPOINT / WATI_ACCESS_TOKEN not set; cannot reply on WhatsApp")
        return
    token = WATI_ACCESS_TOKEN if WATI_ACCESS_TOKEN.lower().startswith("bearer ") else f"Bearer {WATI_ACCESS_TOKEN}"
    try:
        resp = requests.post(
            f"{WATI_API_ENDPOINT}/api/v1/sendSessionMessage/{wa_id}",
            params={"messageText": text},
            headers={"Authorization": token},
            timeout=20,
        )
        if resp.status_code >= 300:
            log.error("Wati send failed %s: %s", resp.status_code, resp.text[:300])
    except requests.RequestException:
        log.exception("Wati send error")


def send_instagram_message(recipient_id, text):
    if not META_ACCESS_TOKEN:
        log.error("META_ACCESS_TOKEN not set; cannot reply on Instagram")
        return
    try:
        resp = requests.post(
            f"{META_GRAPH_URL}/me/messages",
            json={"recipient": {"id": recipient_id}, "message": {"text": text}},
            headers={"Authorization": f"Bearer {META_ACCESS_TOKEN}"},
            timeout=20,
        )
        if resp.status_code >= 300:
            log.error("Instagram send failed %s: %s", resp.status_code, resp.text[:300])
    except requests.RequestException:
        log.exception("Instagram send error")


# --------------------------------------------------------------------------
# Webhook plumbing
# --------------------------------------------------------------------------
_seen = OrderedDict()
_seen_lock = threading.Lock()


def already_processed(key):
    """Webhook providers retry; make sure each message is handled once."""
    if not key:
        return False
    with _seen_lock:
        if key in _seen:
            return True
        _seen[key] = True
        while len(_seen) > 2000:
            _seen.popitem(last=False)
    return False


def handle_in_background(channel, sender_id, text, sender_fn):
    def work():
        try:
            reply = generate_reply(channel, sender_id, text)
            sender_fn(sender_id, reply)
        except Exception:
            log.exception("Failed handling %s message", channel)

    threading.Thread(target=work, daemon=True).start()


def valid_meta_signature():
    if not META_APP_SECRET:
        return True
    signature = request.headers.get("X-Hub-Signature-256", "")
    expected = "sha256=" + hmac.new(META_APP_SECRET.encode(), request.get_data(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
@app.get("/")
def home():
    return f"{CLINIC_NAME} AI Agent is running successfully!", 200


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok"}), 200


@app.post("/whatsapp-webhook")
def whatsapp_webhook():
    """Wati -> new incoming WhatsApp message."""
    if WATI_WEBHOOK_SECRET and request.args.get("secret") != WATI_WEBHOOK_SECRET:
        return jsonify({"status": "forbidden"}), 403

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "message": "No JSON payload received"}), 400

    # Ignore our own / staff messages (owner=true) and non-text events to avoid reply loops.
    if data.get("owner") is True:
        return jsonify({"status": "ignored"}), 200
    if data.get("type") not in (None, "text"):
        return jsonify({"status": "ignored"}), 200

    wa_id = data.get("waId")
    text = (data.get("text") or "").strip()
    if not wa_id or not text:
        return jsonify({"status": "ignored"}), 200
    if already_processed(f"wa:{data.get('whatsappMessageId') or data.get('id')}"):
        return jsonify({"status": "duplicate"}), 200

    log.info("WhatsApp message from %s", wa_id)
    handle_in_background("whatsapp", str(wa_id), text, send_whatsapp_message)
    return jsonify({"status": "success"}), 200


@app.route("/webhook", methods=["GET", "POST"])
def instagram_webhook():
    """Meta -> Instagram DMs."""
    if request.method == "GET":
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge", "")
        if mode == "subscribe" and META_VERIFY_TOKEN and hmac.compare_digest(token or "", META_VERIFY_TOKEN):
            return challenge, 200
        if mode or token:
            return "Verification failed", 403
        return "Hello, this is the webhook endpoint.", 200

    if not valid_meta_signature():
        return "Invalid signature", 403

    data = request.get_json(silent=True) or {}
    for entry in data.get("entry", []):
        for event in entry.get("messaging", []):
            message = event.get("message") or {}
            if message.get("is_echo"):  # our own outgoing message
                continue
            text = (message.get("text") or "").strip()
            sender_id = (event.get("sender") or {}).get("id")
            if not text or not sender_id:
                continue
            if already_processed(f"ig:{message.get('mid')}"):
                continue
            log.info("Instagram message from %s", sender_id)
            handle_in_background("instagram", str(sender_id), text, send_instagram_message)
    return "EVENT_RECEIVED", 200


@app.get("/view-bookings")
def view_bookings():
    """Staff-only list of bookings. Needs ADMIN_KEY (header X-Admin-Key or ?key=)."""
    supplied = request.headers.get("X-Admin-Key") or request.args.get("key", "")
    if not ADMIN_KEY or not hmac.compare_digest(supplied, ADMIN_KEY):
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    try:
        with closing(db()) as conn:
            rows = conn.execute("SELECT * FROM bookings ORDER BY id DESC").fetchall()
        return jsonify({"bookings": [dict(r) for r in rows]}), 200
    except Exception as e:
        log.exception("view-bookings failed")
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    try:
        from waitress import serve  # production-grade server

        log.info("Serving on 0.0.0.0:%s with waitress", port)
        serve(app, host="0.0.0.0", port=port, threads=8)
    except ImportError:
        app.run(host="0.0.0.0", port=port)
