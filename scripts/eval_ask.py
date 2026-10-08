from __future__ import annotations

import asyncio
import re
from datetime import timedelta

from sqlalchemy import delete, select

from bot.db import SessionLocal
from bot.embeddings import embed_texts
from bot.handlers.ask import NOT_FOUND, answer_for_chat, build_context
from bot.llm import answer_question
from bot.models import Chat, Decision, Message, MessageChunk, User
from bot.rag import chunker
from bot.rag.retrieve import retrieve_chunks
from bot.timeutil import now_utc
from scripts.seed_demo import seed_transcript

DEMO_CHAT_ID = -999900000002
DECISION_ID = 7777
DECISION_USER_ID = -800001
DECISION_QUESTION = "how long is the presentation"
NAMES = ["Wei Ming", "Priya", "Jun Hao", "Aisha", "Ravi"]
ID_RE = re.compile(r"\[(-?\d+)\]")

TOPICS = [
    ("the database", "we decided to use Postgres with pgvector for storage", "What database are we using?"),
    ("the meeting", "let's meet on Thursday at 6pm in the library", "When and where is the next meeting?"),
    ("the survey", "the survey will use Google Forms and be shared in the chat", "What tool are we using for the survey?"),
    ("the budget", "our total budget is capped at 50 dollars", "What is our budget for the project?"),
    ("the poster", "the poster should use the school colours and our mascot", "What should the poster look like?"),
    ("the demo", "the demo will run on my laptop at the showcase", "Where will the demo run?"),
    ("user testing", "Priya will handle the user testing sessions", "Who is doing the user testing?"),
    ("the report", "the final report is due on the last Friday of term", "When is the final report due?"),
    ("the weather API", "we will call the weather API every hour", "How often do we call the weather API?"),
    ("the video", "the video should be no longer than three minutes", "How long should the video be?"),
]


def build_dataset(now):
    base = now - timedelta(hours=8)
    entries = []
    expected = []
    index = 0
    for topic_index, (keyword, fact, question) in enumerate(TOPICS):
        for i in range(8):
            text = fact if i == 0 else f"{keyword} note {i}, nothing decided"
            entries.append(
                {
                    "name": NAMES[index % len(NAMES)],
                    "text": text,
                    "sent_at": (base + timedelta(minutes=5 * index)).isoformat(),
                }
            )
            index += 1
        expected.append((question, -(topic_index * 8 + 1)))
    return entries, expected


def _ids_in_chunks(chunks) -> set[int]:
    ids: set[int] = set()
    for chunk in chunks:
        ids.update(int(match) for match in ID_RE.findall(chunk.text))
    return ids


def _cleanup() -> None:
    with SessionLocal() as session:
        chat = session.scalar(
            select(Chat).where(Chat.telegram_chat_id == DEMO_CHAT_ID)
        )
        if chat is None:
            return
        session.execute(delete(MessageChunk).where(MessageChunk.chat_id == chat.id))
        session.execute(delete(Decision).where(Decision.chat_id == chat.id))
        session.execute(delete(Message).where(Message.chat_id == chat.id))
        session.delete(chat)
        session.commit()


async def main() -> None:
    now = now_utc()
    entries, expected = build_dataset(now)

    _cleanup()
    with SessionLocal() as session:
        chat = seed_transcript(session, DEMO_CHAT_ID, entries, title="Eval group")
        user = session.scalar(
            select(User).where(User.telegram_user_id == DECISION_USER_ID)
        )
        if user is None:
            user = User(telegram_user_id=DECISION_USER_ID, first_name="Raeann")
            session.add(user)
            session.flush()
        session.add(
            Decision(
                chat_id=chat.id,
                user_id=user.id,
                text="presentation will be 10 minutes",
                telegram_message_id=DECISION_ID,
            )
        )
        session.commit()
        chat_db_id = chat.id
    with SessionLocal() as session:
        await chunker._process_chat(session, chat_db_id, now)

    retrieved_hits = 0
    cited_hits = 0
    for question, expected_id in expected:
        embedding = (await embed_texts([question]))[0]
        chunks = await retrieve_chunks(chat_db_id, embedding)
        got_retrieved = expected_id in _ids_in_chunks(chunks)
        with SessionLocal() as session:
            context_text, _ = build_context(session, chat_db_id, chunks)
        answer = await answer_question(question, context_text)
        got_cited = f"[[{expected_id}]]" in answer
        retrieved_hits += got_retrieved
        cited_hits += got_cited
        print(
            f"{'ok ' if got_retrieved else 'MISS'} retrieved | "
            f"{'ok ' if got_cited else 'MISS'} cited | {question}"
        )

    total = len(expected)
    print(f"\nretrieved {retrieved_hits}/{total}  cited {cited_hits}/{total}")

    embedding = (await embed_texts([DECISION_QUESTION]))[0]
    chunks = await retrieve_chunks(chat_db_id, embedding)
    with SessionLocal() as session:
        context_text, _ = build_context(session, chat_db_id, chunks)
    answer = await answer_question(DECISION_QUESTION, context_text)
    decision_ok = f"[[{DECISION_ID}]]" in answer
    print(
        f"{'ok ' if decision_ok else 'MISS'} decision cited | {DECISION_QUESTION}"
    )
    print(f"decision cited {1 if decision_ok else 0}/1")

    with SessionLocal() as session:
        empty = Chat(telegram_chat_id=-999900000003, title="Empty group")
        session.add(empty)
        session.commit()
        empty_id = empty.id
    empty_reply = await answer_for_chat(
        empty_id, -999900000003, "what is the capital of France"
    )
    empty_ok = empty_reply == NOT_FOUND
    print(f"{'ok ' if empty_ok else 'MISS'} not-found reply | empty chat")
    print(f"not-found {1 if empty_ok else 0}/1")
    with SessionLocal() as session:
        session.delete(session.get(Chat, empty_id))
        session.commit()

    _cleanup()


if __name__ == "__main__":
    asyncio.run(main())
