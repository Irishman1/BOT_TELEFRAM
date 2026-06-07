from aiogram import Router, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart, Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from datetime import datetime, timedelta
from urllib.parse import urlencode

from config import GROUP_ID, GROUP_NAME, GROUP_DESCRIPTION, SERVER_URL, PLANS
from database import upsert_user, get_user, save_invite_link, get_invite_link, activate_subscription

router = Router()


def make_payment_url(order_id: str, amount: int, plan_name: str, bot_username: str) -> str:
    """Генеруємо посилання на фейкову сторінку оплати"""
    params = urlencode({
        "order":  order_id,
        "amount": amount,
        "plan":   plan_name,
        "name":   GROUP_NAME,
        "bot":    f"https://t.me/{bot_username}",
    })
    return f"{SERVER_URL}/pay?{params}"


# ─── /start ─────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot):
    tg_id = message.from_user.id
    name  = message.from_user.first_name

    await upsert_user(
        tg_id=tg_id,
        username=message.from_user.username or "",
        full_name=message.from_user.full_name,
    )

    user = await get_user(tg_id)
    if user and user["is_active"] and user["expires_at"]:
        expires   = datetime.fromisoformat(user["expires_at"])
        days_left = (expires - datetime.now()).days
        kb = InlineKeyboardBuilder()
        kb.button(text="🔗 Перейти до групи", callback_data="get_link")
        kb.button(text="🔄 Продовжити підписку", callback_data="show_plans")
        kb.adjust(1)
        await message.answer(
            f"👋 Привіт, {name}!\n\n"
            f"✅ У тебе активна підписка\n"
            f"📅 Діє до: <b>{expires.strftime('%d.%m.%Y')}</b> (ще {days_left} дн.)",
            parse_mode="HTML",
            reply_markup=kb.as_markup()
        )
    else:
        kb = InlineKeyboardBuilder()
        for key, plan in PLANS.items():
            kb.button(
                text=f"💳 {plan['name']} — {plan['price']} грн",
                callback_data=f"buy:{key}"
            )
        kb.adjust(1)
        await message.answer(
            f"👋 Привіт, {name}!\n\n"
            f"🔐 <b>{GROUP_NAME}</b>\n"
            f"{GROUP_DESCRIPTION}\n\n"
            f"Обери тариф:",
            parse_mode="HTML",
            reply_markup=kb.as_markup()
        )


# ─── Показати тарифи ────────────────────────────────────────

@router.callback_query(lambda c: c.data == "show_plans")
async def show_plans(callback: CallbackQuery):
    kb = InlineKeyboardBuilder()
    for key, plan in PLANS.items():
        kb.button(
            text=f"💳 {plan['name']} — {plan['price']} грн",
            callback_data=f"buy:{key}"
        )
    kb.adjust(1)
    await callback.message.edit_text(
        f"💳 <b>Тарифи — {GROUP_NAME}</b>\n\nОбери тариф:",
        parse_mode="HTML",
        reply_markup=kb.as_markup()
    )
    await callback.answer()


# ─── Вибір тарифу → посилання на сторінку оплати ────────────

@router.callback_query(lambda c: c.data.startswith("buy:"))
async def choose_plan(callback: CallbackQuery, bot: Bot):
    plan_key = callback.data.split(":")[1]
    plan     = PLANS[plan_key]
    tg_id    = callback.from_user.id

    import time
    order_id = f"sub_{tg_id}_{plan_key}_{int(time.time())}"

    # Зберігаємо order_id щоб потім активувати підписку
    from database import save_pending_order
    await save_pending_order(order_id, tg_id, plan_key)

    me = await bot.get_me()
    pay_url = make_payment_url(order_id, plan["price"], plan["name"], me.username)

    kb = InlineKeyboardBuilder()
    kb.button(text=f"💳 Оплатити {plan['price']} грн", url=pay_url)
    kb.button(text="⬅️ Назад", callback_data="show_plans")
    kb.adjust(1)

    await callback.message.edit_text(
        f"📦 <b>{plan['name']}</b> — {plan['price']} грн\n\n"
        f"Натисни кнопку нижче — відкриється сторінка оплати.\n"
        f"Після оплати бот автоматично надішле посилання на групу.\n\n"
        f"🔒 Захищено SSL",
        parse_mode="HTML",
        reply_markup=kb.as_markup()
    )
    await callback.answer()


# ─── /getlink ────────────────────────────────────────────────

@router.message(Command("getlink"))
@router.callback_query(lambda c: c.data == "get_link")
async def get_group_link(event, bot: Bot):
    message = event.message if isinstance(event, CallbackQuery) else event
    tg_id   = event.from_user.id

    user = await get_user(tg_id)
    if not user or not user["is_active"]:
        text = "❌ Немає активної підписки. Оформити: /start"
        if isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)
        else:
            await message.answer(text)
        if isinstance(event, CallbackQuery):
            await event.answer()
        return

    existing = await get_invite_link(tg_id)
    if existing:
        link = existing
    else:
        expires_at = datetime.now() + timedelta(hours=48)
        invite = await bot.create_chat_invite_link(
            chat_id=GROUP_ID, member_limit=1, expire_date=expires_at
        )
        await save_invite_link(tg_id, invite.invite_link, expires_at)
        link = invite.invite_link

    kb = InlineKeyboardBuilder()
    kb.button(text="👥 Перейти до групи", url=link)

    await message.answer(
        f"🔗 <b>Твоє посилання:</b>\n\n{link}\n\n"
        f"⚠️ Одноразове — тільки для тебе!",
        parse_mode="HTML",
        reply_markup=kb.as_markup()
    )
    if isinstance(event, CallbackQuery):
        await event.answer()


# ─── /help ───────────────────────────────────────────────────

@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "ℹ️ <b>Допомога</b>\n\n"
        "/start — головне меню\n"
        "/getlink — посилання на групу\n"
        "/help — довідка",
        parse_mode="HTML"
    )
