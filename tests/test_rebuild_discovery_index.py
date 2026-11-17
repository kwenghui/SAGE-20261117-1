"""The title index can be rebuilt from the knowledge base and is read back by Sage's own
readers (tools/rebuild_discovery_index.py)."""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import rebuild_discovery_index as rebuild  # noqa: E402
from utils import catalogue_listing, kb  # noqa: E402
from utils.discovery_controller import safe_catalogue_rows  # noqa: E402


class RebuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.db = self.data / "knowledge_base.sqlite3"
        self.out = self.data / "discovery_index.sqlite3"
        con = kb.open_db(self.db)
        kb.upsert_table(con, "M000001", "CPF Members   And Balances, Annual")
        kb.upsert_table(con, "M000002", "Resident Population, Annual")
        kb.upsert_table(con, "D000003", "X" * 400)          # title too long for the reader
        with con:
            # Not a Table Builder page ID, and a stored link that is not the official page.
            con.execute("INSERT INTO kb_tables(table_id,title,source_url) VALUES(?,?,?)",
                        ("17581", "A Numeric Table", "https://example.com/17581"))
            con.execute("UPDATE kb_tables SET source_url='https://example.com/x', "
                        "theme='Society', frequency='Annual' WHERE table_id='M000002'")
        con.close()
        self.lines = []

    def tearDown(self):
        self.tmp.cleanup()

    def builder(self, **options):
        return rebuild.DiscoveryIndexBuilder(self.db, self.out, say=self.lines.append, **options)

    def test_the_new_index_is_read_by_both_of_sages_readers(self):
        report = self.builder().run()
        self.assertEqual((report.tables_in_knowledge_base, report.written), (4, 2))
        self.assertEqual(report.readable_by_sage, 2)
        self.assertTrue(report.verified)
        rows, count = safe_catalogue_rows(self.out)             # the choose-a-table reader
        self.assertEqual([row["table_id"] for row in rows], ["M000001", "M000002"])
        listing = catalogue_listing.list_local_singstat_tables(  # the "which tables" reader
            "which tables contain CPF data", index_path=self.out)
        self.assertIn("M000001", listing["answer"])
        self.assertIn("among **2 tables", listing["answer"])

    def test_links_are_rebuilt_from_the_id_never_copied(self):
        self.builder().run()
        with sqlite3.connect(self.out) as con:
            links = dict(con.execute("SELECT table_id, source_url FROM docs"))
            title = con.execute("SELECT title FROM docs WHERE table_id='M000001'").fetchone()[0]
        self.assertEqual(links["M000002"], "https://tablebuilder.singstat.gov.sg/table/TS/M000002")
        self.assertNotIn("example.com", " ".join(links.values()))
        self.assertEqual(title, "CPF Members And Balances, Annual")  # spacing tidied only

    def test_what_is_left_out_is_reported(self):
        report = self.builder().run()
        self.assertEqual(report.skipped_id, ["17581"])
        self.assertEqual(report.skipped_title, ["D000003"])
        text = rebuild.summary(report)
        self.assertIn("17581", text)
        self.assertIn("D000003", text)

    def test_an_existing_index_is_not_replaced_without_force_and_is_kept_with_it(self):
        self.out.write_bytes(b"the old index")
        with self.assertRaises(rebuild.RebuildError):
            self.builder().run()
        self.assertEqual(self.out.read_bytes(), b"the old index")
        report = self.builder(force=True).run()
        self.assertEqual(Path(report.kept_old_file).read_bytes(), b"the old index")
        self.assertEqual(safe_catalogue_rows(self.out)[1], 2)

    def test_a_dry_run_writes_nothing(self):
        report = self.builder(dry_run=True).run()
        self.assertEqual(report.written, 2)
        self.assertFalse(self.out.exists())
        self.assertEqual(list(self.data.glob("*.part")), [])
        self.assertIn("would be written", rebuild.summary(report))

    def test_the_knowledge_base_is_not_changed(self):
        before = self.db.read_bytes()
        self.builder().run()
        self.assertEqual(self.db.read_bytes(), before)

    def test_problems_are_reported_as_errors_not_tracebacks(self):
        missing = rebuild.DiscoveryIndexBuilder(self.data / "none.sqlite3", self.out,
                                                say=self.lines.append)
        with self.assertRaises(rebuild.RebuildError):
            missing.run()
        (self.data / "broken.sqlite3").write_bytes(b"not a database")
        broken = rebuild.DiscoveryIndexBuilder(self.data / "broken.sqlite3", self.out,
                                               say=self.lines.append)
        with self.assertRaises(rebuild.RebuildError):
            broken.run()
        empty = self.data / "empty.sqlite3"
        kb.open_db(empty).close()
        nothing = rebuild.DiscoveryIndexBuilder(empty, self.out, say=self.lines.append)
        with self.assertRaises(rebuild.RebuildError):
            nothing.run()
        self.assertFalse(self.out.exists())
        self.assertEqual(rebuild.main(["--db", str(self.data / "none.sqlite3"),
                                       "--out", str(self.out)]), 2)


if __name__ == "__main__":
    unittest.main()
