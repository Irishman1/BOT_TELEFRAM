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
        f"🛠 <b>Панель администратора</b>\n\n"
        f"👥 Активных подписок: <b>{stats['active']}</b>\n"
        f"📊 Всего пользователей: <b>{stats['total']}</b>\n"
        f"⚠️ Заканчивается скоро: <b>{stats['expiring_soon']}</b>\n"
        f"💰 Доход за месяц: <b>{stats['monthly_revenue']:.2f} €</b>\n\n"
        f"<b>Команды:</b>\n"
        f"/find @username — найти пользователя\n"
        f"/give @username 30 — выдать доступ на N дней\n"
        f"/kick @username — удалить пользователя\n"
        f"/broadcast — разослать сообщение всем",
        parse_mode="HTML"
    )


# ─── /find @username ────────────────────────────────────────

@router.message(Command("find"))
async def cmd_find(message: Message):
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Использование: /find @username")
        return

    user = await find_user_by_username(parts[1])
    if not user:
        await message.answer("❌ Пользователь не найден")
        return

    expires = datetime.fromisoformat(user["expires_at"]) if user["expires_at"] else None
    plan = PLANS.get(user["plan"], {})

    status = "✅ Активна" if user["is_active"] else "❌ Неактивна"
    expires_str = expires.strftime("%d.%m.%Y") if expires else "—"
    days_left = (expires - datetime.now()).days if expires and user["is_active"] else 0

    await message.answer(
        f"👤 <b>@{user['username']}</b> (ID: {user['tg_id']})\n"
        f"Имя: {user['full_name']}\n"
        f"Подписка: {status}\n"
        f"Тариф: {plan.get('name', user['plan'] or '—')}\n"
        f"Действует до: {expires_str} (ещё {days_left} дн.)\n\n"
        f"/give @{user['username']} 30 — продлить\n"
        f"/kick @{user['username']} — удалить",
        parse_mode="HTML"
    )


# ─── /give @username days ────────────────────────────────────

@router.message(Command("give"))
async def cmd_give(message: Message, bot: Bot):
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()
    if len(parts) < 3:
        await message.answer("Использование: /give @username количество_дней")
        return

    username, days_str = parts[1], parts[2]
    try:
        days = int(days_str)
    except ValueError:
        await message.answer("❌ Введи число дней")
        return

    user = await find_user_by_username(username)
    if not user:
        await message.answer("❌ Пользователь не найден")
        return

    new_expiry = await activate_subscription(user["tg_id"], "manual", days)

    try:
        await bot.send_message(
            user["tg_id"],
            f"🎁 Администратор предоставил тебе доступ на <b>{days} дней</b>!\n"
            f"📅 Подписка действует до: <b>{new_expiry.strftime('%d.%m.%Y')}</b>",
            parse_mode="HTML"
        )
    except Exception:
        pass

    await message.answer(
        f"✅ Пользователю @{username} выдан доступ на {days} дней\n"
        f"До: {new_expiry.strftime('%d.%m.%Y')}"
    )


# ─── /kick @username ─────────────────────────────────────────

@router.message(Command("kick"))
async def cmd_kick(message: Message, bot: Bot):
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Использование: /kick @username")
        return

    user = await find_user_by_username(parts[1])
    if not user:
        await message.answer("❌ Пользователь не найден")
        return

    await deactivate_user(user["tg_id"])

    try:
        await bot.ban_chat_member(chat_id=GROUP_ID, user_id=user["tg_id"])
        await bot.unban_chat_member(chat_id=GROUP_ID, user_id=user["tg_id"])
    except Exception as e:
        await message.answer(f"⚠️ Не удалось кикнуть из канала: {e}")
        return

    try:
        await bot.send_message(
            user["tg_id"],
            "❌ Твой доступ к каналу отменён администратором."
        )
    except Exception:
        pass

    await message.answer(f"✅ @{parts[1]} удалён из канала")


# ─── /broadcast ──────────────────────────────────────────────

@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, bot: Bot):
    if not is_admin(message.from_user.id):
        return

    text = message.text.replace("/broadcast", "").strip()
    if not text:
        await message.answer(
            "Использование:\n/broadcast Текст сообщения\n\n"
            "Сообщение будет отправлено всем активным подписчикам."
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
        f"📨 Рассылка завершена\n"
        f"✅ Отправлено: {sent}\n"
        f"❌ Ошибок: {failed}"
    )
