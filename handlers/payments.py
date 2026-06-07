import logging
from pathlib import Path
from aiohttp import web
from aiogram import Bot
from datetime import datetime, timedelta

from config import PLANS, GROUP_ID, WEBHOOK_PATH
from database import (
    get_pending_order, delete_pending_order,
    payment_exists, save_payment,
    activate_subscription, save_invite_link,
    get_user
)

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent.parent / "static"


async def serve_pay_page(request: web.Request) -> web.Response:
    """Віддаємо HTML сторінку оплати"""
    html = (STATIC_DIR / "pay.html").read_text(encoding="utf-8")
    return web.Response(text=html, content_type="text/html")


async def confirm_payment(request: web.Request) -> web.Response:
    """Підтвердження оплати від фейкової сторінки"""
    bot: Bot = request.app["bot"]
    order_id = request.rel_url.query.get("order", "")

    if not order_id:
        return web.Response(status=400, text="no order")

    # Захист від дублів
    if await payment_exists(order_id):
        return web.Response(text="ok")

    order = await get_pending_order(order_id)
    if not order:
        return web.Response(status=404, text="order not found")

    tg_id    = order["tg_id"]
    plan_key = order["plan"]
    plan     = PLANS.get(plan_key)
    if not plan:
        return web.Response(status=400, text="bad plan")

    # Зберігаємо платіж
    await save_payment(order_id, tg_id, plan["price"], plan_key, "success")
    await delete_pending_order(order_id)

    # Активуємо підписку
    new_expiry = await activate_subscription(tg_id, plan_key, plan["days"])

    # Генеруємо одноразову ссилку на групу
    expires_at = datetime.now() + timedelta(hours=48)
    try:
        invite = await bot.create_chat_invite_link(
            chat_id=GROUP_ID,
            member_limit=1,
            expire_date=expires_at
        )
        await save_invite_link(tg_id, invite.invite_link, expires_at)
        link = invite.invite_link
    except Exception as e:
        logger.error(f"Failed to create invite link: {e}")
        link = None

    # Відправляємо юзеру в Telegram
    try:
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        msg = (
            f"✅ <b>Оплата підтверджена!</b>\n\n"
            f"📦 Тариф: {plan['name']}\n"
            f"💰 Сплачено: {plan['price']} грн\n"
            f"📅 Підписка до: <b>{new_expiry.strftime('%d.%m.%Y')}</b>\n"
        )
        kb = InlineKeyboardBuilder()
        if link:
            msg += f"\n🔗 Твоє посилання на групу:\n{link}\n\n⚠️ Одноразове — тільки для тебе!"
            kb.button(text="👥 Приєднатися до групи", url=link)
        await bot.send_message(tg_id, msg, parse_mode="HTML",
                               reply_markup=kb.as_markup() if link else None)
    except Exception as e:
        logger.error(f"Failed to send message to {tg_id}: {e}")

    logger.info(f"Payment confirmed: order={order_id} tg_id={tg_id} plan={plan_key}")
    return web.Response(text="ok")


async def handle_liqpay_webhook(request: web.Request) -> web.Response:
    """Реальний LiqPay webhook (на майбутнє)"""
    bot: Bot = request.app["bot"]
    try:
        from config import LIQPAY_PRIVATE_KEY
        if not LIQPAY_PRIVATE_KEY:
            return web.Response(text="liqpay not configured")

        from liqpay import verify_webhook, decode_webhook
        data_post = await request.post()
        data      = data_post.get("data", "")
        signature = data_post.get("signature", "")

        if not verify_webhook(data, signature):
            return web.Response(status=400, text="bad signature")

        payload  = decode_webhook(data)
        status   = payload.get("status")
        order_id = payload.get("order_id", "")

        if status not in ("success", "sandbox"):
            return web.Response(text="ok")

        # Далі той самий флоу що і у confirm_payment
        if await payment_exists(order_id):
            return web.Response(text="ok")

        order = await get_pending_order(order_id)
        if not order:
            return web.Response(text="ok")

        tg_id    = order["tg_id"]
        plan_key = order["plan"]
        plan     = PLANS[plan_key]

        await save_payment(order_id, tg_id, float(payload.get("amount", plan["price"])), plan_key, "success")
        await delete_pending_order(order_id)
        new_expiry = await activate_subscription(tg_id, plan_key, plan["days"])

        expires_at = datetime.now() + timedelta(hours=48)
        invite = await bot.create_chat_invite_link(
            chat_id=GROUP_ID, member_limit=1, expire_date=expires_at
        )
        await save_invite_link(tg_id, invite.invite_link, expires_at)

        from aiogram.utils.keyboard import InlineKeyboardBuilder
        kb = InlineKeyboardBuilder()
        kb.button(text="👥 Приєднатися до групи", url=invite.invite_link)
        await bot.send_message(
            tg_id,
            f"✅ <b>Оплата успішна!</b>\n\n"
            f"📦 {plan['name']} | 💰 {plan['price']} грн\n"
            f"📅 До: <b>{new_expiry.strftime('%d.%m.%Y')}</b>\n\n"
            f"🔗 {invite.invite_link}",
            parse_mode="HTML",
            reply_markup=kb.as_markup()
        )
    except Exception as e:
        logger.exception(f"LiqPay webhook error: {e}")
        return web.Response(status=500)

    return web.Response(text="ok")


def setup_webhook_server(bot: Bot) -> web.Application:
    app = web.Application()
    app["bot"] = bot
    app.router.add_get("/pay",              serve_pay_page)
    app.router.add_get("/payment/confirm",  confirm_payment)
    app.router.add_post(WEBHOOK_PATH,       handle_liqpay_webhook)
    return app
