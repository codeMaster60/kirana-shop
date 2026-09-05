import os, json, hmac, hashlib
from datetime import datetime
from flask import Flask, request
from dotenv import load_dotenv

load_dotenv()
SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
app = Flask(__name__)

def log(action, detail):
    with open("audit.log", "a") as f:
        f.write(f"{datetime.now().isoformat()}\t{action}\t{detail}\n")
        f.flush()

@app.route("/webhook", methods=["POST"])
def webhook():
    body = request.get_data()
    sig = request.headers.get("X-Razorpay-Signature", "")
    expected = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, sig):
        log("webhook_rejected", "bad signature")
        return "invalid", 400

    event = json.loads(body)
    kind = event.get("event")
    pay = event.get("payload", {}).get("payment", {}).get("entity", {})
    log("webhook", f"{kind} id={pay.get('id')} amount={pay.get('amount',0)/100}")
    print(f"  [webhook] {kind} - Rs.{pay.get('amount',0)/100}")
    return "ok", 200

if __name__ == "__main__":
    app.run(port=5001)
