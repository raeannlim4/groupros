from __future__ import annotations

import logging

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
logger = logging.getLogger(__name__)


async def post_init(application: Application) -> None:
    init_db()
    reminders.backfill_reminders()
    me = await application.bot.get_me()
    logger.info("Polling as @%s", me.username)


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
