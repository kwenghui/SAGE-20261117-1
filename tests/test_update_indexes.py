"""The one-run index update: every pending table once, Chroma in rounds, failures reported
(tools/update_indexes.py). No network, key or Chroma library is used."""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import update_indexes  # noqa: E402
from utils import kb  # noqa: E402


class _FakeChroma:
    """Stands in for kb.ChromaIndex: each sync() returns the next prepared result."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def sync(self, con, limit):
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class IndexUpdaterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "knowledge_base.sqlite3"
        con = kb.open_db(self.db)
        for table_id in ("M000001", "M000002", "M000003"):
            kb.upsert_table(con, table_id, "A test table " + table_id)
        con.commit()
        con.close()
        self.lines = []

    def tearDown(self):
        self.tmp.cleanup()

    def updater(self, chroma=None, **options):
        return update_indexes.IndexUpdater(
            self.db, Path(self.tmp.name) / "chroma", delay=0, say=self.lines.append,
            open_chroma=(lambda: chroma) if chroma is not None else None, **options)

    def _mark_complete(self, con, table_id, **_options):
        with con:
            con.execute("UPDATE kb_tables SET series_status='complete' WHERE table_id=?",
                        (table_id,))
        return {"table_id": table_id}

    def test_every_pending_table_is_read_once_and_a_failure_does_not_stop_the_run(self):
        def inspect(con, table_id, **options):
            if table_id == "M000001":  # the FIRST table fails: the others must still be read
                raise RuntimeError("Official API failed")
            return self._mark_complete(con, table_id)

        with mock.patch.object(kb, "inspect_table", inspect):
            report = self.updater().run(chroma_step=False)
        self.assertEqual(report.tables_done, ["M000002", "M000003"])
        self.assertEqual([table for table, _ in report.tables_failed], ["M000001"])
        self.assertFalse(report.clean)
        self.assertEqual(report.after["series_complete_tables"], 2)

    def test_named_tables_are_read_even_when_complete_and_bad_ids_are_refused(self):
        seen = []
        with mock.patch.object(kb, "inspect_table",
                               lambda con, table_id, **o: seen.append(table_id)
                               or self._mark_complete(con, table_id)):
            self.updater().run(chroma_step=False)
            seen.clear()
            self.updater().run(chroma_step=False, tables=["m000002"])
        self.assertEqual(seen, ["M000002"])
        with self.assertRaises(update_indexes.IndexUpdateError):
            self.updater().run(chroma_step=False, tables=["../etc/passwd"])

    def test_chroma_is_synced_in_rounds_until_nothing_is_left(self):
        chroma = _FakeChroma([
            {"indexed": 500, "pending_in_batch": 500, "chroma_count": 500},
            {"indexed": 120, "pending_in_batch": 120, "chroma_count": 620},
            {"indexed": 0, "pending_in_batch": 0, "chroma_count": 620},
        ])
        report = self.updater(chroma).run(sqlite_step=False)
        self.assertEqual((chroma.calls, report.chroma_indexed, report.chroma_left), (3, 620, 0))
        self.assertTrue(report.clean)

    def test_a_chroma_failure_is_reported_and_keeps_what_was_done(self):
        chroma = _FakeChroma([
            {"indexed": 500, "pending_in_batch": 500, "chroma_count": 500},
            ValueError("This index was built with another model"),
        ])
        report = self.updater(chroma).run(sqlite_step=False)
        self.assertEqual(report.chroma_indexed, 500)
        self.assertIn("another model", report.chroma_error)
        self.assertFalse(report.clean)

    def test_no_progress_ends_the_chroma_step_instead_of_looping_for_ever(self):
        stuck = {"indexed": 0, "pending_in_batch": 7, "chroma_count": 10}
        chroma = _FakeChroma([stuck] * 10)
        report = self.updater(chroma).run(sqlite_step=False)
        self.assertEqual(chroma.calls, update_indexes.MAX_IDLE_ROUNDS)
        self.assertIn("no progress", report.chroma_error)

    def test_an_index_that_cannot_be_opened_does_not_undo_the_table_step(self):
        def no_key():
            raise RuntimeError("OPENAI_API_KEY required")

        updater = update_indexes.IndexUpdater(self.db, self.tmp.name, delay=0,
                                              say=self.lines.append, open_chroma=no_key)
        with mock.patch.object(kb, "inspect_table", self._mark_complete):
            report = updater.run()
        self.assertEqual(len(report.tables_done), 3)
        self.assertIn("OPENAI_API_KEY", report.chroma_error)

    def test_a_dry_run_changes_nothing(self):
        before = self.db.read_bytes()
        called = []
        with mock.patch.object(kb, "inspect_table", lambda *a, **k: called.append(a)):
            report = self.updater(_FakeChroma([]), dry_run=True).run(
                backup=True, keywords=["cpi"], optimize=True)
        self.assertEqual(called, [])
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(list(Path(self.tmp.name).glob("*.backup-*")), [])
        self.assertTrue(report.dry_run)
        missing = update_indexes.IndexUpdater(Path(self.tmp.name) / "none.sqlite3",
                                              self.tmp.name, dry_run=True, say=self.lines.append)
        with self.assertRaises(update_indexes.IndexUpdateError):
            missing.run()
        self.assertFalse((Path(self.tmp.name) / "none.sqlite3").exists())

    def test_backup_is_a_readable_copy(self):
        report = self.updater().run(backup=True, sqlite_step=False, chroma_step=False)
        with sqlite3.connect(report.backup) as copy:
            self.assertEqual(copy.execute("SELECT COUNT(*) FROM kb_tables").fetchone()[0], 3)

    def test_titles_option_rebuilds_the_title_index_from_the_updated_knowledge_base(self):
        from utils.discovery_controller import safe_catalogue_rows

        with mock.patch.object(kb, "inspect_table", self._mark_complete):
            report = self.updater().run(chroma_step=False, titles=True)
        self.assertEqual(report.titles_written, 3)
        self.assertEqual(safe_catalogue_rows(self.db.with_name("discovery_index.sqlite3"))[1], 3)
        self.assertTrue(report.clean)
        dry = self.updater(dry_run=True).run(chroma_step=False, titles=True)
        self.assertEqual(dry.titles_written, 3)

    def test_the_summary_names_failures(self):
        with mock.patch.object(kb, "inspect_table",
                               mock.Mock(side_effect=RuntimeError("down"))):
            report = self.updater().run(chroma_step=False)
        text = update_indexes.summary(report)
        self.assertIn("3 table(s) FAILED", text)
        self.assertIn("M000001: RuntimeError: down", text)


class CommandLine(unittest.TestCase):
    def test_conflicting_options_are_refused(self):
        with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
            update_indexes.parse_arguments(["--sqlite-only", "--chroma-only"])
        with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
            update_indexes.parse_arguments(["--chroma-only", "--discover", "cpi"])

    def test_relative_paths_are_inside_the_sage_folder(self):
        self.assertEqual(update_indexes._in_sage_folder("data/x.sqlite3"),
                         update_indexes.ROOT / "data" / "x.sqlite3")


if __name__ == "__main__":
    unittest.main()
