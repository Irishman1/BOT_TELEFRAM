import logging
from pathlib import Path
from aiohttp import web
from aiogram import Bot
from datetime import datetime, timedelta

from config import PLANS, GROUP_ID, WEBHOOK_PATH, BOT_USERNAME
from database import (
    get_pending_order, delete_pending_order,
    payment_exists, save_payment,
    activate_subscription, save_invite_link,
    use_promo,
)

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent.parent / "static"


async def serve_pay_page(request: web.Request) -> web.Response:
    html = (STATIC_DIR / "pay.html").read_text(encoding="utf-8")
    return web.Response(text=html, content_type="text/html")


async def _finalize_order(bot: Bot, order: dict, paypal_token: str) -> bool:
    """Зараховує оплату: підписка + інвайт + повідомлення. Повертає True при успіху."""
    order_id   = order["order_id"]
    tg_id      = order["tg_id"]
    plan_key   = order["plan"]
    promo_code = order.get("promo_code")
    plan       = PLANS.get(plan_key)
    if not plan:
        return False

    price = plan["price"]
    if promo_code:
        from database import get_promo
        promo = await get_promo(promo_code)
        if promo:
            price = round(price * (100 - promo["discount"]) / 100, 2)
            await use_promo(promo_code)

    await save_payment(order_id, tg_id, price, plan_key, "success",
                        paypal_order_id=paypal_token, promo_code=promo_code)
    await delete_pending_order(order_id)
    new_expiry = await activate_subscription(tg_id, plan_key, plan["days"])

    # Генеруємо одноразову ссилку на 15 хвилин
    expires_at = datetime.now() + timedelta(minutes=15)
    try:
        invite = await bot.create_chat_invite_link(
            chat_id=GROUP_ID, member_limit=1, expire_date=expires_at
        )
        await save_invite_link(tg_id, invite.invite_link, expires_at)
        link = invite.invite_link
    except Exception as e:
        logger.error(f"Invite link error: {e}")
        link = None

    # Надсилаємо в Telegram
    try:
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        kb = InlineKeyboardBuilder()
        msg = (
            f"✅ <b>Оплата через PayPal успішна!</b>\n\n"
            f"📦 Тариф: {plan['name']}\n"
            f"💰 Сплачено: {price} €\n"
            f"📅 Підписка до: <b>{new_expiry.strftime('%d.%m.%Y')}</b>\n"
        )
        if link:
            msg += (
                f"\n\n🔗 <b>Твоє посилання на групу:</b>\n{link}\n\n"
                f"⏱ <b>Діє лише 15 хвилин!</b>\n"
                f"⚠️ Одноразове — тільки для тебе. Не передавай нікому."
            )
            kb.button(text="👥 Приєднатися до групи", url=link)
        await bot.send_message(tg_id, msg, parse_mode="HTML",
                               reply_markup=kb.as_markup() if link else None)
    except Exception as e:
        logger.error(f"Telegram message error: {e}")

    logger.info(f"PayPal payment captured: order={order_id} tg_id={tg_id}")
    return True


async def paypal_success(request: web.Request) -> web.Response:
    """Юзер повернувся після оплати PayPal"""
    bot: Bot = request.app["bot"]
    order_id      = request.rel_url.query.get("order", "")
    paypal_token  = request.rel_url.query.get("token", "")  # PayPal order ID

    if not order_id or not paypal_token:
        return web.Response(text="Bad request", status=400)

    if await payment_exists(order_id):
        return web.Response(text=_success_html(), content_type="text/html")

    order = await get_pending_order(order_id)
    if not order:
        return web.Response(text=_error_html(), content_type="text/html")

    tg_id = order["tg_id"]

    # Capture PayPal payment
    from paypal import capture_order
    try:
        captured = await capture_order(paypal_token)
    except Exception as e:
        logger.error(f"PayPal capture error: {e}")
        await _notify_payment_failed(bot, tg_id)
        return web.Response(text=_error_html(), content_type="text/html")

    if not captured:
        await _notify_payment_failed(bot, tg_id)
        return web.Response(text=_error_html(), content_type="text/html")

    ok = await _finalize_order(bot, order, paypal_token)
    if not ok:
        return web.Response(text=_error_html(), content_type="text/html")

    return web.Response(text=_success_html(), content_type="text/html")


async def paypal_webhook(request: web.Request) -> web.Response:
    """Webhook від PayPal — підстраховка, якщо юзер закрив сторінку до редіректу"""
    bot: Bot = request.app["bot"]
    try:
        data = await request.json()
    except Exception:
        return web.Response(status=400)

    event_type = data.get("event_type", "")
    resource = data.get("resource", {})

    if event_type == "CHECKOUT.ORDER.APPROVED":
        paypal_order_id = resource.get("id", "")
        if not paypal_order_id:
            return web.Response(status=200)

        from database import get_pending_order_by_paypal_id
        order = await get_pending_order_by_paypal_id(paypal_order_id)
        if not order:
            return web.Response(status=200)

        if await payment_exists(order["order_id"]):
            return web.Response(status=200)

        from paypal import capture_order
        try:
            captured = await capture_order(paypal_order_id)
        except Exception as e:
            logger.error(f"Webhook capture error: {e}")
            return web.Response(status=200)

        if captured:
            await _finalize_order(bot, order, paypal_order_id)

    return web.Response(status=200)


async def _notify_payment_failed(bot: Bot, tg_id: int):
    """Повідомляємо юзера що оплата не пройшла, з можливістю спробувати ще раз"""
    try:
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        kb = InlineKeyboardBuilder()
        kb.button(text="🔄 Спробувати ще раз", callback_data="show_plans")
        await bot.send_message(
            tg_id,
            "❌ <b>Оплата не пройшла</b>\n\n"
            "Щось пішло не так під час обробки платежу. "
            "Спробуй ще раз або зверніться в підтримку.",
            parse_mode="HTML",
            reply_markup=kb.as_markup()
        )
    except Exception as e:
        logger.error(f"Failed to notify payment failure to {tg_id}: {e}")


async def paypal_cancel(request: web.Request) -> web.Response:
    """Юзер скасував оплату"""
    return web.Response(text=_cancel_html(), content_type="text/html")


def _success_html():
    return f"""<!DOCTYPE html><html lang="uk"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Оплата успішна</title>
<style>*{{box-sizing:border-box;margin:0;padding:0}}body{{font-family:sans-serif;background:#f5f6fa;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:16px}}.card{{background:#fff;border-radius:16px;padding:36px 28px;text-align:center;max-width:360px;width:100%;box-shadow:0 4px 24px rgba(0,0,0,.08)}}.icon{{font-size:52px;margin-bottom:16px}}h1{{font-size:22px;font-weight:700;color:#1a1a2e;margin-bottom:8px}}p{{font-size:15px;color:#6b7280;margin-bottom:24px}}a{{display:inline-block;background:#0070ba;color:#fff;padding:12px 28px;border-radius:10px;text-decoration:none;font-weight:600;font-size:15px}}</style></head>
<body><div class="card"><div class="icon">✅</div><h1>Оплату здійснено!</h1>
<p>Повернись до бота — він вже надіслав тобі посилання на групу.</p>
<a href="https://t.me/{BOT_USERNAME}">Повернутися до бота →</a></div></body></html>"""


def _error_html():
    return f"""<!DOCTYPE html><html lang="uk"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Помилка</title>
<style>*{{box-sizing:border-box;margin:0;padding:0}}body{{font-family:sans-serif;background:#f5f6fa;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:16px}}.card{{background:#fff;border-radius:16px;padding:36px 28px;text-align:center;max-width:360px;width:100%;box-shadow:0 4px 24px rgba(0,0,0,.08)}}.icon{{font-size:52px;margin-bottom:16px}}h1{{font-size:22px;font-weight:700;color:#dc2626;margin-bottom:8px}}p{{font-size:15px;color:#6b7280;margin-bottom:24px}}a{{display:inline-block;background:#534AB7;color:#fff;padding:12px 28px;border-radius:10px;text-decoration:none;font-weight:600;font-size:15px}}</style></head>
<body><div class="card"><div class="icon">❌</div><h1>Помилка оплати</h1>
<p>Щось пішло не так. Поверніться до бота і спробуйте ще раз.</p>
<a href="https://t.me/{BOT_USERNAME}">Повернутися до бота →</a></div></body></html>"""


def _cancel_html():
    return f"""<!DOCTYPE html><html lang="uk"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Скасовано</title>
<style>*{{box-sizing:border-box;margin:0;padding:0}}body{{font-family:sans-serif;background:#f5f6fa;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:16px}}.card{{background:#fff;border-radius:16px;padding:36px 28px;text-align:center;max-width:360px;width:100%;box-shadow:0 4px 24px rgba(0,0,0,.08)}}.icon{{font-size:52px;margin-bottom:16px}}h1{{font-size:22px;font-weight:700;color:#1a1a2e;margin-bottom:8px}}p{{font-size:15px;color:#6b7280;margin-bottom:24px}}a{{display:inline-block;background:#534AB7;color:#fff;padding:12px 28px;border-radius:10px;text-decoration:none;font-weight:600;font-size:15px}}</style></head>
<body><div class="card"><div class="icon">↩️</div><h1>Оплату скасовано</h1>
<p>Ви скасували оплату. Поверніться до бота щоб спробувати ще раз.</p>
<a href="https://t.me/{BOT_USERNAME}">Повернутися до бота →</a></div></body></html>"""


def setup_webhook_server(bot: Bot) -> web.Application:
    app = web.Application()
    app["bot"] = bot
    app.router.add_get("/pay",               serve_pay_page)
    app.router.add_get("/payment/success",   paypal_success)
    app.router.add_get("/payment/cancel",    paypal_cancel)
    app.router.add_post("/payment/webhook",  paypal_webhook)
    return app
