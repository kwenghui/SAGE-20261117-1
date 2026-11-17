"""The search evaluation reports low-overlap questions separately (tools/evaluate_discovery.py)."""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools._stand_ins import install_stand_ins  # noqa: E402

install_stand_ins()  # streamlit/openai stand-ins when the real libraries are absent

from utils import credentials  # noqa: E402

if not credentials.has_openai_api_key():
    credentials.set_openai_api_key("sk-" + "test0" * 8)  # shape only; never sent anywhere

from tools import evaluate_discovery  # noqa: E402


class SearchEvaluationIsHonest(unittest.TestCase):
    def test_title_overlap(self):
        title = "Resident Households by Type of Dwelling"
        self.assertEqual(
            evaluate_discovery.title_overlap(
                "How many resident households are there by type of dwelling?", title
            ),
            1.0,
        )
        self.assertLess(evaluate_discovery.title_overlap("Where do families live?", title), 0.5)
        self.assertIsNone(evaluate_discovery.title_overlap("anything", ""))

    def test_your_own_questions_can_be_measured(self):
        with tempfile.TemporaryDirectory() as folder:
            cases = Path(folder) / "held_out.csv"
            cases.write_text(
                "question,table_id,table_title\n"
                'Where do families live?,M000001,Resident Households by Type of Dwelling\n'
                'Resident households by type of dwelling?,M000001,'
                "Resident Households by Type of Dwelling\n"
                ",M000002,\n",
                encoding="utf-8",
            )
            loaded = evaluate_discovery.load_cases(10, 7, cases)
            self.assertEqual(len(loaded), 2)
            low = evaluate_discovery.low_overlap_positions(loaded, cases)
            self.assertEqual([loaded[i][0] for i in low], ["Where do families live?"])

    def test_the_bundled_audit_still_loads(self):
        self.assertEqual(len(evaluate_discovery.load_cases(5, 7)), 5)


if __name__ == "__main__":
    unittest.main()
