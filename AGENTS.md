# SPEC.md — Telegram Group Coordination Bot

You are helping me build a Telegram bot for student project groups. Read this whole file before writing any code. Follow it strictly. If something is ambiguous, make the simplest reasonable choice consistent with this spec and list your assumptions at the end. Only ask before deleting data or making changes that are hard to undo.

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
7. **Meeting minutes**: `/meeting` on a Zoom transcript (`.vtt`) or plain text posts a summary of the discussion — decisions and action items — with buttons to log decisions and add tasks.

**Planning (week 3):**
8. **Project brief**: `/brief` reads the assignment brief (PDF, Word or text) and saves the key facts — deadline, presentation, deliverables, weighting — so the group and the AI share one source of truth.
9. **Plan**: `/plan` turns the brief's final deadline into 4–7 milestones you can claim as unclaimed tasks.

**Built in n8n, not Python (week 2):**
10. **Weekly report**: every Sunday evening, posts completed tasks, upcoming deadlines and decisions.

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
# Stronger model (/ask, /catchup, /meeting)
ASK_MODEL=
# Disable model reasoning for speed (set to `off` to skip it; leave blank to keep default)
LLM_REASONING=
# Ask the provider to sort by throughput (set to `throughput`; leave blank to keep default)
LLM_PROVIDER_SORT=
# Max output tokens per call
CAPTURE_MAX_TOKENS=200
ASK_MAX_TOKENS=600

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
  timeutil.py  links.py  vtt.py  documents.py  brief.py
  handlers/
    onboarding.py    # /start, bot added to group, /forget, /help
    capture.py       # store messages, task capture, confirm buttons
    tasks.py         # /tasks, mark done, claim
    decisions.py     # /decide
    reminders.py     # reminder job, DM buttons
    ask.py           # /ask
    catchup.py       # /catchup
    meeting.py       # /meeting (transcript summaries)
    brief.py         # /brief (assignment brief facts)
    plan.py          # /plan (milestones)
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
- **Task**: id, chat_id, owner_user_id (nullable = unclaimed), title, due_at (nullable), status (`pending`, `open`, `done`, `rejected`), source (`ai`, `manual`), source_message_id, confirm_message_id, created_at, completed_at
- **Reminder**: id, task_id, remind_at, sent_at (nullable), kind (`day_before`, `due_soon`)
- **Decision**: id, chat_id, user_id, text, telegram_message_id, created_at
- **ProjectBrief**: id, chat_id (unique), facts (JSON), source_filename, uploaded_by_user_id, created_at

## 7. Feature specs

### 7.1 Onboarding
- **Bot added to group**: post one intro message: what it does, what it stores (group text messages, tasks, decisions), that messages are processed by AI providers, and that `/forget` deletes your data. Buttons: "Enable private reminders" → `https://t.me/<bot_username>?start=enable`, and "Privacy info" → `PRIVACY_URL` if set.
- **Group title**: keep `Chat.title` current — update it whenever the bot sees a message in that group and when Telegram sends a group-title-changed event.
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
- `/mytasks`: in private, list the user's open tasks across all their groups, each group's tasks under a bold group-title header, sorted by due date within the group, each task with a `✅` button to mark it done. In a group, reply "Send /mytasks to me privately to see tasks across all your groups."

### 7.3a Manual task creation
- `/addtask <text>` (group only) parses a title and due date using the same LLM date logic as capture (no date → no deadline, with the 120-day sanity check). Owner is the sender. No confirmation: create the Task as `open` with `source='manual'`, create reminders, and reply `✅ Added: <task> (<Name>, due <date>)` with an `[↩️ Undo]` button that only the creator can use (deletes the task and its reminders, edits the message to "Removed").
- `/addtask open <text>`: same, but `owner_user_id = NULL` (unclaimed). Reply `📋 Added unclaimed: <task> — anyone can claim it from /tasks`. No reminders until someone claims it.
- `/addtask` with no text: show usage covering both forms.
- `Task.source` is `'ai'` for captured tasks and `'manual'` for `/addtask`.

### 7.4 Decisions
- `/decide <text>` stores a Decision and replies `📝 Logged: <text>`. Without text, show usage.
- `/decisions`: show the latest 10 decisions for this group, newest first, each line `• <text> — <name>, <date>` with a link to the original `/decide` message when `links.message_link` returns one (skip the link otherwise). A `🗑` button per decision (short label) lets only the person who logged it delete it; others get an alert. Deleting refreshes the list in place. If there are none, reply "No decisions logged yet. Use /decide <text> to log one."
- Decisions are included in `/ask` context, `/catchup` and the weekly report.

### 7.5 Reminders
- When a task becomes `open` with a due date: create Reminders at **due − 24h** and **due − 3h**, skipping past times.
- `job_queue.run_repeating(..., interval=60)`: find reminders with `remind_at <= now`, `sent_at IS NULL`, task `open`. DM the owner: `⏰ [<group title>] "<task>" is due <relative time>.` (group title from the task's chat) with `[👍 On track] [⏰ Need more time]`. Set `sent_at`.
- DM fails with `telegram.error.Forbidden` → set `can_dm=False`, mark reminder sent; if `nudged_for_dm` is False, post once in the group: `@<name>, tap below to get private deadline reminders` with the enable button, then set `nudged_for_dm=True`. Never mention the task in the group.
- **On track** → edit to "Great, good luck!"
- **Need more time** → show `[+1 day] [+2 days] [+1 week]` and `[📅 Pick a day]`.
  - **Pick a day** shows the next 7 days as buttons labelled like `Fri 9 Oct` (2 per row), plus `[← Back]`. Choosing a day sets the deadline to 11:59pm that day in `DEFAULT_TZ`.
  - Options whose new deadline would not be later than the current deadline are hidden/skipped.
  - After choosing, delete unsent reminders, create new ones, then ask `Tell <group title> about the new deadline? [Yes] [Keep private]`. If yes, post neutrally to the task's own group: `🗓 Updated: "<task>" is now due <date>.`
  - Callback data stays under 64 bytes, e.g. the picked day is `rem_day:<task_id>:<YYYY-MM-DD>`.

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
- Otherwise the LLM produces at most 6 bullets, in this order: decisions made, files/links shared, schedule changes, tasks, anything that mentions the requesting user by name or asks for their input ("Needs your input"), then the off-topic bullet last. Cap input at ~100,000 characters (most recent messages win).
- The LLM must mark every bullet as project-related or off-topic. Off-topic items (decisions or discussion unrelated to the group's project) are combined into ONE bullet placed last, with NO citation links, using neutral wording only, e.g. "• Also logged: 3 decisions unrelated to the project." Never omit off-topic items.
- The prompt must forbid describing messages or people with judgmental or emotional labels (e.g. "romantic", "hostile", "rude", "weird"). Describe what was decided or shared, never characterise it.
- Each project bullet may cite at most 2 messages (same citation + link logic as /ask); pick the most relevant. The off-topic bullet has no citations.
- **Send privately** if `can_dm`; otherwise reply in the group. The keyword `here` (`/catchup here`, `/catchup 2d here`, `/catchup here 2d`) posts the summary in the group instead; any other input shows usage. When posted in the group, "Needs your input" must name the requester, e.g. "Needs Raeann's input:". Update only the requester's `last_catchup_at`, whichever way it is sent.

### 7.9 Meeting minutes (`/meeting`)
- `/meeting` works in a group as the caption of a document, or as a reply to a document or a text message. Accepted inputs: `.vtt` (Zoom audio transcript), `.txt`, or plain text. With no input, show usage: the host downloads the transcript (`.vtt`) from Zoom → Recordings & Transcripts, then sends it to the group with `/meeting`.
- Preprocess `.vtt`: drop the `WEBVTT` header, cue numbers and repeated timestamps; keep each cue's start time (`mm:ss` or `h:mm:ss`) and the speaker name when the line starts with `Name: `. Merge consecutive cues by the same speaker into one line formatted `[mm:ss] Name: text`. Cap the cleaned text at ~150,000 characters; if longer, reply that the transcript is too long.
- The LLM returns JSON: `{"title", "duration_minutes", "attendees": [names], "topics": [...], "decisions": [{"text", "timestamp", "speaker"}], "action_items": [{"owner_name" or null, "task", "due" ISO or null, "timestamp"}], "open_questions": [...]}`. Same date rules and 120-day check as `/addtask`, relative to the meeting date (the file date, or today).
- Grounding: drop any decision or action item whose timestamp is not in the cleaned transcript. If nothing at all survives, reply "I couldn't find clear decisions or action items in this transcript." and still show the topics.
- Post in the group in the format: 📋 header with date and duration, 👥 attendees, Discussed, Decisions (with `(mm:ss, Speaker)`), Action items (with `(mm:ss)`), Open questions.
- Each decision gets a `[📝 Log]` button (anyone can tap; logs via the Decision table; the button disappears or shows ✅ once logged).
- Each action item gets a `[📌 Add task]` button. Match `owner_name` to a member of this chat by first name or username (case-insensitive). If matched, only that person can confirm (same alert pattern as capture). If unmatched or null, confirming creates an unclaimed task. Confirmed tasks get reminders as normal.
- Store the posted minutes (not the transcript) as a bot Message row so the chunker indexes it for `/ask` and `/catchup`. Never store the transcript file contents anywhere.

### 7.10 Project brief (`/brief`)
- `/brief` works in a group as the caption of, or a reply to, a `.pdf`, `.docx` or `.txt` file, or as pasted text. Extract text with `pypdf` (PDF) and `python-docx` (Word). Same size limits and error handling as `/meeting` (too long, empty, unsupported type, unreadable). If a PDF yields almost no text, reply: "This PDF looks scanned (images only), so I can't read it. Try a text-based PDF or paste the text."
- The LLM returns JSON facts with `milestones` as a list: `[{"name", "due_text", "due_date" (ISO or null), "deliverables": [...], "quote"}]`, plus `word_limit`, `group_size`, `weighting` (each `value`/`date`/`quote`). Each milestone's `deliverables` are the submissions due at that milestone. A milestone is a deadline by which something must be submitted or presented; the final submission deadline is the last milestone. Deadlines exclude advice ("start 3 weeks before the deadline"), post-project housekeeping ("after Week 18") and teaching-team activities ("in Week 5 or 6 we will demo"). For a presentation, record in the milestone whether it is live or recorded and its maximum length. Relative/teaching-week dates (e.g. "Week 11") stay as text with `due_date` null — never guess a date.
- Grounding: normalise the document and each quote (lowercase, fold curly quotes/dashes to plain, remove every non-alphanumeric character), then drop any milestone, deliverable or scalar fact whose `quote` is not found. This tolerates the broken spacing pypdf leaves in Word-exported PDFs. Log at INFO how many facts were extracted vs kept, and at DEBUG each dropped quote. The same normalisation is used wherever the bot checks a quote against source text, including `/meeting`.
- Save one `ProjectBrief` per chat, replacing the previous one: the facts JSON, the source filename, who uploaded it, and when. Never store the full document text.
- Post the summary in the group, grouped by milestone: 🗓 each milestone with its date (or teaching-week text) and its deliverables beneath it, then ⚖️ weighting, 📝 word limit, 👥 group size, then "❓ Not stated in brief:" listing missing key fields.
- `/brief` with no input shows the saved brief if one exists, otherwise usage.
- `/brief set <milestone name or "deadline"> <date>` sets `due_date` for that milestone, using the same LLM date logic as `/addtask` ("deadline" = the last milestone; the name is fuzzy-matched against milestone names). Show who changed it. Never tell the user to upload the brief when one is already saved.
- The brief's facts are included in `/ask` context as a clearly labelled authoritative "PROJECT BRIEF" section (above decisions). Citations to brief facts render as "(from brief)" with no link.

### 7.11 Plan (`/plan`)
- `/plan` in a group plans the saved brief's milestones that have a `due_date` in the future, each separately, working back from its own date. `/plan by <date>` plans to that date instead. If the brief is not saved and no date is given, show usage.
- For each future milestone the LLM generates 4–7 tasks working backwards from that milestone's date, based on its deliverables, leaving a buffer day. It receives the chat's existing open tasks and must not duplicate them. All task dates must be between today and the milestone's date inclusive; drop any that are not. The same task is never generated twice across milestones.
- Milestones whose date is in the past are ignored with a one-line note each: "Skipping <name> milestone — already past".
- If no future milestone has a `due_date`, reply: "Your brief gives deadlines as teaching weeks, not dates: <each due_text>. Set one with /brief set deadline <date>, then run /plan again." Never tell the user to upload the brief when one is already saved.
- Show the plan with `[Create as unclaimed tasks] [Cancel]`. Anyone can tap. **Create** inserts `open`, unclaimed tasks (`owner_user_id = NULL`, `source = 'plan'`) and edits the message to "✅ Added N tasks — claim them on /tasks". **Cancel** edits it to "Plan dismissed".

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
8. `/meeting`: transcript summaries with decisions and action items.
9. `/brief` and `/plan`: project brief facts and milestone plans.

## Parking lot
(Ideas go here first. Only build after the current phase works and is committed.)

- Edit task title/deadline from the board (owner only). Revisit after Phase 6.
- `/setname` for custom group nicknames.
- Assign tasks to others with assignee confirmation.
- Reply to a missed message with `/addtask` to capture it.
- Fetch Zoom cloud recordings and transcripts via the Zoom API.
- Speech-to-text for Telegram voice notes.