"""The question box callbacks: Clear, Submit and choosing a table (ui/question_form.py)."""

import sys
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


class _State(dict):
    """A stand-in for st.session_state: items are also attributes."""

    __getattr__ = dict.get
    __setattr__ = dict.__setitem__


class QuestionFormCallbacks(unittest.TestCase):
    def setUp(self):
        from ui import question_form

        self.form = question_form
        self.state = _State()
        patch = mock.patch.object(question_form.st, "session_state", self.state)
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_editor_grows_with_the_question_within_limits(self):
        height = self.form.question_editor_height
        self.assertEqual(height(""), 118)
        self.assertEqual(height(None), 118)
        self.assertGreater(height("word " * 80), height("word " * 20))
        self.assertEqual(height("line\n" * 500), 520)

    def test_clear_keeps_the_sign_in_and_drops_the_answers(self):
        self.state.update(
            {
                "sage_authenticated": True,
                "sage_auth_at": 1.0,
                "sage_auth_seen": 2.0,
                "_sage_security_session_id": "s" * 40,
                "_sage_auth_role": "user",
                "sage_theme_choice": "dark",
                "core": "the loaded core",
                "messages": [{"role": "assistant", "content": "an answer"}],
                "discovery_confirmed_table": {"table_id": "M000000"},
                "question_editor": "old",
            }
        )
        self.form.clear_question_editor()
        self.assertEqual(
            self.state,
            {
                "sage_authenticated": True,
                "sage_auth_at": 1.0,
                "sage_auth_seen": 2.0,
                "_sage_security_session_id": "s" * 40,
                "_sage_auth_role": "user",
                "sage_theme_choice": "dark",
                "core": "the loaded core",
            },
        )

    def test_clear_never_signs_anyone_in(self):
        self.state.update({"sage_authenticated": False, "messages": []})
        self.form.clear_question_editor()
        self.assertNotIn("sage_authenticated", self.state)

    def test_edit_restores_the_submitted_question(self):
        self.state.update(
            {"sage_submitted_question": "the submitted one", "question_editor": "something else"}
        )
        self.form.enable_question_editing()
        self.assertEqual(self.state["question_editor"], "the submitted one")
        self.assertIs(self.state["sage_question_editing"], True)

    def test_a_table_that_was_not_offered_cannot_be_chosen(self):
        plan = {"action": "review", "choices": [{"table_id": "M000001"}]}
        self.state.update(
            {
                "discovery_pending": {"plan": plan, "question": "q"},
                "sage_discovery_table": "M999999",
            }
        )
        with mock.patch.object(self.form, "validate_selection", return_value=False) as check:
            self.form.continue_discovered_analysis()
        check.assert_called_once_with(plan, "M999999")
        self.assertNotIn("queued_question", self.state)
        self.assertNotIn("discovery_confirmed_table", self.state)
        self.assertIn("no longer available", self.state["question_feedback"])

    def test_a_valid_choice_continues_the_original_question_without_history(self):
        self.state.update(
            {
                "discovery_pending": {"plan": {}, "question": "the original"},
                "sage_discovery_table": "M000001",
            }
        )
        with (
            mock.patch.object(self.form, "validate_selection", return_value=True),
            mock.patch.object(self.form, "clear_previous_results"),
        ):
            self.form.continue_discovered_analysis()
        self.assertEqual(self.state["queued_question"], ("the original", True))
        self.assertEqual(
            self.state["discovery_confirmed_table"],
            {"question": "the original", "table_id": "M000001"},
        )
        self.assertEqual(self.state["submit_history"], [])

    def test_a_broader_search_needs_a_pending_review(self):
        self.form.continue_discovery_broader_search()
        self.assertNotIn("queued_question", self.state)
        self.state.update(
            {
                "discovery_pending": {"plan": {"action": "review"}, "question": "the original"},
                "discovery_source_hint": {"table_id": "M000001"},
            }
        )
        with mock.patch.object(self.form, "clear_previous_results"):
            self.form.continue_discovery_broader_search()
        self.assertEqual(self.state["queued_question"], ("the original", True))
        self.assertIsNone(self.state["discovery_source_hint"])
        self.assertIsNone(self.state["discovery_pending"])


if __name__ == "__main__":
    unittest.main()
