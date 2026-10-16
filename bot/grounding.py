"""Shared text normalisation for quote grounding.

pypdf extracts broken spacing from Word-exported PDFs (for example "this ter m",
"back -end" or "allowed , Strictly NO extension of deadline )") while the model
quotes the clean version. Comparing after removing everything that is not a
letter or digit ignores that damage, along with curly quotes, dashes and case.
"""

from __future__ import annotations

import re

# Curly quotes, dashes, spaces and other common unicode punctuation -> ASCII.
_FOLD = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u201f": '"',
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
        "\u2212": "-",
        "\u00a0": " ",
        "\u200b": "",
        "\ufeff": "",
    }
)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalise_for_grounding(text: str) -> str:
    """Lowercase, fold curly punctuation, then keep only [a-z0-9].

    Removing every non-alphanumeric character (including spaces) makes the
    comparison tolerant of the broken spacing pypdf leaves behind.
    """
    folded = (text or "").lower().translate(_FOLD)
    return _NON_ALNUM.sub("", folded)


def is_grounded(quote: str, normalised_document: str) -> bool:
    """True when the quote appears in a document already normalised."""
    needle = normalise_for_grounding(quote)
    if not needle:
        return False
    return needle in normalised_document
