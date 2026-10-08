from __future__ import annotations

import json
import sys
import zlib
from datetime import datetime, timezone

from sqlalchemy import select

from bot.db import SessionLocal
from bot.models import Chat, Message, User

DEFAULT_CHAT_ID = -999900000001
DEMO_USER_BASE = -900000


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _demo_user_id(name: str) -> int:
    return DEMO_USER_BASE - (zlib.crc32(name.encode()) % 100000)


def seed_transcript(session, telegram_chat_id: int, entries, title="Demo group") -> Chat:
    chat = session.scalar(
        select(Chat).where(Chat.telegram_chat_id == telegram_chat_id)
    )
    if chat is None:
        chat = Chat(telegram_chat_id=telegram_chat_id, title=title)
        session.add(chat)
        session.flush()

    users: dict[str, User] = {}
    for index, entry in enumerate(entries):
        name = entry["name"]
        if name not in users:
            telegram_user_id = _demo_user_id(name)
            user = session.scalar(
                select(User).where(User.telegram_user_id == telegram_user_id)
            )
            if user is None:
                user = User(telegram_user_id=telegram_user_id, first_name=name)
                session.add(user)
                session.flush()
            users[name] = user
        session.add(
            Message(
                chat_id=chat.id,
                user_id=users[name].id,
                telegram_message_id=-(index + 1),
                text=entry["text"],
                sent_at=_parse_dt(entry["sent_at"]),
            )
        )

    session.flush()
    return chat


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m scripts.seed_demo <transcript.json> [telegram_chat_id]")
        return

    with open(sys.argv[1], encoding="utf-8") as fh:
        entries = json.load(fh)
    chat_id = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_CHAT_ID

    with SessionLocal() as session:
        chat = seed_transcript(session, chat_id, entries)
        session.commit()
        print(
            f"Seeded {len(entries)} messages into chat "
            f"{chat.telegram_chat_id} (id {chat.id})."
        )


if __name__ == "__main__":
    main()
