"""Real numbers used for the wrong thing are refused (utils/claim_checks.py,
utils/question_numbers.py). Each wrong statement below used to pass as verified."""

import logging
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from utils import answer_verification as checks  # noqa: E402
from utils.claim_binding import LEDGER_KEY, question_numbers_allowed  # noqa: E402
from utils.claim_checks import (  # noqa: E402
    ClaimCheck,
    EvidenceFacts,
    ExtraClaimChecks,
    all_violations,
)
from utils.question_numbers import QuestionNumberPolicy  # noqa: E402

ROWS = [["Alpha Units", "2019", 13000.0], ["Alpha Units", "2020", 13500.0],
        ["Beta Units", "2019", 6000.0], ["Beta Units", "2020", 6100.0]]


def evidence(unit="Number", **extra):
    """One complete official result with two series over 2019-2020."""
    result = {
        "success": True, "table_complete": True, "table_id": "M000000",
        "title": "Alpha and beta units 1990 - 2024",
        "source_url": "https://tablebuilder.singstat.gov.sg/table/TS/M000000",
        "series_summary": [
            {"series_name": "Alpha Units", "series_no": "1", "unit": unit,
             "latest_period": "2020", "latest_value": 13500.0, "earliest_period": "2019",
             "earliest_value": 13000.0, "change_latest_minus_earliest": 500.0,
             "observation_count": 2, "percentage_change": 3.846154},
            {"series_name": "Beta Units", "series_no": "2", "unit": unit,
             "latest_period": "2020", "latest_value": 6100.0, "earliest_period": "2019",
             "earliest_value": 6000.0, "change_latest_minus_earliest": 100.0,
             "observation_count": 2, "percentage_change": 1.666667},
        ],
        "observations": [{"series_name": name, "series_no": "1", "unit": unit,
                          "period": period, "value": value} for name, period, value in ROWS],
        "numeric_claims": [13000.0, 13500.0, 6000.0, 6100.0],
        LEDGER_KEY: [list(row) for row in ROWS],
        "start_year": 2019, "end_year": 2020,
    }
    result.update(extra)
    return [result]


def supported(answer, question="How many alpha units were there?", rows=None):
    """True when the full answer check accepts ``answer``."""
    return checks.full_answer_numbers_supported(answer, rows or evidence(), question, log=False)


class QuestionOnlyNumbers(unittest.TestCase):
    """A number that exists only in the question is never a statistic."""

    def test_a_whole_number_from_the_question_is_refused_as_a_fact(self):
        self.assertFalse(supported("Yes, there were 5,000 alpha units in 2020.",
                                   "Is it true there were 5000 alpha units in 2020?"))
        self.assertFalse(supported("There were 99999 alpha units in 2020.",
                                   "Did we have 99999 alpha units in 2020?"))
        # A year-shaped number used as a count is refused too.
        self.assertFalse(supported("There were 2000 alpha units in 2020.",
                                   "Were there 2000 alpha units in 2020?"))

    def test_plain_wording_may_still_be_echoed(self):
        policy = QuestionNumberPolicy
        self.assertTrue(policy("Over the last 10 years the series rose.").is_plain_wording("10"))
        self.assertTrue(policy("The top 5 are listed below.").is_plain_wording("5"))
        self.assertTrue(policy("Residents aged 15 and over are covered.").is_plain_wording("15"))
        self.assertTrue(policy("Those aged 15-64 are covered.").is_plain_wording("64"))
        self.assertTrue(policy("No figures are published for 2030.").is_plain_wording("2030"))
        self.assertTrue(policy("The index uses 2019 = 100.").is_plain_wording("100"))

    def test_statistic_shapes_are_refused(self):
        policy = QuestionNumberPolicy
        self.assertFalse(policy("The rate was 9.9%.").is_plain_wording("9.9"))
        self.assertFalse(policy("About 9 million were counted.").is_plain_wording("9"))
        self.assertFalse(policy("There were 300 of them.").is_plain_wording("300"))
        self.assertFalse(policy("It rose to 2000 by the end.").is_plain_wording("2000"))
        self.assertFalse(policy("Nothing here.").is_plain_wording("300"))  # not in the answer
        # Plain in one place and a statistic in another: refused.
        self.assertFalse(policy("In the last 10 years, 10 were added.").is_plain_wording("10"))

    def test_numbers_already_in_the_evidence_are_not_judged_here(self):
        self.assertEqual(question_numbers_allowed({"2020"}, {"2020"}, "anything"), set())


class WrongUseOfRealNumbers(unittest.TestCase):
    def test_correct_statements_still_pass(self):
        self.assertTrue(supported("There were 13,500 alpha units in 2020, up from 13,000 in 2019."))
        self.assertTrue(supported("Alpha units numbered 13,500 in 2020."))
        self.assertTrue(supported("Alpha units grew 3.85% between 2019 and 2020."))

    def test_wrong_scale_word(self):
        self.assertFalse(supported("There were 13,500 thousand alpha units in 2020."))
        # ...but a value published in thousands may be written in thousands.
        self.assertTrue(supported("There were 13,500 thousand alpha units in 2020.",
                                  rows=evidence(unit="Thousand")))

    def test_year_used_as_a_quantity(self):
        self.assertFalse(supported("The number of alpha units reached 1990 in 2020."))
        self.assertFalse(supported("Alpha units rose by 2024 over the period."))
        self.assertFalse(supported("That is an increase of 2019 for alpha units."))

    def test_another_series_change(self):
        self.assertFalse(supported("Alpha units grew 1.67% between 2019 and 2020."))
        self.assertTrue(supported("Beta units grew 1.67% between 2019 and 2020."))

    def test_values_swapped_between_two_series(self):
        self.assertFalse(supported(
            "Alpha units numbered 6,100 in 2020 while beta units numbered 13,500."))
        self.assertTrue(supported(
            "Alpha units numbered 13,500 in 2020 while beta units numbered 6,100."))

    def test_numbers_written_in_words(self):
        self.assertFalse(supported("There were roughly fourteen thousand alpha units in 2020."))
        self.assertTrue(supported("There were thirteen thousand alpha units in 2019."))
        self.assertTrue(supported("Several thousand alpha units were counted."))  # no figure

    def test_a_stated_multiple_must_match_the_two_values(self):
        # 13,500 / 6,100 = 2.21
        self.assertTrue(supported(
            "There were 13,500 alpha units in 2020, more than twice the 6,100 beta units."))
        self.assertTrue(supported("Alpha units stood at 13,500, about twice the 6,100 of beta."))
        self.assertFalse(supported(
            "There were 13,500 alpha units in 2020, more than three times the 6,100 beta units."))
        self.assertFalse(supported("Alpha units stood at 13,500, about half the 6,100 of beta."))
        self.assertFalse(supported("Alpha units stood at 13,500, less than twice the 6,100."))
        # Nothing to compare with: not judged.
        self.assertTrue(supported("That is roughly twice the 6,100 beta units of 2020."))

    def test_the_short_summary_gets_the_same_checks(self):
        full = "There were 13,500 alpha units in 2020 and 6,100 beta units."
        self.assertFalse(checks.simple_answer_numbers_supported(
            "Alpha units numbered 6,100 in 2020.", full, evidence()))
        self.assertTrue(checks.simple_answer_numbers_supported(
            "Alpha units numbered 13,500 in 2020.", full, evidence()))


class ChecksFailClosed(unittest.TestCase):
    def test_truncated_evidence_is_never_used_to_reject_a_series(self):
        rows = evidence()
        del rows[0][LEDGER_KEY]
        rows[0]["observations_truncated"] = True
        facts = EvidenceFacts(rows, checks.numeric_tokens)
        self.assertFalse(facts.complete)
        self.assertEqual(ExtraClaimChecks().run(
            "Alpha units numbered 6,100 in 2020.", rows, checks.numeric_tokens), [])

    def test_a_rule_that_cannot_run_refuses_the_answer(self):
        class Broken(ClaimCheck):
            name = "broken"

            def problems(self, text, facts):
                raise RuntimeError("defect")

        logging.disable(logging.CRITICAL)
        try:
            problems = ExtraClaimChecks([Broken()]).run("x", evidence(), checks.numeric_tokens)
        finally:
            logging.disable(logging.NOTSET)
        self.assertEqual(problems, ["check: the broken check could not run"])

    def test_malformed_evidence_is_tolerated(self):
        self.assertEqual(all_violations("No numbers here.", [None, "text", {}],
                                        checks.numeric_tokens), [])



class ChangeColumnsUseOneKindOfFigure(unittest.TestCase):
    """A table of changes shows the amount in every row and the percentage beside it."""

    NEGATIVE_START = {
        "series_name": "Gamma Units", "series_no": "3", "unit": "Number",
        "latest_period": "2020", "latest_value": 75584.5, "earliest_period": "2019",
        "earliest_value": -3689.1, "change_latest_minus_earliest": 79273.6,
        "observation_count": 2,  # no percentage_change: the starting value is negative
    }
    LARGE_RISE = {
        "series_name": "Delta Units", "series_no": "4", "unit": "Number",
        "latest_period": "2020", "latest_value": 142827.7, "earliest_period": "2019",
        "earliest_value": 3899.6, "change_latest_minus_earliest": 138928.1,
        "observation_count": 2, "percentage_change": 3562.624372,
    }

    def rows(self):
        result = evidence()[0]
        result["series_summary"] = [self.NEGATIVE_START, self.LARGE_RISE]
        ledger = [["Gamma Units", "2019", -3689.1], ["Gamma Units", "2020", 75584.5],
                  ["Delta Units", "2019", 3899.6], ["Delta Units", "2020", 142827.7]]
        result[LEDGER_KEY] = ledger
        result["numeric_claims"] = [row[2] for row in ledger]
        result["observations"] = [{"series_name": n, "unit": "Number", "period": p, "value": v}
                                  for n, p, v in ledger]
        return [result]

    def test_a_rounded_percentage_of_1000_or_more_is_accepted_with_separators(self):
        """ "3,562.62%" was refused (only the six-decimal form passed): the comma hid it."""
        answer = ("| Series | 2019 | 2020 | Change | Change (%) |\n|---|---|---|---|---|\n"
                  "| Delta Units | 3,899.6 | 142,827.7 | 138,928.1 | 3,562.62% |\n"
                  "| Gamma Units | -3,689.1 | 75,584.5 | 79,273.6 | not meaningful |")
        self.assertTrue(supported(answer, "How did the units change?", rows=self.rows()))
        self.assertTrue(supported("Delta units rose 3562.6% from 2019 to 2020.",
                                  "How did the units change?", rows=self.rows()))

    def test_a_different_large_percentage_is_still_refused(self):
        self.assertFalse(supported("Delta units rose 3,562.99% from 2019 to 2020.",
                                   "How did the units change?", rows=self.rows()))
        self.assertFalse(supported("Delta units rose 13,562.62% from 2019 to 2020.",
                                   "How did the units change?", rows=self.rows()))

    def test_the_model_is_told_to_keep_one_kind_of_figure_per_column(self):
        from utils import core_prompts

        self.assertIn("ONE KIND OF FIGURE PER COLUMN", core_prompts.SYSTEM_INSTRUCTIONS)
        self.assertIn("not meaningful", core_prompts.SYSTEM_INSTRUCTIONS)


if __name__ == "__main__":
    unittest.main()
