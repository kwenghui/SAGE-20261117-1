"""Which model answers: one default name, one model when locked, no model list on the page (utils/model_client.py)."""

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

from utils import model_client, settings  # noqa: E402


class OneDefaultModelName(unittest.TestCase):
    def test_the_default_is_defined_once(self):
        self.assertEqual(model_client.MODEL_OPTIONS[0], settings.DEFAULT_CHAT_MODEL)
        typed = []
        for name in [ROOT / "app.py", *(ROOT / "ui").glob("*.py"), *(ROOT / "utils").glob("*.py")]:
            if name.name != "settings.py" and (
                '"' + settings.DEFAULT_CHAT_MODEL + '"' in name.read_text(encoding="utf-8")
                or "'" + settings.DEFAULT_CHAT_MODEL + "'" in name.read_text(encoding="utf-8")
            ):
                typed.append(name.name)
        self.assertEqual(typed, [])


class OneModelWhenLocked(unittest.TestCase):
    def test_only_the_configured_model_may_answer(self):
        locked = model_client.allowed_models(
            {"SAGE_LOCK_MODEL": "1", "SAGE_CHAT_MODEL": "gpt-next-2026-11-01"}
        )
        self.assertEqual(locked, ("gpt-next-2026-11-01",))
        default = model_client.allowed_models({"SAGE_LOCK_MODEL": "1"})
        self.assertEqual(default, (model_client.DEFAULT_CHAT_MODEL,))
        bad = model_client.allowed_models({"SAGE_LOCK_MODEL": "1", "SAGE_CHAT_MODEL": "x y; rm"})
        self.assertEqual(bad, (model_client.DEFAULT_CHAT_MODEL,))

    def test_another_model_is_refused_for_a_question(self):
        other = model_client.MODEL_OPTIONS[1]
        with mock.patch.dict("os.environ", {"SAGE_LOCK_MODEL": "1", "SAGE_CHAT_MODEL": ""}):
            with self.assertRaises(ValueError):
                with model_client.selected_model_scope(other):
                    pass
            with model_client.selected_model_scope(model_client.DEFAULT_CHAT_MODEL):
                pass

    def test_unlocked_is_unchanged(self):
        self.assertEqual(model_client.allowed_models({}), model_client.MODEL_OPTIONS)
        self.assertEqual(
            model_client.allowed_models({"SAGE_LOCK_MODEL": "0"}), model_client.MODEL_OPTIONS
        )


class NoModelListInThePage(unittest.TestCase):
    def test_the_sidebar_has_no_model_selector(self):
        source = (ROOT / "ui" / "sidebar.py").read_text(encoding="utf-8")
        self.assertNotIn("Model for new questions", source)
        self.assertNotIn("model_selection_enabled", source)
        self.assertNotIn('"Model: ', source)  # the model name is not shown in the panel either
        self.assertNotIn("set by SAGE_CHAT_MODEL", source)
        # No widget is bound to the model: only the operator's setting decides it.
        self.assertNotIn('key="sage_selected_model"', source)
        self.assertIn('st.session_state["sage_selected_model"] = (', source)



class ModelPinAdvice(unittest.TestCase):
    """Sage reports what still makes answers unrepeatable; it never chooses a model."""

    def test_pinned_and_locked_needs_no_advice(self):
        env = {"SAGE_CHAT_MODEL": "gpt-5.1-2026-08-07", "SAGE_LOCK_MODEL": "1"}
        self.assertIsNone(model_client.model_pin_advice(env))

    def test_each_missing_part_is_named(self):
        self.assertIn("SAGE_CHAT_MODEL is not set", model_client.model_pin_advice({}))
        undated = model_client.model_pin_advice({"SAGE_CHAT_MODEL": "gpt-5.1",
                                                 "SAGE_LOCK_MODEL": "1"})
        self.assertIn("not a dated snapshot", undated)
        self.assertNotIn("SAGE_LOCK_MODEL", undated)
        unlocked = model_client.model_pin_advice({"SAGE_CHAT_MODEL": "gpt-5.1-2026-08-07"})
        self.assertIn("SAGE_LOCK_MODEL is not 1", unlocked)
        self.assertIn("ignored", model_client.model_pin_advice({"SAGE_CHAT_MODEL": "bad name!"}))


if __name__ == "__main__":
    unittest.main()
