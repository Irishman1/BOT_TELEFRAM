import logging
from aiogram import Bot
from aiogram.types import FSInputFile
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime

from config import GROUP_ID, ADMIN_IDS, WEEKLY_ADMIN_IDS
from database import (
    get_expiring_users, mark_notified,
    get_expired_users, deactivate_user,
    get_stats, get_buy_clicks_count, DB_PATH,
)

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler(timezone="Europe/Kyiv")


async def notify_expiring(bot: Bot, days: int):
    """Уведомляем юзеров что подписка истекает через N дней"""
    users = await get_expiring_users(days)

    for user in users:
        try:
            expires = datetime.fromisoformat(user["expires_at"])
            if days == 3:
                emoji, text = "⚠️", f"Подписка заканчивается через <b>3 дня</b> ({expires.strftime('%d.%m.%Y')})"
            else:
                emoji, text = "🔴", f"Подписка заканчивается <b>завтра</b> ({expires.strftime('%d.%m.%Y')})"

            await bot.send_message(
                user["tg_id"],
                f"{emoji} {text}\n\n"
                f"Чтобы не потерять доступ к каналу — продли подписку:\n/start",
                parse_mode="HTML"
            )
            await mark_notified(user["tg_id"], days)
            logger.info(f"Notified {user['tg_id']} — {days} days left")
        except Exception as e:
            logger.warning(f"Failed to notify {user['tg_id']}: {e}")


async def kick_expired_users(bot: Bot):
    """Кикаем юзеров у которых истекла подписка"""
    users = await get_expired_users()

    for user in users:
        tg_id = user["tg_id"]
        try:
            # Деактивируем в БД
            await deactivate_user(tg_id)

            # Кикаем из канала
            await bot.ban_chat_member(chat_id=GROUP_ID, user_id=tg_id)
            await bot.unban_chat_member(chat_id=GROUP_ID, user_id=tg_id)
            # unban нужен чтобы юзер мог снова вступить после новой оплаты

            # Уведомляем
            await bot.send_message(
                tg_id,
                "❌ <b>Твоя подписка закончилась</b>\n\n"
                "Ты удалён из канала.\n\n"
                "Чтобы получить доступ снова — оформи подписку:\n/start",
                parse_mode="HTML"
            )
            logger.info(f"Kicked expired user: {tg_id}")
        except Exception as e:
            logger.warning(f"Failed to kick {tg_id}: {e}")


async def daily_backup(bot: Bot):
    """Щоденно надсилаємо адмінам бекап БД та статистику"""
    stats = await get_stats()
    buy_clicks = await get_buy_clicks_count()

    text = (
        f"🗄 <b>Ежедневный бекап</b> — {datetime.now().strftime('%d.%m.%Y')}\n\n"
        f"📊 <b>Статистика</b>\n"
        f"👥 Активных подписок: <b>{stats['active']}</b>\n"
        f"👤 Всего пользователей: <b>{stats['total']}</b>\n"
        f"💰 Доход за месяц: <b>{stats['monthly_revenue']:.2f} €</b>\n"
        f"⏳ Заканчивается в течение 3 дней: <b>{stats['expiring_soon']}</b>\n"
        f"🖱 Кликов по \"Оплатить\": <b>{buy_clicks}</b>"
    )

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_document(
                admin_id,
                FSInputFile(DB_PATH),
                caption=text,
                parse_mode="HTML",
                disable_notification=True
            )
        except Exception as e:
            logger.warning(f"Failed to send daily backup to {admin_id}: {e}")


async def weekly_stats(bot: Bot):
    """Еженедельная статистика для дополнительных администраторов"""
    if not WEEKLY_ADMIN_IDS:
        return
    stats = await get_stats()
    buy_clicks = await get_buy_clicks_count()

    text = (
        f"📊 <b>Еженедельная статистика</b> — {datetime.now().strftime('%d.%m.%Y')}\n\n"
        f"👥 Активных подписок: <b>{stats['active']}</b>\n"
        f"👤 Всего пользователей: <b>{stats['total']}</b>\n"
        f"💰 Доход за месяц: <b>{stats['monthly_revenue']:.2f} €</b>\n"
        f"⏳ Заканчивается в течение 3 дней: <b>{stats['expiring_soon']}</b>\n"
        f"🖱 Кликов по \"Оплатить\": <b>{buy_clicks}</b>"
    )

    for admin_id in WEEKLY_ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML", disable_notification=True)
        except Exception as e:
            logger.warning(f"Failed to send weekly stats to {admin_id}: {e}")


async def start_scheduler(bot: Bot):
    """Запускаем все задачи планировщика"""

    # Проверяем истёкшие подписки — каждый час
    scheduler.add_job(
        kick_expired_users, "interval", hours=1,
        kwargs={"bot": bot},
        id="kick_expired"
    )

    # Уведомление за 3 дня — каждый день в 10:00
    scheduler.add_job(
        notify_expiring, "cron", hour=10, minute=0,
        kwargs={"bot": bot, "days": 3},
        id="notify_3d"
    )

    # Уведомление за 1 день — каждый день в 10:00
    scheduler.add_job(
        notify_expiring, "cron", hour=10, minute=0,
        kwargs={"bot": bot, "days": 1},
        id="notify_1d"
    )

    # Ежедневный бекап БД + статистика — в 8:00 по Черногории (9:00 Киев)
    scheduler.add_job(
        daily_backup, "cron", hour=9, minute=0,
        kwargs={"bot": bot},
        id="daily_backup"
    )

    # Еженедельная статистика — каждый понедельник в 9:00 по Киеву
    scheduler.add_job(
        weekly_stats, "cron", day_of_week="mon", hour=9, minute=0,
        kwargs={"bot": bot},
        id="weekly_stats"
    )

    scheduler.start()
    logger.info("Scheduler started")
