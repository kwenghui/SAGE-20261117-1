"""The 'how this answer was produced' rows (utils/pipeline_audit.py)."""

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

from utils import pipeline_audit  # noqa: E402

SOURCE = "https://tablebuilder.singstat.gov.sg/table/TS/M000000"


def _table(**changes):
    result = {
        "success": True,
        "table_id": "M000000",
        "table_complete": True,
        "source_url": SOURCE,
        "title": "A Table",
    }
    result.update(changes)
    return {"tool_results": [result]}


class HowThisAnswerWasProduced(unittest.TestCase):
    COLUMNS = {"Stage", "Status", "Database / service", "Decision / evidence"}

    def test_nothing_recorded_is_reported_as_not_recorded_never_guessed(self):
        self.assertEqual(pipeline_audit.stage_rows(None), [])
        rows = pipeline_audit.stage_rows({})
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(set(row), self.COLUMNS)
        first = rows[0]
        self.assertTrue(first["Stage"].startswith("1"))
        self.assertEqual(first["Status"], "Not recorded")

    def test_the_scope_decision_is_reported_as_recorded(self):
        def first_status(scope):
            intent = {"in_scope": scope, "summary": "resident population", "reason": "statistics"}
            return pipeline_audit.stage_rows({"intent_decision": intent})[0]["Status"]

        self.assertEqual(first_status(True), "In scope")
        self.assertEqual(first_status(False), "Out of scope")
        self.assertEqual(first_status(None), "Recorded")

    def test_evidence_text_is_capped(self):
        row = pipeline_audit._row("1", "ok", "x", "y" * 5000)
        self.assertEqual(len(row["Decision / evidence"]), 650)

    def test_stages_come_in_pipeline_order(self):
        response = {
            "intent_decision": {"in_scope": True, "summary": "s", "reason": "r"},
            "rag_results": [],
            **_table(),
        }
        numbers = [
            int(row["Stage"][0])
            for row in pipeline_audit.stage_rows(response)
            if row["Stage"][:1].isdigit()
        ]
        self.assertEqual(numbers, sorted(numbers))


if __name__ == "__main__":
    unittest.main()
