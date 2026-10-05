from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

from bot import config


@lru_cache(maxsize=1)
def get_tz() -> ZoneInfo:
    return ZoneInfo(config.DEFAULT_TZ)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def to_local(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(get_tz())


def format_absolute(dt: datetime, now: datetime | None = None) -> str:
    """Format as e.g. 'Thu 9 Oct, 11:59pm' in DEFAULT_TZ."""
    local = to_local(dt)
    reference = to_local(now) if now is not None else to_local(now_utc())
    date_part = f"{local.strftime('%a')} {local.day} {local.strftime('%b')}"
    if local.year != reference.year:
        date_part = f"{date_part} {local.year}"
    hour = local.hour % 12 or 12
    ampm = "am" if local.hour < 12 else "pm"
    return f"{date_part}, {hour}:{local.minute:02d}{ampm}"


def format_for_llm(dt: datetime | None = None) -> str:
    """Current date/time in DEFAULT_TZ with weekday, for the model prompt."""
    local = to_local(dt) if dt is not None else to_local(now_utc())
    raw_offset = local.strftime("%z")
    offset = f"{raw_offset[:3]}:{raw_offset[3:]}" if raw_offset else ""
    hour = local.hour % 12 or 12
    ampm = "am" if local.hour < 12 else "pm"
    return (
        f"{local.strftime('%A')} {local.day} {local.strftime('%B')} {local.year}, "
        f"{hour}:{local.minute:02d}{ampm} "
        f"({config.DEFAULT_TZ}, UTC{offset})"
    )
