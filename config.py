import os
from dotenv import load_dotenv

load_dotenv()

# Telegram
BOT_TOKEN = os.getenv("BOT_TOKEN", "your_bot_token_here")
GROUP_ID = int(os.getenv("GROUP_ID", "-100xxxxxxxxx"))
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "123456789").split(",")))

# Server
SERVER_URL = os.getenv("SERVER_URL", "https://your-app.railway.app")
WEBHOOK_PATH = "/webhook/liqpay"

# Група
GROUP_NAME = "Назва вашої групи"
GROUP_DESCRIPTION = "Опис — що отримає учасник"

# Тарифи
PLANS = {
    "1m": {"name": "1 місяць",  "days": 30,  "price": 5,  "currency": "EUR"},
    "3m": {"name": "3 місяці",  "days": 90,  "price": 13, "currency": "EUR"},
    "6m": {"name": "6 місяців", "days": 180, "price": 25, "currency": "EUR"},
}

# PayPal
PAYPAL_CLIENT_ID     = os.getenv("PAYPAL_CLIENT_ID", "")
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "")
PAYPAL_MODE          = os.getenv("PAYPAL_MODE", "sandbox")

# Bot
BOT_USERNAME = os.getenv("BOT_USERNAME", "")
