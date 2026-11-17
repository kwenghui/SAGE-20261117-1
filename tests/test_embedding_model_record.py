"""The semantic index records its embedding model, and search refuses another one (utils/kb.py, utils/hybrid_discovery.py)."""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools._stand_ins import install_stand_ins  # noqa: E402

install_stand_ins()  # streamlit/openai stand-ins when the real libraries are absent

from utils import credentials  # noqa: E402

if not credentials.has_openai_api_key():
    credentials.set_openai_api_key("sk-" + "test0" * 8)  # shape only; never sent anywhere

from utils import hybrid_discovery, kb, rag_engine  # noqa: E402


class TheIndexRemembersItsEmbeddingModel(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.con = kb.open_db(Path(self.folder.name) / "kb.sqlite3")
        self.addCleanup(self.con.close)

    def _index(self, model):
        index = kb.ChromaIndex.__new__(kb.ChromaIndex)  # no Chroma needed for the record
        index.embedding_model = model
        index.col = mock.Mock()
        index.col.count.return_value = 0
        return index

    def test_no_record_until_the_index_is_built(self):
        self.assertIsNone(kb.recorded_embedding_model(self.con))
        self.assertIsNone(hybrid_discovery._other_embedding_model(self.con, "openai"))
        plain = sqlite3.connect(":memory:")  # an older knowledge base: no kb_info table
        self.addCleanup(plain.close)
        self.assertIsNone(kb.recorded_embedding_model(plain))

    def test_building_records_the_model_and_refuses_to_mix_another(self):
        self._index(kb.EMBEDDING_MODEL).sync(self.con)
        self.assertEqual(kb.recorded_embedding_model(self.con), kb.EMBEDDING_MODEL)
        self._index(kb.EMBEDDING_MODEL).sync(self.con)  # the same model again is fine
        with self.assertRaises(ValueError):
            self._index("another-model").sync(self.con)

    def test_search_skips_an_index_built_with_another_model(self):
        self._index("another-model").sync(self.con)
        self.assertEqual(
            hybrid_discovery._other_embedding_model(self.con, "openai"), "another-model"
        )
        chroma = Path(self.folder.name) / "chroma"
        chroma.mkdir()
        (chroma / "chroma.sqlite3").write_bytes(b"SQLite format 3\x00")
        trace = {"chroma": {}}
        with (
            mock.patch.dict(
                "os.environ", {"SAGE_CHROMA_DIR": str(chroma), "SAGE_CHROMA_SEARCH": "1"}
            ),
            mock.patch.object(hybrid_discovery, "_chroma_index") as opened,
            self.assertLogs("sage.hybrid_discovery", level="WARNING"),
        ):
            hits = hybrid_discovery._semantic_hits(self.con, "q", trace, "openai", None, 0.0)
        self.assertEqual(hits, [])
        opened.assert_not_called()
        self.assertIn("another embedding model", trace["chroma"]["status"])

    def test_one_definition_of_the_embedding_model(self):
        from utils import core_config

        self.assertIs(core_config.EMBEDDING_MODEL, kb.EMBEDDING_MODEL)

    def test_the_notes_vector_cache_is_keyed_by_model(self):
        vector = mock.Mock(embedding=[0.5, 0.5])
        with (
            mock.patch.object(
                rag_engine.client.embeddings,
                "create",
                return_value=mock.Mock(data=[vector], usage=None),
            ) as create,
            mock.patch.dict(rag_engine._QUERY_VECTOR_CACHE, {}, clear=True),
        ):
            rag_engine.create_embeddings(["same text"])
            rag_engine.create_embeddings(["same text"])
            self.assertEqual(create.call_count, 1)
            with mock.patch.object(rag_engine, "EMBEDDING_MODEL", "another-model"):
                rag_engine.create_embeddings(["same text"])
            self.assertEqual(create.call_count, 2)


if __name__ == "__main__":
    unittest.main()
