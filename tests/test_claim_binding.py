"""Numbers must be tied to the right period, series and meaning; names matched as whole words.

Covers the fixes of November 2026: utils/claim_binding.py (called from
utils.answer_verification.full_answer_numbers_supported), whole-word series
matching in utils/table_tools.py, AI-service failures no longer reported as
"out of scope", and settings in .env being read before they are used.
"""

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from utils.answer_verification import (  # noqa: E402
    full_answer_numbers_supported,
    simple_answer_numbers_supported,
)
from utils.claim_binding import LEDGER_KEY, period_value_ledger  # noqa: E402
from utils.model_evidence import compact_tool_result_for_model  # noqa: E402
from utils.request_gate import GateChecks, gate_request  # noqa: E402
from utils.security_selfcheck import run_checks  # noqa: E402
from utils.table_tools import _select_generic_series  # noqa: E402

SOURCE = "https://tablebuilder.singstat.gov.sg/table/TS/M000000"
QUESTION = "How did the rate change from 2019 to 2024?"


def _row(name, period, value):
    return {"series_name": name, "series_no": "1", "unit": "Per Cent", "period": period,
            "value": value}


def _evidence(**extra):
    return [
        {
            "success": True,
            "table_complete": True,
            "analysis_ready": True,
            "table_id": "M000000",
            "source_url": SOURCE,
            "observations": [
                _row("Residents", "2019", 3.1),
                _row("Residents", "2024", 2.7),
                _row("Citizens", "2019", 3.3),
                _row("Citizens", "2024", 2.9),
            ],
            "series_count_available": 2,
            **extra,
        }
    ]


def _accepted(answer, question=QUESTION, evidence=None):
    return full_answer_numbers_supported(
        answer, _evidence() if evidence is None else evidence, question, log=False
    )


class NumbersAreBoundToTheirMeaning(unittest.TestCase):
    def test_a_correct_answer_is_still_accepted(self):
        self.assertTrue(_accepted("The resident rate fell from 3.1% in 2019 to 2.7% in 2024."))
        self.assertTrue(_accepted("In 2024 the resident rate was 2.7%, down from 3.1% in 2019."))

    def test_a_value_given_for_the_wrong_year_is_refused(self):
        self.assertFalse(_accepted("The resident rate was 2.7% in 2019 and 3.1% in 2024."))
        self.assertFalse(_accepted("In 2019 the resident rate was 2.7%."))

    def test_a_value_given_for_the_wrong_series_is_refused(self):
        self.assertFalse(_accepted("The rate for residents was 3.3% in 2019."))

    def test_the_stated_direction_must_match_the_numbers(self):
        self.assertFalse(_accepted("The resident rate rose from 3.1% to 2.7%."))
        self.assertTrue(_accepted("The resident rate fell from 3.1% to 2.7%."))

    def test_a_share_is_not_an_amount(self):
        self.assertFalse(_accepted("About 2.7 million residents were affected in 2024."))

    def test_a_number_only_in_the_question_is_not_evidence(self):
        asked = "Is it true that the resident rate was 9.9% in 2024?"
        self.assertFalse(_accepted("Yes, the resident rate was 9.9% in 2024.", asked))
        # ...but plain wording from the question may be echoed.
        asked = "How did the rate change over the last 5 years?"
        self.assertTrue(_accepted("Over the last 5 years the resident rate fell to 2.7%.", asked))

    def test_a_decade_in_words_is_not_a_figure(self):
        # The evidence covers 2019 and 2024: "the 2010s" and "2020s" have a year in it.
        self.assertTrue(_accepted("In the late 2010s the resident rate stood at 3.1% in 2019."))
        self.assertTrue(_accepted("Early in the 2020s the resident rate was 2.7% in 2024."))
        # A decade with no year in the evidence, or the bare year, is still refused.
        self.assertFalse(_accepted("In the 1990s the resident rate was 3.1%."))
        self.assertFalse(_accepted("The resident rate was 3.1% in 2010, in the 2010s."))

    def test_counts_and_series_ids_cannot_back_a_statistic(self):
        self.assertFalse(_accepted("The rate was 2% in 2024."))
        self.assertTrue(_accepted("The table has 2 series; residents were at 2.7% in 2024."))

    def test_truncated_evidence_is_never_used_to_reject_a_pairing(self):
        # Only a preview of the observations is present: other years are unseen.
        evidence = _evidence(observations_truncated=True)
        self.assertTrue(_accepted("The resident rate was 2.7% in 2019.", evidence=evidence))

    def test_the_ledger_covers_every_row_and_is_hidden_from_the_model(self):
        frame = pd.DataFrame(
            {"series_name": ["Residents"] * 3, "period": ["2018", "2019", "2020"],
             "value": [3.0, 3.1, 4.1]}
        )
        ledger = period_value_ledger(frame)
        self.assertEqual(ledger[-1], ["Residents", "2020", 4.1])
        self.assertEqual(period_value_ledger(frame, limit=2), [])
        evidence = _evidence(observations_truncated=True, **{LEDGER_KEY: ledger})
        self.assertTrue(_accepted("The resident rate was 4.1% in 2020.", evidence=evidence))
        self.assertFalse(_accepted("The resident rate was 4.1% in 2019.", evidence=evidence))
        self.assertNotIn(LEDGER_KEY, compact_tool_result_for_model(evidence[0]))


def _result(rows, summaries=None, unit="Per Cent"):
    """One complete tool result with its full ledger: rows are (series, period, value)."""
    return {
        "success": True,
        "table_complete": True,
        "analysis_ready": True,
        "table_id": "M000000",
        "source_url": SOURCE,
        "start_year": None,
        "end_year": None,
        "observations": [
            {"series_name": name, "series_no": "1", "unit": unit, "period": period, "value": value}
            for name, period, value in rows
        ],
        LEDGER_KEY: [[name, period, value] for name, period, value in rows],
        "series_summary": summaries or [],
    }


RATE = [
    _result(
        [
            ("Residents", "2019", 3.1),
            ("Residents", "2020", 4.1),
            ("Residents", "2024", 2.7),
            ("Citizens", "2019", 3.3),
            ("Citizens", "2020", 4.2),
            ("Citizens", "2024", 2.9),
        ],
        [
            {
                "series_name": "Residents",
                "earliest_period": "2019",
                "latest_period": "2024",
                "earliest_value": 3.1,
                "latest_value": 2.7,
                "change_latest_minus_earliest": -0.4,
            }
        ],
    )
]
GROWTH = [_result([("GDP Growth", "2019", 1.3), ("GDP Growth", "2020", -3.9),
                   ("GDP Growth", "2021", 9.7)])]
POPULATION = [
    _result(
        [("Total Population", "2019", 5703569), ("Total Population", "2024", 6036860)],
        [
            {
                "series_name": "Total Population",
                "earliest_value": 5703569,
                "latest_value": 6036860,
                "change_latest_minus_earliest": 333291,
                "percentage_change": 5.843552,
            }
        ],
        unit="Number",
    )
]
BALANCE = [_result([("Net Balance", "2023", -3200), ("Net Balance", "2024", 1500)],
                   unit="Million Dollars")]


class NumbersKeepTheirSign(unittest.TestCase):
    """The text checks ignore signs, so the sign of a value is checked here."""

    def test_a_fall_cannot_be_described_as_a_rise(self):
        for wrong in (
            "The resident rate increased by 0.4 percentage points between 2019 and 2024.",
            "An increase of 0.4 percentage points.",
            "The resident rate was up 0.4 points.",
        ):
            with self.subTest(wrong=wrong):
                self.assertFalse(_accepted(wrong, evidence=RATE))
        for right in (
            "The resident rate fell by 0.4 percentage points.",
            "A decline of 0.4 percentage points over the period.",
            "The resident rate was 0.4 percentage points lower in 2024 than in 2019.",
            "The change was -0.4 percentage points.",
            "The change was \u22120.4 percentage points.",
        ):
            with self.subTest(right=right):
                self.assertTrue(_accepted(right, evidence=RATE))

    def test_a_rise_cannot_be_described_as_a_fall(self):
        self.assertTrue(_accepted("The total population grew by 5.84%, an increase of 333,291.",
                                  evidence=POPULATION))
        self.assertFalse(_accepted("The total population fell by 5.84%.", evidence=POPULATION))
        self.assertFalse(_accepted("A decrease of 333,291 people.", evidence=POPULATION))

    def test_a_negative_observation_needs_its_minus_sign_or_wording(self):
        self.assertTrue(_accepted("GDP growth was -3.9% in 2020.", evidence=GROWTH))
        self.assertTrue(_accepted("GDP contracted by 3.9% in 2020 and grew 9.7% in 2021.",
                                  evidence=GROWTH))
        self.assertFalse(_accepted("GDP grew 3.9% in 2020.", evidence=GROWTH))
        self.assertFalse(_accepted("GDP growth was 3.9% in 2020.", evidence=GROWTH))
        self.assertTrue(_accepted("The net balance was a deficit of 3,200 in 2023.",
                                  evidence=BALANCE))
        self.assertFalse(_accepted("The net balance was 3,200 in 2023.", evidence=BALANCE))

    def test_a_table_cell_keeps_its_sign(self):
        table = "| Year | GDP growth |\n|---|---|\n| 2020 | {} |"
        self.assertTrue(_accepted(table.format("-3.9"), evidence=GROWTH))
        self.assertFalse(_accepted(table.format("3.9"), evidence=GROWTH))
        # A header that says the column is a decrease explains the missing sign.
        self.assertTrue(_accepted("| Year | Contraction (%) |\n|---|---|\n| 2020 | 3.9 |",
                                  evidence=GROWTH))

    def test_a_positive_value_cannot_be_written_as_negative(self):
        self.assertFalse(_accepted("GDP growth was -9.7% in 2021.", evidence=GROWTH))

    def test_ranges_and_dashes_are_not_minus_signs(self):
        self.assertTrue(_accepted("Between 2019-2024 the resident rate went from 3.1% to 2.7%.",
                                  evidence=RATE))


class OtherWaysToMisuseARealNumber(unittest.TestCase):
    def test_direction_in_other_wordings(self):
        self.assertFalse(_accepted("The resident rate went from 3.1% up to 2.7%.", evidence=RATE))
        self.assertFalse(_accepted("The resident rate was 2.7% in 2024, up from 3.1% in 2019.",
                                   evidence=RATE))
        self.assertFalse(_accepted("An increase from 3.1% to 2.7% for residents.", evidence=RATE))
        self.assertTrue(_accepted("In 2024 the resident rate was 2.7%, down from 3.1% in 2019.",
                                  evidence=RATE))

    def test_value_and_year_in_brackets_or_after_a_colon(self):
        self.assertTrue(_accepted("Residents: 3.1% (2019), 2.7% (2024). "
                                  "Citizens: 3.3% (2019), 2.9% (2024).", evidence=RATE))
        self.assertFalse(_accepted("Citizens: 3.1 (2019), 2.7 (2024).", evidence=RATE))
        self.assertFalse(_accepted("Residents: 2.7% (2019).", evidence=RATE))
        self.assertTrue(_accepted("2019: 3.1%; 2024: 2.7% for residents.", evidence=RATE))
        self.assertFalse(_accepted("2019: 2.7% for residents.", evidence=RATE))

    def test_a_year_is_not_a_count(self):
        self.assertFalse(_accepted("About 2024 residents were unemployed.", evidence=RATE))
        self.assertFalse(_accepted("Some 2024 people were affected.", evidence=RATE))
        self.assertFalse(_accepted("The resident rate was 2,024 in the latest year.",
                                   evidence=RATE))
        self.assertTrue(_accepted("Around 2020 the resident rate was at its highest, 4.1%.",
                                  evidence=RATE))

    def test_a_peak_must_be_the_highest_published_value(self):
        self.assertTrue(_accepted("The resident rate peaked at 4.1% in 2020.", evidence=RATE))
        self.assertFalse(_accepted("The resident rate peaked at 3.1% in 2019.", evidence=RATE))
        self.assertFalse(_accepted("The resident rate bottomed out at 3.1% in 2019.",
                                   evidence=RATE))
        self.assertTrue(_accepted("The resident rate bottomed out at 2.7% in 2024.",
                                  evidence=RATE))

    def test_the_short_summary_gets_the_same_checks(self):
        full = "The resident rate fell by 0.4 percentage points, from 3.1% to 2.7%."
        self.assertTrue(simple_answer_numbers_supported(
            "The resident rate fell by 0.4 percentage points.", full, RATE))
        self.assertFalse(simple_answer_numbers_supported(
            "The resident rate rose by 0.4 percentage points.", full, RATE))


class SeriesNamesMatchWholeWords(unittest.TestCase):
    def setUp(self):
        self.table = pd.DataFrame(
            {
                "series_no": ["1", "2", "3", "4"],
                "series_name": ["Total", "Females", "Males", "Women Aged 65 & Over"],
                "unit": ["Number"] * 4,
            }
        )

    def _names(self, phrase):
        return _select_generic_series(self.table, phrase, 4)["series_name"].tolist()

    def test_a_label_never_selects_a_longer_label_that_contains_it(self):
        self.assertEqual(self._names("male"), ["Males"])
        self.assertEqual(self._names("males"), ["Males"])
        self.assertEqual(self._names("men"), [])

    def test_whole_words_and_word_starts_still_match(self):
        self.assertEqual(self._names("females"), ["Females"])
        self.assertEqual(self._names("women aged 65"), ["Women Aged 65 & Over"])
        self.assertEqual(self._names("wome"), ["Women Aged 65 & Over"])


class AiServiceFailureIsNotOutOfScope(unittest.TestCase):
    def _gate(self, classify, decision=None):
        return gate_request(
            "How many residents are there?",
            chat_history=[],
            combined_user_input=None,
            intent_decision=decision,
            checks=GateChecks(
                sanitize=lambda text: text,
                is_injection=lambda text: False,
                security_response=lambda reason: {"answer": "blocked", "security_blocked": True},
                combine=lambda text, **_: text,
                on_injection=None,
                validate=lambda value: value,
                fast_classify=lambda combined, question: None,
                ai_classify=classify,
                out_of_scope=lambda summary=None, reason="out_of_scope": {
                    "answer": "Sage is for official Singapore statistics.",
                    "scope_blocked": True,
                    "scope_reason": reason,
                },
                on_intent_error=lambda: None,
                on_out_of_scope=lambda: None,
            ),
        )

    def test_a_failed_intent_call_says_the_service_is_unavailable(self):
        def broken(_combined):
            raise ConnectionError("network down")

        blocked = self._gate(broken).blocked_response
        self.assertTrue(blocked["service_unavailable"])
        self.assertTrue(blocked["scope_blocked"])  # still fail closed: nothing is analysed
        self.assertNotIn("official Singapore statistics", blocked["answer"])

    def test_a_failed_preview_decision_is_treated_the_same_way(self):
        decision = {"summary": "x", "in_scope": False, "relevant_datasets": [],
                    "reason": "intent_classification_failed"}
        blocked = self._gate(lambda _c: None, decision).blocked_response
        self.assertTrue(blocked["service_unavailable"])

    def test_budget_and_capacity_stops_keep_their_own_error(self):
        from utils.errors import ModelCapacityError

        def busy(_combined):
            raise ModelCapacityError("busy")

        with self.assertRaises(ModelCapacityError):
            self._gate(busy)

    def test_a_real_out_of_scope_decision_is_unchanged(self):
        decision = {"summary": "x", "in_scope": False, "relevant_datasets": [],
                    "reason": "out_of_scope"}
        blocked = self._gate(lambda _c: None, decision).blocked_response
        self.assertNotIn("service_unavailable", blocked)


class SettingsAreReadInTheRightOrder(unittest.TestCase):
    def test_app_loads_dotenv_before_any_other_sage_module(self):
        tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
        sage_imports = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                sage_imports += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                sage_imports.append(node.module or "")
        sage_imports = [name for name in sage_imports if name.split(".")[0] in ("utils", "ui")]
        self.assertEqual(sage_imports[0], "utils.early_env")

    def test_progress_steps_are_shown_only_with_technical_details(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn('show_steps = bool(st.session_state.get("show_debug"))', source)
        self.assertIn("step if show_steps else None", source)

    def test_self_check_sees_either_spelling_of_the_sign_in_switch(self):
        for name in ("Need_Password", "NEED_PASSWORD"):
            found = {finding.id for finding in run_checks({name: "0"})}
            self.assertIn("SC-01", found, name)

    def test_startup_loads_the_environment_before_the_self_check(self):
        source = (ROOT / "ui" / "startup.py").read_text(encoding="utf-8")
        body = source[source.index("def warn_if_theme_config_missing") :]
        self.assertLess(body.index("load_environment_once()"),
                        body.index("run_security_selfcheck()"))


if __name__ == "__main__":
    unittest.main()
