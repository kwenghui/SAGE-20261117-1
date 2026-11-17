"""The generic table tools: computed changes, the rows the model is shown, cell values (utils/table_tools.py)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
from utils import credentials  # noqa: E402
from utils.answer_verification import full_answer_numbers_supported  # noqa: E402
from utils.table_tools import (  # noqa: E402
    MAX_GENERIC_OBSERVATIONS,
    _observation_preview,
    _series_summaries,
)

SOURCE = "https://tablebuilder.singstat.gov.sg/table/TS/M000000"


def _frame(unit="Number", values=(200.0, 250.0)):
    return pd.DataFrame(
        {
            "series_no": ["1", "1"],
            "series_name": ["Residents", "Residents"],
            "unit": [unit, unit],
            "period": ["2019", "2024"],
            "value": list(values),
        }
    )


import math
from tools._stand_ins import install_stand_ins  # noqa: E402

install_stand_ins()  # streamlit/openai stand-ins when the real libraries are absent


if not credentials.has_openai_api_key():
    credentials.set_openai_api_key("sk-" + "test0" * 8)  # shape only; never sent anywhere

from utils.table_tools import _coerce_singstat_value  # noqa: E402


class PercentageChangeIsComputedByTheTool(unittest.TestCase):
    def _summary(self, frame):
        selected = frame[["series_no", "series_name", "unit"]].drop_duplicates()
        return _series_summaries(selected, frame)[0]

    def test_relative_change_is_in_the_summary(self):
        self.assertEqual(self._summary(_frame())["percentage_change"], 25.0)

    def test_not_given_for_shares_or_a_zero_start(self):
        self.assertNotIn("percentage_change", self._summary(_frame(unit="Per Cent")))
        self.assertNotIn("percentage_change", self._summary(_frame(values=(0.0, 5.0))))

    def test_the_answer_may_quote_it_and_nothing_else(self):
        frame = _frame(values=(300.0, 337.0))
        evidence = [
            {
                "success": True,
                "table_complete": True,
                "analysis_ready": True,
                "table_id": "M000000",
                "source_url": SOURCE,
                "series_summary": [self._summary(frame)],
                "observations": frame.to_dict("records"),
            }
        ]
        question = "How much did the resident count grow?"
        self.assertTrue(
            full_answer_numbers_supported("The count grew by 12.3%.", evidence, question, log=False)
        )
        self.assertFalse(
            full_answer_numbers_supported("The count grew by 14.9%.", evidence, question, log=False)
        )


class TheModelSeesRecentRowsOfEverySeries(unittest.TestCase):
    def test_a_long_selection_keeps_the_latest_periods_of_each_series(self):
        years = [str(year) for year in range(1960, 2025)]
        frame = pd.DataFrame(
            {
                "series_no": ["1"] * len(years) + ["2"] * len(years),
                "series_name": ["A"] * len(years) + ["B"] * len(years),
                "period": years * 2,
                "value": [float(i) for i in range(2 * len(years))],
            }
        )
        preview = _observation_preview(frame)
        self.assertEqual(len(preview), MAX_GENERIC_OBSERVATIONS)
        self.assertEqual(set(preview["series_no"]), {"1", "2"})
        self.assertIn("2024", set(preview["period"]))
        self.assertNotIn("1960", set(preview["period"]))

    def test_a_short_selection_is_unchanged(self):
        self.assertEqual(len(_observation_preview(_frame())), 2)


class OfficialCellValues(unittest.TestCase):
    def test_numbers_are_read_and_non_numbers_are_missing(self):
        self.assertEqual(_coerce_singstat_value("1,234.5"), 1234.5)
        self.assertEqual(_coerce_singstat_value("12%"), 12.0)
        for missing in ("-", "na", "", None, "12.3p"):
            with self.subTest(missing=missing):
                self.assertTrue(math.isnan(_coerce_singstat_value(missing)))

    def test_a_typographic_minus_sign_keeps_the_value_negative(self):
        self.assertEqual(_coerce_singstat_value("−3.2"), -3.2)

    def test_infinity_and_nan_are_never_figures(self):
        for text in ("inf", "-inf", "Infinity", "nan", "NaN"):
            with self.subTest(text=text):
                self.assertTrue(math.isnan(_coerce_singstat_value(text)))


if __name__ == "__main__":
    unittest.main()
