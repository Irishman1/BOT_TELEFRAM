import os
from dotenv import load_dotenv

load_dotenv()

# Telegram
BOT_TOKEN = os.getenv("BOT_TOKEN", "your_bot_token_here")
GROUP_ID = int(os.getenv("GROUP_ID", "-100xxxxxxxxx"))
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "123456789").split(",")))
EXTRA_ADMIN_IDS = [int(x) for x in os.getenv("EXTRA_ADMIN_IDS", "").split(",") if x.strip()]
ALL_ADMIN_IDS = list(dict.fromkeys(ADMIN_IDS + EXTRA_ADMIN_IDS))


def encode_id(tg_id: int) -> str:
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    result = ""
    n = tg_id
    while n:
        result = chars[n % 36] + result
        n //= 36
    return result or "0"


def decode_id(code: str):
    try:
        return int(code.upper(), 36)
    except (ValueError, AttributeError):
        return None

# Server
SERVER_URL = os.getenv("SERVER_URL", "https://your-app.railway.app")
WEBHOOK_PATH = "/webhook/liqpay"

# Группа
GROUP_NAME = "Сообщество для родителей детей с речевыми трудностями"

# Тарифы
PLANS = {
    "basic":    {"name": "Базовый пакет",    "days": 30, "price": 14.99, "currency": "EUR", "description": "Закрытое Telegram-сообщество для родителей. Доступ на 30 дней", "ls_variant_id": os.getenv("LS_VARIANT_1M", ""), "whop_plan_id": os.getenv("WHOP_PLAN_BASIC", ""), "whop_product_id": os.getenv("WHOP_PRODUCT_BASIC", "")},
    "standard": {"name": "Стандартный пакет","days": 30, "price": 24.99, "currency": "EUR", "description": "Всё из Базового + индивидуальный разбор и личная онлайн-встреча", "ls_variant_id": os.getenv("LS_VARIANT_3M", ""), "whop_plan_id": os.getenv("WHOP_PLAN_STANDARD", ""), "whop_product_id": os.getenv("WHOP_PRODUCT_STANDARD", "")},
}

WHOP_COMPANY_ID = os.getenv("WHOP_COMPANY_ID", "")

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
    "Что вас ждет в сообществе!\n\n"
    "1. 🎥 Практические видеоуроки\n"
    "Полезные видео с рекомендациями, упражнениями и разбором различных ситуаций.\n\n"
    "2. 📚 Авторская методика Тойтерапия\n"
    "Подходы, игры и упражнения, которые мы используем в своей практике.\n\n"
    "3. 🎙 Закрытые эфиры\n"
    "Прямые эфиры с разбором актуальных тем и ответами на вопросы участников.\n\n"
    "4. 💬 Ответы на ваши вопросы\n"
    "Мы регулярно разбираем вопросы участников сообщества в формате текста, видео и эфиров.\n\n"
    "5. 📖 Полезные материалы\n"
    "Игры, чек-листы, памятки, статьи, рекомендации и другие материалы для самостоятельной работы с ребенком.\n\n"
    "6. Интенсивы каждые 3 мес\n"
    "Практика для родителей и детей на самые актуальные темы по развитию речи.\n\n"
    "📌 Полное описание возможностей сообщества и всех материалов доступно по ссылке ниже.\n"
    "Спасибо, что вы с нами! ❤️\n\n"
    '<a href="https://drive.google.com/uc?export=download&id=1dk4IKP_BuNyZFr-0kBeAh9QhUFchl22D">Более детальная информация</a>'
)
