from __future__ import annotations

import unittest

from bot.vtt import clean_vtt

SIMPLE = """\
WEBVTT

1
00:00:01.000 --> 00:00:04.000
Raeann Lim: Hello everyone, let's start.

2
00:00:04.000 --> 00:00:08.000
Tan Wei: Sure. First item is the survey.
"""

MERGED = """\
WEBVTT

1
00:00:01.000 --> 00:00:04.000
Raeann Lim: Hello everyone,

2
00:00:04.000 --> 00:00:08.000
Raeann Lim: let's start with the agenda.

3
00:00:08.000 --> 00:00:12.000
Tan Wei: Sure, I'll take the survey.
"""

NO_SPEAKERS = """\
WEBVTT

1
00:00:01.000 --> 00:00:04.000
Hello everyone.

2
00:00:04.000 --> 00:00:08.000
Let's begin.
"""

HOURS = """\
WEBVTT

1
00:59:58.000 --> 01:00:00.000
Raeann Lim: Almost an hour in.

2
01:00:00.000 --> 01:00:05.000
Tan Wei: An hour already.
"""

INLINE_TIMESTAMP = """\
WEBVTT

1
00:00:01.000 --> 00:00:04.000
Raeann Lim: <00:00:02.000>So the plan is set.
"""


class VttCleanerTest(unittest.TestCase):
    def test_header_and_cue_numbers_removed(self) -> None:
        cleaned = clean_vtt(SIMPLE)
        self.assertNotIn("WEBVTT", cleaned)
        self.assertNotIn("\n1\n", cleaned)
        self.assertNotIn("-->", cleaned)

    def test_speaker_and_text_kept(self) -> None:
        cleaned = clean_vtt(SIMPLE)
        lines = cleaned.splitlines()
        self.assertEqual(lines[0], "[00:01] Raeann Lim: Hello everyone, let's start.")
        self.assertEqual(lines[1], "[00:04] Tan Wei: Sure. First item is the survey.")

    def test_consecutive_same_speaker_merged(self) -> None:
        cleaned = clean_vtt(MERGED)
        lines = cleaned.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(
            lines[0],
            "[00:01] Raeann Lim: Hello everyone, let's start with the agenda.",
        )
        self.assertEqual(lines[1], "[00:08] Tan Wei: Sure, I'll take the survey.")

    def test_missing_speaker_names(self) -> None:
        cleaned = clean_vtt(NO_SPEAKERS)
        lines = cleaned.splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0], "[00:01] Hello everyone. Let's begin.")

    def test_timestamps_over_an_hour(self) -> None:
        cleaned = clean_vtt(HOURS)
        lines = cleaned.splitlines()
        self.assertTrue(lines[0].startswith("[59:58]"))
        self.assertTrue(lines[1].startswith("[1:00:00]"))

    def test_inline_timestamp_removed(self) -> None:
        cleaned = clean_vtt(INLINE_TIMESTAMP)
        self.assertEqual(cleaned, "[00:01] Raeann Lim: So the plan is set.")

    def test_empty_input(self) -> None:
        self.assertEqual(clean_vtt("WEBVTT\n"), "")

    def test_plain_paragraph_is_not_a_cue(self) -> None:
        # A cue needs a timestamp line; free text is ignored by the cleaner.
        self.assertEqual(clean_vtt("Just some text.\nNo timestamps here."), "")


if __name__ == "__main__":
    unittest.main()
