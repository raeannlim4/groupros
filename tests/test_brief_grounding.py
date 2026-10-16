from __future__ import annotations

import unittest
from datetime import datetime, timezone

from bot.brief import (
    BriefFact,
    BriefFacts,
    BriefMilestone,
    find_milestone,
    ground_facts,
    missing_labels,
)
from bot.grounding import is_grounded, normalise_for_grounding
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

# pypdf mangles Word-exported PDFs with broken spacing and stray punctuation.
BROKEN_DOCUMENT = """
Deliverable: back -end API and this ter m report.
Deadline: Week 12 Friday 9am (No further changes allowed , Strictly NO
extension of deadline )
"""


class GroundingHelperTest(unittest.TestCase):
    def test_removes_non_alphanumeric(self) -> None:
        self.assertEqual(normalise_for_grounding("Back -end, this ter m!"), "backendthisterm")

    def test_folds_curly_quotes_and_dashes(self) -> None:
        self.assertEqual(normalise_for_grounding("\u201cBack\u2013end\u201d"), "backend")

    def test_grounds_clean_quote_against_broken_document(self) -> None:
        document = normalise_for_grounding(BROKEN_DOCUMENT)
        self.assertTrue(
            is_grounded(
                "Deadline: Week 12 Friday 9am (No further changes allowed, "
                "Strictly NO extension of deadline)",
                document,
            )
        )
        self.assertTrue(is_grounded("back-end API", document))
        self.assertTrue(is_grounded("this term report", document))

    def test_fabricated_quote_is_not_grounded(self) -> None:
        document = normalise_for_grounding(BROKEN_DOCUMENT)
        self.assertFalse(
            is_grounded("Deadline extended to Week 13 by the teaching team", document)
        )


class GroundFactsTest(unittest.TestCase):
    def test_keeps_milestone_whose_quote_is_present(self) -> None:
        facts = BriefFacts(
            milestones=[
                BriefMilestone(
                    name="Final deadline",
                    due_text="Friday 11 October 2026, 11:59pm",
                    due_date=datetime(2026, 10, 11, 15, 59, tzinfo=timezone.utc),
                    quote="Final submission due Friday 11 October 2026 by 23:59.",
                )
            ],
            group_size=BriefFact(
                value="4 to 5", quote="Groups of 4 to 5 students."
            ),
        )
        ground_facts(facts, DOCUMENT)
        self.assertEqual(len(facts.milestones), 1)
        self.assertIsNotNone(facts.group_size)

    def test_drops_milestone_whose_quote_is_missing(self) -> None:
        facts = BriefFacts(
            milestones=[
                BriefMilestone(
                    name="Final deadline",
                    due_text="tomorrow",
                    quote="Submit by tomorrow at noon.",
                )
            ],
            weighting=BriefFact(value="50%", quote="Worth 50% of the grade."),
        )
        ground_facts(facts, DOCUMENT)
        self.assertEqual(facts.milestones, [])
        self.assertIsNone(facts.weighting)

    def test_grounding_ignores_whitespace_and_case(self) -> None:
        facts = BriefFacts(
            milestones=[
                BriefMilestone(
                    name="Presentation",
                    due_text="Week 12",
                    quote="  presentation   IN week 12\n(10 minutes).  ",
                )
            ],
        )
        ground_facts(facts, DOCUMENT)
        self.assertEqual(len(facts.milestones), 1)

    def test_drops_ungrounded_deliverable_keeps_grounded(self) -> None:
        facts = BriefFacts(
            milestones=[
                BriefMilestone(
                    name="Final deadline",
                    due_text="Friday 11 October 2026",
                    quote="Final submission due Friday 11 October 2026 by 23:59.",
                    deliverables=[
                        BriefFact(
                            value="Report",
                            quote="Submit a report (PDF) of no more than 3000 words.",
                        ),
                        BriefFact(value="Video", quote="Record a 5 minute video."),
                    ],
                )
            ]
        )
        ground_facts(facts, DOCUMENT)
        self.assertEqual(len(facts.milestones), 1)
        self.assertEqual(
            [d.value for d in facts.milestones[0].deliverables], ["Report"]
        )

    def test_grounds_broken_spacing_from_word_pdf(self) -> None:
        facts = BriefFacts(
            milestones=[
                BriefMilestone(
                    name="Final deadline",
                    due_text="Week 12 Friday 9am",
                    quote=(
                        "Deadline: Week 12 Friday 9am (No further changes allowed, "
                        "Strictly NO extension of deadline)"
                    ),
                    deliverables=[
                        BriefFact(value="Back-end API", quote="back -end API"),
                        BriefFact(value="Term report", quote="this ter m report"),
                    ],
                )
            ]
        )
        ground_facts(facts, BROKEN_DOCUMENT)
        self.assertEqual(len(facts.milestones), 1)
        self.assertEqual(
            [d.value for d in facts.milestones[0].deliverables],
            ["Back-end API", "Term report"],
        )

    def test_fabricated_quote_is_dropped(self) -> None:
        facts = BriefFacts(
            milestones=[
                BriefMilestone(
                    name="Final deadline",
                    due_text="Week 13",
                    quote="Deadline extended to Week 13 by the teaching team",
                )
            ]
        )
        ground_facts(facts, BROKEN_DOCUMENT)
        self.assertEqual(facts.milestones, [])

    def test_empty_quote_is_dropped(self) -> None:
        facts = BriefFacts(
            milestones=[BriefMilestone(name="Presentation", quote="")]
        )
        ground_facts(facts, DOCUMENT)
        self.assertEqual(facts.milestones, [])

    def test_accepts_strings_and_dicts_from_llm(self) -> None:
        facts = BriefFacts.model_validate(
            {
                "milestones": {
                    "name": "Proposal",
                    "due_text": "Week 8",
                    "deliverables": "the proposal",
                },
                "word_limit": "3000 words",
            }
        )
        self.assertEqual(len(facts.milestones), 1)
        self.assertEqual(facts.milestones[0].name, "Proposal")
        self.assertEqual(
            [d.value for d in facts.milestones[0].deliverables], ["the proposal"]
        )
        self.assertEqual(facts.word_limit.value, "3000 words")


class FindMilestoneTest(unittest.TestCase):
    def setUp(self) -> None:
        self.facts = BriefFacts(
            milestones=[
                BriefMilestone(name="Proposal", due_text="Week 8"),
                BriefMilestone(name="Presentation", due_text="Week 12"),
                BriefMilestone(name="Final deadline", due_text="Week 15"),
            ]
        )

    def test_deadline_alias_is_last_milestone(self) -> None:
        self.assertIs(find_milestone(self.facts, "deadline"), self.facts.milestones[-1])

    def test_fuzzy_name_match(self) -> None:
        self.assertIs(
            find_milestone(self.facts, "the proposal deadline"),
            self.facts.milestones[0],
        )

    def test_exact_name_match(self) -> None:
        self.assertIs(
            find_milestone(self.facts, "Presentation"), self.facts.milestones[1]
        )

    def test_unknown_name_returns_none(self) -> None:
        self.assertIsNone(find_milestone(self.facts, "team sync"))


class MissingLabelsTest(unittest.TestCase):
    def test_lists_missing_key_fields(self) -> None:
        facts = BriefFacts(
            milestones=[
                BriefMilestone(
                    name="Final deadline",
                    due_text="Fri 11 Oct",
                    quote="Final submission due Friday 11 October 2026 by 23:59.",
                )
            ],
            group_size=BriefFact(value="4-5", quote="Groups of 4 to 5 students."),
        )
        ground_facts(facts, DOCUMENT)
        missing = missing_labels(facts)
        self.assertNotIn("Deadlines", missing)
        self.assertNotIn("Group size", missing)
        self.assertIn("Word limit", missing)
        self.assertIn("Weighting", missing)

    def test_deadlines_label_missing_when_list_empty(self) -> None:
        facts = BriefFacts()
        self.assertIn("Deadlines", missing_labels(facts))


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
