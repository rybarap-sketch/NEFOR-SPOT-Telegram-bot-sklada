from __future__ import annotations

import asyncio
import fcntl
import logging
from typing import IO

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramConflictError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent

from salon_cost_bot.config import Settings
from salon_cost_bot.database import Database
from salon_cost_bot.handlers.admin import router as admin_router
from salon_cost_bot.handlers.calculations import router as calculations_router
from salon_cost_bot.handlers.common import (
    is_admin,
    router as common_router,
    setup_common_handlers,
)
from salon_cost_bot.handlers.history import router as history_router
from salon_cost_bot.services import SalonService


class BotInstanceLock:
    def __init__(self, database_path) -> None:
        self.path = database_path.with_suffix(database_path.suffix + ".lock")
        self._file: IO[str] | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("w")
        try:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            self._file.close()
            self._file = None
            raise RuntimeError(
                "Бот уже запущен с этой базой. Оставьте только один polling-процесс."
            ) from error

    def release(self) -> None:
        if self._file is not None:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            self._file.close()
            self._file = None


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("aiogram.event").setLevel(
        getattr(logging, level, logging.INFO)
    )


async def run() -> None:
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    logger = logging.getLogger("nefor.bot")

    lock = BotInstanceLock(settings.database_path)
    lock.acquire()
    db = Database(settings.database_path)
    bot: Bot | None = None
    try:
        await db.open()
        bot = Bot(token=settings.bot_token)
        service = SalonService(db)
        dispatcher = Dispatcher(storage=MemoryStorage())
        dispatcher["settings"] = settings
        dispatcher["service"] = service
        setup_common_handlers(settings=settings, service=service)
        dispatcher.include_router(common_router)
        dispatcher.include_router(history_router)
        dispatcher.include_router(calculations_router)
        dispatcher.include_router(admin_router)

        async def report_unhandled_error(event: ErrorEvent) -> bool:
            update_id = event.update.update_id if event.update else "unknown"
            logger.error(
                "Unhandled update error; update_id=%s type=%s",
                update_id,
                type(event.exception).__name__,
            )
            if event.update and event.update.message:
                try:
                    user_id = (
                        event.update.message.from_user.id
                        if event.update.message.from_user
                        else None
                    )
                    await event.update.message.answer(
                        "Не удалось обработать запрос. Попробуйте ещё раз; "
                        "если ошибка повторится, сообщите администратору."
                    )
                    if is_admin(settings, user_id):
                        logger.error("Unhandled error occurred in an admin request")
                except Exception:
                    logger.debug("Could not send the user-facing error notice")
            return True

        dispatcher.errors.register(report_unhandled_error)

        try:
            me = await bot.get_me()
        except Exception as error:
            logger.error(
                "Could not connect to Telegram Bot API; error_type=%s",
                type(error).__name__,
            )
            raise RuntimeError(
                "Не удалось подключиться к Telegram Bot API. Проверьте BOT_TOKEN и сетевой доступ."
            ) from error
        logger.info("Telegram bot authenticated; username=%s", me.username or "unknown")
        logger.info("Starting Telegram long polling")
        try:
            await dispatcher.start_polling(
                bot,
                allowed_updates=dispatcher.resolve_used_update_types(),
                close_bot_session=False,
            )
        except TelegramConflictError as error:
            logger.critical(
                "Telegram rejected duplicate polling. Stop the other bot process; "
                "production requires exactly one polling worker."
            )
            raise RuntimeError("Обнаружен второй процесс long polling.") from error
    finally:
        if bot is not None:
            await bot.session.close()
        await db.close()
        lock.release()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logging.getLogger("nefor.bot").info("Bot stopped")


if __name__ == "__main__":
    main()
