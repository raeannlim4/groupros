"""Assignment-brief facts: the pydantic models and quote grounding."""

from __future__ import annotations

import difflib
import logging
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from bot.grounding import is_grounded, normalise_for_grounding

logger = logging.getLogger(__name__)

# (JSON key, display label) for the scalar key facts, in display order.
SCALAR_FIELDS: tuple[tuple[str, str], ...] = (
    ("word_limit", "Word limit"),
    ("group_size", "Group size"),
    ("weighting", "Weighting"),
)

DEADLINE_ALIASES = {"deadline", "final deadline", "final_deadline"}


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


class BriefMilestone(BaseModel):
    name: str = ""
    due_text: str = ""
    due_date: datetime | None = None
    deliverables: list[BriefFact] = Field(default_factory=list)
    quote: str = ""

    @field_validator("name", "due_text", "quote", mode="before")
    @classmethod
    def _none_to_empty(cls, value):
        return value or ""

    @field_validator("due_date", mode="before")
    @classmethod
    def _empty_date_to_none(cls, value):
        return value or None

    @field_validator("deliverables", mode="before")
    @classmethod
    def _coerce_deliverables(cls, value):
        return _as_facts(value)


class BriefFacts(BaseModel):
    milestones: list[BriefMilestone] = Field(default_factory=list)
    word_limit: BriefFact | None = None
    group_size: BriefFact | None = None
    weighting: BriefFact | None = None

    @field_validator("milestones", mode="before")
    @classmethod
    def _coerce_milestones(cls, value):
        if value is None:
            return []
        if isinstance(value, dict):
            value = [value]
        return value

    @field_validator("word_limit", "group_size", "weighting", mode="before")
    @classmethod
    def _coerce_fact(cls, value):
        return _as_fact(value)


def has_value(fact: BriefFact | None) -> bool:
    return fact is not None and bool(fact.value.strip())


def _count_facts(facts: BriefFacts) -> int:
    total = len(facts.milestones)
    for milestone in facts.milestones:
        total += len(milestone.deliverables)
    for key, _label in SCALAR_FIELDS:
        if getattr(facts, key) is not None:
            total += 1
    return total


def _grounded(quote: str, document: str) -> bool:
    return is_grounded(quote, document)


def ground_facts(facts: BriefFacts, document: str) -> BriefFacts:
    """Drop any milestone, deliverable or scalar fact whose quote is missing.

    The document is normalised once; every quote is compared against it with
    spacing, punctuation, case and curly punctuation ignored.
    """
    normalised = normalise_for_grounding(document)
    extracted = _count_facts(facts)
    dropped: list[str] = []

    milestones = []
    for milestone in facts.milestones:
        if not milestone.quote.strip() or not _grounded(milestone.quote, normalised):
            dropped.append(milestone.quote or milestone.name)
            continue
        kept = []
        for deliverable in milestone.deliverables:
            if deliverable.quote.strip() and _grounded(deliverable.quote, normalised):
                kept.append(deliverable)
            else:
                dropped.append(deliverable.quote or deliverable.value)
        milestone.deliverables = kept
        milestones.append(milestone)
    facts.milestones = milestones

    for key, _label in SCALAR_FIELDS:
        fact = getattr(facts, key)
        if fact is None:
            continue
        if fact.quote.strip() and _grounded(fact.quote, normalised):
            continue
        dropped.append(fact.quote or fact.value)
        setattr(facts, key, None)

    logger.info(
        "Brief grounding: kept %d of %d facts", _count_facts(facts), extracted
    )
    for quote in dropped:
        logger.debug("Dropped brief fact, quote not in document: %r", quote)
    return facts


def _name_key(text: str) -> str:
    return normalise_for_grounding(text)


def find_milestone(
    facts: BriefFacts, query: str
) -> BriefMilestone | None:
    """Return the milestone matching ``query``, or None.

    ``deadline`` (and its aliases) means the last milestone. Otherwise the name
    is fuzzy-matched against the milestone names.
    """
    milestones = facts.milestones
    if not milestones:
        return None
    text = (query or "").strip()
    if text.lower() in DEADLINE_ALIASES:
        return milestones[-1]
    needle = _name_key(text)
    if not needle:
        return None
    for milestone in milestones:
        key = _name_key(milestone.name)
        if key and needle in key:
            return milestone
    for milestone in milestones:
        key = _name_key(milestone.name)
        if key and key in needle:
            return milestone
    by_key = {_name_key(m.name): m for m in milestones if _name_key(m.name)}
    close = difflib.get_close_matches(needle, list(by_key), n=1, cutoff=0.6)
    if close:
        return by_key[close[0]]
    return None


def missing_labels(facts: BriefFacts) -> list[str]:
    """Labels of the key fields the brief does not state."""
    missing: list[str] = []
    if not facts.milestones:
        missing.append("Deadlines")
    missing.extend(
        label for key, label in SCALAR_FIELDS if not has_value(getattr(facts, key))
    )
    return missing
