"""Further checks that a real number is not used for the wrong thing.

utils/claim_binding.py ties a value to its period, sign and series for the
sentence shapes it knows. A review found wrong statements that still passed,
because every number in them exists somewhere in the evidence:

    wrong scale       "13,500 thousand" for a value published as 13,500 (unit: Number)
    year as a value   "reached 1990 in 2020", "an increase of 2024"
    another series    "A grew 1.67%" when 1.67% is the change of series B
    swapped subject   "A numbered 6,100" when 6,100 is a value of series B only
    spelled numbers   "fourteen thousand" (words were never checked at all)
    stated multiples  "A, more than twice the B" when A is not twice B

Each check below closes one of those gaps. As in claim_binding, a check can
only REJECT an answer, never approve a number, and it judges only what the
evidence can prove: a value Sage cannot place (truncated evidence) is left
alone rather than refused on a guess.

    EvidenceFacts            what the tool results say (values, units, changes)
    ClaimCheck               base class: one rule, problems(text, facts) -> reasons
    ExtraClaimChecks         runs every rule; a rule that fails to run REFUSES the answer
    all_violations(...)      claim_binding.binding_violations plus these rules

The same rules apply to every table: only the shape of tool results and of
the wording is used. No subject word, table ID or statistic appears here.

Pure: no model, network or Streamlit.

Used by: utils/answer_verification.py.
Tested in: tests/test_claim_checks.py.
"""

from __future__ import annotations

import json
import logging
import math
import re

from utils.claim_binding import (
    LEDGER_KEY,
    _GENERIC_NAMES,
    _names_mentioned,
    _sentence_around,
    binding_violations,
)

LOG = logging.getLogger("sage_ui")

_NUMBER = r"\d[\d,]*(?:\.\d+)?"
_YEAR = r"(?:18|19|20|21)\d{2}"
_APPROXIMATELY = r"(?:about\s+|around\s+|roughly\s+|nearly\s+|almost\s+|some\s+|approximately\s+)?"
#: Scale words and the ways a published unit or title may spell them.
_SCALE_SPELLINGS = {
    "thousand": ("thousand", "'000", "(000)", "000s"),
    "million": ("million", "mil", "mn", "$m"),
    "billion": ("billion", "bil", "bn", "$b"),
    "trillion": ("trillion", "tril", "tn"),
}
#: Powers of ten of the scale words (a hundred is 10 to the power 2).
_SCALE_POWER = {"hundred": 2, "thousand": 3, "million": 6, "billion": 9, "trillion": 12}


class ClaimCheckError(Exception):
    """A claim check could not run; the answer it was judging is refused (fail closed)."""


def _as_float(text) -> float | None:
    """A printed number ("1,234.5") as a non-negative float, or None."""
    try:
        return abs(round(float(str(text).replace(",", "")), 6))
    except ValueError:
        return None


class EvidenceFacts:
    """What the official tool results say, in the form the checks below need.

    Built once per answer from the evidence list (successful tool results):

        tokens             every number written anywhere in the evidence (as text)
        numbers            the same, as floats
        values             every published value (sizes, without sign)
        values_by_series   series name (lower case) -> its published values
        units_by_value     published value -> the units it is published in
        scale_text         every unit and table title, lower case (for scale words)
        changes_by_series  series name -> its computed changes, as printed (1 and 2 decimals)
        complete           False when some result's rows are only a truncated preview

    Checks that reject on "this value belongs to another series" must not run
    when ``complete`` is False: unseen rows could hold the value.
    """

    def __init__(self, evidence, numeric_tokens) -> None:
        """Read every result; ``numeric_tokens`` is answer_verification.numeric_tokens."""
        self.tokens: set = set()
        self.values: set = set()
        self.values_by_series: dict = {}
        self.units_by_value: dict = {}
        self.scale_text: list = []
        self.changes_by_series: dict = {}
        self.complete = True
        for result in evidence or []:
            if not isinstance(result, dict):
                continue
            self.tokens |= numeric_tokens(json.dumps(result, ensure_ascii=False, default=str))
            self.scale_text.append(str(result.get("title") or "").casefold())
            self._read_rows(result)
            self._read_changes(result)

    # -- construction ---------------------------------------------------------------------
    def _read_rows(self, result) -> None:
        """Add the (series, value, unit) rows of one result; note when they are incomplete."""
        units = {}
        preview = []
        for row in result.get("observations") or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("series_name") or "")
            units[name] = str(row.get("unit") or "")
            if type(row.get("value")) in (int, float) and math.isfinite(row["value"]):
                preview.append((name, float(row["value"])))
        for entry in result.get("series_summary") or []:
            if isinstance(entry, dict) and entry.get("series_name"):
                units.setdefault(str(entry["series_name"]), str(entry.get("unit") or ""))
        ledger = result.get(LEDGER_KEY)
        if isinstance(ledger, list) and ledger:
            rows = [
                (str(item[0]), float(item[2]))
                for item in ledger
                if isinstance(item, (list, tuple))
                and len(item) == 3
                and type(item[2]) in (int, float)
                and math.isfinite(item[2])
            ]
        else:
            rows = preview
            if result.get("observations_truncated") or not rows:
                self.complete = False  # other periods and series are unseen
        for name, value in rows:
            size = abs(round(value, 6))
            unit = units.get(name, "")
            self.values.add(size)
            self.values_by_series.setdefault(name.casefold(), set()).add(size)
            self.units_by_value.setdefault(size, set()).add(unit.casefold())
            self.scale_text.append(unit.casefold())

    def _read_changes(self, result) -> None:
        """Add each series' computed changes, in the forms an answer may print them."""
        for entry in result.get("series_summary") or []:
            if not isinstance(entry, dict) or not entry.get("series_name"):
                continue
            printed = self.changes_by_series.setdefault(str(entry["series_name"]).casefold(), set())
            for field in ("percentage_change", "change_latest_minus_earliest"):
                change = entry.get(field)
                if type(change) in (int, float) and math.isfinite(change):
                    for digits in (1, 2):
                        printed.add(f"{abs(float(change)):.{digits}f}")

    # -- questions the checks ask --------------------------------------------------------
    @property
    def numbers(self) -> set:
        """Every number written anywhere in the evidence, as floats ("13000.0" and "13000" agree)."""
        found = set()
        for token in self.tokens:
            try:
                found.add(float(token))
            except ValueError:
                continue  # not a plain number ("1.2.3"): cannot back a spelled-out figure
        return found

    @property
    def names(self) -> set:
        """Every series name the evidence has rows or changes for (lower case)."""
        return set(self.values_by_series) | set(self.changes_by_series)

    def publishes_scale(self, word: str) -> bool:
        """True when a unit or table title of the evidence is in ``word`` (e.g. "thousand")."""
        spellings = _SCALE_SPELLINGS.get(word.casefold(), (word.casefold(),))
        return any(spelling in text for text in self.scale_text for spelling in spellings)


class ClaimCheck:
    """One rule. Subclasses set ``name`` and implement ``problems``."""

    name = "claim"

    def problems(self, text: str, facts: EvidenceFacts) -> list[str]:
        """Reasons ``text`` misuses a real number ([] when none)."""
        raise NotImplementedError


class ScaleWordCheck(ClaimCheck):
    """A published value keeps its scale: 13,500 (unit: Number) is not "13,500 thousand".

    Judged only for numbers that ARE published values, and only when no unit
    or title of the evidence is in that scale. "5.2 million" for a value
    published in "Million Dollars" is therefore accepted.
    """

    name = "scale"
    _PATTERN = re.compile(
        rf"(?<![\w.,])(?P<v>{_NUMBER})\s+(?P<scale>thousand|million|billion|trillion)\b", re.I
    )

    def problems(self, text, facts) -> list[str]:
        """One reason per published value written with a scale the evidence does not use."""
        found = []
        for match in self._PATTERN.finditer(text):
            size = _as_float(match["v"])
            if size is None or size not in facts.values:
                continue  # not a published value: the membership check judges it
            if not facts.publishes_scale(match["scale"]):
                found.append(f"scale: {match['v']} is not published in {match['scale'].lower()}")
        return found


class YearAsQuantityCheck(ClaimCheck):
    """A calendar year is not a level or a change ("reached 1990 in 2020", "an increase of 2024").

    A year's digits are in the evidence as a period (or in a table title), so
    the membership check lets them through. Refused unless the same number is
    also a published value. Ambiguous wording ("had risen by 2024.") is left alone.
    """

    name = "year"
    _LEVEL = re.compile(
        r"\b(?:reached|hit|totall?ed|numbered|recorded|stood\s+at|was|were)\s+"
        rf"{_APPROXIMATELY}(?P<y>{_YEAR})\s+(?:in|by|during|for)\s+{_YEAR}\b",
        re.I,
    )
    _CHANGE_OF = re.compile(
        r"\b(?:increase|rise|decrease|decline|drop|gain|fall|growth|reduction)\s+of\s+"
        rf"{_APPROXIMATELY}(?P<y>{_YEAR})(?![\d,.]*\d)",
        re.I,
    )
    _CHANGE_BY = re.compile(
        r"\b(?:rose|fell|grew|increased|decreased|declined|dropped|climbed|shrank|expanded|"
        rf"up|down)\s+by\s+{_APPROXIMATELY}(?P<y>{_YEAR})\s+"
        r"(?:over|during|between|from|since|across|in\s+the|units?|people|persons?|points?)\b",
        re.I,
    )

    def problems(self, text, facts) -> list[str]:
        """One reason per year written where a quantity belongs."""
        found = []
        for pattern in (self._LEVEL, self._CHANGE_OF, self._CHANGE_BY):
            for match in pattern.finditer(text):
                if float(match["y"]) not in facts.values:
                    found.append(f"year: {match['y']} is a year, not a published quantity")
        return found


class ForeignChangeCheck(ClaimCheck):
    """A computed change belongs to its series: "A grew 1.67%" needs 1.67% to be A's change.

    Judged when the sentence names at least one series and the percentage is
    the computed change of some OTHER series only (not a change or a published
    value of any series the sentence names).
    """

    name = "series-change"
    _PATTERN = re.compile(rf"(?<![\w.,])(?P<v>{_NUMBER})\s*(?:%|per\s?cent\b|percent\b)", re.I)

    def problems(self, text, facts) -> list[str]:
        """One reason per percentage that is another series' computed change."""
        found = []
        every_change = set().union(*facts.changes_by_series.values()) if (
            facts.changes_by_series
        ) else set()
        for match in self._PATTERN.finditer(text):
            printed = match["v"].replace(",", "")
            if printed not in every_change:
                continue
            named = _names_mentioned(
                _sentence_around(text, match.start(), match.end()), facts.names
            )
            if not named:
                continue
            own_changes = set().union(*(facts.changes_by_series.get(n, set()) for n in named))
            own_values = set().union(*(facts.values_by_series.get(n, set()) for n in named))
            if printed not in own_changes and _as_float(printed) not in own_values:
                found.append(f"series: {match['v']}% is the change of another series")
        return found


class SubjectValueCheck(ClaimCheck):
    """"<Series name> numbered X" needs X to be a value of THAT series.

    Catches two real values swapped between two series in one sentence. Only
    the direct shape "name + verb + number" is judged, with the longest
    matching series name, and only when the evidence rows are complete.
    """

    name = "series-value"
    _VERB = (
        r"(?:numbered|totall?ed|stood\s+at|reached|was|were|came\s+to|amounted\s+to|"
        r"recorded|had)"
    )

    def problems(self, text, facts) -> list[str]:
        """One reason per value stated for a series that does not publish it."""
        if not facts.complete:
            return []
        found, judged = [], set()
        # Longest names first: "B C" is judged before a shorter series "C" inside it.
        for name in sorted(facts.values_by_series, key=len, reverse=True):
            if name in _GENERIC_NAMES or len(name) < 4:
                continue
            words = r"\s+".join(re.escape(word) for word in name.split())
            pattern = re.compile(
                rf"(?<![\w-]){words}s?\s+{self._VERB}\s+{_APPROXIMATELY}"
                rf"(?P<v>{_NUMBER})(?![\d,]*\d)",
                re.I,
            )
            for match in pattern.finditer(text):
                span = match.span("v")
                if span in judged:
                    continue
                judged.add(span)
                size = _as_float(match["v"])
                if size is None or size not in facts.values:
                    continue  # not a published value (a year, a count): not judged here
                if size not in facts.values_by_series[name]:
                    found.append(f"series: {match['v']} is not a value of the series named")
        return found


class SpelledNumberCheck(ClaimCheck):
    """A quantity written in words ("fourteen thousand") must be a published figure too.

    Words were invisible to the membership check. Accepted when the number it
    spells is in the evidence, or when its leading figure is in the evidence
    and the evidence is published in that scale ("two million" for a value 2
    in "Million Dollars").
    """

    name = "words"
    _UNITS = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
        "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
        "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
        "nineteen": 19,
    }
    _TENS = {
        "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
        "eighty": 80, "ninety": 90,
    }
    _PATTERN = re.compile(
        r"\b(?P<tens>" + "|".join(_TENS) + r")?(?:[-\s])?(?P<unit>" + "|".join(
            sorted(_UNITS, key=len, reverse=True)
        ) + r")?\s+(?P<scale>hundred|thousand|million|billion|trillion)\b",
        re.I,
    )

    def problems(self, text, facts) -> list[str]:
        """One reason per spelled-out quantity the evidence does not publish."""
        found = []
        for match in self._PATTERN.finditer(text):
            leading = self._TENS.get((match["tens"] or "").lower(), 0) + self._UNITS.get(
                (match["unit"] or "").lower(), 0
            )
            if not leading:
                continue  # "several thousand", "the million": no figure is stated
            scale = match["scale"].lower()
            # Compared as numbers: the evidence writes 13000 as "13000.0".
            if float(leading * 10 ** _SCALE_POWER[scale]) in facts.numbers:
                continue
            if float(leading) in facts.numbers and facts.publishes_scale(scale):
                continue
            found.append(f"words: '{' '.join(match.group(0).split())}' is not a published figure")
        return found


class StatedMultipleCheck(ClaimCheck):
    """"A is more than twice the B" must agree with A and B.

    utils/claim_binding.py already checks "doubled from A to B". This is the
    comparison between two values written the other way round: "13,500, more
    than twice the 6,100 of the other group". Judged only when B and the
    nearest published value before it in the same sentence (A) are both
    published values; a comparison with no numbers ("far higher than") states
    nothing that can be checked and is left alone.

    Leeway, as for "doubled": 15% for a plain or approximate multiple;
    "nearly/almost" needs at least 80% of it and not more than it; "more
    than/over/at least" needs at least the multiple; "less than" needs less;
    "exactly" allows 1%.
    """

    name = "multiple"
    _MULTIPLES = {
        "twice": 2.0, "double": 2.0, "triple": 3.0, "treble": 3.0, "three times": 3.0,
        "four times": 4.0, "five times": 5.0, "half": 0.5,
    }
    _PATTERN = re.compile(
        r"(?:\b(?P<q>more\s+than|over|at\s+least|less\s+than|nearly|almost|about|around|"
        r"roughly|exactly)\s+)?"
        r"\b(?P<m>twice|double|triple|treble|three\s+times|four\s+times|five\s+times|half)\s+"
        r"(?:the\s+|as\s+(?:many|much|high|large|big)\s+as\s+(?:the\s+)?)?"
        rf"(?P<b>{_NUMBER})(?![\d,]*\d)",
        re.I,
    )
    _ANY_NUMBER = re.compile(rf"(?<![\w.,])({_NUMBER})(?![\d,]*\d)")

    def problems(self, text, facts) -> list[str]:
        """One reason per stated multiple that the two values do not bear out."""
        found = []
        for match in self._PATTERN.finditer(text):
            second = _as_float(match["b"])
            if not second or second not in facts.values:
                continue
            first = self._value_before(text, match.start(), facts)
            if first is None:
                continue
            multiple = self._MULTIPLES[" ".join(match["m"].lower().split())]
            qualifier = " ".join((match["q"] or "").lower().split())
            if not self._agrees(first / second, multiple, qualifier):
                found.append(
                    f"multiple: {match['m'].lower()} does not describe the two values given"
                )
        return found

    def _value_before(self, text, position, facts):
        """The nearest published value written earlier in the same sentence, or None."""
        sentence_start = max(text.rfind("\n", 0, position), text.rfind(". ", 0, position)) + 1
        for candidate in reversed(self._ANY_NUMBER.findall(text[sentence_start:position])):
            size = _as_float(candidate)
            if size is not None and size in facts.values:
                return size
        return None

    @staticmethod
    def _agrees(ratio, multiple, qualifier) -> bool:
        """True when ``ratio`` (first value / second value) fits the stated multiple."""
        if qualifier in ("more than", "over", "at least"):
            return ratio >= multiple * 0.999
        if qualifier == "less than":
            return ratio < multiple
        if qualifier in ("nearly", "almost"):
            return multiple * 0.8 <= ratio <= multiple * 1.001
        if qualifier == "exactly":
            return abs(ratio - multiple) <= multiple * 0.01
        return abs(ratio - multiple) <= multiple * 0.15


class ExtraClaimChecks:
    """Runs every rule above on one answer.

    A rule that raises is a defect in Sage, not evidence that the answer is
    right. The answer is REFUSED (one "check: ..." reason) and the error is
    logged by type, without the answer text: showing a number that could not
    be checked would break Sage's main rule.
    """

    DEFAULT_CHECKS = (
        ScaleWordCheck,
        YearAsQuantityCheck,
        ForeignChangeCheck,
        SubjectValueCheck,
        SpelledNumberCheck,
        StatedMultipleCheck,
    )

    def __init__(self, checks=None) -> None:
        """Use ``checks`` (ClaimCheck instances) or one instance of every default rule."""
        self.checks = (
            list(checks) if checks is not None else [check() for check in self.DEFAULT_CHECKS]
        )

    def facts(self, evidence, numeric_tokens) -> EvidenceFacts:
        """Read the evidence; ClaimCheckError when it cannot be read."""
        try:
            return EvidenceFacts(evidence, numeric_tokens)
        except (TypeError, ValueError, KeyError, AttributeError, IndexError) as error:
            raise ClaimCheckError("evidence could not be read") from error

    def run(self, answer, evidence, numeric_tokens) -> list[str]:
        """Every reason the answer misuses a real number ([] when none were found)."""
        text = str(answer or "")
        try:
            facts = self.facts(evidence, numeric_tokens)
        except ClaimCheckError as error:
            LOG.error("Claim checks could not read the evidence: %s",
                      type(error.__cause__).__name__)
            return ["check: the evidence could not be read by the claim checks"]
        problems = []
        for check in self.checks:
            try:
                problems += check.problems(text, facts)
            except Exception as error:  # noqa: BLE001 - any failure of a rule refuses the answer
                LOG.error("Claim check %r failed to run: %s", check.name, type(error).__name__)
                problems.append(f"check: the {check.name} check could not run")
        return problems


_EXTRA_CHECKS = ExtraClaimChecks()


def all_violations(answer, evidence, numeric_tokens) -> list[str]:
    """Every reason an answer misuses real numbers: claim_binding's rules, then these.

    ``numeric_tokens`` is utils.answer_verification.numeric_tokens (passed in so
    this module does not import its caller).
    """
    return binding_violations(answer, evidence, numeric_tokens) + _EXTRA_CHECKS.run(
        answer, evidence, numeric_tokens
    )
