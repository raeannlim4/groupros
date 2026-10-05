import os

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _to_psycopg_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


TELEGRAM_BOT_TOKEN = _require("TELEGRAM_BOT_TOKEN")
DATABASE_URL = _to_psycopg_url(_require("DATABASE_URL"))

LLM_BASE_URL = _require("LLM_BASE_URL")
LLM_API_KEY = _require("LLM_API_KEY")
CAPTURE_MODEL = _require("CAPTURE_MODEL")
ASK_MODEL = _require("ASK_MODEL")

EMBEDDING_BASE_URL = _require("EMBEDDING_BASE_URL")
EMBEDDING_API_KEY = _require("EMBEDDING_API_KEY")
EMBEDDING_MODEL = _require("EMBEDDING_MODEL")
EMBEDDING_DIM = int(_require("EMBEDDING_DIM"))

DEFAULT_TZ = os.environ.get("DEFAULT_TZ", "Asia/Singapore")
PRIVACY_URL = os.environ.get("PRIVACY_URL") or None
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
