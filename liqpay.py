import base64
import hashlib
import json
import time
from config import LIQPAY_PUBLIC_KEY, LIQPAY_PRIVATE_KEY, SERVER_URL, WEBHOOK_PATH


def _sign(data: str) -> str:
    return base64.b64encode(
        hashlib.sha1((LIQPAY_PRIVATE_KEY + data + LIQPAY_PRIVATE_KEY).encode()).digest()
    ).decode()


def create_payment_url(order_id: str, amount: int, description: str, currency: str = "UAH") -> str:
    params = {
        "version": "3",
        "public_key": LIQPAY_PUBLIC_KEY,
        "action": "pay",
        "amount": str(amount),
        "currency": currency,
        "description": description,
        "order_id": order_id,
        "result_url": "https://t.me/" + "your_bot_username",  # заменить на @username бота
        "server_url": SERVER_URL + WEBHOOK_PATH,
        "language": "uk",
    }
    data = base64.b64encode(json.dumps(params).encode()).decode()
    signature = _sign(data)
    return f"https://www.liqpay.ua/api/3/checkout?data={data}&signature={signature}"


def verify_webhook(data: str, signature: str) -> bool:
    """Проверяем подпись webhook от LiqPay"""
    expected = _sign(data)
    return expected == signature


def decode_webhook(data: str) -> dict:
    """Декодируем данные webhook"""
    return json.loads(base64.b64decode(data).decode())


def make_order_id(tg_id: int, plan: str) -> str:
    return f"sub_{tg_id}_{plan}_{int(time.time())}"
