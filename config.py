import os
from dotenv import load_dotenv

load_dotenv()

# Telegram
BOT_TOKEN = os.getenv("BOT_TOKEN", "your_bot_token_here")
GROUP_ID = int(os.getenv("GROUP_ID", "-100xxxxxxxxx"))  # ID закритої групи (супергрупи)
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "123456789").split(",")))

# LiqPay (залишаємо на майбутнє, коли зробиш платним)
LIQPAY_PUBLIC_KEY = os.getenv("LIQPAY_PUBLIC_KEY", "")
LIQPAY_PRIVATE_KEY = os.getenv("LIQPAY_PRIVATE_KEY", "")
SERVER_URL = os.getenv("SERVER_URL", "https://your-app.railway.app")
WEBHOOK_PATH = "/webhook/liqpay"

# Група
GROUP_NAME = "Назва вашої групи"
GROUP_DESCRIPTION = "Опис — що отримає учасник"

# Режим: True = безкоштовно (зараз), False = платно
FREE_ACCESS = True
