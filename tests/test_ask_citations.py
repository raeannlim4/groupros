from __future__ import annotations

import unittest
from datetime import datetime, timezone

from bot.handlers.ask import render_answer
from bot.timeutil import format_absolute

SUPERGROUP_ID = -1001234567890
WHEN = datetime(2026, 10, 2, 6, 5, tzinfo=timezone.utc)
LINK_123 = f"https://t.me/c/{str(SUPERGROUP_ID)[4:]}/123"


def _sources():
    return {123: WHEN, "brief": None}


class RenderAnswerCitationTest(unittest.TestCase):
    def test_valid_id_becomes_link(self) -> None:
        rendered, valid = render_answer(
            "Raeann said so [[123]].", SUPERGROUP_ID, _sources()
        )
        self.assertEqual(
            rendered, f'Raeann said so <a href="{LINK_123}">123</a>.'
        )
        self.assertEqual(valid, 1)

    def test_invalid_id_is_removed(self) -> None:
        rendered, valid = render_answer(
            "Rumour [[999]] here.", SUPERGROUP_ID, _sources()
        )
        self.assertEqual(rendered, "Rumour here.")
        self.assertEqual(valid, 0)

    def test_no_leftover_spaces_after_removal(self) -> None:
        rendered, valid = render_answer(
            "Two cites [[123]] and [[999]] end.", SUPERGROUP_ID, _sources()
        )
        self.assertEqual(
            rendered, f'Two cites <a href="{LINK_123}">123</a> and end.'
        )
        self.assertNotIn("  ", rendered)
        self.assertEqual(valid, 1)

    def test_brief_citation_becomes_label(self) -> None:
        rendered, valid = render_answer(
            "Due Friday [[brief]].", SUPERGROUP_ID, _sources()
        )
        self.assertEqual(rendered, "Due Friday (from brief).")
        self.assertEqual(valid, 1)

    def test_brief_citation_dropped_when_no_brief(self) -> None:
        rendered, valid = render_answer(
            "Due [[brief]].", SUPERGROUP_ID, {123: WHEN}
        )
        self.assertEqual(rendered, "Due.")
        self.assertEqual(valid, 0)

    def test_non_supergroup_shows_date_instead_of_link(self) -> None:
        rendered, valid = render_answer("Said [[123]].", -12345, {123: WHEN})
        self.assertEqual(rendered, f"Said {format_absolute(WHEN)}.")
        self.assertEqual(valid, 1)


if __name__ == "__main__":
    unittest.main()
