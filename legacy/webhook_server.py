"""
TradingView Webhook Alert Server
Receives alerts from TradingView and forwards them via Gmail email.
"""

import os
import smtplib
import json
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Config from .env
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
ALERT_TO_EMAIL = os.getenv("ALERT_TO_EMAIL", GMAIL_USER)
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")  # Optional: basic auth token
PORT = int(os.getenv("PORT", 5000))


def send_email(subject: str, body: str):
    """Send an alert email via Gmail SMTP."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    msg["To"] = ALERT_TO_EMAIL

    # Plain text fallback
    text_part = MIMEText(body, "plain")

    # HTML version
    html_body = f"""
    <html><body style="font-family: Arial, sans-serif; padding: 20px;">
        <h2 style="color: #e67e22;">📈 TradingView Alert</h2>
        <div style="background: #f4f4f4; padding: 15px; border-radius: 8px; white-space: pre-wrap;">
{body}
        </div>
        <p style="color: #999; font-size: 12px;">Received at {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}</p>
    </body></html>
    """
    html_part = MIMEText(html_body, "html")

    msg.attach(text_part)
    msg.attach(html_part)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, ALERT_TO_EMAIL, msg.as_string())

    logging.info(f"Email sent: {subject}")


@app.route("/webhook", methods=["POST"])
def webhook():
    # Optional secret check
    if WEBHOOK_SECRET:
        token = request.headers.get("X-Webhook-Secret", "")
        if token != WEBHOOK_SECRET:
            logging.warning("Unauthorized webhook attempt")
            return jsonify({"error": "Unauthorized"}), 401

    # TradingView sends plain text or JSON
    content_type = request.content_type or ""
    if "application/json" in content_type:
        data = request.get_json(silent=True) or {}
        alert_message = data.get("message", json.dumps(data, indent=2))
        ticker = data.get("ticker", "Unknown")
        exchange = data.get("exchange", "")
    else:
        alert_message = request.data.decode("utf-8").strip()
        ticker = "Crypto"
        exchange = ""

    if not alert_message:
        return jsonify({"error": "Empty payload"}), 400

    ticker_label = f"{exchange}:{ticker}" if exchange else ticker
    subject = f"🚨 Alert: {ticker_label} — {datetime.now().strftime('%H:%M UTC')}"

    try:
        send_email(subject, alert_message)
        logging.info(f"Alert processed for {ticker_label}")
        return jsonify({"status": "ok", "message": "Alert sent"}), 200
    except Exception as e:
        logging.error(f"Failed to send email: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "running", "time": datetime.utcnow().isoformat()}), 200


if __name__ == "__main__":
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        raise RuntimeError("GMAIL_USER and GMAIL_APP_PASSWORD must be set in .env")
    logging.info(f"Starting webhook server on port {PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False)
