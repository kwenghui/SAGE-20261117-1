"""Code that was moved out of the core lives in ONE module each (utils/catalogue_listing.py, utils/core_prompts.py)."""

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

from utils import core_prompts, hybrid_discovery  # noqa: E402
from utils import core_secure as core  # noqa: E402


class TableListingLivesInItsOwnModule(unittest.TestCase):
    def test_the_core_uses_the_listing_module(self):
        from utils import catalogue_listing

        self.assertIs(core.list_local_singstat_tables, catalogue_listing.list_local_singstat_tables)
        self.assertIs(core.is_table_catalogue_request, catalogue_listing.is_table_catalogue_request)

    def test_the_listing_module_needs_no_ai_client(self):
        import ast

        tree = ast.parse((ROOT / "utils" / "catalogue_listing.py").read_text(encoding="utf-8"))
        imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        imported |= {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        self.assertFalse({"openai", "utils.core_config", "utils.core_secure"} & imported)

    def test_a_missing_index_lists_nothing(self):
        from utils import catalogue_listing

        missing = ROOT / "data" / "no-such-index.sqlite3"
        found = catalogue_listing.list_local_singstat_tables("tables about housing", missing)
        self.assertEqual(found["count"], 0)
        self.assertIn("unavailable", found["answer"])  # says so; never an invented list


class PromptsLiveInOneModule(unittest.TestCase):
    def test_the_core_uses_the_wording_of_the_prompt_module(self):
        for name in (
            "TOOLS",
            "INTENT_GATE_INSTRUCTIONS",
            "SYSTEM_INSTRUCTIONS",
            "_EVIDENCE_RULES",
            "_AUDITED_SOURCE_NOTE",
        ):
            with self.subTest(name=name):
                self.assertIs(getattr(core, name), getattr(core_prompts, name))

    def test_exactly_the_four_generic_tools_with_strict_schemas(self):
        self.assertEqual({tool["name"] for tool in core_prompts.TOOLS}, core.GENERIC_TOOL_NAMES)
        for tool in core_prompts.TOOLS:
            with self.subTest(tool=tool["name"]):
                self.assertIs(tool["strict"], True)
                self.assertIs(tool["parameters"]["additionalProperties"], False)
                self.assertEqual(
                    set(tool["parameters"]["required"]), set(tool["parameters"]["properties"])
                )

    def test_the_search_thread_setting_is_within_its_bounds(self):
        self.assertTrue(2 <= hybrid_discovery.SEARCH_THREADS <= 32)


if __name__ == "__main__":
    unittest.main()
