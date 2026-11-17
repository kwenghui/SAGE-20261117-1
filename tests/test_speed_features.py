"""Speed features: the verified-answer cache and live progress lines."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools._stand_ins import install_stand_ins  # noqa: E402

install_stand_ins()
from ui import answer_cache  # noqa: E402
from utils.progress import progress_sink, report  # noqa: E402

RESPONSE = {"tool_results": [{"success": True, "table_id": "M000001"}]}
MESSAGES = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a",
                                                "delivery_status": "verified"}]


class AnswerCache(unittest.TestCase):

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        patches = [mock.patch.dict(os.environ, {"SAGE_ANSWER_CACHE_MINUTES": "60"}),
                   mock.patch.object(answer_cache, "_disk_path",
                                     return_value=Path(self.folder.name) / "cache.sqlite3"),
                   mock.patch("utils.official_source.data_last_updated",
                              return_value="30 Jun 2026")]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        answer_cache.clear()

    def test_same_wording_same_answer_other_settings_not(self):
        key = answer_cache.cache_key("How many teachers?", "gpt-x")
        self.assertEqual(key, answer_cache.cache_key("  how many   TEACHERS? ", "gpt-x"))
        self.assertNotEqual(key, answer_cache.cache_key("How many teachers?", "gpt-y"))
        self.assertNotEqual(key, answer_cache.cache_key("How many teachers?", "gpt-x", "M000002"))
        answer_cache.store(key, MESSAGES, RESPONSE, persist=False)
        self.assertEqual(answer_cache.lookup(key)["messages"], MESSAGES)

    def test_new_official_data_or_age_invalidates(self):
        key = answer_cache.cache_key("q", "m")
        answer_cache.store(key, MESSAGES, RESPONSE, persist=False)
        with mock.patch("utils.official_source.data_last_updated", return_value="31 Jul 2026"):
            self.assertIsNone(answer_cache.lookup(key))          # SingStat published again
        with mock.patch.object(answer_cache.time, "time", return_value=10 ** 12):
            self.assertIsNone(answer_cache.lookup(key))          # too old

    def test_sample_questions_survive_a_restart_typed_ones_do_not(self):
        sample, typed = answer_cache.cache_key("sample", "m"), answer_cache.cache_key("typed", "m")
        answer_cache.store(sample, MESSAGES, RESPONSE, persist=True)
        answer_cache.store(typed, MESSAGES, RESPONSE, persist=False)
        with answer_cache._LOCK:
            answer_cache._MEMORY.clear()                          # a server restart
        self.assertIsNotNone(answer_cache.lookup(sample))
        self.assertIsNone(answer_cache.lookup(typed))

    def test_an_answer_checked_by_older_code_is_never_reused(self):
        old_key = answer_cache.cache_key("sample", "m")
        answer_cache.store(old_key, MESSAGES, RESPONSE, persist=True)
        self.assertIsNotNone(answer_cache.lookup(old_key))
        # A Sage update followed by a restart: new code, empty memory, same disk file.
        with answer_cache._LOCK:
            answer_cache._MEMORY.clear()
            answer_cache._CHECKED_DISKS.clear()
        with mock.patch.object(answer_cache, "_CODE_VERSION", "another-version"):
            new_key = answer_cache.cache_key("sample", "m")
            self.assertNotEqual(new_key, old_key)
            self.assertIsNone(answer_cache.lookup(new_key))
            con = answer_cache._disk()                            # the old entry is gone from disk
            try:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM answers").fetchone()[0], 0)
            finally:
                con.close()
            answer_cache.store(new_key, MESSAGES, RESPONSE, persist=True)
            with answer_cache._LOCK:
                answer_cache._MEMORY.clear()
            self.assertIsNotNone(answer_cache.lookup(new_key))    # the new code's own answers stay
        with answer_cache._LOCK:
            answer_cache._CHECKED_DISKS.clear()

    def test_the_code_version_follows_the_program_files(self):
        first = answer_cache.code_version()
        self.assertEqual(first, answer_cache.code_version())
        self.assertRegex(first, r"^[0-9a-f]{32}$")
        with mock.patch.object(answer_cache, "_CODE_VERSION", None), \
                mock.patch.object(Path, "read_bytes", return_value=b"changed"):
            self.assertNotEqual(answer_cache.code_version(), first)

    def test_the_reasoning_setting_is_part_of_the_key(self):
        with mock.patch.dict(os.environ, {"SAGE_REASONING_EFFORT": ""}):
            default = answer_cache.cache_key("q", "m")
        with mock.patch.dict(os.environ, {"SAGE_REASONING_EFFORT": "low"}):
            self.assertNotEqual(default, answer_cache.cache_key("q", "m"))

    def test_expired_answers_are_removed_from_disk(self):
        with mock.patch.object(answer_cache.time, "time", return_value=1_000_000.0):
            answer_cache.store(answer_cache.cache_key("old", "m"), MESSAGES, RESPONSE,
                               persist=True)
        with mock.patch.object(answer_cache.time, "time", return_value=1_000_000.0 + 2 * 3600):
            answer_cache.store(answer_cache.cache_key("new", "m"), MESSAGES, RESPONSE,
                               persist=True)
        con = answer_cache._disk()
        try:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM answers").fetchone()[0], 1)
        finally:
            con.close()

    def test_switched_off(self):
        with mock.patch.dict(os.environ, {"SAGE_ANSWER_CACHE_MINUTES": "0"}):
            key = answer_cache.cache_key("q", "m")
            answer_cache.store(key, MESSAGES, RESPONSE, persist=True)
            self.assertIsNone(answer_cache.lookup(key))


class ProgressLines(unittest.TestCase):

    def test_reports_reach_the_page_and_never_break_an_answer(self):
        shown = []
        with progress_sink(shown.append):
            report("Reading   SingStat M000001")
        report("ignored without a page")
        self.assertEqual(shown, ["Reading SingStat M000001…"])

        def broken(_text):
            raise RuntimeError("display gone")
        with progress_sink(broken):
            report("still fine")                                  # no exception

    def test_every_line_ends_with_one_ellipsis(self):
        shown = []
        with progress_sink(shown.append):
            for text in ("Possible official tables: A; B", "Checking it…", "Reading table...",
                         "Done.", "Found:"):
                report(text)
        self.assertEqual(shown, ["Possible official tables: A; B…", "Checking it…",
                                 "Reading table…", "Done…", "Found…"])


if __name__ == "__main__":
    unittest.main()
