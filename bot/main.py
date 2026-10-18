from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web as aio_web
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot import config
from bot.db import init_db
from bot.handlers import (
    ask,
    brief,
    capture,
    catchup,
    decisions,
    meeting,
    onboarding,
    plan,
    reminders,
    tasks,
)
from bot.rag import chunker

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# httpx logs full request URLs at INFO, which include the Telegram bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

WEB_INDEX = Path(__file__).parent / "web" / "index.html"


async def _serve_index(request: aio_web.Request) -> aio_web.Response:
    page = WEB_INDEX.read_text(encoding="utf-8").replace(
        "__BOT_USERNAME__", request.app["bot_username"]
    )
    return aio_web.Response(text=page, content_type="text/html")


async def _serve_health(request: aio_web.Request) -> aio_web.Response:
    return aio_web.Response(text="ok")


async def start_web_server(
    application: Application, bot_username: str
) -> None:
    """Start the landing page on Railway's PORT, alongside long polling."""
    if config.PORT is None:
        return
    web_app = aio_web.Application()
    web_app["bot_username"] = bot_username
    web_app.router.add_get("/", _serve_index)
    web_app.router.add_get("/health", _serve_health)
    runner = aio_web.AppRunner(web_app, shutdown_timeout=5.0)
    await runner.setup()
    site = aio_web.TCPSite(runner, "0.0.0.0", config.PORT)
    await site.start()
    application.bot_data["web_runner"] = runner
    logger.info("Landing page listening on 0.0.0.0:%s", config.PORT)


async def stop_web_server(application: Application) -> None:
    runner = application.bot_data.pop("web_runner", None)
    if runner is not None:
        await runner.cleanup()


async def post_init(application: Application) -> None:
    init_db()
    reminders.backfill_reminders()
    me = await application.bot.get_me()
    logger.info("Polling as @%s", me.username)
    await start_web_server(application, me.username or "")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Exception while handling update", exc_info=context.error)


def main() -> None:
    application = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .concurrent_updates(8)
        .get_updates_connect_timeout(30)
        .get_updates_read_timeout(30)
        .connect_timeout(30)
        .read_timeout(30)
        .post_init(post_init)
        .post_stop(stop_web_server)
        .build()
    )
    application.add_handler(CommandHandler("start", onboarding.start))
    application.add_handler(CommandHandler("help", onboarding.help_command))
    application.add_handler(CommandHandler("forget", onboarding.forget))
    application.add_handler(CommandHandler("tasks", tasks.task_board))
    application.add_handler(CommandHandler("addtask", tasks.add_task))
    application.add_handler(CommandHandler("mytasks", tasks.my_tasks))
    application.add_handler(CommandHandler("decide", decisions.decide))
    application.add_handler(CommandHandler("decisions", decisions.decisions))
    application.add_handler(CommandHandler("ask", ask.ask))
    application.add_handler(CommandHandler("catchup", catchup.catchup))
    application.add_handler(CommandHandler("meeting", meeting.meeting))
    application.add_handler(CommandHandler("brief", brief.brief))
    application.add_handler(CommandHandler("plan", plan.plan))
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS
            & filters.CaptionRegex(r"^/meeting(?:@\w+)?(?:\s|$)")
            & filters.Document.ALL,
            meeting.meeting,
        )
    )
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS
            & filters.CaptionRegex(r"^/brief(?:@\w+)?(?:\s|$)")
            & filters.Document.ALL,
            brief.brief,
        )
    )
    application.add_handler(
        MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, onboarding.bot_added)
    )
    application.add_handler(
        MessageHandler(
            filters.StatusUpdate.NEW_CHAT_TITLE, onboarding.chat_title_changed
        )
    )
    application.add_handler(
        MessageHandler(filters.StatusUpdate.MIGRATE, onboarding.chat_migrated)
    )
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
            capture.store_message,
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            capture.handle_capture_callback, pattern=r"^(cap_add|cap_no):"
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            tasks.handle_task_callback, pattern=r"^(task_done|task_claim|task_undo):"
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            tasks.handle_mytasks_callback, pattern=r"^mytask_done:"
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            decisions.handle_decision_callback, pattern=r"^dec_rm:"
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            onboarding.handle_forget_callback,
            pattern=r"^(forget_confirm|forget_cancel):",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            reminders.handle_reminder_callback,
            pattern=r"^(rem_ontrack|rem_more|rem_plus1|rem_plus2|rem_plus7|rem_tell_yes|rem_tell_no):",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            meeting.handle_meeting_callback, pattern=r"^(mtg_log|mtg_add):"
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            plan.handle_plan_callback, pattern=r"^plan_(create|cancel):"
        )
    )
    application.add_handler(
        MessageHandler(filters.ChatType.GROUPS, onboarding.refresh_chat_title),
        group=1,
    )
    if application.job_queue is not None:
        application.job_queue.run_repeating(
            reminders.reminder_job, interval=60, first=15
        )
        application.job_queue.run_repeating(
            chunker.chunk_job, interval=120, first=30
        )
    application.add_error_handler(error_handler)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
