"""What the page does with each kind of result from the analysis, stated outcome by outcome.

ui/answer_pipeline.process_question was covered only by a behaviour digest: a
hash that says "something changed" but not what the right behaviour is. These
tests run the same scripted scenarios (tools/characterise_process_question.py:
a fake analytical core, no network, no key) and assert the outcome that
matters for each: is the answer shown as verified, which fixed message
replaces it, and how it is logged.

The scenarios run once, in a separate Python process, because they replace
Streamlit with an in-memory stand-in.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT))

from utils.answer_verification import full_answer_is_refusal  # noqa: E402


def _run_scenarios() -> dict:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        out = Path(folder) / "scenarios.json"
        done = subprocess.run(  # noqa: S603 - our own script, fixed arguments
            [sys.executable, str(ROOT / "tools" / "characterise_process_question.py"),
             str(ROOT), str(out)],
            cwd=str(ROOT), capture_output=True, text=True, timeout=600, check=False,
        )
        if done.returncode != 0 or not out.exists():
            raise AssertionError("scenario run failed:\n" + done.stderr[-2000:])
        return json.loads(out.read_text(encoding="utf-8"))


class ProcessQuestionOutcomes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = _run_scenarios()

    def _last(self, name) -> dict:
        messages = self.results[name]["messages"] or [{}]
        return messages[-1]

    def _logged(self, name) -> list:
        """(kind, category) for each question-log call of the scenario."""
        found = []
        for call in self.results[name]["calls"]:
            if call[0] == "failed":
                found.append(("failed", call[2]))
            elif call[0] == "success":
                found.append(("success", call[2]))
        return found

    def _asked_the_core(self, name) -> bool:
        return any(call[0] == "ask_sage" for call in self.results[name]["calls"])

    # --- refused before any analysis ---------------------------------------------
    def test_an_empty_question_does_nothing(self):
        self.assertFalse(self.results["empty"]["ret"])
        self.assertFalse(self._asked_the_core("empty"))
        self.assertEqual(self._logged("empty"), [])

    def test_an_over_long_question_is_refused_without_calling_the_model(self):
        self.assertFalse(self.results["too_long"]["ret"])
        self.assertFalse(self._asked_the_core("too_long"))
        self.assertEqual(self._logged("too_long"), [("failed", "question_too_long")])

    # --- shown as verified ---------------------------------------------------------
    def test_an_evidenced_answer_is_shown_as_verified_and_logged_as_success(self):
        for name in ("verified", "manual", "selected_ok", "hint", "comparison_request"):
            last = self._last(name)
            self.assertTrue(last["verified"], name)
            self.assertEqual(last["delivery_status"], "verified", name)
            self.assertEqual(self._logged(name), [("success", "verified")], name)
            self.assertIn("6,036,900", last["content"], name)

    def test_a_verified_chart_is_delivered_with_its_answer(self):
        last = self._last("chart_delivered")
        self.assertTrue(last["verified"])
        self.assertTrue(last.get("chart_spec"))

    def test_a_requested_chart_that_fails_its_checks_is_reported_not_faked(self):
        last = self._last("chart_present")
        self.assertFalse(last.get("chart_spec"))
        self.assertEqual(last["delivery_status"], "requested_chart_unavailable")
        self.assertEqual(self._logged("chart_present"),
                         [("failed", "requested_chart_unavailable")])

    def test_a_catalogue_listing_needs_no_figures(self):
        self.assertEqual(self._last("catalogue")["delivery_status"], "verified")

    # --- replaced by a fixed message, never shown as verified ---------------------------
    def _assert_replaced(self, name, status, *, absent):
        last = self._last(name)
        self.assertFalse(last["verified"], name)
        self.assertEqual(last["delivery_status"], status, name)
        self.assertNotIn(absent, last["content"], name)
        self.assertEqual(self._logged(name), [("failed", status)], name)

    def test_an_answer_without_official_evidence_is_replaced(self):
        self._assert_replaced("no_evidence", "unverified_or_unavailable", absent="6 million")

    def test_an_answer_from_an_incomplete_table_is_replaced(self):
        self._assert_replaced("incomplete_table", "unverified_or_unavailable", absent="6,036,900")

    def test_an_answer_citing_an_unofficial_address_is_replaced(self):
        self._assert_replaced("unofficial_url", "unverified_or_unavailable", absent="6,036,900")

    def test_numbers_that_are_not_in_the_evidence_are_never_shown(self):
        self._assert_replaced("unsupported_numbers", "numeric_evidence_unmatched",
                              absent="9,999,999")
        self.assertNotIn("47.3", self._last("unsupported_numbers")["content"])

    def test_an_answer_about_a_different_population_is_replaced(self):
        self._assert_replaced("population_conflict", "no_supported_answer", absent="6,036,900")

    def test_a_table_other_than_the_one_the_user_chose_is_refused(self):
        self._assert_replaced("selected_mismatch", "unverified_or_unavailable", absent="6,036,900")
        self.assertIn("M999999", self._last("selected_mismatch")["content"])

    def test_an_empty_model_answer_is_not_a_verified_answer(self):
        self._assert_replaced("empty_answer", "no_supported_answer", absent="6,036,900")

    def test_a_model_refusal_is_not_a_verified_answer(self):
        # Used to be shown, logged and cached as "verified".
        self._assert_replaced("refusal", "no_supported_answer", absent="I can't answer")

    # --- refusals and failures ----------------------------------------------------------
    def test_scope_and_security_refusals_keep_their_own_category(self):
        self.assertEqual(self._logged("scope"), [("failed", "out_of_scope")])
        self.assertEqual(self._logged("security"), [("failed", "security_blocked")])
        for name in ("scope", "security"):
            self.assertFalse(self._last(name)["verified"], name)

    def test_a_budget_stop_explains_itself_and_is_logged_as_such(self):
        for name in ("budget", "budget_debug"):
            self.assertIn("safety limit", self._last(name)["content"], name)
            self.assertEqual(self._logged(name), [("failed", "question_token_budget")], name)

    def test_an_unexpected_error_shows_no_internal_detail(self):
        for name, hidden in (("crash", "kaboom"), ("sage_error", "boom")):
            last = self._last(name)
            self.assertFalse(last["verified"], name)
            self.assertNotIn(hidden, last["content"].lower(), name)
            self.assertEqual(self._logged(name), [("failed", "processing_exception")], name)


class PlainRefusals(unittest.TestCase):
    def test_a_bare_refusal_is_recognised(self):
        for text in ("I can't answer that question.", "I’m sorry, but I cannot help with that.",
                     "Sorry, I am unable to provide that.", "Unable to answer."):
            self.assertTrue(full_answer_is_refusal(text), text)

    def test_an_answer_that_explains_with_figures_is_not_a_refusal(self):
        for text in ("I cannot find 2024 in this table; the latest value is 5.5 in 2023.",
                     "The rate fell over the period.",
                     "I can confirm the share stayed broadly stable."):
            self.assertFalse(full_answer_is_refusal(text), text)


if __name__ == "__main__":
    unittest.main()
