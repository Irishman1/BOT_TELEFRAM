import aiohttp
import base64
import os

PAYPAL_CLIENT_ID     = os.getenv("PAYPAL_CLIENT_ID", "")
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "")
PAYPAL_MODE          = os.getenv("PAYPAL_MODE", "sandbox")  # sandbox або live

BASE_URL = "https://api-m.sandbox.paypal.com" if PAYPAL_MODE == "sandbox" else "https://api-m.paypal.com"


async def get_access_token() -> str:
    credentials = base64.b64encode(f"{PAYPAL_CLIENT_ID}:{PAYPAL_CLIENT_SECRET}".encode()).decode()
    async with aiohttp.ClientSession() as s:
        r = await s.post(
            f"{BASE_URL}/v1/oauth2/token",
            headers={"Authorization": f"Basic {credentials}", "Content-Type": "application/x-www-form-urlencoded"},
            data="grant_type=client_credentials"
        )
        data = await r.json()
        return data["access_token"]


async def create_order(amount: float, currency: str, order_id: str, return_url: str, cancel_url: str) -> dict:
    token = await get_access_token()
    async with aiohttp.ClientSession() as s:
        r = await s.post(
            f"{BASE_URL}/v2/checkout/orders",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "intent": "CAPTURE",
                "purchase_units": [{
                    "reference_id": order_id,
                    "amount": {
                        "currency_code": currency,
                        "value": f"{amount:.2f}"
                    },
                    "description": "Підписка на групу"
                }],
                "application_context": {
                    "return_url": return_url,
                    "cancel_url": cancel_url,
                    "brand_name": "Telegram Bot",
                    "user_action": "PAY_NOW"
                }
            }
        )
        data = await r.json()
        approve_url = next((l["href"] for l in data.get("links", []) if l["rel"] == "approve"), None)
        return {"id": data.get("id"), "approve_url": approve_url}


async def capture_order(paypal_order_id: str) -> bool:
    token = await get_access_token()
    async with aiohttp.ClientSession() as s:
        r = await s.post(
            f"{BASE_URL}/v2/checkout/orders/{paypal_order_id}/capture",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={}
        )
        data = await r.json()
        return data.get("status") == "COMPLETED"
