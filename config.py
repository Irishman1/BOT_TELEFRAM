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
    "1m": {"name": "1 місяць",  "days": 30,  "price": 5,  "currency": "EUR", "ls_variant_id": os.getenv("LS_VARIANT_1M", "")},
    "3m": {"name": "3 місяці",  "days": 90,  "price": 15, "currency": "EUR", "ls_variant_id": os.getenv("LS_VARIANT_3M", "")},
    "6m": {"name": "6 місяців", "days": 180, "price": 30, "currency": "EUR", "ls_variant_id": os.getenv("LS_VARIANT_6M", "")},
}

# PayPal
PAYPAL_CLIENT_ID     = os.getenv("PAYPAL_CLIENT_ID", "")
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "")
PAYPAL_MODE          = os.getenv("PAYPAL_MODE", "sandbox")

# Lemon Squeezy
LEMONSQUEEZY_API_KEY        = os.getenv("LEMONSQUEEZY_API_KEY", "")
LEMONSQUEEZY_STORE_ID       = os.getenv("LEMONSQUEEZY_STORE_ID", "")
LEMONSQUEEZY_WEBHOOK_SECRET = os.getenv("LEMONSQUEEZY_WEBHOOK_SECRET", "")

# Платіжний провайдер: "paypal" або "lemonsqueezy"
PAYMENT_PROVIDER = os.getenv("PAYMENT_PROVIDER", "paypal")

# Bot
BOT_USERNAME = os.getenv("BOT_USERNAME", "")
