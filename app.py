import os
import sqlite3
from flask import Flask, request, jsonify
import google.generativeai as genai

app = Flask(__name__)
def init_db():
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_name TEXT NOT NULL,
            phone_number TEXT NOT NULL,
            preferred_time TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

# Run database setup on startup
init_db()

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
    import requests

VERIFY_TOKEN = "drshikhaclinic_secure_token" # You can choose any password string here
ACCESS_TOKEN = "YOUR_META_INSTAGRAM_ACCESS_TOKEN" # From your Meta Developer Dashboard
PHONE_NUMBER_ID = "YOUR_PHONE_OR_INSTAGRAM_ACCOUNT_ID"

@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        # Meta Webhook Verification
        mode = request.args.get('hub.mode')
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')
        
        if mode and token:
            if mode == 'subscribe' and token == VERIFY_TOKEN:
                return challenge, 200
            else:
                return "Verification failed", 403
        return "Hello, this is the webhook endpoint.", 200

    elif request.method == 'POST':
        data = request.json
        try:
            # Parse incoming Instagram/Messenger message
            messaging_event = data['entry'][0]['messaging'][0]
            if 'message' in messaging_event and 'text' in messaging_event['message']:
                sender_id = messaging_event['sender']['id']
                message_text = messaging_event['message']['text']
                
                # Configure Gemini model with the booking tool
                model = genai.GenerativeModel(
                    model_name='gemini-1.5-flash',
                    tools=[book_clinic_token],
                    system_instruction="You are a polite AI receptionist for Dr. Shikha's Clinic in Bidar. Help patients book tokens using the book_clinic_token tool."
                )
                
                # Get response from Gemini
                response = model.generate_content(message_text)
                reply_text = response.text
                
                # Send reply back to user via Instagram API
                send_instagram_message(sender_id, reply_text)
                
        except Exception as e:
            print(f"Error processing webhook: {e}")
            
        return "EVENT_RECEIVED", 200

def send_instagram_message(recipient_id, message_text):
    url = f"https://graph.facebook.com/v18.0/me/messages?access_token={ACCESS_TOKEN}"
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": message_text}
    }
    headers = {"Content-Type": "application/json"}
    requests.post(url, json=payload, headers=headers)
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))

@app.route('/view-bookings', methods=['GET'])
def view_bookings():
    try:
        import sqlite3
        conn = sqlite3.connect('clinic.db')
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM bookings")
        data = cursor.fetchall()
        conn.close()
        return jsonify({"bookings": data}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__'):
   import requests

VERIFY_TOKEN = "drshikhaclinic_secure_token" # You can choose any password string here
ACCESS_TOKEN = "YOUR_META_INSTAGRAM_ACCESS_TOKEN" # From your Meta Developer Dashboard
PHONE_NUMBER_ID = "YOUR_PHONE_OR_INSTAGRAM_ACCOUNT_ID"

@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        # Meta Webhook Verification
        mode = request.args.get('hub.mode')
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')
        
        if mode and token:
            if mode == 'subscribe' and token == VERIFY_TOKEN:
                return challenge, 200
            else:
                return "Verification failed", 403
        return "Hello, this is the webhook endpoint.", 200

    elif request.method == 'POST':
        data = request.json
        try:
            # Parse incoming Instagram/Messenger message
            messaging_event = data['entry'][0]['messaging'][0]
            if 'message' in messaging_event and 'text' in messaging_event['message']:
                sender_id = messaging_event['sender']['id']
                message_text = messaging_event['message']['text']
                
                # Configure Gemini model with the booking tool
                model = genai.GenerativeModel(
                    model_name='gemini-1.5-flash',
                    tools=[book_clinic_token],
                    system_instruction="You are a polite AI receptionist for Dr. Shikha's Clinic in Bidar. Help patients book tokens using the book_clinic_token tool."
                )
                
                # Get response from Gemini
                response = model.generate_content(message_text)
                reply_text = response.text
                
                # Send reply back to user via Instagram API
                send_instagram_message(sender_id, reply_text)
                
        except Exception as e:
            print(f"Error processing webhook: {e}")
            
        return "EVENT_RECEIVED", 200

def send_instagram_message(recipient_id, message_text):
    url = f"https://graph.facebook.com/v18.0/me/messages?access_token={ACCESS_TOKEN}"
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": message_text}
    }
    headers = {"Content-Type": "application/json"}
    requests.post(url, json=payload, headers=headers)
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))


