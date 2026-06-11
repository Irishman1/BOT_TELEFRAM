import aiohttp
import hashlib
import hmac
import os

LEMONSQUEEZY_API_KEY        = os.getenv("LEMONSQUEEZY_API_KEY", "")
LEMONSQUEEZY_STORE_ID       = os.getenv("LEMONSQUEEZY_STORE_ID", "")
LEMONSQUEEZY_WEBHOOK_SECRET = os.getenv("LEMONSQUEEZY_WEBHOOK_SECRET", "")

BASE_URL = "https://api.lemonsqueezy.com/v1"


async def create_checkout(variant_id: str, order_id: str, tg_id: int, plan_key: str, redirect_url: str = None) -> dict:
    """Створює checkout-сесію Lemon Squeezy і повертає {"id":..., "url":...}"""
    if not LEMONSQUEEZY_API_KEY or not LEMONSQUEEZY_STORE_ID:
        raise Exception("Lemon Squeezy: не задані LEMONSQUEEZY_API_KEY / LEMONSQUEEZY_STORE_ID")
    if not variant_id:
        raise Exception(f"Lemon Squeezy: не задано variant_id для тарифу {plan_key}")

    headers = {
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
        "Authorization": f"Bearer {LEMONSQUEEZY_API_KEY}",
    }

    attributes = {
        "checkout_data": {
            "custom": {
                "order_id": order_id,
                "tg_id": str(tg_id),
                "plan": plan_key,
            }
        },
    }
    if redirect_url:
        attributes["product_options"] = {"redirect_url": redirect_url}

    payload = {
        "data": {
            "type": "checkouts",
            "attributes": attributes,
            "relationships": {
                "store": {"data": {"type": "stores", "id": str(LEMONSQUEEZY_STORE_ID)}},
                "variant": {"data": {"type": "variants", "id": str(variant_id)}},
            }
        }
    }

    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{BASE_URL}/checkouts", headers=headers, json=payload)
        data = await r.json()
        if "data" not in data:
            raise Exception(f"Lemon Squeezy error: {data.get('errors')}")
        return {
            "id": data["data"]["id"],
            "url": data["data"]["attributes"]["url"],
        }


def verify_signature(raw_body: bytes, signature: str) -> bool:
    """Перевіряє підпис вебхука Lemon Squeezy (заголовок X-Signature)"""
    if not LEMONSQUEEZY_WEBHOOK_SECRET or not signature:
        return False
    digest = hmac.new(LEMONSQUEEZY_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature)
