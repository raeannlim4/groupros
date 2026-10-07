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
from bot.handlers import capture, decisions, onboarding, reminders, tasks

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
        .post_init(post_init)
        .build()
    )
    application.add_handler(CommandHandler("start", onboarding.start))
    application.add_handler(CommandHandler("help", onboarding.help_command))
    application.add_handler(CommandHandler("tasks", tasks.task_board))
    application.add_handler(CommandHandler("mytasks", tasks.my_tasks))
    application.add_handler(CommandHandler("decide", decisions.decide))
    application.add_handler(CommandHandler("decisions", decisions.decisions))
    application.add_handler(
        MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, onboarding.bot_added)
    )
    application.add_handler(
        MessageHandler(
            filters.StatusUpdate.NEW_CHAT_TITLE, onboarding.chat_title_changed
        )
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
            tasks.handle_task_callback, pattern=r"^(task_done|task_claim):"
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
            reminders.handle_reminder_callback,
            pattern=r"^(rem_ontrack|rem_more|rem_pick|rem_day|rem_plus1|rem_plus2|rem_plus7|rem_tell_yes|rem_tell_no):",
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
    application.add_error_handler(error_handler)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
