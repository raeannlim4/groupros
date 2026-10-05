from __future__ import annotations

import logging

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from bot import config
from bot.models import Base, Chat, ChatMember, User

logger = logging.getLogger(__name__)

engine = create_engine(config.DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db() -> None:
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

    Base.metadata.create_all(engine)

    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            conn.execute(
                text(f'ALTER TABLE "{table.name}" ENABLE ROW LEVEL SECURITY')
            )
    logger.info("Database ready (%d tables).", len(Base.metadata.tables))


def get_or_create_chat(
    session: Session, telegram_chat_id: int, title: str | None = None
) -> Chat:
    chat = session.scalar(
        select(Chat).where(Chat.telegram_chat_id == telegram_chat_id)
    )
    if chat is None:
        chat = Chat(telegram_chat_id=telegram_chat_id, title=title)
        session.add(chat)
        session.flush()
    elif title and chat.title != title:
        chat.title = title
    return chat


def get_or_create_chat_member(
    session: Session, chat_id: int, user_id: int
) -> ChatMember:
    member = session.scalar(
        select(ChatMember).where(
            ChatMember.chat_id == chat_id, ChatMember.user_id == user_id
        )
    )
    if member is None:
        member = ChatMember(chat_id=chat_id, user_id=user_id)
        session.add(member)
        session.flush()
    return member


def get_or_create_user(
    session: Session,
    telegram_user_id: int,
    username: str | None = None,
    first_name: str | None = None,
) -> User:
    user = session.scalar(
        select(User).where(User.telegram_user_id == telegram_user_id)
    )
    if user is None:
        user = User(
            telegram_user_id=telegram_user_id,
            username=username,
            first_name=first_name,
        )
        session.add(user)
        session.flush()
    else:
        if username is not None:
            user.username = username
        if first_name is not None:
            user.first_name = first_name
    return user
