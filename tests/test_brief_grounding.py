from __future__ import annotations

import unittest
from datetime import datetime, timezone

from bot.brief import BriefFact, BriefFacts, ground_facts, missing_labels
from bot.handlers.plan import filter_milestones
from bot.llm import PlanMilestone

DOCUMENT = """
Assignment 2 brief
Groups of 4 to 5 students.
Submit a report (PDF) of no more than 3000 words.
Final submission due Friday 11 October 2026 by 23:59.
Presentation in Week 12 (10 minutes).
This assignment is worth 40% of your final grade.
Proposal due Week 8.
"""


class GroundFactsTest(unittest.TestCase):
    def test_keeps_facts_whose_quote_is_present(self) -> None:
        facts = BriefFacts(
            final_deadline=BriefFact(
                value="Friday 11 Oct 2026",
                date=datetime(2026, 10, 11, 15, 59, tzinfo=timezone.utc),
                quote="Final submission due Friday 11 October 2026 by 23:59.",
            ),
            group_size=BriefFact(
                value="4 to 5", quote="Groups of 4 to 5 students."
            ),
        )
        ground_facts(facts, DOCUMENT)
        self.assertIsNotNone(facts.final_deadline)
        self.assertIsNotNone(facts.group_size)

    def test_drops_facts_whose_quote_is_missing(self) -> None:
        facts = BriefFacts(
            final_deadline=BriefFact(
                value="tomorrow", quote="Submit by tomorrow at noon."
            ),
            weighting=BriefFact(value="50%", quote="Worth 50% of the grade."),
        )
        ground_facts(facts, DOCUMENT)
        self.assertIsNone(facts.final_deadline)
        self.assertIsNone(facts.weighting)

    def test_grounding_ignores_whitespace_and_case(self) -> None:
        facts = BriefFacts(
            group_size=BriefFact(
                value="4-5",
                quote="  groups   OF 4 TO 5\nstudents.  ",
            )
        )
        ground_facts(facts, DOCUMENT)
        self.assertIsNotNone(facts.group_size)

    def test_drops_ungrounded_deliverables_keeps_grounded(self) -> None:
        facts = BriefFacts(
            deliverables=[
                BriefFact(
                    value="Report",
                    quote="Submit a report (PDF) of no more than 3000 words.",
                ),
                BriefFact(value="Video", quote="Record a 5 minute video."),
            ]
        )
        ground_facts(facts, DOCUMENT)
        self.assertEqual([d.value for d in facts.deliverables], ["Report"])

    def test_empty_quote_is_dropped(self) -> None:
        facts = BriefFacts(presentation=BriefFact(value="Week 12", quote=""))
        ground_facts(facts, DOCUMENT)
        self.assertIsNone(facts.presentation)

    def test_accepts_strings_and_dicts_from_llm(self) -> None:
        facts = BriefFacts.model_validate(
            {
                "final_deadline": "Friday 11 Oct 2026",
                "deliverables": "the report",
                "other_key_dates": None,
            }
        )
        self.assertEqual(facts.final_deadline.value, "Friday 11 Oct 2026")
        self.assertEqual([d.value for d in facts.deliverables], ["the report"])
        self.assertEqual(facts.other_key_dates, [])


class MissingLabelsTest(unittest.TestCase):
    def test_lists_missing_key_fields(self) -> None:
        facts = BriefFacts(
            final_deadline=BriefFact(
                value="Fri 11 Oct",
                quote="Final submission due Friday 11 October 2026 by 23:59.",
            ),
            group_size=BriefFact(value="4-5", quote="Groups of 4 to 5 students."),
        )
        ground_facts(facts, DOCUMENT)
        missing = missing_labels(facts)
        self.assertNotIn("Final deadline", missing)
        self.assertNotIn("Group size", missing)
        self.assertIn("Presentation", missing)
        self.assertIn("Deliverables", missing)

    def test_deliverables_label_missing_when_list_empty(self) -> None:
        facts = BriefFacts()
        self.assertIn("Deliverables", missing_labels(facts))


class PlanFilterTest(unittest.TestCase):
    def test_drops_out_of_range_and_duplicates(self) -> None:
        start = datetime(2026, 10, 1).date()
        end = datetime(2026, 10, 20).date()
        milestones = [
            PlanMilestone(title="Draft", due=datetime(2026, 9, 30, tzinfo=timezone.utc)),
            PlanMilestone(title="Survey", due=datetime(2026, 10, 5, tzinfo=timezone.utc)),
            PlanMilestone(
                title="Existing task", due=datetime(2026, 10, 8, tzinfo=timezone.utc)
            ),
            PlanMilestone(
                title="After deadline", due=datetime(2026, 10, 21, tzinfo=timezone.utc)
            ),
            PlanMilestone(title="Survey", due=datetime(2026, 10, 12, tzinfo=timezone.utc)),
            PlanMilestone(title="", due=datetime(2026, 10, 12, tzinfo=timezone.utc)),
            PlanMilestone(title="No date", due=None),
        ]
        kept = filter_milestones(milestones, start, end, ["existing task"])
        self.assertEqual([m.title for m in kept], ["Survey"])


if __name__ == "__main__":
    unittest.main()
