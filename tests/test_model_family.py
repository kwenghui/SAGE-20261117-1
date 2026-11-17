"""Reasoning models are recognised by naming pattern, in one place (utils/model_family.py)."""

import logging
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from utils import token_usage  # noqa: E402
from utils.model_family import ModelFamily  # noqa: E402


class ReasoningModels(unittest.TestCase):
    def test_gpt_generation_five_and_later(self):
        for name in ("gpt-5", "gpt-5.1", "gpt-5-mini", "gpt-6", "gpt-6.2-preview", "gpt-10"):
            with self.subTest(name=name):
                self.assertTrue(ModelFamily.is_reasoning(name))

    def test_o_series(self):
        for name in ("o1", "o3-mini", "o4", "o5-preview"):
            with self.subTest(name=name):
                self.assertTrue(ModelFamily.is_reasoning(name))

    def test_earlier_chat_models_are_not(self):
        for name in ("gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-3.5-turbo", "omni", "other-model"):
            with self.subTest(name=name):
                self.assertFalse(ModelFamily.is_reasoning(name))

    def test_an_unreadable_name_is_not_a_reasoning_model(self):
        logging.disable(logging.CRITICAL)
        try:
            for name in (None, "", "   ", 5, ["gpt-5"]):
                with self.subTest(name=name):
                    self.assertFalse(ModelFamily.is_reasoning(name))
                    self.assertFalse(ModelFamily.accepts_reasoning_effort(name))
        finally:
            logging.disable(logging.NOTSET)


class RetentionPolicyUsesTheFamily(unittest.TestCase):
    def test_encrypted_reasoning_is_requested_for_a_new_generation(self):
        with mock.patch.object(token_usage, "_openai_store_enabled", lambda: False):
            new = token_usage._apply_retention_policy("responses", {"model": "gpt-6"})
            old = token_usage._apply_retention_policy("responses", {"model": "gpt-4.1"})
        self.assertFalse(new["store"])
        self.assertIn("reasoning.encrypted_content", new["include"])
        self.assertNotIn("include", old)


if __name__ == "__main__":
    unittest.main()
