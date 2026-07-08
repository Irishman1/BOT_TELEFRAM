import logging
from aiohttp import web
from aiogram import Bot
from datetime import datetime, timedelta

from config import PLANS, GROUP_ID, WEBHOOK_PATH, BOT_USERNAME, ADMIN_IDS
from database import (
    get_pending_order, delete_pending_order,
    payment_exists, save_payment,
    activate_subscription, save_invite_link,
    use_promo,
)

logger = logging.getLogger(__name__)


async def _finalize_order(bot: Bot, order: dict, paypal_token: str, actual_amount: float = None, provider: str = None) -> bool:
    """Зараховує оплату: підписка + інвайт + повідомлення. Повертає True при успіху."""
    order_id   = order["order_id"]
    tg_id      = order["tg_id"]
    plan_key   = order["plan"]
    promo_code = order.get("promo_code")
    plan       = PLANS.get(plan_key)
    if not plan:
        return False

    if promo_code:
        from database import get_promo
        promo = await get_promo(promo_code)
        if promo:
            await use_promo(promo_code)

    if actual_amount is not None:
        price = actual_amount
    elif promo_code and promo:
        price = round(plan["price"] * (100 - promo["discount"]) / 100, 2)
    else:
        price = plan["price"]

    await save_payment(order_id, tg_id, price, plan_key, "success",
                        paypal_order_id=paypal_token, promo_code=promo_code, provider=provider)
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

    # Отправляем в Telegram
    try:
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        kb = InlineKeyboardBuilder()
        msg = (
            f"✅ <b>Оплата прошла успешно!</b>\n\n"
            f"📦 Тариф: {plan['name']}\n"
            f"💰 Оплачено: {price} €\n"
            f"📅 Подписка до: <b>{new_expiry.strftime('%d.%m.%Y')}</b>\n"
        )
        if link:
            msg += (
                f"\n\n🔗 <b>Твоя ссылка на группу:</b>\n{link}\n\n"
                f"⏱ <b>Действует только 15 минут!</b>\n"
                f"⚠️ Одноразовая — только для тебя. Не передавай никому."
            )
            kb.button(text="👥 Присоединиться к группе", url=link)
        await bot.send_message(tg_id, msg, parse_mode="HTML",
                               reply_markup=kb.as_markup() if link else None)
    except Exception as e:
        logger.error(f"Telegram message error: {e}")

    logger.info(f"PayPal payment captured: order={order_id} tg_id={tg_id}")
    return True


async def paypal_success(request: web.Request) -> web.Response:
    """Юзер повернувся після оплати (PayPal redirect або Lemon Squeezy redirect_url)"""
    bot: Bot = request.app["bot"]
    order_id      = request.rel_url.query.get("order", "")
    paypal_token  = request.rel_url.query.get("token", "")  # PayPal order ID

    if not order_id:
        return web.Response(text="Bad request", status=400)

    if not paypal_token:
        # Lemon Squeezy: підтвердження оплати приходить через webhook
        return web.Response(text=_success_html(), content_type="text/html")

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

    ok = await _finalize_order(bot, order, paypal_token, provider="paypal")
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
            await _finalize_order(bot, order, paypal_order_id, provider="paypal")

    return web.Response(status=200)


async def lemonsqueezy_webhook(request: web.Request) -> web.Response:
    """Webhook від Lemon Squeezy — обробляє оплату замовлення"""
    bot: Bot = request.app["bot"]
    raw = await request.read()

    from lemonsqueezy import verify_signature
    signature = request.headers.get("X-Signature", "")
    if not verify_signature(raw, signature):
        logger.warning("Lemon Squeezy webhook: invalid signature")
        return web.Response(status=401)

    import json
    try:
        data = json.loads(raw)
    except Exception:
        return web.Response(status=400)

    event_name = data.get("meta", {}).get("event_name", "")
    if event_name != "order_created":
        return web.Response(status=200)

    custom = data.get("meta", {}).get("custom_data", {}) or {}
    order_id = custom.get("order_id")
    if not order_id:
        return web.Response(status=200)

    if await payment_exists(order_id):
        return web.Response(status=200)

    order = await get_pending_order(order_id)
    if not order:
        return web.Response(status=200)

    ls_order_id = str(data.get("data", {}).get("id", ""))
    status = data.get("data", {}).get("attributes", {}).get("status", "")
    if status != "paid":
        return web.Response(status=200)

    await _finalize_order(bot, order, ls_order_id, provider="lemonsqueezy")
    return web.Response(status=200)


async def whop_webhook(request: web.Request) -> web.Response:
    """Webhook від Whop — обробляє оплату замовлення"""
    bot: Bot = request.app["bot"]
    raw = await request.read()

    from whop import verify_signature
    webhook_id    = request.headers.get("webhook-id", "")
    timestamp     = request.headers.get("webhook-timestamp", "")
    signature     = request.headers.get("webhook-signature", "")
    if not verify_signature(raw, webhook_id, timestamp, signature):
        logger.warning("Whop webhook: invalid signature")
        return web.Response(status=401)

    import json
    try:
        data = json.loads(raw)
    except Exception:
        return web.Response(status=400)

    event_type = data.get("type", "")
    if event_type != "payment.succeeded":
        return web.Response(status=200)

    payload = data.get("data", {}) or {}
    metadata = payload.get("metadata", {}) or {}
    order_id = metadata.get("order_id")
    whop_payment_id_for_alert = str(payload.get("id", ""))
    if not order_id:
        await _alert_unmatched_payment(bot, whop_payment_id_for_alert, "нет order_id в metadata")
        return web.Response(status=200)

    if await payment_exists(order_id):
        return web.Response(status=200)

    order = await get_pending_order(order_id)
    if not order:
        await _alert_unmatched_payment(bot, whop_payment_id_for_alert, f"заказ {order_id} не найден в pending_orders")
        return web.Response(status=200)

    whop_payment_id = str(payload.get("id", ""))
    actual_amount = payload.get("total")
    await _finalize_order(bot, order, whop_payment_id, actual_amount=actual_amount, provider="whop")
    return web.Response(status=200)


async def _alert_unmatched_payment(bot: Bot, payment_id: str, reason: str):
    """Гроші списані, але не вдалось прив'язати до замовлення — сповіщаємо адміна вручну розібратись"""
    logger.error(f"Whop payment {payment_id} could not be matched to an order: {reason}")
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"⚠️ <b>Внимание! Whop-платёж не привязан к заказу</b>\n\n"
                f"Payment ID: <code>{payment_id}</code>\n"
                f"Причина: {reason}\n\n"
                f"Деньги списаны с клиента, но доступ не выдан автоматически. "
                f"Проверь в Whop дашборде и выдай доступ вручную через админку.",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Failed to alert admin {admin_id}: {e}")


async def _notify_payment_failed(bot: Bot, tg_id: int):
    """Повідомляємо юзера що оплата не пройшла, з можливістю спробувати ще раз"""
    try:
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        kb = InlineKeyboardBuilder()
        kb.button(text="🔄 Попробовать снова", callback_data="show_plans")
        await bot.send_message(
            tg_id,
            "❌ <b>Оплата не прошла</b>\n\n"
            "Что-то пошло не так при обработке платежа. "
            "Попробуй ещё раз или обратись в поддержку.",
            parse_mode="HTML",
            reply_markup=kb.as_markup()
        )
    except Exception as e:
        logger.error(f"Failed to notify payment failure to {tg_id}: {e}")


async def paypal_cancel(request: web.Request) -> web.Response:
    """Юзер скасував оплату"""
    return web.Response(text=_cancel_html(), content_type="text/html")


def _success_html():
    return f"""<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Оплата успешна</title>
<style>*{{box-sizing:border-box;margin:0;padding:0}}body{{font-family:sans-serif;background:#f5f6fa;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:16px}}.card{{background:#fff;border-radius:16px;padding:36px 28px;text-align:center;max-width:360px;width:100%;box-shadow:0 4px 24px rgba(0,0,0,.08)}}.icon{{font-size:52px;margin-bottom:16px}}h1{{font-size:22px;font-weight:700;color:#1a1a2e;margin-bottom:8px}}p{{font-size:15px;color:#6b7280;margin-bottom:24px}}a{{display:inline-block;background:#0070ba;color:#fff;padding:12px 28px;border-radius:10px;text-decoration:none;font-weight:600;font-size:15px}}</style></head>
<body><div class="card"><div class="icon">✅</div><h1>Оплата произведена!</h1>
<p>Вернись в бота — он уже отправил тебе ссылку на группу.</p>
<a href="https://t.me/{BOT_USERNAME}">Вернуться в бота →</a></div></body></html>"""


def _error_html():
    return f"""<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ошибка</title>
<style>*{{box-sizing:border-box;margin:0;padding:0}}body{{font-family:sans-serif;background:#f5f6fa;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:16px}}.card{{background:#fff;border-radius:16px;padding:36px 28px;text-align:center;max-width:360px;width:100%;box-shadow:0 4px 24px rgba(0,0,0,.08)}}.icon{{font-size:52px;margin-bottom:16px}}h1{{font-size:22px;font-weight:700;color:#dc2626;margin-bottom:8px}}p{{font-size:15px;color:#6b7280;margin-bottom:24px}}a{{display:inline-block;background:#534AB7;color:#fff;padding:12px 28px;border-radius:10px;text-decoration:none;font-weight:600;font-size:15px}}</style></head>
<body><div class="card"><div class="icon">❌</div><h1>Ошибка оплаты</h1>
<p>Что-то пошло не так. Вернись в бота и попробуй снова.</p>
<a href="https://t.me/{BOT_USERNAME}">Вернуться в бота →</a></div></body></html>"""


def _cancel_html():
    return f"""<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Отменено</title>
<style>*{{box-sizing:border-box;margin:0;padding:0}}body{{font-family:sans-serif;background:#f5f6fa;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:16px}}.card{{background:#fff;border-radius:16px;padding:36px 28px;text-align:center;max-width:360px;width:100%;box-shadow:0 4px 24px rgba(0,0,0,.08)}}.icon{{font-size:52px;margin-bottom:16px}}h1{{font-size:22px;font-weight:700;color:#1a1a2e;margin-bottom:8px}}p{{font-size:15px;color:#6b7280;margin-bottom:24px}}a{{display:inline-block;background:#534AB7;color:#fff;padding:12px 28px;border-radius:10px;text-decoration:none;font-weight:600;font-size:15px}}</style></head>
<body><div class="card"><div class="icon">↩️</div><h1>Оплата отменена</h1>
<p>Ты отменил оплату. Вернись в бота, чтобы попробовать снова.</p>
<a href="https://t.me/{BOT_USERNAME}">Вернуться в бота →</a></div></body></html>"""


def setup_webhook_server(bot: Bot) -> web.Application:
    app = web.Application()
    app["bot"] = bot
    app.router.add_get("/payment/success",   paypal_success)
    app.router.add_get("/payment/cancel",    paypal_cancel)
    app.router.add_post("/payment/webhook",  paypal_webhook)
    app.router.add_post("/payment/lemonsqueezy/webhook", lemonsqueezy_webhook)
    app.router.add_post("/webhook/whop", whop_webhook)
    return app
