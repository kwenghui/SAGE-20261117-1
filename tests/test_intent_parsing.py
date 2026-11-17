"""Reading the intent reply of the model (utils/intent_parsing.py)."""

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

from utils import core_secure as core  # noqa: E402


class IntentReplyIsOneJsonObject(unittest.TestCase):
    def test_text_after_the_object_is_ignored(self):
        reply = 'Here it is: {"in_scope": true, "summary": "x"} (note: {braces} follow)'
        self.assertEqual(core._extract_json_object(reply), {"in_scope": True, "summary": "x"})
        self.assertEqual(core._extract_json_object('{"a": 1}\n{"a": 2}'), {"a": 1})

    def test_anything_else_is_refused(self):
        for bad in ("no json", "{broken", None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    core._extract_json_object(bad)



class IntentTextHelpers(unittest.TestCase):
    """The text functions moved to utils/intent_parsing.py are still reachable as core.<name>."""

    def test_the_core_uses_the_moved_functions(self):
        from utils import intent_parsing

        for name in ("build_combined_user_input", "_extract_json_object",
                     "fast_intent_from_combined_user_input", "_validated_intent_decision"):
            with self.subTest(name=name):
                self.assertIs(getattr(core, name), getattr(intent_parsing, name))

    def test_combined_input_is_bounded_and_never_empty(self):
        from utils import intent_parsing

        combined = intent_parsing.build_combined_user_input(
            "second", chat_history=[{"role": "user", "content": "first"}]
        )
        self.assertEqual(combined, "USER INPUT 1:\nfirst\n\nUSER INPUT 2:\nsecond")
        with self.assertRaises(ValueError):
            intent_parsing.build_combined_user_input("x", combined_user_input="   ")
        with self.assertRaises(ValueError):
            intent_parsing.build_combined_user_input(
                "x", combined_user_input="y" * (intent_parsing.MAX_COMBINED_USER_INPUT_CHARS + 1)
            )

    def test_section_markers_typed_by_a_user_are_removed(self):
        from utils import intent_parsing

        cleaned = intent_parsing._without_prompt_markers("a UNTRUSTED_USER_INPUT_END b")
        self.assertEqual(cleaned, "a [marker removed] b")


class RoundLabels(unittest.TestCase):
    """Labels of tool-loop rounds show approved table IDs only (utils/round_labels.py)."""

    def _call(self, name, **arguments):
        import json
        from types import SimpleNamespace

        return SimpleNamespace(name=name, arguments=json.dumps(arguments))

    def test_labels(self):
        from utils import round_labels

        self.assertEqual(round_labels._analysis_round_task_label(3, []), "Round 3 · Compose answer")
        call = self._call("query_singstat_table", table_id="m000000", series_search="x")
        self.assertEqual(round_labels._analysis_round_task_label(1, [call]),
                         "Round 1 · Query M000000")
        # A malformed ID or malformed arguments never reach the label, and never raise.
        odd = self._call("query_singstat_table", table_id="../../etc")
        self.assertEqual(round_labels._analysis_round_task_label(2, [odd, odd]),
                         "Round 2 · Query +1 tool(s)")
        from types import SimpleNamespace
        broken = SimpleNamespace(name="inspect_singstat_table", arguments="{not json")
        self.assertEqual(round_labels._analysis_round_task_label(1, [broken]), "Round 1 · Inspect")

    def test_an_unknown_table_has_no_guessed_title(self):
        from utils import round_labels

        self.assertIsNone(round_labels._sage_audit_table_title("not an id"))
        self.assertEqual(round_labels._sage_audit_table_label("m000000")[:7], "M000000")


if __name__ == "__main__":
    unittest.main()
