"""Cells SingStat publishes with a flag ("12.3p") are shown as published and never charted.

Decision of 16 November 2026: such a cell is displayed exactly as it is in
the official table, in a table; no graph is drawn for a series that has one.
"""

import math
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools._stand_ins import install_stand_ins  # noqa: E402

install_stand_ins()  # streamlit/openai stand-ins when the real libraries are absent

import pandas as pd  # noqa: E402

from utils import chart_gallery, core_prompts, table_tools  # noqa: E402
from utils.answer_verification import (  # noqa: E402
    full_answer_numbers_supported,
    official_evidence,
    structured_endpoint_chart_from_answer,
)
from utils.singstat_generic_normalization import flagged_cells, flagged_text  # noqa: E402

TABLE = "M000000"
SOURCE = f"https://tablebuilder.singstat.gov.sg/table/TS/{TABLE}"
META = {"table_id": TABLE, "title": "Output, Annual", "table_complete": True,
        "frequency": "Annual", "source_url": SOURCE}
NAN = float("nan")


def _frame(last_output="12.3p"):
    """Two annual series; the 2024 cell of "Output" is published as ``last_output``."""
    cells = [
        ("1", "Output", "2022", "10.1"), ("1", "Output", "2023", "11.2"),
        ("1", "Output", "2024", last_output),
        ("2", "Input", "2022", "5.0"), ("2", "Input", "2023", "6.0"), ("2", "Input", "2024", "7.0"),
    ]
    return pd.DataFrame([
        {"series_no": no, "series_name": name, "unit": "Index", "period": period,
         "year": int(period), "value_raw": raw,
         "value": table_tools._coerce_singstat_value(raw)}
        for no, name, period, raw in cells
    ])


class WhatCountsAsAFlaggedCell(unittest.TestCase):
    def test_a_figure_with_a_flag_is_kept_as_published(self):
        for raw in ("12.3p", "(4.5)", "<5", "1,234 e"):
            with self.subTest(raw=raw):
                self.assertEqual(flagged_text(raw, NAN), raw)

    def test_plain_numbers_and_empty_cells_are_not_flagged(self):
        self.assertIsNone(flagged_text("12.3", 12.3))
        self.assertIsNone(flagged_text(12.3, 12.3))
        for raw in ("-", "na", "", None, "..", "n.a.", "nil", "–", "not available"):
            with self.subTest(raw=raw):
                self.assertIsNone(flagged_text(raw, NAN))

    def test_cells_are_listed_with_their_series_and_period(self):
        self.assertEqual(flagged_cells(_frame()), [
            {"series_name": "Output", "series_no": "1", "unit": "Index", "period": "2024",
             "published": "12.3p"}
        ])
        self.assertEqual(flagged_cells(_frame("12.3")), [])
        self.assertEqual(flagged_cells(_frame().drop(columns="value_raw")), [])


class TheQueryToolShowsThemAndDrawsNoChart(unittest.TestCase):
    def _query(self, frame, **arguments):
        # The local store is switched off: a test must never create data/app_cache.sqlite3.
        with mock.patch.object(table_tools, "retrieve_generic_singstat_table",
                               return_value=(frame, dict(META))), \
                mock.patch.object(table_tools, "SAGE_STORE_ENABLED", False):
            return table_tools.query_singstat_table(TABLE, **arguments)

    def test_a_series_with_a_flagged_cell(self):
        result = self._query(_frame(), series_search="series id:1", max_series=1)
        self.assertTrue(result["success"])
        self.assertEqual(result["flagged_cells"][0]["published"], "12.3p")
        self.assertEqual(result["flagged_cells"][0]["period"], "2024")
        self.assertIn("exactly as published", result["flagged_note"])
        # No chart of any kind.
        self.assertIsNone(result["chart_spec"])
        self.assertIsNone(result["latest_comparison_spec"])
        self.assertEqual(result["chart_kind"], "none")
        self.assertIs(result["chart_source_verified"], False)
        self.assertIn("no chart", result["chart_suppressed_reason"])
        # The flagged figure is not a number anywhere in the evidence.
        self.assertNotIn(12.3, [row["value"] for row in result["observations"]])
        self.assertEqual(result["series_summary"][0]["latest_period"], "2023")
        self.assertTrue(all(math.isfinite(v) for v in result["numeric_claims"]))

    def test_a_series_without_one_is_unchanged(self):
        result = self._query(_frame(), series_search="series id:2", max_series=1)
        self.assertNotIn("flagged_cells", result)
        self.assertIsNotNone(result["chart_spec"])
        self.assertIs(result["chart_source_verified"], True)

    def test_a_flagged_cell_outside_the_years_asked_for_does_not_matter(self):
        result = self._query(_frame(), series_search="series id:1", max_series=1,
                             start_year=2022, end_year=2023)
        self.assertNotIn("flagged_cells", result)
        self.assertIsNotNone(result["chart_spec"])

    def test_a_comparison_with_a_flagged_cell_draws_no_chart(self):
        with mock.patch.object(table_tools, "retrieve_generic_singstat_table",
                               return_value=(_frame(), dict(META))):
            result = table_tools.compare_singstat_series(TABLE, "series id:1", "series id:2")
        self.assertTrue(result["success"], result.get("error"))
        self.assertEqual(result["flagged_cells"][0]["published"], "12.3p")
        self.assertIsNone(result["chart_spec"])
        self.assertIs(result["chart_source_verified"], False)

    def test_the_model_is_told_how_to_show_them(self):
        self.assertIn("flagged_cells", core_prompts.SYSTEM_INSTRUCTIONS)
        self.assertIn("exactly as published", core_prompts.SYSTEM_INSTRUCTIONS)


class TheAnswerMustKeepTheFlag(unittest.TestCase):
    def setUp(self):
        with mock.patch.object(table_tools, "retrieve_generic_singstat_table",
                               return_value=(_frame(), dict(META))), \
                mock.patch.object(table_tools, "SAGE_STORE_ENABLED", False):
            result = table_tools.query_singstat_table(TABLE, "series id:1", max_series=1)
        self.evidence = official_evidence({"tool_results": [result]})
        self.assertEqual(len(self.evidence), 1)

    def _accepted(self, answer):
        return full_answer_numbers_supported(answer, self.evidence, "How did output change?",
                                             log=False)

    def test_as_published_in_a_table_is_accepted(self):
        self.assertTrue(self._accepted(
            "| Year | Output |\n|---|---|\n| 2022 | 10.1 |\n| 2023 | 11.2 |\n| 2024 | 12.3p |\n\n"
            "The 2024 figure carries SingStat's flag."
        ))

    def test_the_bare_number_is_refused(self):
        self.assertFalse(self._accepted("Output was 12.3 in 2024."))
        self.assertFalse(self._accepted("| Year | Output |\n|---|---|\n| 2024 | 12.3 |"))
        self.assertFalse(self._accepted("Output was 12.3p in 2024, so it rose to 12.3."))

    def test_no_trend_chart_is_built_from_the_answer(self):
        answer = ("| Series | 2022 | 2023 | Change |\n|---|---|---|---|\n"
                  "| Output | 10.1 | 11.2 | 1.1 |\n\nSource: SingStat " + TABLE)
        self.assertIsNone(structured_endpoint_chart_from_answer(
            "How did output change?", answer, self.evidence))


class TheTableExplorerDoesNotChartThem(unittest.TestCase):
    def test_the_series_keeps_its_published_cell_and_is_left_off_the_charts(self):
        found = chart_gallery.table_series_charts(TABLE, _frame(), dict(META))
        by_name = {entry["leaf_name"]: entry for entry in found["series"]}
        self.assertEqual(by_name["Output"]["flagged"], {"2024": "12.3p"})
        self.assertNotIn("flagged", by_name["Input"])
        self.assertNotIn("2024", by_name["Output"]["observations"])
        drawn = {line["name"] for chart in found["charts"] for line in chart.get("series", [])}
        self.assertTrue(any("Input" in name for name in drawn))
        self.assertFalse(any("Output" in name for name in drawn))

    def test_the_explorer_lists_flagged_cells_as_published(self):
        from ui import table_explorer

        entries = chart_gallery.table_series_charts(TABLE, _frame(), dict(META))["series"]
        self.assertEqual(table_explorer.flagged_rows(entries), [
            {"Series": "Output", "Period": "2024", "Published as": "12.3p", "Unit": "Index"}
        ])
        self.assertEqual(table_explorer.flagged_rows(
            [entry for entry in entries if not entry.get("flagged")]), [])


def _cube(flag="6.5p"):
    """Occupation x (year > measure): the 2024 "Average" of Clerks is published as ``flag``."""

    def year(label, count, average):
        return {"key": label, "columns": [{"key": "Number", "value": count},
                                          {"key": "Average", "value": average}]}

    return {"row": [
        {"rowText": "Total", "columns": [year("2023", "30", "5.0"), year("2024", "33", "5.5")]},
        {"rowText": "Managers", "columns": [year("2023", "10", "7.0"), year("2024", "11", "7.5")]},
        {"rowText": "Clerks", "columns": [year("2023", "20", "4.0"), year("2024", "22", flag)]},
        {"rowText": "Cleaners", "columns": [year("2023", "5", "3.0"), year("2024", "6", "3.5")]},
    ]}


class MultiDimensionalTables(unittest.TestCase):
    def test_the_parser_keeps_a_flagged_cell_as_text_not_as_a_number(self):
        from utils.cube_slice import parse_cube

        cube = parse_cube(_cube())
        self.assertEqual(cube["flagged"], {("Clerks", ("Average",), "2024"): "6.5p"})
        self.assertNotIn(("Clerks", ("Average",), "2024"), cube["unique"])
        self.assertEqual(parse_cube(_cube("6.5"))["flagged"], {})
        self.assertEqual(parse_cube(_cube("-"))["flagged"], {})

    def test_the_slice_shows_it_as_published_and_draws_no_chart(self):
        from utils.cube_slice import crosstab_overview

        overview = crosstab_overview(_cube(), table_id=TABLE, source_url=SOURCE,
                                     group="Average", period="2024")
        clerks = next(row for row in overview["period_table"] if row["Category"] == "Clerks")
        self.assertEqual(clerks["2024"], "6.5p")
        self.assertEqual(clerks["2023"], 4.0)
        self.assertIsNone(overview["chart_spec"])
        self.assertIn("exactly as published", overview["flagged_note"])
        self.assertIn({"Category": "Clerks", "Value": "6.5p"}, overview["rows"])

    def test_another_period_of_the_same_slice_is_charted_as_before(self):
        from utils.cube_slice import crosstab_overview

        overview = crosstab_overview(_cube(), table_id=TABLE, source_url=SOURCE,
                                     group="Average", period="2023")
        self.assertIsNotNone(overview["chart_spec"])
        self.assertEqual(overview["flagged_note"], "")
        self.assertEqual(overview["chart_spec"]["categories"], ["Managers", "Clerks", "Cleaners"])
        plain = crosstab_overview(_cube("6.5"), table_id=TABLE, source_url=SOURCE,
                                  group="Average", period="2024")
        self.assertIsNotNone(plain["chart_spec"])

    def test_the_cube_query_lists_flagged_cells_of_the_answered_slice_only(self):
        from utils.cube_slice import cube_query

        found = cube_query(_cube(), series_search="Clerks")
        self.assertTrue(found["ok"])
        flagged = found.get("flagged") or []
        if found["group"] == "Average":
            self.assertEqual(flagged, [{"row": "Clerks", "column_group": "Average",
                                        "period": "2024", "published": "6.5p"}])
        else:  # the other measure was chosen: its cells carry no flag
            self.assertEqual(flagged, [])
        self.assertNotIn("flagged", cube_query(_cube(), series_search="Managers"))
        self.assertNotIn("flagged", cube_query(_cube("6.5"), series_search="Clerks"))

    def test_the_tool_result_for_a_cube_has_no_chart_when_a_cell_is_flagged(self):
        found = {"ok": True, "group": "Average", "unit": "Average", "periods": ["2023", "2024"],
                 "rows": [{"name": "Clerks", "values": {"2023": 4.0}}], "other_groups": [],
                 "n_groups": 2, "note": "n",
                 "flagged": [{"row": "Clerks", "column_group": "Average", "period": "2024",
                              "published": "6.5p"}]}
        with mock.patch.object(table_tools, "_nested_cube_slice",
                               return_value=(_cube(), found)):
            result = table_tools._cube_query_fallback(TABLE, dict(META), "Clerks", None, None,
                                                      1, True)
        self.assertTrue(result["success"])
        self.assertEqual(result["flagged_cells"], [
            {"series_name": "Clerks \u00b7 Average", "series_no": "", "unit": "Average",
             "period": "2024", "published": "6.5p"}])
        self.assertIsNone(result["chart_spec"])
        self.assertIs(result["chart_source_verified"], False)


if __name__ == "__main__":
    unittest.main()
