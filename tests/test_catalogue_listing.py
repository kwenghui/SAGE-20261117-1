"""'Which tables cover X?' is answered from whichever local catalogue file exists
(utils/catalogue_listing.py)."""

import logging
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from utils import catalogue_listing, kb  # noqa: E402

QUESTION = "which tables contains CPF data"
LINK = "https://tablebuilder.singstat.gov.sg/table/TS/"


class CatalogueFallback(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        self.data.mkdir()
        self.patch = mock.patch.object(catalogue_listing, "APP_DIR", Path(self.tmp.name))
        self.patch.start()
        logging.disable(logging.CRITICAL)

    def tearDown(self):
        logging.disable(logging.NOTSET)
        self.patch.stop()
        self.tmp.cleanup()

    def knowledge_base(self):
        con = kb.open_db(self.data / "knowledge_base.sqlite3")
        kb.upsert_table(con, "M000001", "CPF Members And Balances, Annual")
        kb.upsert_table(con, "M000002", "Resident Population, Annual")
        with con:  # a row that must never be shown: the link is not the official page
            con.execute("INSERT INTO kb_tables(table_id,title,source_url) VALUES(?,?,?)",
                        ("M000003", "CPF Withdrawals", "https://example.com/M000003"))
        con.close()

    def title_index(self, title="CPF Contribution Rates, Annual"):
        con = sqlite3.connect(self.data / "discovery_index.sqlite3")
        con.execute("CREATE TABLE docs(table_id TEXT, title TEXT, source_url TEXT, doc_type TEXT)")
        con.execute("INSERT INTO docs VALUES(?,?,?,?)", ("M000009", title, LINK + "M000009", "t"))
        con.commit()
        con.close()

    def test_no_file_at_all_says_the_index_is_unavailable(self):
        result = catalogue_listing.list_local_singstat_tables(QUESTION)
        self.assertEqual(result["count"], 0)
        self.assertIn("unavailable", result["answer"])

    def test_the_knowledge_base_is_used_when_the_title_index_is_missing(self):
        self.knowledge_base()
        result = catalogue_listing.list_local_singstat_tables(QUESTION)
        self.assertIn("M000001", result["answer"])
        self.assertNotIn("M000002", result["answer"])   # not about the subject
        self.assertNotIn("M000003", result["answer"])   # not an official link
        self.assertNotIn("unavailable", result["answer"])

    def test_the_title_index_comes_first_when_both_exist(self):
        self.knowledge_base()
        self.title_index()
        result = catalogue_listing.list_local_singstat_tables(QUESTION)
        self.assertIn("M000009", result["answer"])
        self.assertNotIn("M000001", result["answer"])

    def test_a_damaged_title_index_falls_back_to_the_knowledge_base(self):
        self.knowledge_base()
        (self.data / "discovery_index.sqlite3").write_bytes(b"this is not a database")
        result = catalogue_listing.list_local_singstat_tables(QUESTION)
        self.assertIn("M000001", result["answer"])

    def test_only_damaged_files_says_it_could_not_be_read(self):
        (self.data / "discovery_index.sqlite3").write_bytes(b"this is not a database")
        result = catalogue_listing.list_local_singstat_tables(QUESTION)
        self.assertIn("could not be read", result["answer"])
        with self.assertRaises(catalogue_listing.CatalogueUnreadable):
            catalogue_listing.CatalogueSource.for_request().read()

    def test_a_named_file_is_the_only_one_tried(self):
        self.knowledge_base()
        result = catalogue_listing.list_local_singstat_tables(
            QUESTION, index_path=self.data / "missing.sqlite3")
        self.assertIn("unavailable", result["answer"])

    def test_ways_of_asking_are_not_subject_words(self):
        for question in ("which tables contains CPF data", "list tables that include cpf",
                         "what datasets have information regarding CPF",
                         "show tables about the Central Provident Fund"):
            with self.subTest(question=question):
                patterns = catalogue_listing._catalogue_subject_patterns(question)
                self.assertEqual(set(patterns), {("central provident fund",), ("cpf",)})
        # A subject Sage has no vocabulary entry for: its own words, without the asking words.
        self.assertEqual(catalogue_listing._catalogue_subject_patterns(
            "which tables contain zebra crossings"), (("crossings", "zebra"),))
        # A request with no subject lists nothing (never the whole catalogue).
        self.assertEqual(catalogue_listing._catalogue_subject_patterns(
            "which tables are there"), ())

    def test_the_abbreviation_and_the_full_name_find_each_other(self):
        self.knowledge_base()
        con = kb.open_db(self.data / "knowledge_base.sqlite3")
        kb.upsert_table(con, "M000004", "Central Provident Fund Contributions, Annual")
        con.commit()
        con.close()
        answer = catalogue_listing.list_local_singstat_tables(QUESTION)["answer"]
        self.assertIn("M000001", answer)
        self.assertIn("M000004", answer)
        self.assertIn("Found **2 matching tables**", answer)

    def test_nothing_is_written_to_the_files(self):
        self.knowledge_base()
        before = (self.data / "knowledge_base.sqlite3").read_bytes()
        catalogue_listing.list_local_singstat_tables(QUESTION)
        self.assertEqual((self.data / "knowledge_base.sqlite3").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
