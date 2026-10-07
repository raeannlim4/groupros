from __future__ import annotations

from datetime import datetime, timedelta, timezone
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


def _time_only(local: datetime) -> str:
    hour = local.hour % 12 or 12
    ampm = "am" if local.hour < 12 else "pm"
    return f"{hour}:{local.minute:02d}{ampm}"


def format_relative(dt: datetime, now: datetime | None = None) -> str:
    """Phrase a deadline relative to now, e.g. 'in 3 hours', 'today 11:59pm'."""
    local = to_local(dt)
    reference = to_local(now) if now is not None else to_local(now_utc())
    seconds = (local - reference).total_seconds()
    if seconds <= 0:
        return "now"
    if seconds < 12 * 3600:
        hours = max(1, round(seconds / 3600))
        return f"in {hours} hour{'s' if hours != 1 else ''}"
    if local.date() == reference.date():
        return f"today {_time_only(local)}"
    if local.date() == (reference + timedelta(days=1)).date():
        return f"tomorrow {_time_only(local)}"
    return format_absolute(local, now=reference)


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
