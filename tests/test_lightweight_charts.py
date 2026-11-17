"""Altair charts refuse malformed or unverified chart specifications (utils/lightweight_charts.py)."""

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


class AltairChartsRefuseUnverifiedValues(unittest.TestCase):
    def setUp(self):
        try:
            from utils import lightweight_charts
        except ImportError:  # altair is not installed on this machine
            self.skipTest("needs altair")
        self.charts = lightweight_charts

    def test_a_malformed_specification_is_refused(self):
        good = {"categories": ["2023", "2024"], "series": [{"name": "A", "values": [1.0, 2.0]}]}
        for bad in (
            None,
            {"categories": "2023", "series": []},
            {**good, "categories": []},
            {**good, "series": []},
            {**good, "series": [{"name": "A", "values": [1.0]}]},  # a value is missing
            {**good, "series": [{"name": "A", "values": [1.0, float("nan")]}]},
            {**good, "series": [{"name": "A", "values": [1.0, "2.0"]}]},  # text is not a value
            {**good, "series": [{"name": "A", "values": [1.0, True]}]},
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.charts.to_altair_chart(bad)


if __name__ == "__main__":
    unittest.main()
