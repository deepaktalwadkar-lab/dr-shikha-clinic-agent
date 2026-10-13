import os
import sqlite3
from flask import Flask, request, jsonify
import google.generativeai as genai

app = Flask(__name__)

# Configure Gemini API Key from environment variables
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

# 1. Database helper function and Tool for Gemini
def book_clinic_token(patient_name: str, phone_number: str, preferred_time: str) -> dict:
    """
    Book a token for a patient at Dr. Shikha's Clinic and save it to the SQLite database.
    
    Args:
        patient_name: Name of the patient.
        phone_number: Contact phone number of the patient.
        preferred_time: Preferred time or date for consultation.
    """
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    
    # Create bookings table if it doesn't exist
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_name TEXT,
            phone_number TEXT,
            preferred_time TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        INSERT INTO bookings (patient_name, phone_number, preferred_time)
        VALUES (?, ?, ?)
    ''', (patient_name, phone_number, preferred_time))
    
    booking_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    token_number = f"TOKEN-#{booking_id + 100}"
    print(f"Executing tool: book_clinic_token | Assigned {token_number} for {patient_name}")
    
    return {
        "status": "success",
        "token_number": token_number,
        "consultation_fee": "₹200",
        "payment_link": "https://upi.link/pay-200-dr-shikha",
        "clinic_location": "Bidar near JJ Tower",
        "timings": "10:00 AM to 12:00 AM"
    }

# Configure Gemini Model with the tool
model = genai.GenerativeModel(
    model_name='gemini-1.5-flash',
    tools=[book_clinic_token],
    system_instruction=(
        "You are the AI receptionist for Dr. Shikha's Clinic located in Bidar near JJ Tower. "
        "Operating hours are 10:00 AM to 12:00 AM. Consultation fee is ₹200. "
        "Help patients inquire about timings and book token numbers by collecting their name, phone number, and preferred time, "
        "then call the book_clinic_token tool."
    )
)

@app.route('/', methods=['GET'])
def home():
    return "Dr. Shikha's Clinic AI Agent is running successfully!", 200

@app.route('/whatsapp-webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "No JSON payload received"}), 400

    # Extract sender ID (handles WhatsApp waId or Instagram sender.id)
    sender = data.get('waId') or data.get('sender', {}).get('id') or data.get('from')
    
    # Extract incoming message text
    message_text = data.get('text') or data.get('message', {}).get('text')
    
    if message_text and sender:
        try:
            # Start chat session and generate response
            chat = model.start_chat(enable_automatic_function_calling=True)
            response = chat.send_message(message_text)
            reply_text = response.text
            
            # TODO: Call Wati/Instagram Send API here using sender and reply_text
            print(f"User ({sender}): {message_text}")
            print(f"AI Reply: {reply_text}")
            
        except Exception as e:
            print(f"Error processing AI response: {str(e)}")
            
    return jsonify({"status": "success"}), 200

@app.route('/view-bookings', methods=['GET'])
def view_bookings():
    try:
        conn = sqlite3.connect('clinic.db')
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM bookings")
        data = cursor.fetchall()
        conn.close()
        return jsonify({"bookings": data}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))


@app.route('/view-bookings', methods=['GET'])
def view_bookings():
    try:
        conn = sqlite3.connect('clinic.db')
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM bookings")
        data = cursor.fetchall()
        conn.close()
        return jsonify({"bookings": data}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
