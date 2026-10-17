# Group Project Bot

A Telegram bot that helps a student project group stay on top of tasks. It sits in
a group chat and handles the coordination nobody wants to do: noticing when someone
commits to something, tracking deadlines, logging decisions, summarising what you
missed, turning a meeting transcript into minutes, and reading the assignment brief
so the group and the AI share one source of truth.

Core principle: **the AI suggests, humans decide.** The bot never creates, changes or
deletes a task without a human tapping a button.

## What it does

- **Task capture** — reads group messages and spots commitments ("I'll do the slides").
  It asks before adding anything, and the person who made the commitment confirms.
- **Task board** — who is doing what and what is due, with claim and done buttons.
- **Reminders** — private DMs before a deadline, with on-track / need-more-time buttons.
- **Decisions** — log and recall group decisions.
- **Catch-up** — a private summary of what you missed.
- **Meeting minutes** — send a Zoom transcript, get decisions and action items.
- **Project brief** — send the assignment brief, get the key facts saved.
- **Plan** — turn the brief's deadline into milestones you can claim as tasks.

## Commands

| Command | What it does | Example |
| --- | --- | --- |
| `/start` | In a group, shows help. In a DM, enables private reminders. | `/start` |
| `/help` | Lists every command. | `/help` |
| `/tasks` | Shows the task board. | `/tasks` |
| `/mytasks` | Your open tasks across all your groups. | `/mytasks` |
| `/addtask <text>` | Add a task yourself (you own it). | `/addtask Essay draft by Friday` |
| `/addtask open <text>` | Add an unclaimed task anyone can claim. | `/addtask open Buy poster board` |
| `/decide <text>` | Log a group decision. | `/decide We meet Tuesdays 7pm` |
| `/decisions` | Show recent group decisions. | `/decisions` |
| `/ask <question>` | Ask about past discussion. | `/ask when is the final deadline?` |
| `/catchup` | Private summary since your last catch-up. | `/catchup` |
| `/catchup 2d` | Last 2 days (any days/hours/minutes/weeks). | `/catchup 2d` |
| `/catchup here` | Post the summary in the group instead. | `/catchup here` |
| `/meeting` | Summarise a transcript (`.vtt`, `.txt` or pasted text). | Send a `.vtt` with caption `/meeting` |
| `/brief` | Read the assignment brief and save its key facts. | Send the brief PDF with caption `/brief` |
| `/brief set <field> <value>` | Override a brief date. | `/brief set deadline 20 Nov` |
| `/plan` | Turn the brief's deadline into milestones. | `/plan` |
| `/plan by <date>` | Plan against a date instead of the brief. | `/plan by 20 Nov` |
| `/forget` | Delete your stored messages. | `/forget` |

## Local setup (Windows)

1. Install **Python 3.13** (the repo has a `.python-version` file; Railway reads it too).
2. Get the code and create a virtual environment:

   ```
   git clone <your-repo-url>
   cd hackday
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. Copy the example env file and fill it in:

   ```
   copy .env.example .env
   ```

4. Create a **separate dev bot** with @BotFather and put its token in `.env`. Never run
   the dev bot and the production bot on the same token at the same time — Telegram
   allows only one long-polling connection per token, so one of them will fail.

5. Run the bot:

   ```
   python -m bot.main
   ```

6. Run the unit tests:

   ```
   python -m unittest discover -s tests
   ```

## Environment variables

All configuration comes from environment variables (loaded from `.env` locally, set in
the Railway dashboard in production). `.env.example` lists every one.

| Variable | Required | What it is |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | yes | Bot token from @BotFather. |
| `DATABASE_URL` | yes | Postgres connection string (Supabase Session pooler). `postgres://` and `postgresql://` are rewritten to `postgresql+psycopg://` automatically. |
| `LLM_BASE_URL` | yes | Base URL of an OpenAI-compatible chat API. |
| `LLM_API_KEY` | yes | API key for the chat provider. |
| `CAPTURE_MODEL` | yes | Model for the frequent, small calls (capture, parsing). |
| `ASK_MODEL` | yes | Stronger model for `/ask`, `/catchup`, `/brief`, `/plan`, `/meeting`. |
| `LLM_REASONING` | no | `off` disables the model's reasoning/thinking. Default `off`. |
| `LLM_PROVIDER_SORT` | no | OpenRouter provider routing, e.g. `throughput`. Default `throughput`. |
| `CAPTURE_MAX_TOKENS` | no | Response cap for capture calls. Default `200`. |
| `ASK_MAX_TOKENS` | no | Response cap for `/ask` and other large calls. Default `600`. |
| `EMBEDDING_BASE_URL` | yes | Base URL of an OpenAI-compatible embeddings API. |
| `EMBEDDING_API_KEY` | yes | API key for the embeddings provider. |
| `EMBEDDING_MODEL` | yes | Embedding model name. |
| `EMBEDDING_DIM` | yes | Output size of the embedding model; must match the model. |
| `DEFAULT_TZ` | no | Timezone for display. Default `Asia/Singapore`. |
| `ASK_MAX_DISTANCE` | no | Cosine-distance cutoff below which `/ask` skips the LLM. Default `0.6`. |
| `PRIVACY_URL` | no | If set, the intro message shows a "Privacy info" button. |
| `LOG_LEVEL` | no | Python log level. Default `INFO`. Keep it at `INFO` in production. |

## Deploying on Railway

1. Push the repo to GitHub and connect it as a new Railway project.
2. Set every variable from the table above in the Railway **Variables** tab.
3. Set the start command to:

   ```
   python -m bot.main
   ```

   Railway reads `.python-version` (Python 3.13) and `requirements.txt` automatically.
4. **Only one instance may poll at a time.** Long polling does not allow two
   connections on the same token. Before deploying, stop your local bot, or run the
   local one with a separate dev bot token. If you scale to more than one replica,
   exactly one must poll.
5. The database tables are created on startup. The `vector` extension is created first.

## Tests

```
python -m unittest discover -s tests
```

Covers time conversion and display, link building, VTT cleaning, brief grounding and
RAG chat-scoping.

## Known issues

- **Button state is in memory.** Plan, meeting and brief confirmations live in the
  process. After a restart or redeploy, old buttons reply that the action expired and
  ask you to run the command again.
- **Schema changes are not migrated.** Tables are created with `metadata.create_all`,
  which creates missing tables but does not add columns to existing tables. Any future
  column must be added to the database by hand (no Alembic by design).
- **`EMBEDDING_DIM` changes invalidate embeddings.** If you change the embedding model
  or its dimension, drop the `messagechunk` table and set `chunked = false` on all
  messages so they are re-embedded.
- **`/brief set` only takes milestone names or `deadline`.** Other scalar facts are
  updated by re-sending the brief.
- **Only direct dependencies are pinned.** Transitive packages (for example httpx or
  APScheduler, pulled in by the Telegram and OpenAI SDKs) are resolved at install time.
- **The weekly report is not in this repo.** It runs as an n8n workflow that queries the
  same database.
- **`/ask` logs its full context at `DEBUG`.** This includes message text. Production
  runs at `INFO`, so it is not emitted, but do not run production at `DEBUG`.
