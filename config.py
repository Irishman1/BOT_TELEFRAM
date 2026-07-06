import os
from dotenv import load_dotenv

load_dotenv()

# Telegram
BOT_TOKEN = os.getenv("BOT_TOKEN", "your_bot_token_here")
GROUP_ID = int(os.getenv("GROUP_ID", "-100xxxxxxxxx"))
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "123456789").split(",")))
WEEKLY_ADMIN_IDS = [int(x) for x in os.getenv("WEEKLY_ADMIN_IDS", "").split(",") if x.strip()]

# Server
SERVER_URL = os.getenv("SERVER_URL", "https://your-app.railway.app")
WEBHOOK_PATH = "/webhook/liqpay"

# Группа
GROUP_NAME = "Сообщество для родителей детей с речевыми трудностями"

# Тарифи
PLANS = {
    "1m": {"name": "1 месяц",   "days": 30,  "price": 5,  "currency": "EUR", "ls_variant_id": os.getenv("LS_VARIANT_1M", "")},
    "3m": {"name": "3 месяца",  "days": 90,  "price": 15, "currency": "EUR", "ls_variant_id": os.getenv("LS_VARIANT_3M", "")},
    "6m": {"name": "6 месяцев", "days": 180, "price": 30, "currency": "EUR", "ls_variant_id": os.getenv("LS_VARIANT_6M", "")},
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

# Приветственное сообщение после оплаты
WELCOME_TEXT = (
    "👋 Добро пожаловать!\n\n"
    "Вы вошли в наше сообщество для родителей детей с речевыми трудностями.\n\n"
    "Здесь собраны знания, которые помогут лучше понимать своего ребенка и поддерживать его развитие каждый день.\n\n"
    "Что вас ждет внутри:\n\n"
    "📚 Библиотека знаний по развитию речи, коммуникации и пониманию речи.\n\n"
    "🤖 ИИ-помощник, который поможет быстро найти упражнения, рекомендации и материалы по вашему запросу.\n\n"
    "🎥 Видеоразборы и практические примеры.\n\n"
    "🎙 Эфиры с ответами на вопросы родителей.\n\n"
    "📝 Домашние задания, игры и материалы для занятий.\n\n"
    "❤️ Поддержка и сообщество родителей, которые проходят похожий путь.\n\n"
    "Наша цель — сделать профессиональные знания понятными и доступными каждой семье.\n\n"
    "Начните с раздела «Библиотека знаний» или задайте свой вопрос ИИ-помощнику.\n\n"
    "Рады быть рядом на вашем пути к пониманию, коммуникации и речи вашего ребенка."
)
