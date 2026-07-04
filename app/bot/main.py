import asyncio

from aiohttp import web
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
)

from app.bot.handlers.commands import (
    cmd_cd,
    cmd_close,
    cmd_current,
    cmd_download,
    cmd_health,
    cmd_help,
    cmd_interrupt,
    cmd_last,
    cmd_listfiles,
    cmd_logs,
    cmd_model,
    cmd_mode,
    cmd_new,
    cmd_pwd,
    cmd_refresh,
    cmd_rename,
    cmd_restart,
    cmd_session_info,
    cmd_sessions,
    cmd_set_model,
    cmd_shutdown,
    cmd_status,
    cmd_switch,
    cmd_upload,
)
from app.bot.handlers.messages import handle_document, handle_message
from app.config import settings
from app.database import db
from app.logging import StructuredLogger, get_logger, setup_logging
from app.services.session_manager import SessionManager

logger = StructuredLogger(get_logger(__name__))


async def health_check(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def post_init(application: Application) -> None:  # type: ignore[type-arg]
    session_manager = SessionManager(db, settings.opencode_executable)
    application.bot_data["session_manager"] = session_manager

    await db.init_db()
    await session_manager.restore_sessions()

    if settings.webhook_mode and settings.webhook_url:
        await application.bot.set_webhook(
            url=settings.webhook_url,
            allowed_updates=Update.ALL_TYPES,
            secret_token=settings.webhook_secret or None,
        )
        logger.info("webhook_set", url=settings.webhook_url)

    logger.info("bot_started")


async def post_shutdown(application: Application) -> None:  # type: ignore[type-arg]
    session_manager: SessionManager = application.bot_data.get("session_manager")
    if session_manager:
        await session_manager.cleanup()
    await db.close()
    logger.info("bot_stopped")


def create_application() -> Application:  # type: ignore[type-arg]
    setup_logging()

    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_handler(CommandHandler("new", cmd_new))
    application.add_handler(CommandHandler("sessions", cmd_sessions))
    application.add_handler(CommandHandler("ls", cmd_sessions))
    application.add_handler(CommandHandler("switch", cmd_switch))
    application.add_handler(CommandHandler("current", cmd_current))
    application.add_handler(CommandHandler("close", cmd_close))
    application.add_handler(CommandHandler("restart", cmd_restart))
    application.add_handler(CommandHandler("interrupt", cmd_interrupt))
    application.add_handler(CommandHandler("pwd", cmd_pwd))
    application.add_handler(CommandHandler("cd", cmd_cd))
    application.add_handler(CommandHandler("rename", cmd_rename))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("upload", cmd_upload))
    application.add_handler(CommandHandler("download", cmd_download))
    application.add_handler(CommandHandler("listfiles", cmd_listfiles))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("logs", cmd_logs))
    application.add_handler(CommandHandler("session_info", cmd_session_info))
    application.add_handler(CommandHandler("model", cmd_model))
    application.add_handler(CommandHandler("set_model", cmd_set_model))
    application.add_handler(CommandHandler("mode", cmd_mode))
    application.add_handler(CommandHandler("last", cmd_last))
    application.add_handler(CommandHandler("refresh", cmd_refresh))
    application.add_handler(CommandHandler("health", cmd_health))
    application.add_handler(CommandHandler("shutdown", cmd_shutdown))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    return application


async def run_webhook(application: Application) -> None:  # type: ignore[type-arg]
    """Run the bot in webhook mode with aiohttp server."""
    web_app = web.Application()

    async def handle_webhook(request: web.Request) -> web.Response:
        await application.process_update(request)
        return web.Response(text="OK")

    web_app.add_routes([
        web.get("/health", health_check),
        web.post(settings.webhook_path, handle_webhook),
    ])

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", settings.webhook_port)
    await site.start()

    logger.info("webhook_server_started", port=settings.webhook_port)

    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


def main() -> None:
    application = create_application()

    if settings.webhook_mode:
        asyncio.run(run_webhook(application))
    else:
        application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
