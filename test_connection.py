import os
import razorpay
from dotenv import load_dotenv

load_dotenv()

client = razorpay.Client(auth=(
    os.getenv("RAZORPAY_KEY_ID"),
    os.getenv("RAZORPAY_KEY_SECRET")
))

order = client.order.create({
    "amount": 3000,
    "currency": "INR",
    "receipt": "test-001"
})

print("Order created:", order["id"])
print("Amount:", order["amount"] / 100, "INR")
