from __future__ import annotations


def message_link(chat_id: int, message_id: int) -> str | None:
    """Deep link to a message, only for supergroups (chat id starts with -100)."""
    if str(chat_id).startswith("-100"):
        return f"https://t.me/c/{str(chat_id)[4:]}/{message_id}"
    return None
