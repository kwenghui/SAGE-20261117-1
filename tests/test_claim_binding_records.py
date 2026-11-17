"""Rounded negatives, all-time records and stated multiples (utils/claim_binding.py)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools._stand_ins import install_stand_ins  # noqa: E402

install_stand_ins()  # streamlit/openai stand-ins when the real libraries are absent

from utils import credentials  # noqa: E402

if not credentials.has_openai_api_key():
    credentials.set_openai_api_key("sk-" + "test0" * 8)  # shape only; never sent anywhere

from utils.answer_verification import numeric_tokens  # noqa: E402
from utils.claim_binding import LEDGER_KEY, binding_violations  # noqa: E402

SOURCE = "https://tablebuilder.singstat.gov.sg/table/TS/M000000"


def _evidence(rows, **extra):
    return [
        {
            "success": True,
            "table_complete": True,
            "analysis_ready": True,
            "table_id": "M000000",
            "source_url": SOURCE,
            "start_year": None,
            "end_year": None,
            "observations": [
                {"series_name": n, "series_no": "1", "unit": "Per Cent", "period": p, "value": v}
                for n, p, v in rows
            ],
            LEDGER_KEY: [[n, p, v] for n, p, v in rows],
            "series_summary": [],
            **extra,
        }
    ]


class RoundedNegativesAndAllTimeRecords(unittest.TestCase):
    GROWTH = [
        ("GDP Growth", "2019", 1.3),
        ("GDP Growth", "2020", -3.23),
        ("GDP Growth", "2021", 9.71),
    ]
    RATE = [("Rate", "2019", 3.1), ("Rate", "2020", 4.1), ("Rate", "2024", 2.7)]

    def _problems(self, answer, evidence):
        return binding_violations(answer, evidence, numeric_tokens)

    def test_a_rounded_negative_value_cannot_be_called_growth(self):
        evidence = _evidence(self.GROWTH)
        self.assertTrue(self._problems("GDP grew 3.2% in 2020.", evidence))
        self.assertTrue(self._problems("GDP grew by 3.23% in 2020.", evidence))
        self.assertEqual(self._problems("GDP contracted 3.2% in 2020.", evidence), [])
        self.assertEqual(self._problems("GDP grew 9.7% in 2021.", evidence), [])

    def test_a_rounding_shared_with_a_positive_value_is_not_judged(self):
        evidence = _evidence(self.GROWTH + [("Other", "2020", 3.2)])
        self.assertEqual(self._problems("Other grew 3.2% in 2020.", evidence), [])

    def test_an_all_time_record_needs_the_whole_series(self):
        whole = _evidence(self.RATE)
        some_years = _evidence(self.RATE, start_year=2019, end_year=2024)
        plain = "The rate peaked at 4.1% in 2020."
        for record in (
            "The rate peaked at 4.1% in 2020, the highest ever.",
            "The rate hit a record high of 4.1 in 2020.",
            "The rate peaked at 4.1% in 2020, its highest on record.",
        ):
            with self.subTest(record=record):
                self.assertEqual(self._problems(record, whole), [])
                self.assertTrue(self._problems(record, some_years))
        self.assertEqual(self._problems(plain, some_years), [])


class StatedMultiplesMatchTheNumbers(unittest.TestCase):
    def _problems(self, answer):
        return binding_violations(answer, [], numeric_tokens)

    def test_doubled_tripled_and_halved(self):
        for right in (
            "The count doubled from 100 to 200.",
            "The count doubled from 100 in 2019 to 215 in 2024.",
            "The count more than doubled from 100 to 260.",
            "The count nearly doubled from 100 to 185.",
            "The count roughly tripled from 1,000 to 3,200.",
            "The count halved from 200 to 100.",
            "The count more than halved from 200 to 80.",
            "The count quadrupled from 2.5 to 10.",
        ):
            with self.subTest(right=right):
                self.assertEqual(self._problems(right), [])
        for wrong in (
            "The count doubled from 100 to 130.",
            "The count doubled from 100 to 300.",
            "The count more than doubled from 100 to 190.",
            "The count nearly doubled from 100 to 140.",
            "The count nearly doubled from 100 to 230.",
            "The count tripled from 1,000 to 2,000.",
            "The count halved from 200 to 170.",
            "The count doubled from 200 to 100.",
        ):
            with self.subTest(wrong=wrong):
                self.assertTrue(self._problems(wrong))

    def test_years_are_not_compared(self):
        self.assertEqual(self._problems("Output doubled from 2019 to 2024."), [])


if __name__ == "__main__":
    unittest.main()
