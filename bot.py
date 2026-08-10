import asyncio
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeDefault, BotCommandScopeChat, TelegramObject

from config import BOT_TOKEN, ALL_ADMIN_IDS
from database import init_db, upsert_user
from handlers import user, admin
from handlers.payments import setup_webhook_server
from scheduler import start_scheduler
from admin.panel import setup_admin

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class UpsertUserMiddleware(BaseMiddleware):
    """Реєструє юзера в базі при БУДЬ-ЯКІЙ взаємодії з ботом, не тільки на /start"""
    async def __call__(self, handler, event: TelegramObject, data: dict):
        tg_user = data.get("event_from_user")
        if tg_user and not tg_user.is_bot:
            try:
                await upsert_user(
                    tg_id=tg_user.id,
                    username=tg_user.username or "",
                    full_name=tg_user.full_name,
                )
            except Exception as e:
                logger.warning(f"UpsertUserMiddleware failed for {tg_user.id}: {e}")
        return await handler(event, data)

USER_COMMANDS = [
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="status", description="Моя подписка"),
    BotCommand(command="history", description="История платежей"),
    BotCommand(command="getlink", description="Ссылка на группу"),
    BotCommand(command="support", description="Написать в поддержку"),
    BotCommand(command="cancel", description="Отменить текущее действие"),
    BotCommand(command="help", description="Справка"),
]

ADMIN_COMMANDS = USER_COMMANDS + [
    BotCommand(command="admin", description="Панель администратора"),
    BotCommand(command="find", description="Найти пользователя"),
    BotCommand(command="give", description="Выдать доступ"),
    BotCommand(command="kick", description="Удалить пользователя"),
    BotCommand(command="broadcast", description="Разослать текст всем"),
    BotCommand(command="notify", description="Рассылка по тарифам"),
]


async def setup_bot_commands(bot: Bot):
    await bot.set_my_commands(USER_COMMANDS, scope=BotCommandScopeDefault())
    for admin_id in ALL_ADMIN_IDS:
        try:
            await bot.set_my_commands(ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception as e:
            logger.warning(f"Failed to set admin commands for {admin_id}: {e}")


async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(UpsertUserMiddleware())

    dp.include_router(user.router)
    dp.include_router(admin.router)

    await init_db()
    await setup_bot_commands(bot)
    await start_scheduler(bot)

    # Один сервер — і webhook і адмінка
    app = setup_webhook_server(bot)
    setup_admin(app)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    logger.info("Server started on :8080 (webhook + admin panel)")

    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
