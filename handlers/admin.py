from aiogram import Router, Bot
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from datetime import datetime, timedelta

from config import ADMIN_IDS, GROUP_ID, PLANS
from database import (
    get_stats, find_user_by_username, get_user,
    activate_subscription, deactivate_user, get_all_active_users
)

router = Router()


def is_admin(tg_id: int) -> bool:
    return tg_id in ADMIN_IDS


# ─── /admin ─────────────────────────────────────────────────

@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        return

    stats = await get_stats()

    await message.answer(
        f"🛠 <b>Адмін-панель</b>\n\n"
        f"👥 Активних підписок: <b>{stats['active']}</b>\n"
        f"📊 Всього користувачів: <b>{stats['total']}</b>\n"
        f"⚠️ Закінчується скоро: <b>{stats['expiring_soon']}</b>\n"
        f"💰 Дохід цього місяця: <b>{stats['monthly_revenue']:.0f} грн</b>\n\n"
        f"<b>Команди:</b>\n"
        f"/find @username — знайти користувача\n"
        f"/give @username 30 — дати доступ на N днів\n"
        f"/kick @username — видалити користувача\n"
        f"/broadcast — розіслати повідомлення всім",
        parse_mode="HTML"
    )


# ─── /find @username ────────────────────────────────────────

@router.message(Command("find"))
async def cmd_find(message: Message):
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Використання: /find @username")
        return

    user = await find_user_by_username(parts[1])
    if not user:
        await message.answer("❌ Користувача не знайдено")
        return

    expires = datetime.fromisoformat(user["expires_at"]) if user["expires_at"] else None
    plan = PLANS.get(user["plan"], {})

    status = "✅ Активна" if user["is_active"] else "❌ Неактивна"
    expires_str = expires.strftime("%d.%m.%Y") if expires else "—"
    days_left = (expires - datetime.now()).days if expires and user["is_active"] else 0

    await message.answer(
        f"👤 <b>@{user['username']}</b> (ID: {user['tg_id']})\n"
        f"Ім'я: {user['full_name']}\n"
        f"Підписка: {status}\n"
        f"Тариф: {plan.get('name', user['plan'] or '—')}\n"
        f"Діє до: {expires_str} (ще {days_left} дн.)\n\n"
        f"/give @{user['username']} 30 — продовжити\n"
        f"/kick @{user['username']} — видалити",
        parse_mode="HTML"
    )


# ─── /give @username days ────────────────────────────────────

@router.message(Command("give"))
async def cmd_give(message: Message, bot: Bot):
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()
    if len(parts) < 3:
        await message.answer("Використання: /give @username кількість_днів")
        return

    username, days_str = parts[1], parts[2]
    try:
        days = int(days_str)
    except ValueError:
        await message.answer("❌ Введи число днів")
        return

    user = await find_user_by_username(username)
    if not user:
        await message.answer("❌ Користувача не знайдено")
        return

    new_expiry = await activate_subscription(user["tg_id"], "manual", days)

    # Уведомляем пользователя
    try:
        await bot.send_message(
            user["tg_id"],
            f"🎁 Адміністратор надав тобі доступ на <b>{days} днів</b>!\n"
            f"📅 Підписка діє до: <b>{new_expiry.strftime('%d.%m.%Y')}</b>\n\n"
            f"Отримай посилання на канал: /getlink",
            parse_mode="HTML"
        )
    except Exception:
        pass

    await message.answer(
        f"✅ Користувачу @{username} надано доступ на {days} днів\n"
        f"До: {new_expiry.strftime('%d.%m.%Y')}"
    )


# ─── /kick @username ─────────────────────────────────────────

@router.message(Command("kick"))
async def cmd_kick(message: Message, bot: Bot):
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Використання: /kick @username")
        return

    user = await find_user_by_username(parts[1])
    if not user:
        await message.answer("❌ Користувача не знайдено")
        return

    await deactivate_user(user["tg_id"])

    try:
        await bot.ban_chat_member(chat_id=GROUP_ID, user_id=user["tg_id"])
        await bot.unban_chat_member(chat_id=GROUP_ID, user_id=user["tg_id"])
    except Exception as e:
        await message.answer(f"⚠️ Не вдалось кікнути з каналу: {e}")
        return

    try:
        await bot.send_message(
            user["tg_id"],
            "❌ Твій доступ до каналу було скасовано адміністратором."
        )
    except Exception:
        pass

    await message.answer(f"✅ @{parts[1]} видалено з каналу")


# ─── /broadcast ──────────────────────────────────────────────

@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, bot: Bot):
    if not is_admin(message.from_user.id):
        return

    # Текст после команды
    text = message.text.replace("/broadcast", "").strip()
    if not text:
        await message.answer(
            "Використання:\n/broadcast Текст повідомлення\n\n"
            "Повідомлення буде надіслано всім активним підписникам."
        )
        return

    users = await get_all_active_users()
    sent, failed = 0, 0

    for user in users:
        try:
            await bot.send_message(user["tg_id"], text)
            sent += 1
        except Exception:
            failed += 1

    await message.answer(
        f"📨 Розсилка завершена\n"
        f"✅ Надіслано: {sent}\n"
        f"❌ Помилок: {failed}"
    )
