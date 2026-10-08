from __future__ import annotations

import asyncio
import unittest

from sqlalchemy import delete

from bot import config
from bot.db import SessionLocal
from bot.handlers.ask import build_context
from bot.models import Chat, Decision, Message, MessageChunk, User
from bot.rag.retrieve import search_chunks
from bot.timeutil import now_utc


def _vector() -> list[float]:
    vector = [0.0] * config.EMBEDDING_DIM
    vector[0] = 1.0
    return vector


class RagScopeTest(unittest.TestCase):
    def setUp(self) -> None:
        with SessionLocal() as session:
            user = User(telegram_user_id=-700001, first_name="Scope")
            chat_a = Chat(telegram_chat_id=-100700000001, title="Chat A")
            chat_b = Chat(telegram_chat_id=-100700000002, title="Chat B")
            session.add_all([user, chat_a, chat_b])
            session.flush()

            message_a = Message(
                chat_id=chat_a.id,
                user_id=user.id,
                telegram_message_id=101,
                text="alpha message",
                sent_at=now_utc(),
            )
            message_b = Message(
                chat_id=chat_b.id,
                user_id=user.id,
                telegram_message_id=202,
                text="beta message",
                sent_at=now_utc(),
            )
            session.add_all([message_a, message_b])
            session.flush()

            session.add_all(
                [
                    MessageChunk(
                        chat_id=chat_a.id,
                        first_message_id=message_a.id,
                        last_message_id=message_a.id,
                        text="[101] Chat A chunk",
                        embedding=_vector(),
                    ),
                    MessageChunk(
                        chat_id=chat_b.id,
                        first_message_id=message_b.id,
                        last_message_id=message_b.id,
                        text="[202] Chat B chunk",
                        embedding=_vector(),
                    ),
                    Decision(
                        chat_id=chat_a.id,
                        user_id=user.id,
                        text="alpha decision",
                        telegram_message_id=111,
                    ),
                    Decision(
                        chat_id=chat_b.id,
                        user_id=user.id,
                        text="beta decision",
                        telegram_message_id=222,
                    ),
                ]
            )
            session.commit()
            self.user_id = user.id
            self.a_id = chat_a.id
            self.b_id = chat_b.id

    def tearDown(self) -> None:
        with SessionLocal() as session:
            session.execute(
                delete(MessageChunk).where(
                    MessageChunk.chat_id.in_([self.a_id, self.b_id])
                )
            )
            session.execute(
                delete(Decision).where(Decision.chat_id.in_([self.a_id, self.b_id]))
            )
            session.execute(
                delete(Message).where(Message.chat_id.in_([self.a_id, self.b_id]))
            )
            session.query(Chat).filter(Chat.id.in_([self.a_id, self.b_id])).delete(
                synchronize_session=False
            )
            session.query(User).filter(User.id == self.user_id).delete(
                synchronize_session=False
            )
            session.commit()

    def test_search_chunks_is_chat_scoped(self) -> None:
        results = asyncio.run(search_chunks(self.a_id, _vector()))
        self.assertTrue(results)
        for chunk, _ in results:
            self.assertEqual(chunk.chat_id, self.a_id)
        self.assertNotIn("Chat B chunk", " ".join(c.text for c, _ in results))

    def test_context_decisions_are_chat_scoped(self) -> None:
        with SessionLocal() as session:
            context, sources = build_context(session, self.a_id, [])
        self.assertIn(111, sources)
        self.assertNotIn(222, sources)
        self.assertIn("alpha decision", context)
        self.assertNotIn("beta decision", context)


if __name__ == "__main__":
    unittest.main()
