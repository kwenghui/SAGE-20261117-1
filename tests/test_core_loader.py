"""Loading the analytical core and building its history (ui/core_loader.py)."""

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

from ui import core_loader  # noqa: E402


class CoreLoader(unittest.TestCase):
    def test_no_key_no_core(self):
        self.assertIsNone(core_loader.load_core(""))
        self.assertIsNone(core_loader.load_core(None))

    def test_history_keeps_only_role_and_content_of_recent_messages(self):
        import streamlit as st

        messages = [
            {"role": "user", "content": f"q{n}", "chart": object(), "verified": True}
            for n in range(core_loader.MAX_CORE_HISTORY_MESSAGES + 5)
        ]
        with mock.patch.object(st, "session_state", mock.Mock(messages=messages)):
            history = core_loader.build_core_history()
        self.assertEqual(len(history), core_loader.MAX_CORE_HISTORY_MESSAGES)
        self.assertEqual(set(history[-1]), {"role", "content"})
        self.assertEqual(history[-1]["content"], messages[-1]["content"])


if __name__ == "__main__":
    unittest.main()
