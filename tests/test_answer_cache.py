"""The verified-answer cache: private copies, and noticing new official data (ui/answer_cache.py)."""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ui import answer_cache  # noqa: E402
from utils import credentials  # noqa: E402
from tools._stand_ins import install_stand_ins  # noqa: E402

install_stand_ins()  # streamlit/openai stand-ins when the real libraries are absent


if not credentials.has_openai_api_key():
    credentials.set_openai_api_key("sk-" + "test0" * 8)  # shape only; never sent anywhere


class CachedAnswersAreNotShared(unittest.TestCase):
    def test_each_lookup_gets_its_own_copy(self):
        key = answer_cache.cache_key("copy test", "m")
        with (
            mock.patch.dict(os.environ, {"SAGE_ANSWER_CACHE_MINUTES": "60"}),
            mock.patch.object(answer_cache, "_table_dates", return_value={}),
        ):
            answer_cache.store(key, [{"role": "assistant", "content": "x"}], {}, persist=False)
            first, second = answer_cache.lookup(key), answer_cache.lookup(key)
        first["messages"][-1]["served_from_cache"] = 1.0
        self.assertNotIn("served_from_cache", second["messages"][-1])

    def test_a_discovery_hint_is_part_of_the_key(self):
        plain = answer_cache.cache_key("q", "m")
        self.assertEqual(plain, answer_cache.cache_key("q", "m", None, None, None))
        self.assertNotEqual(plain, answer_cache.cache_key("q", "m", None, None, "M000001"))

    def test_numpy_numbers_stay_numbers(self):
        import numpy as np

        self.assertEqual(answer_cache._plain(np.int64(7)), 7)
        self.assertIsInstance(answer_cache._plain(np.int64(7)), int)


class CachedAnswersNoticeNewData(unittest.TestCase):
    def _lookup_with_copy_aged(self, age, **settings):
        from ui import answer_cache
        from utils import official_source as source

        local = mock.Mock()
        local.table_cache_age_seconds.return_value = age
        patches = [
            mock.patch.object(source, "_LOCAL_STORE", local),
            mock.patch.object(source, "SAGE_OFFLINE_ONLY", settings.get("offline", False)),
            mock.patch.object(source, "SAGE_STORE_ENABLED", True),
            mock.patch.object(source, "_schedule_background_refresh"),
        ]
        started = [patch.start() for patch in patches]
        for patch in patches:
            self.addCleanup(patch.stop)
        answer_cache._refresh_old_copies({"M000001": "30 Jun 2026"})
        return started[-1]

    def test_an_old_copy_is_refreshed_in_the_background(self):
        from utils import official_source as source

        refresh = self._lookup_with_copy_aged(source.CACHE_DURATION_SECONDS + 1)
        refresh.assert_called_once_with("M000001")

    def test_a_fresh_or_missing_copy_is_left_alone(self):
        self._lookup_with_copy_aged(10).assert_not_called()
        self._lookup_with_copy_aged(None).assert_not_called()

    def test_offline_mode_never_goes_online(self):
        from utils import official_source as source

        refresh = self._lookup_with_copy_aged(source.CACHE_DURATION_SECONDS * 5, offline=True)
        refresh.assert_not_called()

    def test_a_store_problem_never_breaks_the_lookup(self):
        import sqlite3

        from ui import answer_cache
        from utils import official_source as source

        local = mock.Mock()
        local.table_cache_age_seconds.side_effect = sqlite3.OperationalError("locked")
        with (
            mock.patch.object(source, "_LOCAL_STORE", local),
            mock.patch.object(source, "SAGE_STORE_ENABLED", True),
            mock.patch.object(source, "SAGE_OFFLINE_ONLY", False),
        ):
            answer_cache._refresh_old_copies({"M000001": None})  # no exception


if __name__ == "__main__":
    unittest.main()
