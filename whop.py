import aiohttp
import base64
import hashlib
import hmac
import os

WHOP_API_KEY        = os.getenv("WHOP_API_KEY", "")
WHOP_WEBHOOK_SECRET  = os.getenv("WHOP_WEBHOOK_SECRET", "")

BASE_URL = "https://api.whop.com/api/v1"


async def create_checkout(plan_id: str, order_id: str, tg_id: int, plan_key: str, redirect_url: str = None) -> dict:
    """Створює checkout-сесію Whop і повертає {"id":..., "url":...}"""
    if not WHOP_API_KEY:
        raise Exception("Whop: не задано WHOP_API_KEY")
    if not plan_id:
        raise Exception(f"Whop: не задано plan_id для тарифу {plan_key}")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {WHOP_API_KEY}",
    }

    payload = {
        "mode": "payment",
        "plan_id": plan_id,
        "metadata": {
            "order_id": order_id,
            "tg_id": str(tg_id),
            "plan": plan_key,
        },
    }
    if redirect_url:
        payload["redirect_url"] = redirect_url

    async with aiohttp.ClientSession() as s:
        r = await s.post(f"{BASE_URL}/checkout_configurations", headers=headers, json=payload)
        data = await r.json()
        if "id" not in data:
            raise Exception(f"Whop error: {data}")
        purchase_url = data.get("purchase_url", "")
        if purchase_url.startswith("/"):
            purchase_url = f"https://whop.com{purchase_url}"
        return {
            "id": data["id"],
            "url": purchase_url,
        }


def verify_signature(raw_body: bytes, webhook_id: str, timestamp: str, signature: str) -> bool:
    """Перевіряє підпис вебхука Whop (Standard Webhooks: webhook-id/-timestamp/-signature)"""
    if not WHOP_WEBHOOK_SECRET or not signature or not webhook_id or not timestamp:
        return False

    secret = WHOP_WEBHOOK_SECRET
    if secret.startswith("whsec_"):
        secret_bytes = base64.b64decode(secret[len("whsec_"):] + "==")
    elif secret.startswith("ws_"):
        secret_bytes = bytes.fromhex(secret[len("ws_"):])
    else:
        secret_bytes = base64.b64decode(secret + "==")

    signed_content = f"{webhook_id}.{timestamp}.{raw_body.decode('utf-8')}"
    expected_sig = hmac.new(secret_bytes, signed_content.encode("utf-8"), hashlib.sha256).digest()

    for part in signature.split(" "):
        version, _, sig_b64 = part.partition(",")
        if version != "v1" or not sig_b64:
            continue
        try:
            sig_bytes = base64.b64decode(sig_b64)
        except Exception:
            continue
        if hmac.compare_digest(expected_sig, sig_bytes):
            return True
    return False
