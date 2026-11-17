"""Is the column axis of a table TIME, or a set of categories?

Most SingStat tables have one column per period ("2023", "2024 Q1"). Some have
one column per CATEGORY instead ("Total", "Males", "Females", or age bands).
The query tools used to treat every multi-column table as a time series, so a
category table got an "earliest" and a "latest" column (in alphabetical
order), a "change" between them, a "percentage change" and a trend line. None
of those exists in the official table, yet all of them were handed to the
answer check as verified figures.

PeriodAxis answers the one question the tools need: may these column labels be
read as moments in time? The rule is the same for every table and fails
closed: the axis is time only when EVERY label carries a calendar year
(utils.periods.year_from_period). One label without a year (a total, a band,
a group) makes the whole axis categorical.

    PeriodAxis(labels).is_time         True when every label is a period
    PeriodAxis.from_frame(frame)       the axis of a normalised table frame
    PeriodAxis.note()                  the sentence given to the model

Pure: no model, network or Streamlit.

Used by: utils/table_tools.py.
Tested in: tests/test_period_axis.py.
"""

from __future__ import annotations

import logging

from utils.periods import year_from_period

LOG = logging.getLogger("sage")

#: Axis kinds reported in tool results (``column_axis``).
AXIS_TIME = "time"
AXIS_CATEGORIES = "categories"

#: Sent to the model when the columns are categories. It is an instruction about
#: wording only; the values themselves stay available as observations.
CATEGORY_AXIS_NOTE = (
    "The columns of this table are categories, not periods. There is no earliest or "
    "latest period, no change over time and no trend here: report the published value "
    "of each category and do not describe one category as following another in time."
)


class PeriodAxisError(ValueError):
    """The column labels could not be read at all (not a list of labels)."""


class PeriodAxis:
    """The column labels of one selection, classified as time or categories.

    Labels are compared as trimmed text; empty labels are ignored. An axis
    with no usable label is NOT time (nothing proves that it is).
    """

    def __init__(self, labels) -> None:
        """Keep the distinct, non-empty labels in their first-seen order."""
        try:
            cleaned = [str(label).strip() for label in labels if label is not None]
        except TypeError as error:
            raise PeriodAxisError("column labels must be a list of labels") from error
        self.labels = tuple(dict.fromkeys(label for label in cleaned if label))

    @classmethod
    def from_frame(cls, frame) -> "PeriodAxis":
        """The axis of a normalised table frame (its ``period`` column).

        A frame without that column, or one that cannot be read, gives an
        empty axis: not time, so nothing time-based is computed from it.
        """
        try:
            if frame is None or "period" not in getattr(frame, "columns", ()):
                return cls(())
            return cls(frame["period"].dropna().astype(str).tolist())
        except (PeriodAxisError, AttributeError, KeyError, TypeError, ValueError) as error:
            # Fail closed: an unreadable axis is treated as "not time".
            LOG.warning("Column axis unreadable (%s); treated as categories", type(error).__name__)
            return cls(())

    @property
    def count(self) -> int:
        """Number of distinct column labels."""
        return len(self.labels)

    @property
    def is_time(self) -> bool:
        """True only when there are labels and EVERY one of them carries a calendar year."""
        return bool(self.labels) and all(
            year_from_period(label) is not None for label in self.labels
        )

    @property
    def kind(self) -> str:
        """``"time"`` or ``"categories"`` (the value of ``column_axis`` in tool results)."""
        return AXIS_TIME if self.is_time else AXIS_CATEGORIES

    @property
    def supports_change_over_time(self) -> bool:
        """True when an earliest/latest pair, a change and a trend line are meaningful.

        Needs a time axis with at least two periods. A single period has no
        change; categories have no order in time.
        """
        return self.is_time and self.count >= 2

    def note(self) -> str | None:
        """The wording rule for the model when the columns are categories, else None."""
        return None if self.is_time or self.count < 2 else CATEGORY_AXIS_NOTE
