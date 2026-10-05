from __future__ import annotations

import asyncio
from datetime import datetime

from bot.llm import classify_commitment
from bot.timeutil import now_utc

ORIGINAL_DATASET = [
    {"name": "Wei Ming", "text": "ok I'll do the survey by Wed", "label": True},
    {"name": "Priya", "text": "I settle the slides tonight", "label": True},
    {"name": "Jun Hao", "text": "can, I take the intro part", "label": True},
    {"name": "Aisha", "text": "leave it to me, I'll write the report", "label": True},
    {"name": "Wei Ming", "text": "I'll email prof tomorrow", "label": True},
    {"name": "Priya", "text": "i do the data cleaning by friday can", "label": True},
    {"name": "Jun Hao", "text": "I'll book the room already", "label": True},
    {"name": "Aisha", "text": "later I handle the printing", "label": True},
    {"name": "Wei Ming", "text": "I'll finish the lit review by Sunday", "label": True},
    {"name": "Priya", "text": "ok i help with the poster", "label": True},
    {"name": "Jun Hao", "text": "I'll settle the citations", "label": True},
    {"name": "Aisha", "text": "count me in for the video editing", "label": True},
    {"name": "Wei Ming", "text": "I'm going to do the testing next week", "label": True},
    {"name": "Priya", "text": "I can do the slides by tmr", "label": True},
    {"name": "Jun Hao", "text": "I'll take over the demo script", "label": True},
    {"name": "Aisha", "text": "someone do the survey by Wed", "label": False},
    {"name": "Wei Ming", "text": "can you do the survey?", "label": False},
    {"name": "Priya", "text": "who is doing the slides ah?", "label": False},
    {"name": "Jun Hao", "text": "did you submit already?", "label": False},
    {"name": "Aisha", "text": "I did the survey yesterday", "label": False},
    {"name": "Wei Ming", "text": "I already finished the report", "label": False},
    {"name": "Priya", "text": "wow nice work everyone", "label": False},
    {"name": "Jun Hao", "text": "the survey is done", "label": False},
    {"name": "Aisha", "text": "maybe we should meet", "label": False},
    {"name": "Wei Ming", "text": "I think we need to split the work", "label": False},
    {"name": "Priya", "text": "hahaha ok", "label": False},
    {"name": "Jun Hao", "text": "can we push the deadline?", "label": False},
    {"name": "Aisha", "text": "going to lecture now", "label": False},
    {"name": "Wei Ming", "text": "don't forget to submit", "label": False},
    {"name": "Priya", "text": "the prof replied already", "label": False},
]

REAL_DATASET = [
    {
        "name": "Wei Ming",
        "text": "im going to pull his appendix out ofhis body",
        "label": False,
    },
    {"name": "Priya", "text": "i do prototyping can", "label": True},
    {"name": "Jun Hao", "text": "oki i can work on the empathy portion", "label": True},
    {
        "name": "Aisha",
        "text": (
            "i think i q ok w any part of iter1, aside frm 1.3 cuz i assume yj "
            "and alex changed q abit, maybe yall can choose first"
        ),
        "label": False,
    },
    {
        "name": "Wei Ming",
        "text": "can i say your interviewee is from SIT year 1",
        "label": False,
    },
    {
        "name": "Priya",
        "text": (
            "@yijieeee do for 1-4, @bapofu do for 5-9, @d3besh do for 10-14, "
            "@Alex_nsy do for 15-19, @pistacheoo do for 20-24, @aal1ya do for "
            "25-29, i do for 30-34 can?"
        ),
        "label": True,
    },
    {"name": "Jun Hao", "text": "i can do that by fri", "label": True},
    {
        "name": "Aisha",
        "text": (
            "i updated the hard scenario to make it longer and align with the "
            "prototype."
        ),
        "label": False,
    },
    {"name": "Wei Ming", "text": "i can do cleaning up of docs also", "label": True},
    {"name": "Priya", "text": "I CAN GO HOME???", "label": False},
]


async def evaluate(
    label: str, dataset: list[dict], now: datetime
) -> tuple[float, float]:
    tp = fp = tn = fn = 0
    for row in dataset:
        result = await classify_commitment(row["text"], row["name"], now)
        predicted = bool(
            result is not None
            and result.is_commitment
            and result.confidence >= 0.7
        )
        expected = row["label"]
        if expected and predicted:
            tp += 1
        elif expected and not predicted:
            fn += 1
        elif not expected and predicted:
            fp += 1
        else:
            tn += 1
        mark = "ok " if predicted == expected else "MISS"
        print(f"  {mark} label={int(expected)} pred={int(predicted)}  {row['text']}")

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    print(
        f"{label}: total={len(dataset)} tp={tp} fp={fp} tn={tn} fn={fn} "
        f"precision={precision:.2f} recall={recall:.2f}"
    )
    return precision, recall


async def main() -> None:
    now = now_utc()
    print("== original set ==")
    await evaluate("original", ORIGINAL_DATASET, now)
    print()
    print("== real set ==")
    await evaluate("real", REAL_DATASET, now)


if __name__ == "__main__":
    asyncio.run(main())
