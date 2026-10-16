"""Assignment-brief facts: the pydantic models and quote grounding."""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

_WS_RE = re.compile(r"\s+")

# (JSON key, display label) for the scalar key facts, in display order.
KEY_FIELDS: tuple[tuple[str, str], ...] = (
    ("final_deadline", "Final deadline"),
    ("presentation", "Presentation"),
    ("word_limit", "Word limit"),
    ("group_size", "Group size"),
    ("weighting", "Weighting"),
)


class BriefFact(BaseModel):
    value: str = ""
    date: datetime | None = None
    quote: str = ""

    @field_validator("value", "quote", mode="before")
    @classmethod
    def _none_to_empty(cls, value):
        return value or ""

    @field_validator("date", mode="before")
    @classmethod
    def _empty_date_to_none(cls, value):
        return value or None


def _as_fact(value):
    if value is None:
        return None
    if isinstance(value, str):
        return {"value": value} if value.strip() else None
    return value


def _as_facts(value):
    if value is None:
        return []
    if isinstance(value, (str, dict)):
        value = [value]
    facts = []
    for item in value:
        fact = _as_fact(item)
        if fact is not None:
            facts.append(fact)
    return facts


class BriefFacts(BaseModel):
    final_deadline: BriefFact | None = None
    presentation: BriefFact | None = None
    deliverables: list[BriefFact] = Field(default_factory=list)
    word_limit: BriefFact | None = None
    group_size: BriefFact | None = None
    weighting: BriefFact | None = None
    other_key_dates: list[BriefFact] = Field(default_factory=list)

    @field_validator(
        "final_deadline",
        "presentation",
        "word_limit",
        "group_size",
        "weighting",
        mode="before",
    )
    @classmethod
    def _coerce_fact(cls, value):
        return _as_fact(value)

    @field_validator("deliverables", "other_key_dates", mode="before")
    @classmethod
    def _coerce_facts(cls, value):
        return _as_facts(value)


def _normalise(text: str) -> str:
    return _WS_RE.sub(" ", text).strip().lower()


def _grounded(fact: BriefFact, document: str) -> bool:
    quote = _normalise(fact.quote)
    if not quote:
        return False
    return quote in document


def ground_facts(facts: BriefFacts, document: str) -> BriefFacts:
    """Drop any fact whose quote is not found in the document text."""
    normalised = _normalise(document)
    for key, _label in KEY_FIELDS:
        fact = getattr(facts, key)
        if fact is not None and not _grounded(fact, normalised):
            setattr(facts, key, None)
    facts.deliverables = [
        fact for fact in facts.deliverables if _grounded(fact, normalised)
    ]
    facts.other_key_dates = [
        fact for fact in facts.other_key_dates if _grounded(fact, normalised)
    ]
    return facts


def has_value(fact: BriefFact | None) -> bool:
    return fact is not None and bool(fact.value.strip())


def missing_labels(facts: BriefFacts) -> list[str]:
    """Labels of the key fields the brief does not state."""
    missing = [
        label for key, label in KEY_FIELDS if not has_value(getattr(facts, key))
    ]
    if not facts.deliverables:
        missing.append("Deliverables")
    return missing
