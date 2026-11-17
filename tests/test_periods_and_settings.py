"""Period labels in time order, and settings that fail closed (utils/periods.py, utils/credentials.py, utils/model_client.py)."""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from utils import credentials, model_client  # noqa: E402
from utils.errors import CredentialError  # noqa: E402
from utils.periods import period_sort_key  # noqa: E402
from tools._stand_ins import install_stand_ins  # noqa: E402

install_stand_ins()  # streamlit/openai stand-ins when the real libraries are absent


if not credentials.has_openai_api_key():
    credentials.set_openai_api_key("sk-" + "test0" * 8)  # shape only; never sent anywhere


class PeriodsAndSettings(unittest.TestCase):
    def test_the_year_is_not_read_as_a_quarter_or_half(self):
        self.assertEqual(period_sort_key("2024 Q1")[:4], (0, 2024, 2, 1))
        self.assertEqual(period_sort_key("2023 Q2")[:4], (0, 2023, 2, 2))
        self.assertEqual(period_sort_key("2024 1Q")[:4], (0, 2024, 2, 1))
        self.assertEqual(period_sort_key("2022 H1")[:4], (0, 2022, 1, 1))

    def test_a_configured_model_outside_the_built_in_list_is_usable(self):
        allowed = model_client.allowed_models({"SAGE_CHAT_MODEL": "gpt-next-1"})
        self.assertEqual(allowed[0], "gpt-next-1")
        self.assertEqual(model_client.allowed_models({}), model_client.MODEL_OPTIONS)
        bad = model_client.allowed_models({"SAGE_CHAT_MODEL": "x y; rm"})
        self.assertEqual(bad, model_client.MODEL_OPTIONS)

    def test_a_malformed_legacy_environment_key_is_refused(self):
        credentials._reset_for_tests()
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "not a key"}, clear=False):
            os.environ.pop("KEY", None)
            with self.assertRaises(CredentialError):
                credentials.get_openai_api_key()

    def test_production_requirements_are_exact_and_match_the_lock(self):
        lock = (ROOT / "requirements.lock").read_text(encoding="utf-8")
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#"):
                self.assertIn("==", line)
                self.assertIn(line.strip().lower() + " \\", lock.lower())


class HalfYearPeriods(unittest.TestCase):
    def test_a_semester_is_a_half_year_not_a_month(self):
        self.assertEqual(period_sort_key("2024 Sem 1")[:4], (0, 2024, 1, 1))
        self.assertEqual(period_sort_key("2024 Semester 2")[:4], (0, 2024, 1, 2))
        self.assertEqual(period_sort_key("2024 H2")[:4], (0, 2024, 1, 2))

    def test_month_labels_are_unchanged(self):
        self.assertEqual(period_sort_key("2026M01")[:4], (0, 2026, 3, 1))
        self.assertEqual(period_sort_key("2026 Month 3")[:4], (0, 2026, 3, 3))
        self.assertEqual(period_sort_key("2024 Mar")[:4], (0, 2024, 3, 3))


if __name__ == "__main__":
    unittest.main()
