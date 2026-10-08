from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from bot.llm import parse_task
from bot.timeutil import get_tz, now_utc

EXAMPLES = [
    {"text": "slides by tmr", "title": "slides", "due": ("tomorrow", 23, 59)},
    {"text": "intro eod", "title": "intro", "due": ("today", 23, 59)},
    {"text": "refs by thurs 9pm", "title": "refs", "due": ("dow", 3, 21, 0)},
    {"text": "poster nxt wk", "title": "poster", "due": ("plus", 7, 23, 59)},
    {"text": "survey", "title": "survey", "due": None},
    {
        "text": "do lit review by next fri",
        "title": "do lit review",
        "due": ("next_dow", 4, 23, 59),
    },
    {
        "text": "submit report this fri",
        "title": "submit report",
        "due": ("dow", 4, 23, 59),
    },
    {"text": "clean data tdy", "title": "clean data", "due": ("today", 23, 59)},
    {"text": "write script by mon", "title": "write script", "due": ("dow", 0, 23, 59)},
    {"text": "user testing tmrw 3pm", "title": "user testing", "due": ("tomorrow", 15, 0)},
    {"text": "citations by wed", "title": "citations", "due": ("dow", 2, 23, 59)},
    {
        "text": "fix the prototype by eod",
        "title": "fix the prototype",
        "due": ("today", 23, 59),
    },
    {
        "text": "read papers by tmr 9pm",
        "title": "read papers",
        "due": ("tomorrow", 21, 0),
    },
    {
        "text": "presentation next tues",
        "title": "presentation",
        "due": ("next_dow", 1, 23, 59),
    },
    {"text": "emails", "title": "emails", "due": None},
]


def _upcoming(now_local, target: int, extra_days: int):
    delta = (target - now_local.weekday()) % 7
    return now_local.date() + timedelta(days=delta + extra_days)


def _next_week_weekday(now_local, target: int):
    days_to_next_monday = (7 - now_local.weekday()) % 7 or 7
    next_monday = now_local.date() + timedelta(days=days_to_next_monday)
    return next_monday + timedelta(days=target)


def expected_datetime(spec, now_local):
    if spec is None:
        return None
    kind = spec[0]
    if kind == "today":
        day, hour, minute = now_local.date(), spec[1], spec[2]
    elif kind == "tomorrow":
        day, hour, minute = now_local.date() + timedelta(days=1), spec[1], spec[2]
    elif kind == "plus":
        day, hour, minute = now_local.date() + timedelta(days=spec[1]), spec[2], spec[3]
    elif kind == "dow":
        day, hour, minute = _upcoming(now_local, spec[1], 0), spec[2], spec[3]
    else:  # next_dow
        day, hour, minute = _next_week_weekday(now_local, spec[1]), spec[2], spec[3]
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=now_local.tzinfo)


def same_due(got, expected, tz) -> bool:
    if got is None or expected is None:
        return got is None and expected is None
    return got.astimezone(tz) == expected


async def main() -> None:
    now = now_utc()
    tz = get_tz()
    now_local = now.astimezone(tz)
    title_pass = 0
    date_pass = 0

    for example in EXAMPLES:
        parsed = await parse_task(example["text"], now)
        got_title = parsed.task.strip() if parsed is not None else None
        got_due = parsed.due if parsed is not None else None
        expected = expected_datetime(example["due"], now_local)

        title_ok = got_title == example["title"]
        date_ok = same_due(got_due, expected, tz)
        title_pass += title_ok
        date_pass += date_ok

        print(f"[{'T' if title_ok else 'F'}{'D' if date_ok else 'F'}] {example['text']!r}")
        print(f"    title got={got_title!r} want={example['title']!r}")
        print(
            "    due   got="
            f"{got_due.astimezone(tz) if got_due else None} want={expected}"
        )

    total = len(EXAMPLES)
    print(f"\ntitle {title_pass}/{total}  date {date_pass}/{total}")


if __name__ == "__main__":
    asyncio.run(main())
