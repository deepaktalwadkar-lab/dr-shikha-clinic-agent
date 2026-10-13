import os
import json
import random
import sqlite3
import requests
from flask import Flask, request, jsonify
from google import genai
from google.genai import types

app = Flask(__name__)

# Initialize Gemini Client
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

# Wati Configuration Variables
WATI_API_ENDPOINT = os.environ.get("WATI_API_ENDPOINT")
WATI_BEARER_TOKEN = os.environ.get("WATI_BEARER_TOKEN")

# -------------------------------------------------------------------
# DATABASE SETUP (SQLite)
# -------------------------------------------------------------------
def init_db():
    conn = sqlite3.connect("clinic_database.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_number TEXT,
            patient_name TEXT,
            patient_phone TEXT,
            booking_datetime TEXT,
            payment_status TEXT DEFAULT 'Pending'
        )
    """)
    conn.commit()
    conn.close()

init_db()

# -------------------------------------------------------------------
# TOOL / FUNCTION: Book Token & Save to SQL Database
# -------------------------------------------------------------------
def book_clinic_token(patient_name: str, patient_phone: str, preferred_datetime: str) -> str:
    token_number = f"TOKEN-#{random.randint(100, 999)}"
    payment_link = f"https://upipay.me/drshikhaclinic/200?ref={token_number}"
    
    try:
        conn = sqlite3.connect("clinic_database.db")
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO bookings (token_number, patient_name, patient_phone, booking_datetime) VALUES (?, ?, ?, ?)",
            (token_number, patient_name, patient_phone, preferred_datetime)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[SQL ERROR]: {e}")

    booking_record = {
        "status": "SUCCESS",
        "clinic": "Dr. Shikha's Clinic",
        "location": "Bidar near JJ Tower",
        "consultation_fee": "₹200",
        "token_number": token_number,
        "patient_name": patient_name,
        "patient_phone": patient_phone,
        "booking_datetime": preferred_datetime,
        "upi_payment_link": payment_link
    }
    return json.dumps(booking_record)

system_instruction = """
You are the official AI Medical Receptionist for "Dr. Shikha's Clinic" located in Bidar near JJ Tower.
Clinic Details:
- Doctor Type: General Physician
- Operating Hours: Morning 10:00 AM to 12:00 PM (Midnight), 7 days a week
- Consultation Fee: ₹200 (payable online via UPI link or cash at counter)

Instructions:
1. Help patients with operating hours, fee structure, location, and general inquiries.
2. When a patient wants to book a token/appointment, you MUST collect: Full Name, Mobile Phone Number, Preferred Booking Date & Time.
3. Once all 3 fields are provided, immediately call the tool `book_clinic_token`.
4. After the tool returns success, respond directly to the patient on WhatsApp with their Token Number and UPI Payment Link.
5. Keep responses concise, polite, and clear for mobile reading. Never offer medical advice.
"""

config = types.GenerateContentConfig(
    tools=[book_clinic_token],
    system_instruction=system_instruction,
    temperature=0.2,
)

@app.route("/", methods=["GET"])
def home():
    return "Dr. Shikha's Clinic Agent with SQL Database is Active!"

@app.route("/whatsapp-webhook", methods=["POST"])
def whatsapp_webhook():
    data = request.json
    try:
        sender_phone = data.get("waId") or data.get("phone")
        user_message = data.get("text") or data.get("message", {}).get("text")

        if sender_phone and user_message:
            contents = [{"role": "user", "parts": [{"text": user_message}]}]
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=contents,
                config=config
            )
            
            ai_reply = ""
            if response.function_calls:
                for call in response.function_calls:
                    if call.name == "book_clinic_token":
                        tool_result_str = book_clinic_token(**call.args)
                        tool_msg = types.Part.from_function_response(
                            name=call.name,
                            response={"result": json.loads(tool_result_str)}
                        )
                        contents.append({"role": "model", "parts": response.candidates[0].content.parts})
                        contents.append({"role": "user", "parts": [tool_msg]})
                        
                        final_response = client.models.generate_content(
                            model="gemini-2.5-flash",
                            contents=contents,
                            config=config
                        )
                        ai_reply = final_response.text
            else:
                ai_reply = response.text

            if ai_reply:
                send_wati_message(sender_phone, ai_reply)

    except Exception as e:
        print(f"Error handling webhook: {e}")

    return jsonify({"status": "processed"}), 200

def send_wati_message(phone_number: str, message_text: str):
    if not WATI_API_ENDPOINT or not WATI_BEARER_TOKEN:
        return
    encoded_message = requests.utils.quote(message_text)
    url = f"{WATI_API_ENDPOINT}/sendSessionMessage/{phone_number}?messageText={encoded_message}"
    headers = {
        "Authorization": f"Bearer {WATI_BEARER_TOKEN}",
        "Content-Type": "application/json"
    }
    requests.post(url, headers=headers)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))