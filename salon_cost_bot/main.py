from __future__ import annotations

import logging
import os

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent, Update

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


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


async def run() -> None:
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    logger = logging.getLogger("nefor.bot")

    if not settings.database_url:
        raise RuntimeError("Не задан DATABASE_URL для Neon.")

    db = Database(settings.database_url)
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

        logger.exception(
            "Unhandled update error; update_id=%s",
            update_id,
            exc_info=event.exception,
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
                    logger.error(
                        "Unhandled error occurred in an admin request"
                    )
            except Exception:
                logger.debug(
                    "Could not send the user-facing error notice"
                )

        return True

    dispatcher.errors.register(report_unhandled_error)

    me = await bot.get_me()
    logger.info(
        "Telegram bot authenticated; username=%s",
        me.username or "unknown",
    )

    app = web.Application()

    async def health(request: web.Request) -> web.Response:
        return web.Response(text="NEFOR SPOT bot is running")

    async def telegram_webhook(
        request: web.Request,
    ) -> web.Response:
        try:
            data = await request.json()
            update = Update.model_validate(
                data,
                context={"bot": bot},
            )

            await dispatcher.feed_update(bot, update)
            return web.Response(text="OK")
        except Exception:
            logger.exception("Webhook update failed")
            return web.Response(
                text="ERROR",
                status=500,
            )

    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    app.router.add_post("/telegram-webhook", telegram_webhook)

    render_external_url = os.getenv(
        "RENDER_EXTERNAL_URL",
        "",
    ).rstrip("/")

    webhook_url = os.getenv(
        "WEBHOOK_URL",
        "",
    ).strip()

    if not webhook_url and render_external_url:
        webhook_url = (
            f"{render_external_url}/telegram-webhook"
        )

    if not webhook_url:
        raise RuntimeError(
            "Не удалось определить WEBHOOK_URL."
        )

    await bot.set_webhook(
        webhook_url,
        allowed_updates=dispatcher.resolve_used_update_types(),
        drop_pending_updates=False,
    )

    logger.info(
        "Telegram webhook configured: %s",
        webhook_url,
    )

    port = int(os.getenv("PORT", "10000"))

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        port,
    )

    await site.start()

    logger.info(
        "HTTP server started on port %s",
        port,
    )

    try:
        import asyncio

        await asyncio.Event().wait()
    finally:
        await bot.delete_webhook()
        await bot.session.close()
        await db.close()
        await runner.cleanup()


def main() -> None:
    import asyncio

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logging.getLogger("nefor.bot").info(
            "Bot stopped"
        )


if __name__ == "__main__":
    main()