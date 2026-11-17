"""Tie the numbers in an answer to the period, series and meaning they came from.

utils/answer_verification.py first checks that every number in an answer
appears in the official evidence. That alone is not enough: a real value can
be attached to the wrong year, the wrong series or the wrong kind of quantity
and still "appear in the evidence". The checks here close those gaps. Each one
can only REJECT an answer; none can approve a number.

    period_value_ledger(numeric_df)   every (series, period, value) of a query,
                                      kept with the tool result for these checks
    question_numbers_allowed(...)     which numbers typed by the user may be echoed
    bookkeeping_only_tokens(...)      numbers that exist only as counts/IDs/ranks
    binding_violations(...)           the reasons an answer must not be shown

binding_violations checks, for every table alike: the direction of a stated
change, the SIGN of a value (a published -0.4 is not "an increase of 0.4"),
the period and series a value is quoted for, a share used as an amount, a
year used as a count, and a stated peak or low.

The same rules apply to every table: only the shape of tool results is used.

Used by: utils/answer_verification.py, utils/claim_checks.py,
utils/table_tools.py.
Tested in: tests/test_ask_sage_direct.py, tests/test_claim_binding.py,
tests/test_claim_binding_records.py, tests/test_claim_checks.py.
"""

from __future__ import annotations

import json
import math
import re

from utils.periods import year_from_period
from utils.question_numbers import QuestionNumberPolicy

#: Largest ledger kept with one tool result (rows of [series, period, value]).
MAX_LEDGER_ROWS = 3000
LEDGER_KEY = "period_value_ledger"

#: Result fields that count or identify things. They are facts about the table
#: ("120 series"), never observations, so they cannot back a statistic.
BOOKKEEPING_KEYS = frozenset(
    {
        "series_no",
        "series_id",
        "series_count_available",
        "series_count_selected",
        "series_count_returned",
        "series_total_unfiltered",
        "series_total_matching",
        "observation_count",
        "observation_count_returned",
        "chart_priority",
        "chart_period_count",
        "cube_column_group_count",
        "aligned_period_count",
        "candidate_rank",
        "result_count",
        "rank",
        "offset",
        "next_offset",
        "page_size",
        "max_series",
    }
)

_NUMBER = r"\d[\d,]*(?:\.\d+)?"
_YEAR = r"(?:18|19|20|21)\d{2}"
_SHARE_MARK = r"(?:%|per\s?cent\b|percent\b)"
_SCALE_WORD = r"(?:thousand|million|billion|trillion)\b"
_WORD = r"[A-Za-z$'()/&-]+"
# "3.1% in 2019", "2.7 per cent of residents in 2024"
_VALUE_THEN_YEAR = re.compile(
    rf"(?<![\w.,])(?P<v>{_NUMBER})\s*(?P<mark>{_SHARE_MARK})?"
    rf"(?:\s+{_WORD}){{0,5}}?\s+(?:in|for|during)\s+(?:the\s+year\s+)?(?P<y>{_YEAR})(?![\d,.]*\d)",
    re.I,
)
# "In 2024 the rate was 2.7%" (not "In 2024, up from 3.1%": that value has its own year)
_YEAR_THEN_VALUE = re.compile(
    rf"\b(?:in|for|during|by)\s+(?P<y>{_YEAR}),?"
    rf"(?:\s+(?!from\b|than\b|versus\b|vs\b|compared\b|against\b){_WORD}){{0,7}}?"
    rf"\s+(?P<v>{_NUMBER})(?![\d,]*\d|\s*(?:st|nd|rd|th)\b)",
    re.I,
)
_DIRECTION = re.compile(
    rf"\b(?P<verb>rose|increased|grew|climbed|went\s+up|fell|declined|decreased|dropped|shrank|"
    rf"went\s+down)\s+from\s+[^\d\n.;]{{0,24}}?(?P<a>-?{_NUMBER})(?![\d,]*\d)"
    rf"[^\n.;]{{0,60}}?\bto\s+[^\d\n.;]{{0,24}}?(?P<b>-?{_NUMBER})(?![\d,]*\d)",
    re.I,
)
_UP_VERBS = ("rose", "increased", "grew", "climbed", "went up")
_GENERIC_NAMES = frozenset({"total", "all", "overall", "other", "others", "all items"})
_SHARE_UNITS = ("%", "per cent", "percent", "percentage")

# "2.7% in 2024, up from 3.1%" / "went from 3.1% up to 2.7%" / "an increase from 3.1 to 2.7"
_LATER_THEN_EARLIER = re.compile(
    rf"(?<![\w.,])(?P<b>-?{_NUMBER})(?![\d,]*\d)[^\d\n.;]{{0,30}}?"
    rf"(?:\s+(?:in|for|during)\s+{_YEAR})?,?\s+(?P<dir>up|down)\s+from\s+"
    rf"[^\d\n.;]{{0,24}}?(?P<a>-?{_NUMBER})(?![\d,]*\d)",
    re.I,
)
_FROM_UP_TO = re.compile(
    rf"\bfrom\s+[^\d\n.;]{{0,24}}?(?P<a>-?{_NUMBER})(?![\d,]*\d)[^\n.;]{{0,60}}?"
    rf"\b(?P<dir>up|down)\s+to\s+[^\d\n.;]{{0,24}}?(?P<b>-?{_NUMBER})(?![\d,]*\d)",
    re.I,
)
_NOUN_FROM_TO = re.compile(
    rf"\b(?P<noun>increase|rise|growth|gain|decrease|decline|drop|fall|reduction)\s+from\s+"
    rf"[^\d\n.;]{{0,24}}?(?P<a>-?{_NUMBER})(?![\d,]*\d)"
    rf"[^\n.;]{{0,60}}?\bto\s+[^\d\n.;]{{0,24}}?(?P<b>-?{_NUMBER})(?![\d,]*\d)",
    re.I,
)
_UP_NOUNS = ("increase", "rise", "growth", "gain")
# "doubled from 3.1 to 6.2", "more than tripled from ...", "nearly halved from ..."
_MULTIPLE = re.compile(
    r"\b(?P<q>more\s+than|over|nearly|almost|roughly|about|around)?\s*"
    r"(?P<word>doubled|tripled|trebled|quadrupled|halved)\s+from\s+"
    rf"[^\d\n.;]{{0,24}}?(?P<a>{_NUMBER})(?![\d,]*\d)"
    rf"[^\n.;]{{0,60}}?\bto\s+[^\d\n.;]{{0,24}}?(?P<b>{_NUMBER})(?![\d,]*\d)",
    re.I,
)
_MULTIPLE_OF = {"doubled": 2.0, "tripled": 3.0, "trebled": 3.0, "quadrupled": 4.0, "halved": 2.0}

# --- Sign of a value --------------------------------------------------------------
# Words that say a quantity went UP or DOWN by the number that follows them.
_UP_WORD = (
    r"(?:rose|risen|rises?|rising|increase[sd]?|increasing|grew|grown|grow(?:s|th|ing)?|"
    r"climb(?:s|ed|ing)?|gain(?:s|ed|ing)?|went\s+up|up|higher|expand(?:s|ed|ing)?|expansion)"
)
_DOWN_WORD = (
    r"(?:fell|fallen|falls?|falling|decline[sd]?|declining|decrease[sd]?|decreasing|"
    r"drop(?:s|ped|ping)?|shr[au]nk|shrink(?:s|ing)?|contract(?:s|ed|ing|ion)?|went\s+down|"
    r"down|lower|reduc(?:e[sd]?|ing|tion)|loss|lost)"
)
_APPROXIMATELY = r"(?:about\s+|around\s+|roughly\s+|nearly\s+|almost\s+|some\s+)?"
_MINUS = r"\-\u2212"  # ASCII hyphen-minus and the typographic minus sign
# "increased by 0.4", "grew 12.9%", "an increase of 0.4", "down 3%"
_CHANGE_BY = re.compile(
    rf"\b(?P<word>{_UP_WORD}|{_DOWN_WORD})\s+(?:of\s+|by\s+)?{_APPROXIMATELY}"
    rf"(?P<sign>[{_MINUS}+])?(?P<v>{_NUMBER})(?![\d,]*\d)",
    re.I,
)
_UP_ONLY = re.compile(rf"^{_UP_WORD}$", re.I)
# Any wording that tells the reader a figure is negative or a decrease.
_NEGATIVE_CUE = re.compile(
    rf"\b(?:{_DOWN_WORD}|negative|minus|deficit|outflows?|shortfall|below|under|smaller|fewer|"
    r"less|weaker|worse|slow(?:s|ed|er|ing|down)?|eas(?:ed|ing)|dip(?:s|ped|ping)?|"
    r"slip(?:s|ped|ping)?|slid|slump(?:s|ed)?|sank|sunk|plung(?:e[sd]?|ing)|tumbl(?:e[sd]?|ing)|"
    r"cuts?|moderat(?:e[sd]?|ing|ion)|deflation|exceed(?:s|ed|ing)?|outweigh(?:s|ed)?)\b",
    re.I,
)
# Every number as written, with its sign when one is attached ("-0.4", not "15-64").
_SIGNED_NUMBER = re.compile(
    rf"(?<![\w.,{_MINUS}+])(?P<sign>[{_MINUS}+])?(?P<v>{_NUMBER})(?![\d,]*\d)"
)
#: Computed changes between two periods (their sign depends on the direction of time).
_CHANGE_KEYS = frozenset({"change_latest_minus_earliest", "percentage_change"})
#: Fields that hold a calendar year, not a quantity.
_YEAR_KEYS = frozenset({"year", "start_year", "end_year"})

# --- A year used as a count, value-and-year in other layouts, peaks ----------------
_COUNT_CUE = r"(?:some|an\s+estimated|a\s+total\s+of|totall?ed|totall?ing|numbered|approximately)"
_LOOSE_COUNT_CUE = r"(?:about|around|roughly|nearly|almost)"
_COUNT_VERB = r"(?:were|are|have|had|live[sd]?|work(?:ed|s)?|aged)"
_YEAR_AS_COUNT = re.compile(
    rf"\b(?:{_COUNT_CUE}\s+(?P<y>{_YEAR})\s+[A-Za-z]+"
    rf"|{_LOOSE_COUNT_CUE}\s+(?P<y2>{_YEAR})\s+[A-Za-z]+s\s+{_COUNT_VERB}\b)"
    rf"|(?<![\w.,])(?P<y3>(?:1|2),\d{{3}})(?![\d,.]*\d)"
    rf"|(?<![\w.,])(?P<y4>{_YEAR})\s*(?:{_SHARE_MARK}|{_SCALE_WORD})",
    re.I,
)
# "3.1% (2019)" and "2019: 3.1%"
_VALUE_PAREN_YEAR = re.compile(
    rf"(?<![\w.,])(?P<v>{_NUMBER})\s*(?P<mark>{_SHARE_MARK})?\s*"
    rf"\((?:in\s+)?(?P<y>{_YEAR})\)",
    re.I,
)
_YEAR_COLON_VALUE = re.compile(
    rf"(?<![\w.,/-])(?P<y>{_YEAR})\s*:\s*(?P<v>{_NUMBER})(?![\d,]*\d|\s*(?:st|nd|rd|th)\b)",
    re.I,
)
# Wording that claims a record over the whole history of a series, not just the years read.
_ALL_TIME = re.compile(
    r"\b(?:ever|all[- ]time|on\s+record|record\s+(?:high|low)|since\s+records\s+began)\b", re.I
)
# "peaked at 3.1%", "a record high of 9.2", "bottomed out at 1.9%"
_PEAK = re.compile(
    r"\b(?:(?P<max>peak(?:ed|ing|s)?|record\s+high|all[- ]time\s+high)"
    r"|(?P<min>trough(?:ed)?|bottom(?:ed)?(?:\s+out)?|record\s+low|all[- ]time\s+low))"
    rf"(?:\s+(?:in|during)\s+{_YEAR})?(?:\s+{_WORD}){{0,3}}?\s+(?P<v>{_NUMBER})(?![\d,]*\d)",
    re.I,
)


def period_value_ledger(numeric_df, *, limit: int = MAX_LEDGER_ROWS) -> list:
    """[series name, period, value] for every numeric row of a query ([] when too large).

    The model is shown only a short preview of observations; this complete
    list stays with the tool result so the answer checks can tell WHICH period
    and series a quoted value belongs to.
    """
    if len(numeric_df) > limit:
        return []
    rows = []
    for row in numeric_df.itertuples(index=False):
        value = float(row.value)
        if math.isfinite(value):
            rows.append([str(row.series_name), str(row.period), round(value, 6)])
    return rows


def _as_number(text) -> float | None:
    """A printed number ("1,234.5") as a float, or None."""
    try:
        return float(str(text).replace(",", ""))
    except ValueError:
        return None


def _token(text) -> str:
    """The same normal form utils.answer_verification.numeric_tokens uses."""
    return str(text).replace(",", "").rstrip("%").lstrip("+-")


def _used_as_statistic(token, answer) -> bool:
    """True when the answer presents this number as a measured quantity.

    A share ("9.9%"), a scaled amount ("9 million") or any decimal is a
    statistic. A plain whole number ("the last 10 years", "top 5") is not.
    """
    if "." in token:
        return True
    spaced = r"[,]?".join(re.escape(ch) for ch in token)  # "1234" also written "1,234"
    return bool(
        re.search(rf"(?<![\d.,]){spaced}\s*(?:{_SHARE_MARK}|{_SCALE_WORD})", str(answer), re.I)
    )


def question_numbers_allowed(question_tokens, evidence_tokens, answer) -> set:
    """The user's own numbers that an answer may repeat.

    A number that exists ONLY in the question is not evidence: "is it true the
    rate was 9.9%?" must not make 9.9% a verified figure, and "were there 5000?"
    must not make 5,000 one. Such a number may be echoed only as plain wording
    (a year in a time phrase, "the last 10 years", "the top 5", "aged 15 and
    over"), never as a statistic. The rules are in utils/question_numbers.py.
    """
    only_in_question = set(question_tokens) - set(evidence_tokens)
    return QuestionNumberPolicy(answer).allowed(only_in_question)


def _is_bookkeeping(key) -> bool:
    """True for a counting/identifying field (listed above, or any "..._count" field)."""
    name = str(key)
    return name in BOOKKEEPING_KEYS or name.endswith("_count") or "_count_" in name


_SPAN = re.compile(
    r"(?<![\d.,])(\d{1,3})(?:\s*-\s*|\s+)(?:year|month|quarter|period)s?\b", re.I
)


def span_tokens_allowed(answer, evidence_tokens) -> set:
    """Lengths of time ("over the past 5 years") that follow from the evidence itself.

    Allowed only when the number equals the distance between two years that are
    both in the answer and in the evidence (or that distance plus one, counting
    both ends). Such a phrase used to get a correct answer rejected.
    """
    text = str(answer or "")
    years = sorted(
        {int(y) for y in re.findall(rf"(?<![\d.,]){_YEAR}(?![\d.,]*\d)",
                                    text) if y in evidence_tokens}
    )
    spans = set()
    for index, first in enumerate(years):
        for second in years[index + 1 :]:
            spans.update((second - first, second - first + 1))
    return {match for match in _SPAN.findall(text) if int(match) in spans}


_DECADE = re.compile(r"(?<![\d.,])((?:18|19|20|21)\d0)(?:'|\u2019)?s\b")


def decade_tokens_allowed(answer, evidence_tokens) -> set:
    """Decades named in words ("the late 2000s") when the evidence has a year in them.

    "2000s" is wording, not a figure, but its digits were read as the number
    2000 and a correct sentence was removed when the data began after 2000.
    Allowed only when at least one year of that decade is in the evidence, so
    "the 1990s" is still refused for a series that starts in 2005.
    """
    allowed = set()
    for decade in _DECADE.findall(str(answer or "")):
        if any(str(int(decade) + offset) in evidence_tokens for offset in range(10)):
            allowed.add(decade)
    return allowed


def _without_bookkeeping(node):
    """A copy of a tool result without its counting/identifying fields."""
    if isinstance(node, dict):
        return {
            key: _without_bookkeeping(value)
            for key, value in node.items()
            if not _is_bookkeeping(key)
        }
    if isinstance(node, list):
        return [_without_bookkeeping(item) for item in node]
    return node


def bookkeeping_only_tokens(evidence, numeric_tokens) -> set:
    """Numbers found in the evidence ONLY as counts, ranks, offsets or series IDs."""
    everything = set()
    substantive = set()
    for result in evidence or []:
        everything |= numeric_tokens(json.dumps(result, ensure_ascii=False, default=str))
        substantive |= numeric_tokens(
            json.dumps(_without_bookkeeping(result), ensure_ascii=False, default=str)
        )
    return everything - substantive


def bookkeeping_misused(tokens, answer) -> set:
    """Those bookkeeping numbers the answer presents as statistics.

    "The table has 120 series" is fine. "120%" is not, and a series ID such as
    4.1 may be cited ("series 4.1") but never quoted as a value.
    """
    misused = set()
    for token in tokens:
        if "." in token:
            cited = re.search(
                rf"(?i)\b(?:series|id|no\.?|row)\s*[:#]?\s*{re.escape(token)}(?![\d])", str(answer)
            )
            if not cited:
                misused.add(token)
        elif _used_as_statistic(token, answer):
            misused.add(token)
    return misused


class _Ledger:
    """What the evidence says about which value belongs to which year and series."""

    def __init__(self, evidence, numeric_tokens) -> None:
        self.pairs: dict = {}  # (year, value) -> set of series names
        self.values: set = set()
        self.years: set = set()
        self.derived: set = set()  # changes between periods: not tied to one year
        self.unbound_tokens: set = set()  # results whose rows cannot be read here
        self.share_only_values: set = set()
        self.names: set = set()
        self.by_series: dict = {}  # series name -> {year: published (signed) value}
        self.year_filtered: set = set()  # series queried for a range of years only
        other_unit_values = set()
        for result in evidence or []:
            if not isinstance(result, dict):
                continue
            rows = self._rows(result)
            if rows is None:
                self.unbound_tokens |= numeric_tokens(
                    json.dumps(result, ensure_ascii=False, default=str)
                )
                continue
            for name, period, value, unit, published in rows:
                year = year_from_period(period)
                if year is None:
                    continue
                self.by_series.setdefault(name.casefold(), {})[year] = published
                if result.get("start_year") is not None or result.get("end_year") is not None:
                    self.year_filtered.add(name.casefold())
                self.pairs.setdefault((year, value), set()).add(name.casefold())
                self.values.add(value)
                self.years.add(year)
                self.names.add(name.casefold())
                if unit.casefold().strip() in _SHARE_UNITS:
                    self.share_only_values.add(value)
                else:
                    other_unit_values.add(value)
            for entry in result.get("series_summary") or []:
                for field in ("change_latest_minus_earliest", "percentage_change"):
                    change = entry.get(field) if isinstance(entry, dict) else None
                    if type(change) in (int, float) and math.isfinite(change):
                        self.derived.add(abs(round(float(change), 6)))
                        for digits in (1, 2):  # the renderings the answer may print
                            self.derived.add(abs(round(float(change), digits)))
        self.share_only_values -= other_unit_values

    @staticmethod
    def _rows(result):
        """(name, period, value, unit, published value) rows of one result, or None if incomplete.

        ``value`` is the size of the number (no sign), as it is matched against
        the text; ``published value`` keeps the official sign. Incomplete means:
        no ledger and a truncated observation preview. Such a result is never
        used to reject a pairing (its other periods are unseen).
        """
        units = {}
        rows = []
        for row in result.get("observations") or []:
            if isinstance(row, dict) and type(row.get("value")) in (int, float):
                units[str(row.get("series_name"))] = str(row.get("unit") or "")
                rows.append((str(row.get("series_name")), str(row.get("period")),
                             round(float(row["value"]), 6)))
        ledger = result.get(LEDGER_KEY)
        if isinstance(ledger, list) and ledger:
            rows = [
                (str(item[0]), str(item[1]), round(float(item[2]), 6))
                for item in ledger
                if isinstance(item, (list, tuple))
                and len(item) == 3
                and type(item[2]) in (int, float)
            ]
        elif result.get("observations_truncated") or not rows:
            return None
        return [
            (name, period, abs(value), units.get(name, ""), value) for name, period, value in rows
        ]


def _sentence_around(text, start, end) -> str:
    """The sentence (or table/bullet line) containing text[start:end]."""
    left = max(text.rfind("\n", 0, start), text.rfind(". ", 0, start))
    stops = [pos for pos in (text.find("\n", end), text.find(". ", end)) if pos != -1]
    return text[left + 1 : min(stops) if stops else len(text)]


def _names_mentioned(sentence, names) -> set:
    """Official series names written in the sentence (a plural "s" may differ)."""
    words = [w.rstrip("s") for w in re.findall(r"[a-z0-9]+", sentence.casefold())]
    found = set()
    for name in names:
        if name in _GENERIC_NAMES or len(name) < 4:
            continue
        wanted = [w.rstrip("s") for w in re.findall(r"[a-z0-9]+", name)]
        if wanted and any(
            words[i : i + len(wanted)] == wanted for i in range(len(words) - len(wanted) + 1)
        ):
            found.add(name)
    return found


def _looks_like_year(raw) -> bool:
    """True for a bare four-digit year such as 2019 (not "2,019" or "2019.5")."""
    return bool(re.fullmatch(_YEAR, raw))


class _Signs:
    """Which sign each number has in the evidence (the text checks ignore signs).

    ``anywhere`` maps the size of every numeric field to the signs it is
    published with; ``observed`` holds the sizes of official observations and
    ``changes`` those of computed changes between two periods.
    """

    def __init__(self, evidence) -> None:
        self.anywhere: dict = {}
        self.observed: set = set()
        self.changes: set = set()
        self.quantities: set = set()  # every size that is not a calendar-year field
        self.rounded: dict = {}  # an observation rounded to 1 or 2 decimals -> its signs
        for result in evidence or []:
            if isinstance(result, dict):
                self._walk(result, None)

    def _walk(self, node, key) -> None:
        """Visit every numeric field of a tool result (counts and IDs are skipped)."""
        if isinstance(node, dict):
            for name, value in node.items():
                if not _is_bookkeeping(name):
                    self._walk(value, str(name))
        elif isinstance(node, (list, tuple)):
            for item in node:
                self._walk(item, key)
        elif type(node) in (int, float) and math.isfinite(node) and node != 0:
            self._note(float(node), key)

    def _note(self, number, key) -> None:
        """Record one number under the sizes an answer may print it with."""
        if key in _YEAR_KEYS:
            return
        signs = {1, -1} if str(key).startswith("difference") else {1 if number > 0 else -1}
        # A computed change may be printed to one or two decimals; observations as published.
        for digits in (6, 2, 1) if key in _CHANGE_KEYS else (6,):
            size = abs(round(number, digits))
            if not size:
                continue
            self.anywhere.setdefault(size, set()).update(signs)
            self.quantities.add(size)
            if key in _CHANGE_KEYS:
                self.changes.add(size)
            elif key in ("value", "numeric_claims", LEDGER_KEY):
                self.observed.add(size)
        if key in ("value", "numeric_claims", LEDGER_KEY):
            for digits in (2, 1):  # how the answer may round a published observation
                size = abs(round(number, digits))
                if size:
                    self.rounded.setdefault(size, set()).update(signs)

    def only(self, size, sign) -> bool:
        """True when ``size`` appears in the evidence with this sign and never the other."""
        return self.anywhere.get(size) == {sign}

    def only_rounded_negative(self, size) -> bool:
        """True when ``size`` is in the evidence ONLY as a rounded negative observation.

        "grew 3.2%" for a published -3.23: the exact value is not 3.2, so the
        exact-sign rule cannot see it. Never true for a size that is also
        published exactly, or that rounds from a positive observation too.
        """
        return size not in self.anywhere and self.rounded.get(size) == {-1}


def _context(text, start, end) -> str:
    """The sentence around text[start:end]; for a table row, also that table's header row."""
    sentence = _sentence_around(text, start, end)
    line_start = text.rfind("\n", 0, start) + 1
    if not text[line_start:].lstrip().startswith("|"):
        return sentence
    header = None
    cursor = line_start
    while cursor > 0:  # walk up to the first row of this Markdown table
        previous = text.rfind("\n", 0, cursor - 1) + 1
        row = text[previous : cursor - 1]
        if not row.lstrip().startswith("|"):
            break
        header, cursor = row, previous
    return sentence + ("\n" + header if header else "")


def _direction_problems(text) -> list[str]:
    """A stated rise or fall between two quoted numbers must match those numbers."""
    problems = []
    for match in _DIRECTION.finditer(text):
        rising = re.sub(r"\s+", " ", match["verb"].lower()) in _UP_VERBS
        problems += _direction_problem(match["verb"], match["a"], match["b"], rising)
    for pattern in (_LATER_THEN_EARLIER, _FROM_UP_TO):
        for match in pattern.finditer(text):
            if not (_looks_like_year(match["a"]) or _looks_like_year(match["b"])):
                problems += _direction_problem(
                    match["dir"], match["a"], match["b"], match["dir"].lower() == "up"
                )
    for match in _NOUN_FROM_TO.finditer(text):
        problems += _direction_problem(
            match["noun"], match["a"], match["b"], match["noun"].lower() in _UP_NOUNS
        )
    for match in _MULTIPLE.finditer(text):
        problems += _multiple_problem(match)
    return problems


def _multiple_problem(match) -> list[str]:
    """["multiple: ..."] when "doubled from A to B" is not what A and B show.

    The stated multiple may be off by 15% ("doubled" covers 1.7 to 2.3 times).
    "More than" / "over" needs at least the multiple; "nearly" / "almost" needs
    80% of it or more, but less than the multiple. "Halved" is read the other
    way round (A is twice B).
    """
    first, second = _as_number(match["a"]), _as_number(match["b"])
    if (
        first is None or second is None or first <= 0 or second <= 0
        or _looks_like_year(match["a"]) or _looks_like_year(match["b"])
    ):
        return []
    word = match["word"].lower()
    ratio = (first / second if word == "halved" else second / first) / _MULTIPLE_OF[word]
    qualifier = re.sub(r"\s+", " ", (match["q"] or "").lower())
    if qualifier in ("more than", "over"):
        fits = ratio >= 1.0
    elif qualifier in ("nearly", "almost"):
        fits = 0.8 <= ratio < 1.0
    else:
        fits = 0.85 <= ratio <= 1.15
    if fits:
        return []
    said = (qualifier + " " + word).strip()
    return [f"multiple: '{said}' from {match['a']} to {match['b']}"]


def _direction_problem(word, first_raw, second_raw, rising) -> list[str]:
    """["direction: ..."] when the wording says up but the numbers go down (or the reverse)."""
    first, second = _as_number(first_raw), _as_number(second_raw)
    if first is None or second is None or first == second or rising == (second > first):
        return []
    return [f"direction: '{word}' from {first_raw} to {second_raw}"]


def _sign_problems(text, signs) -> list[str]:
    """A number must keep the sign it has in the evidence.

    1. "increased by 0.4" when the only 0.4 in the evidence is -0.4 (and
       "fell by 0.4" when the computed change is +0.4);
    2. "-0.4" written for a value that is only ever positive;
    3. an official observation that is only ever negative, written without a
       minus sign in a sentence that never says it is negative or a decrease.
    A size published with both signs is never judged.
    """
    problems = []
    governed = set()
    for match in _CHANGE_BY.finditer(text):
        size = _as_number(match["v"])
        if size is None or match["sign"] or _looks_like_year(match["v"]):
            continue
        size = abs(round(size, 6))
        rising = bool(_UP_ONLY.match(re.sub(r"\s+", " ", match["word"])))
        if rising and (signs.only(size, -1) or signs.only_rounded_negative(size)):
            governed.add(match.span("v"))
            problems.append(f"sign: '{match['word']}' {match['v']}, published as -{match['v']}")
        elif not rising and size in signs.changes and signs.only(size, 1):
            governed.add(match.span("v"))
            problems.append(f"sign: '{match['word']}' {match['v']}, computed change is positive")
    for match in _SIGNED_NUMBER.finditer(text):
        size = _as_number(match["v"])
        if size is None or _looks_like_year(match["v"]) or match.span("v") in governed:
            continue
        size = abs(round(size, 6))
        negative = bool(match["sign"]) and match["sign"] != "+"
        known = size in signs.observed or size in signs.changes
        if negative and known and signs.only(size, 1):
            problems.append(f"sign: -{match['v']} is published as a positive value")
        elif (
            not negative
            and size in signs.observed
            and signs.only(size, -1)
            and not _NEGATIVE_CUE.search(_context(text, match.start(), match.end()))
        ):
            problems.append(f"sign: {match['v']} is published as -{match['v']}")
    return problems


def _year_as_count_problems(text, signs) -> list[str]:
    """A calendar year is not a quantity ("about 2024 residents", "2,024", "2024%").

    The digits of a year are in the evidence as a period, so the membership
    check lets them through. Refused unless the same number is also a real
    quantity somewhere in the evidence.
    """
    problems = []
    for match in _YEAR_AS_COUNT.finditer(text):
        raw = next(match[name] for name in ("y", "y2", "y3", "y4") if match[name])
        size = _as_number(raw)
        if size is not None and _looks_like_year(str(int(size))) and size not in signs.quantities:
            problems.append(f"quantity: {raw} is a year, not a published quantity")
    return problems


def _peak_problems(text, ledger) -> list[str]:
    """A stated peak (or low) must be the highest (lowest) published value of its series.

    Judged within the periods of the evidence only, for series with at least
    two periods, and only when the value is an observation of the series the
    sentence names (or of any series, when it names none). A record claimed
    for all time ("ever", "on record", "a record high") also needs the whole
    series: it is refused when the series was read for a range of years only.
    """
    problems = []
    for match in _PEAK.finditer(text):
        size = _as_number(match["v"])
        if size is None or _looks_like_year(match["v"]):
            continue
        size = abs(round(size, 6))
        if _token(match["v"]) in ledger.unbound_tokens:
            continue
        holders = {
            name: values
            for name, values in ledger.by_series.items()
            if len(values) >= 2 and any(abs(value) == size for value in values.values())
        }
        named = _names_mentioned(_sentence_around(text, match.start(), match.end()), ledger.names)
        if named & set(holders):
            holders = {name: holders[name] for name in named & set(holders)}
        pick = max if match["max"] else min
        if holders and not any(
            abs(pick(values.values())) == size for values in holders.values()
        ):
            kind = "highest" if match["max"] else "lowest"
            problems.append(f"peak: {match['v']} is not the {kind} published value")
        elif (
            holders
            and set(holders) <= ledger.year_filtered
            and _ALL_TIME.search(_sentence_around(text, match.start(), match.end()))
        ):
            # Only some years were read: "a record" or "ever" cannot be proved from them.
            problems.append(f"peak: {match['v']} is a record only within the years queried")
    return problems


def _flag_problems(text, evidence, signs) -> list[str]:
    """A figure published with a flag ("12.3p") must be written exactly as published.

    The digits of a flagged cell are in the evidence, so the membership check
    lets a bare "12.3" through, which would present a flagged figure as an
    ordinary one. Refused unless every use of the number sits inside the
    published text. A number that is also an ordinary value elsewhere in the
    evidence is not judged.
    """
    published = set()
    for result in evidence or []:
        for cell in (result.get("flagged_cells") or []) if isinstance(result, dict) else []:
            if isinstance(cell, dict) and isinstance(cell.get("published"), str):
                published.add(cell["published"])
    problems = []
    for flagged in sorted(published):
        inside = [
            (found.start(), found.end()) for found in re.finditer(re.escape(flagged), text)
        ]
        for number in re.findall(_NUMBER, flagged):
            size = _as_number(number)
            if size is None or abs(round(size, 6)) in signs.anywhere:
                continue
            for match in re.finditer(rf"(?<![\w.,]){re.escape(number)}(?![\d,]*\d)", text):
                if not any(start <= match.start() and match.end() <= end for start, end in inside):
                    problems.append(f"flag: {number} is published as {flagged}")
                    break
    return problems


def binding_violations(answer, evidence, numeric_tokens) -> list[str]:
    """Reasons the answer misuses real numbers ([] when none were found).

    Checked, in this order:
      1. direction  - "rose from A to B" needs B > A ("fell" needs B < A); also
                      "B, up from A", "from A up to B" and "an increase from A to B";
                      "doubled from A to B" needs B to be about twice A;
      2. sign       - a value keeps the sign it was published with (_sign_problems);
      3. year       - a calendar year is not a count or a percentage;
      4. period     - a value stated for a year must be an observation OF that year
                      ("3.1% in 2019", "3.1% (2019)", "2019: 3.1%");
      5. series     - ...and of a series the sentence names, when it names one;
      6. quantity   - a value published as a share is not an amount in millions;
      7. peak       - "peaked at X" needs X to be the highest value of its series;
      8. flag       - a figure published with a flag is written exactly as published.

    ``numeric_tokens`` is utils.answer_verification.numeric_tokens (passed in so
    this module does not import its caller).
    """
    text = str(answer or "")
    signs = _Signs(evidence)
    problems = _direction_problems(text)
    problems += _sign_problems(text, signs)
    problems += _year_as_count_problems(text, signs)
    problems += _flag_problems(text, evidence, signs)
    ledger = _Ledger(evidence, numeric_tokens)
    if not ledger.pairs:
        return problems
    bound_spans = set()
    claims = []
    for pattern in (_VALUE_THEN_YEAR, _VALUE_PAREN_YEAR):
        for match in pattern.finditer(text):
            if match.span("v") not in bound_spans:
                bound_spans.add(match.span("v"))
                claims.append(match)
    for pattern in (_YEAR_THEN_VALUE, _YEAR_COLON_VALUE):
        for match in pattern.finditer(text):
            if match.span("v") not in bound_spans:
                bound_spans.add(match.span("v"))
                claims.append(match)
    for match in claims:
        raw = match["v"]
        value, year = _as_number(raw), int(match["y"])
        if value is None or _looks_like_year(raw):
            continue
        value = abs(round(value, 6))
        after = text[match.end("v") : match.end("v") + 24]
        if value in ledger.share_only_values and re.match(rf"\s*{_SCALE_WORD}", after, re.I):
            problems.append(f"quantity: {raw} is published as a share, not an amount")
            continue
        if (
            value not in ledger.values
            or value in ledger.derived
            or year not in ledger.years
            or _token(raw) in ledger.unbound_tokens
        ):
            continue  # not something this ledger can judge: never reject on a guess
        series = ledger.pairs.get((year, value))
        if series is None:
            problems.append(f"period: {raw} is not an observation of {year}")
            continue
        named = _names_mentioned(_sentence_around(text, match.start(), match.end()), ledger.names)
        if named and not (named & series):
            problems.append(f"series: {raw} in {year} belongs to another series")
    return problems + _peak_problems(text, ledger)
