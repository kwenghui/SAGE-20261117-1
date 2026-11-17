"""Which numbers typed by the USER may an answer repeat?

A number that exists only in the question is not evidence. Asking "is it true
there were 5000 of them in 2020?" must never make 5000 a verified figure.
The earlier rule refused such a number only when the answer wrote it as a
decimal, a percentage or a scaled amount ("5 million"); a plain whole number
passed, so "Yes, there were 5,000 of them in 2020" was shown as verified.

QuestionNumberPolicy turns the rule round (fail closed): a question-only
number is refused unless EVERY place the answer writes it is plain wording
that cannot be read as a measured quantity:

    a calendar year in a time phrase     "in 2030", "since 1995", "2019-2030"
    a length of time                     "the last 10 years", "a 5-year period"
    a rank or a selection                "the top 5", "the 3 largest", "the 2nd"
    an age or an age band                "aged 15 and over", "aged 15-64"
    an index base                        "2019 = 100"

Anything else ("5,000 units", "were 2000 of them", "about 300") is treated
as a statistic, which the question cannot supply. The same rules apply to
every table: only the shape of the wording is used.

Pure: no model, network or Streamlit.

Used by: utils/claim_binding.py.
Tested in: tests/test_claim_checks.py.
"""

from __future__ import annotations

import logging
import re

LOG = logging.getLogger("sage_ui")

_YEAR = re.compile(r"(?:18|19|20|21)\d{2}\Z")
_SHARE_OR_SCALE = re.compile(
    r"\s*(?:%|per\s?cent\b|percent\b|thousand\b|million\b|billion\b|trillion\b)", re.I
)

# --- Plain wording AFTER the number ------------------------------------------------
#: "10 years", "5-year", "3 quarters": a length of time, not an observation.
_TIME_UNIT_AFTER = re.compile(
    r"\s*[-–]?\s*(?:years?|yrs?|months?|quarters?|decades?|weeks?|days?)\b", re.I
)
#: "2nd", "10th": a position.
_ORDINAL_AFTER = re.compile(r"(?:st|nd|rd|th)\b", re.I)
#: "3 largest", "5 main": a selection of items, not a measurement.
_RANK_AFTER = re.compile(
    r"\s+(?:largest|biggest|highest|lowest|smallest|main|major|leading|top|most|least)\b", re.I
)
#: "15 and over", "65 & above": an age or size band.
_BAND_AFTER = re.compile(
    r"\s*(?:and|&|or)\s+(?:over|above|older|more|below|under|younger|less)\b", re.I
)

# --- Plain wording BEFORE the number ------------------------------------------------
#: "top 5", "first 3": a rank or a selection.
_RANK_BEFORE = re.compile(r"\b(?:top|first|last|bottom|leading)\s+\Z", re.I)
#: "aged 15", "ages 15-64" (also the second number of the band).
_AGE_BEFORE = re.compile(
    r"\b(?:aged?|ages)\s+(?:\d{1,3}\s*(?:-|–|to)\s*)?\Z", re.I
)
#: "2019 = 100": the base of an index.
_BASE_BEFORE = re.compile(r"=\s*\Z")

# --- A calendar year in a time phrase ---------------------------------------------
#: Words that introduce a moment in time. "to", "by" and "of" are deliberately
#: absent on their own: "rose to 2000" and "a total of 2000" state quantities.
_TIME_WORD_BEFORE = re.compile(
    r"(?:\b(?:in|during|since|until|till|before|after|for|from|between|through|around|circa|"
    r"years?|the|early|late|mid|as\s+at|as\s+of|end\s+of|start\s+of|end|mid)[\s-]+"
    r"|[(\[|/]\s*"  # "(2030)", a table cell, "2029/2030"
    r"|(?:18|19|20|21)\d{2}\s*(?:to|and|-|–|,)\s*"  # the second year of a range
    r"|(?:^|\n)\s*(?:[-*]\s*)?)\Z",  # the start of a line or bullet
    re.I,
)
#: A year followed by a counted thing ("2000 units") is a quantity, not a time.
_FUNCTION_WORD_AFTER = re.compile(
    r"\s+(?:and|or|to|the|was|were|is|are|has|had|have|when|with|as|for|in|by|on|at|of|"
    r"compared|versus|vs|but|while|than|because|there|it|this|that|no|not|nor|after|before|"
    r"until|onwards?|census|survey|edition|figures?|data|level|levels|base|prices?)\b",
    re.I,
)
_WORD_AFTER = re.compile(r"\s+[A-Za-z]")


class QuestionNumberPolicy:
    """Decides which question-only numbers an answer may echo (see the module docstring).

    One instance per answer: ``QuestionNumberPolicy(answer).allowed(tokens)``.
    A token is a number in the normal form of
    utils.answer_verification.numeric_tokens ("1234", "9.9").
    """

    def __init__(self, answer) -> None:
        """Keep the answer text the tokens are looked up in."""
        self.answer = str(answer or "")

    # -- public -----------------------------------------------------------------------
    def allowed(self, tokens) -> set:
        """The tokens the answer uses only as plain wording.

        Never raises: a token that cannot be judged is refused (fail closed)
        and the reason is logged without the answer text.
        """
        permitted = set()
        for token in tokens or ():
            try:
                if self.is_plain_wording(str(token)):
                    permitted.add(token)
            except (re.error, ValueError, TypeError, IndexError) as error:
                LOG.warning(
                    "Question number could not be judged (%s); refused", type(error).__name__
                )
        return permitted

    def is_plain_wording(self, token: str) -> bool:
        """True when EVERY use of ``token`` in the answer is plain wording.

        A decimal is always a statistic. A number the answer does not contain
        in a recognisable form is refused.
        """
        if not token or "." in token:
            return False
        uses = self._uses(token)
        return bool(uses) and all(self._plain_use(token, start, end) for start, end in uses)

    # -- internals ----------------------------------------------------------------------
    def _uses(self, token: str) -> list:
        """(start, end) of every place the answer writes ``token`` ("1234" or "1,234")."""
        spaced = "[,]?".join(re.escape(ch) for ch in token)
        pattern = re.compile(rf"(?<![\d.,]){spaced}(?![\d]|[.,]\d)")
        return [match.span() for match in pattern.finditer(self.answer)]

    def _plain_use(self, token: str, start: int, end: int) -> bool:
        """True when this one use of the number cannot be read as a measured quantity."""
        before, after = self.answer[max(0, start - 40) : start], self.answer[end : end + 40]
        if _SHARE_OR_SCALE.match(after):
            return False  # "9%", "9 million": always a statistic
        if (
            _TIME_UNIT_AFTER.match(after)
            or _ORDINAL_AFTER.match(after)
            or _RANK_AFTER.match(after)
            or _BAND_AFTER.match(after)
            or _RANK_BEFORE.search(before)
            or _AGE_BEFORE.search(before)
            or _BASE_BEFORE.search(before)
        ):
            return True
        written = self.answer[start:end]
        if _YEAR.match(token) and "," not in written:
            return self._year_in_time_phrase(before, after)
        return False

    @staticmethod
    def _year_in_time_phrase(before: str, after: str) -> bool:
        """True for a year introduced by a time word and not followed by a counted thing."""
        if not _TIME_WORD_BEFORE.search(before):
            return False
        return not _WORD_AFTER.match(after) or bool(_FUNCTION_WORD_AFTER.match(after))
