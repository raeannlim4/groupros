from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from bot import config


class Base(DeclarativeBase):
    pass


class Chat(Base):
    __tablename__ = "chat"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    title: Mapped[str | None] = mapped_column(String(255))
    weekly_report_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    link_tip_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(255))
    first_name: Mapped[str | None] = mapped_column(String(255))
    can_dm: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ChatMember(Base):
    __tablename__ = "chatmember"
    __table_args__ = (UniqueConstraint("chat_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chat.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    last_catchup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    nudged_for_dm: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )


class Message(Base):
    __tablename__ = "message"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chat.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    telegram_message_id: Mapped[int] = mapped_column(BigInteger, index=True)
    text: Mapped[str] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    chunked: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", index=True
    )


class MessageChunk(Base):
    __tablename__ = "messagechunk"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chat.id", ondelete="CASCADE"))
    first_message_id: Mapped[int] = mapped_column(ForeignKey("message.id"))
    last_message_id: Mapped[int] = mapped_column(ForeignKey("message.id"))
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(config.EMBEDDING_DIM))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Task(Base):
    __tablename__ = "task"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chat.id", ondelete="CASCADE"))
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    title: Mapped[str] = mapped_column(Text)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    source: Mapped[str] = mapped_column(
        String(16), default="ai", server_default="ai"
    )
    source_message_id: Mapped[int | None] = mapped_column(BigInteger)
    confirm_message_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Reminder(Base):
    __tablename__ = "reminder"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("task.id", ondelete="CASCADE"))
    remind_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str] = mapped_column(String(16))


class Decision(Base):
    __tablename__ = "decision"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chat.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    text: Mapped[str] = mapped_column(Text)
    telegram_message_id: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ProjectBrief(Base):
    __tablename__ = "projectbrief"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chat.id", ondelete="CASCADE"), unique=True
    )
    facts: Mapped[dict] = mapped_column(JSON)
    source_filename: Mapped[str | None] = mapped_column(String(255))
    uploaded_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
