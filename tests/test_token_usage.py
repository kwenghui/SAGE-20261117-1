"""The daily token ceiling survives a restart and is shared between workers (utils/token_usage.py)."""

import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools._stand_ins import install_stand_ins  # noqa: E402

install_stand_ins()  # streamlit/openai stand-ins when the real libraries are absent

from utils import credentials  # noqa: E402

if not credentials.has_openai_api_key():
    credentials.set_openai_api_key("sk-" + "test0" * 8)  # shape only; never sent anywhere

from utils.errors import DailyTokenBudgetExceeded  # noqa: E402
from utils.token_usage import _DailyBudget  # noqa: E402


class DailyCeilingSurvivesARestart(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "data" / "daily.sqlite3"
        limit = mock.patch.dict("os.environ", {"SAGE_DAILY_TOKEN_LIMIT": "1000"})
        limit.start()
        self.addCleanup(limit.stop)

    def _budget(self, path="default"):
        budget = _DailyBudget()
        budget.keep_on_disk(self.path if path == "default" else path)
        return budget

    def test_memory_only_until_the_page_asks_for_a_file(self):
        budget = _DailyBudget()
        budget.add(400)
        self.assertEqual(budget.used_today(), 400)
        self.assertFalse(self.path.exists())

    def test_the_count_survives_a_restart(self):
        self._budget().add(600)
        restarted = self._budget()  # a new process: nothing in memory
        restarted.check()
        self.assertEqual(restarted.used_today(), 600)
        restarted.add(400)
        with self.assertRaises(DailyTokenBudgetExceeded):
            self._budget().check()

    def test_two_workers_share_one_ceiling(self):
        first, second = self._budget(), self._budget()
        first.add(600)
        second.add(500)
        self.assertEqual(first.used_today(), 1100)
        with self.assertRaises(DailyTokenBudgetExceeded):
            first.check()

    def test_a_new_day_starts_from_zero_and_forgets_yesterday(self):
        budget = self._budget()
        with mock.patch.object(_DailyBudget, "_today", return_value=date(2026, 11, 16)):
            budget.add(900)
        with mock.patch.object(_DailyBudget, "_today", return_value=date(2026, 11, 17)):
            budget.check()
            self.assertEqual(budget.used_today(), 0)
            budget.add(10)
        con = sqlite3.connect(self.path)
        try:
            self.assertEqual(
                con.execute("SELECT day, used FROM daily_tokens").fetchall(), [("2026-11-17", 10)]
            )
        finally:
            con.close()

    def test_an_unusable_file_never_breaks_a_question(self):
        blocker = Path(self.folder.name) / "not-a-folder"
        blocker.write_text("x", encoding="utf-8")
        budget = self._budget(blocker / "daily.sqlite3")
        with self.assertLogs("sage", level="WARNING"):
            budget.add(300)
        budget.check()
        self.assertEqual(budget.used_today(), 300)

    def test_the_page_setting_chooses_the_file_or_memory(self):
        from ui import startup
        from utils.token_usage import DAILY_BUDGET

        self.addCleanup(DAILY_BUDGET.keep_on_disk, None)
        with mock.patch.dict("os.environ", {"SAGE_DAILY_TOKEN_FILE": str(self.path)}):
            startup.keep_daily_token_count_on_disk()
        self.assertEqual(DAILY_BUDGET._path, self.path)
        with mock.patch.dict("os.environ", {"SAGE_DAILY_TOKEN_FILE": "off"}):
            startup.keep_daily_token_count_on_disk()
        self.assertIsNone(DAILY_BUDGET._path)


if __name__ == "__main__":
    unittest.main()
