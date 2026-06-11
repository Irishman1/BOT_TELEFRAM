import logging
from aiogram import Bot
from aiogram.types import FSInputFile
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime

from config import GROUP_ID, ADMIN_IDS
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
                emoji, text = "⚠️", f"Підписка закінчується через <b>3 дні</b> ({expires.strftime('%d.%m.%Y')})"
            else:
                emoji, text = "🔴", f"Підписка закінчується <b>завтра</b> ({expires.strftime('%d.%m.%Y')})"

            await bot.send_message(
                user["tg_id"],
                f"{emoji} {text}\n\n"
                f"Щоб не втратити доступ до каналу — продовж підписку:\n/start",
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
                "❌ <b>Твоя підписка закінчилась</b>\n\n"
                "Тебе видалено з каналу.\n\n"
                "Щоб отримати доступ знову — оформи підписку:\n/start",
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
        f"🗄 <b>Щоденний бекап</b> — {datetime.now().strftime('%d.%m.%Y')}\n\n"
        f"📊 <b>Статистика</b>\n"
        f"👥 Активних підписок: <b>{stats['active']}</b>\n"
        f"👤 Всього користувачів: <b>{stats['total']}</b>\n"
        f"💰 Дохід за місяць: <b>{stats['monthly_revenue']:.2f} €</b>\n"
        f"⏳ Закінчується протягом 3 днів: <b>{stats['expiring_soon']}</b>\n"
        f"🖱 Кліків по \"Оплатити\": <b>{buy_clicks}</b>"
    )

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_document(
                admin_id,
                FSInputFile(DB_PATH),
                caption=text,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning(f"Failed to send daily backup to {admin_id}: {e}")


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

    # Щоденний бекап БД + статистика — о 4:00
    scheduler.add_job(
        daily_backup, "cron", hour=4, minute=0,
        kwargs={"bot": bot},
        id="daily_backup"
    )

    scheduler.start()
    logger.info("Scheduler started")
