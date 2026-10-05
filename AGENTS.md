# SPEC.md — Telegram Group Coordination Bot

You are helping me build a Telegram bot for student project groups. Read this whole file before writing any code. Follow it strictly. If something here is ambiguous or seems wrong, ask me instead of guessing.

## 1. What the bot does

The bot sits in a group chat and handles coordination nobody wants to do.

**Core (week 1):**
1. **Task capture**: notices when someone commits to doing something ("ok I'll do the survey by Wed") and asks them to confirm before saving it.
2. **Task board**: `/tasks` shows who is doing what and what is due.
3. **Private reminders**: DMs the owner before a deadline, with options to confirm or ask for more time.
4. **Decision log**: `/decide <text>` records an agreed decision.

**Memory (week 2):**
5. **Ask**: `/ask <question>` answers questions about past discussion using RAG, with links to source messages.
6. **Catch-up**: `/catchup` privately summarises what you missed.

**Built in n8n, not Python (week 2):**
7. **Weekly report**: every Sunday evening, posts completed tasks, upcoming deadlines and decisions.

**Stretch (only if everything above works):**
8. **Assignment breakdown**: `/plan` on an uploaded PDF brief suggests milestones as unclaimed tasks.

Core principle: **the AI suggests, humans decide.** The bot never creates, changes or deletes a task without a human tapping a button.

### Out of scope (do NOT build)
Webhooks for the Python bot, Telegram Mini Apps, web dashboards, contribution tracking, voice notes, Google Docs tracking, Chroma or any second database. If you think one is needed, ask me first.

## 2. Architecture

```
Telegram  <-- long polling -->  Python bot (Railway)  <-->  Postgres + pgvector (Supabase)
                                                                 ^
                                                                 | reads (SQL)
                                                                 |
                                 n8n (Railway)  -----------------+
                                   |
                                   +---- sendMessage via Telegram API
```

- **The Python bot is the ONLY thing that receives Telegram updates.** It uses polling.
- **n8n must never use the Telegram Trigger node.** A Telegram bot can only have one way of receiving updates; a Telegram Trigger would register a webhook and break the Python bot's polling. n8n may *send* messages with the Telegram node (that is safe).
- n8n reads the same Postgres database for scheduled reports. **Python never calls n8n**; there are no webhooks between them.
- Inline buttons in any message (even ones sent by n8n) are handled by the Python bot. Callback data format: `action:id`, e.g. `cap_add:42`. Max 64 bytes.

## 3. Tech stack (fixed — do not substitute)

- Python 3.11+
- `python-telegram-bot[job-queue]` **version 21 or newer**, modern async API: `Application.builder()`, `async def` handlers, `context.job_queue`. **Never** use the v13 API (`Updater`, `dispatcher`, `use_context=True`, capital-F `Filters`).
- Long polling mode (`application.run_polling()`).
- SQLAlchemy 2.0 ORM. Create tables on startup with `metadata.create_all`. Run `CREATE EXTENSION IF NOT EXISTS vector` before creating tables. No Alembic.
- **Postgres only, no SQLite** (pgvector needs Postgres). The database is hosted on **Supabase**, used purely as a plain Postgres server. `DATABASE_URL` comes from env (Supabase's Session pooler connection string). If it starts with `postgres://` or `postgresql://`, rewrite to `postgresql+psycopg://`. Use `psycopg[binary]` and the `pgvector` Python package (`pgvector.sqlalchemy.Vector`).
- Do **not** use the `supabase` Python client, Supabase Auth, Storage, Edge Functions or its REST API. Connect only through SQLAlchemy.
- After `create_all`, run `ALTER TABLE <table> ENABLE ROW LEVEL SECURITY` for every table the bot creates (safe to repeat). The bot connects as the database owner, which bypasses RLS, but this blocks Supabase's public Data API from reading the tables.
- Use `pool_pre_ping=True` on the engine so dropped connections are replaced automatically.
- The `openai` Python SDK for **both** chat and embeddings, used as a generic client for any OpenAI-compatible API. Always construct clients with `base_url` and `api_key` from env vars (`LLM_BASE_URL`/`LLM_API_KEY` for chat, `EMBEDDING_BASE_URL`/`EMBEDDING_API_KEY` for embeddings) so I can switch providers without code changes. Never hardcode a provider, model name or URL. Do not use provider-specific features (only `chat.completions.create` and `embeddings.create`).
- For JSON outputs, instruct the model in the prompt to return JSON only, then strip any ``` fences before parsing and validate with pydantic. Do not rely on provider-specific structured-output modes.
- `pydantic` v2 to validate all LLM JSON output.
- `pypdf` for PDF text (stretch phase only).
- `python-dotenv`, `zoneinfo`.

## 4. Configuration

`.env.example`:

```
TELEGRAM_BOT_TOKEN=
DATABASE_URL=

# Chat model (any OpenAI-compatible API)
LLM_BASE_URL=
LLM_API_KEY=
# Small, fast, cheap model (runs on most messages)
CAPTURE_MODEL=
# Stronger model (/ask, /catchup, /plan)
ASK_MODEL=

# Embeddings (any OpenAI-compatible API; can be a different provider)
EMBEDDING_BASE_URL=
EMBEDDING_API_KEY=
EMBEDDING_MODEL=
# Must match the embedding model's output size
EMBEDDING_DIM=
DEFAULT_TZ=Asia/Singapore
PRIVACY_URL=
LOG_LEVEL=INFO
```

If `EMBEDDING_DIM` changes after chunks exist, existing embeddings are invalid: the README must explain how to drop the `messagechunk` table and reset `chunked=False` to re-embed. `.gitignore` must include `.env`, `__pycache__/`, `.venv/`.

## 5. Project structure

```
bot/
  main.py  config.py  db.py  models.py
  llm.py             # ALL chat-model calls
  embeddings.py      # ALL embedding calls
  timeutil.py  links.py
  handlers/
    onboarding.py    # /start, bot added to group, /forget, /help
    capture.py       # store messages, task capture, confirm buttons
    tasks.py         # /tasks, mark done, claim
    decisions.py     # /decide
    reminders.py     # reminder job, DM buttons
    ask.py           # /ask
    catchup.py       # /catchup
    plan.py          # /plan (stretch)
  rag/
    chunker.py       # chunking job
    retrieve.py      # vector search
scripts/
  eval_capture.py  eval_ask.py  seed_demo.py
tests/
  test_timeutil.py  test_links.py  test_chunker.py
requirements.txt  .env.example  README.md
```

## 6. Data model

All datetimes are **timezone-aware UTC**. Convert to `DEFAULT_TZ` only for display and for telling the LLM the current time. Telegram IDs are BigInteger.

- **Chat**: id, telegram_chat_id (unique), title, weekly_report_enabled (bool, default True), created_at
- **User**: id, telegram_user_id (unique), username, first_name, can_dm (bool, default False), created_at
- **ChatMember**: chat_id, user_id, last_catchup_at (nullable), nudged_for_dm (bool, default False). Unique (chat_id, user_id).
- **Message**: id, chat_id, user_id, telegram_message_id, text, sent_at, chunked (bool, default False)
- **MessageChunk**: id, chat_id, first_message_id, last_message_id (Message.id), text, embedding (Vector(EMBEDDING_DIM)), created_at
- **Task**: id, chat_id, owner_user_id (nullable = unclaimed), title, due_at (nullable), status (`pending`, `open`, `done`, `rejected`), source_message_id, confirm_message_id, created_at, completed_at
- **Reminder**: id, task_id, remind_at, sent_at (nullable), kind (`day_before`, `due_soon`)
- **Decision**: id, chat_id, user_id, text, telegram_message_id, created_at

## 7. Feature specs

### 7.1 Onboarding
- **Bot added to group**: post one intro message: what it does, what it stores (group text messages, tasks, decisions), that messages are processed by AI providers, and that `/forget` deletes your data. Buttons: "Enable private reminders" → `https://t.me/<bot_username>?start=enable`, and "Privacy info" → `PRIVACY_URL` if set.
- **`/start` in private**: set `can_dm=True`, short welcome.
- **`/forget`**: confirm with a button, then delete the user's Message rows, delete every MessageChunk whose range included any of their messages, and set `chunked=False` on remaining messages in those ranges so they get re-chunked. Reply privately if possible.
- **`/help`**: list commands briefly.

### 7.2 Task capture
1. For every non-command group text message: upsert Chat, User, ChatMember; store Message.
2. Skip messages under 8 characters. Otherwise call `llm.classify_commitment()` with the text, sender's first name, and current date/time in `DEFAULT_TZ` including weekday.
3. LLM returns JSON only, validated with pydantic:
   `{"is_commitment": true, "task": "competitor research", "due": "2026-10-09T23:59:00+08:00", "confidence": 0.9}`
   - Only **self-commitments** by the sender count ("I'll do X", "I settle X", "can, I take X", "leave it to me"). Requests to others, questions, jokes and past-tense reports are not.
   - The prompt states the chat is between Singaporean students and may contain Singlish.
   - `due` is null if no deadline. Day with no time → 23:59 that day. "By Thursday" → next upcoming Thursday.
   - On parse/validation failure: log and do nothing.
4. If `is_commitment` and `confidence >= 0.7`: create a `pending` Task and reply **threaded to the original message**: `📌 Add task? <Name>: <task>, due <Thu 9 Oct, 11:59pm>` with `[✅ Add] [❌ No]`.
5. Only the owner can tap; others get an alert: "Only <Name> can confirm this."
6. **Add** → `open`, create reminders, edit message to `✅ Added: <task> (<Name>, due <date>)`. **No** → `rejected`, delete the bot message (fallback: edit to "Okay, not added").

### 7.3 Task board
- `/tasks` groups open tasks: **Overdue**, **Due in 48h**, **Later**, **No deadline**, **Unclaimed**, then last 5 **Done**. Line format: `• <task> — <Name> (due <date>)`.
- Buttons: `✅ <short title>` per open task (owner only), and `🙋 Claim: <short title>` per unclaimed task (anyone; claiming sets owner and creates reminders). Refresh the board in place after any tap.
- Stay under 4096 characters; truncate with "…and N more".

### 7.4 Decisions
- `/decide <text>` stores a Decision and replies `📝 Logged: <text>`. Without text, show usage.
- Decisions are included in `/ask` context, `/catchup` and the weekly report.

### 7.5 Reminders
- When a task becomes `open` with a due date: create Reminders at **due − 24h** and **due − 3h**, skipping past times.
- `job_queue.run_repeating(..., interval=60)`: find reminders with `remind_at <= now`, `sent_at IS NULL`, task `open`. DM the owner: `Hi! "<task>" is due <relative time>.` with `[👍 On track] [⏰ Need more time]`. Set `sent_at`.
- DM fails with `telegram.error.Forbidden` → set `can_dm=False`, mark reminder sent; if `nudged_for_dm` is False, post once in the group: `@<name>, tap below to get private deadline reminders` with the enable button, then set `nudged_for_dm=True`. Never mention the task in the group.
- **On track** → edit to "Great, good luck!"
- **Need more time** → `[+1 day] [+2 days]` → move due_at, delete unsent reminders, create new ones → `Tell the group? [Yes] [Keep private]`. If yes, post neutrally: `🗓 Updated: "<task>" is now due <date>.`

### 7.6 RAG ingestion (`rag/chunker.py`)
- Repeating job every 120 seconds. For each chat, take messages with `chunked=False` in order.
- Group into chunks of up to 8 consecutive messages. Only create a final partial chunk if its newest message is older than 10 minutes (so conversations aren't split mid-flow).
- Chunk text: each line `[<telegram_message_id>] <Thu 2 Oct 14:05> <Name>: <text>`. Prepend the 2 messages before the chunk as context, marked `(context)`.
- Embed chunks in batches via `embeddings.py`, store MessageChunk, set `chunked=True`.
- This also backfills: any stored messages not yet chunked get processed, including ones stored before this feature existed.

### 7.7 Ask
- `/ask <question>` in a group. Embed the question, retrieve the top 6 chunks for **that chat only** by cosine distance. Also include all Decisions from that chat (most recent 20).
- Call `llm.answer_question()`. System prompt: answer **only** from the provided context; if not found, say so; cite with `[[<telegram_message_id>]]`; max 3 sentences.
- Post-process: replace each `[[id]]` via `links.message_link(chat_id, id)`. **Drop any cited id that wasn't in the retrieved context.**
- `links.message_link`: only for supergroups (chat id starts with `-100`): `https://t.me/c/<id without -100>/<message_id>`. Otherwise return None and show the date instead.
- HTML parse mode, escape user text.

### 7.8 Catch-up
- `/catchup` in a group. Window: messages since that member's `last_catchup_at`; if null, last 24 hours. Optional argument like `/catchup 2d` overrides.
- If the window is under 5 messages, reply "Not much happened, you're up to date."
- Otherwise LLM produces at most 6 bullets in this order: decisions made, files/links shared, schedule changes, anything that mentions the requesting user by name or asks for their input ("Needs your input"). Each bullet cites one message (same citation + link logic as /ask). Cap input at ~100,000 characters (most recent messages win).
- **Send privately** if `can_dm`; otherwise reply in the group. Update `last_catchup_at`.

### 7.9 Plan (stretch)
- `/plan` as a reply to a PDF the bot can see, or as a caption on an uploaded PDF. Extract text with `pypdf` (cap ~60,000 chars).
- LLM returns JSON milestones: `[{"title": "...", "due": "ISO datetime"}]`, using the final deadline in the brief and leaving a revision buffer.
- Show the plan with `[Create as unclaimed tasks] [Cancel]`. On create, insert open tasks with `owner_user_id = NULL`.

## 8. n8n (built by me in the n8n UI, not by you)

The weekly report is an n8n workflow. Python's only job is to make the data easy to query. Do not build the weekly report in Python. Make sure table and column names match section 6 exactly, because the n8n SQL depends on them.

## 9. Display formats
- All user-facing dates are shown in `DEFAULT_TZ`. Absolute format: `Thu 9 Oct, 11:59pm` (no year unless it differs from the current year; no leading zero on the day or hour; lowercase am/pm).
- Relative phrasing in reminders: "today 11:59pm", "tomorrow 11:59pm", "in 3 hours" (under 12 hours away), otherwise the absolute format.
- Reminders are only ever sent by DM. If the owner can't receive DMs, the group gets only the one-time enable nudge, never the reminder itself.

## 10. Behaviour rules
- The bot speaks in the group only for: capture prompts, command replies, onboarding, the one-time DM nudge, and optional extension notices.
- All LLM calls go through `llm.py` / `embeddings.py`, with timeouts and exception handling. Commands reply "Sorry, something went wrong, try again." on failure; background jobs log and continue.
- Global error handler logs exceptions. Use `logging`, not `print`.

## 11. Testing and evaluation
- Unit tests: time conversion/display, link building, chunk grouping logic.
- Scripts are run from the repo root as modules, e.g. `python -m scripts.eval_capture`. `scripts/` has an `__init__.py`. No `sys.path` hacks and no `pyproject.toml`.
- `scripts/eval_capture.py`: ~30 labelled messages (commitments, requests to others, questions, jokes, Singlish). Prints precision and recall.
- `scripts/eval_ask.py`: ~10 question/expected-message-id pairs against seeded data. Prints how often the correct message was retrieved in the top 6, and how often the answer cited it.
- `scripts/seed_demo.py`: loads a JSON transcript (`{name, text, sent_at}`) into Message for a chat id, with fake Users and negative telegram_message_ids (no links for these).

## 12. Deployment (Railway)
- Start command: `python -m bot.main`.
- Only one instance may poll at a time. README must say to stop the local bot when the Railway bot is running (or use a separate dev bot token locally).
- Pin dependency versions.

## 13. How to work with me
- Build in the phases below. **Stop after each phase**, summarise in a few lines, and tell me exactly how to test it in Telegram. Wait for me before continuing.
- No features, files or dependencies beyond this spec without asking.
- When changing existing code, list changed files and why.
- Simple, readable code. I must be able to explain it to judges.

### Phases
1. Skeleton, config, DB with pgvector extension, models, `/start`, `/help`, group intro.
2. Message storage, task capture with confirm buttons, `eval_capture.py`.
3. `/tasks` (done + claim buttons) and `/decide`.
4. Reminders job, DM buttons, extension flow, DM nudge. Then I deploy to Railway.
5. Chunker job, embeddings, `/ask`, `seed_demo.py`, `eval_ask.py`.
6. `/catchup`.
7. `/forget` completeness, error handling review, tests, README.
8. (Stretch) `/plan`.

## Parking lot
(Ideas go here first. Only build after the current phase works and is committed.)