"""Category columns are never read as time (utils/period_axis.py, utils/table_tools.py)."""

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from utils import credentials  # noqa: E402

if not credentials.has_openai_api_key():
    credentials.set_openai_api_key("sk-" + "test0" * 8)  # shape only; never sent anywhere

from utils import table_tools  # noqa: E402
from utils.period_axis import AXIS_CATEGORIES, AXIS_TIME, PeriodAxis  # noqa: E402


def _payload(columns_by_series):
    """An official-style payload: {series name: [(column label, value), ...]}."""
    return {
        "id": "M000000",
        "title": "A test table",
        "row": [
            {
                "seriesNo": str(number),
                "rowText": name,
                "uoM": "Number",
                "columns": [{"key": key, "value": str(value)} for key, value in columns],
            }
            for number, (name, columns) in enumerate(columns_by_series.items(), start=1)
        ],
    }


def _query(payload, **arguments):
    """Run the query tool on ``payload`` without the network or the local store."""
    load = lambda table_id, series_search, max_series: (  # noqa: E731
        *table_tools.retrieve_generic_singstat_table(table_id, data_override=payload),
        False,
    )
    with mock.patch.object(table_tools, "_load_table_for_query", load):
        return table_tools.query_singstat_table(
            "M000000", arguments.get("series_search"), None, None,
            arguments.get("max_series", 2), True,
        )


class PeriodAxisRules(unittest.TestCase):
    def test_every_label_must_carry_a_year(self):
        self.assertTrue(PeriodAxis(["2019", "2020 Q1", "Jan 2021", "2017/18"]).is_time)
        self.assertFalse(PeriodAxis(["Total", "Males", "Females"]).is_time)
        self.assertFalse(PeriodAxis(["15 - 19", "20 - 24", "70 & Over"]).is_time)
        # One label without a year makes the whole axis categorical (fail closed).
        self.assertFalse(PeriodAxis(["2019", "2020", "Total"]).is_time)
        # A value band is not a year.
        self.assertFalse(PeriodAxis(["Below 2000", "2000 - 2999"]).is_time)

    def test_empty_or_unreadable_axis_is_not_time(self):
        self.assertFalse(PeriodAxis([]).is_time)
        self.assertFalse(PeriodAxis([None, "", "  "]).is_time)
        self.assertFalse(PeriodAxis.from_frame(None).is_time)
        self.assertEqual(PeriodAxis.from_frame(object()).kind, AXIS_CATEGORIES)

    def test_change_over_time_needs_two_periods(self):
        self.assertFalse(PeriodAxis(["2024"]).supports_change_over_time)
        self.assertTrue(PeriodAxis(["2023", "2024"]).supports_change_over_time)
        self.assertIsNone(PeriodAxis(["2023", "2024"]).note())
        self.assertIn("categories", PeriodAxis(["Total", "Males"]).note())


class QueryToolOnCategoryColumns(unittest.TestCase):
    CATEGORIES = {
        "Group A": [("Total", 3000), ("Males", 1400), ("Females", 1600)],
        "Group B": [("Total", 500), ("Males", 240), ("Females", 260)],
    }

    def test_no_change_trend_or_chart_is_made_up(self):
        result = _query(_payload(self.CATEGORIES))
        self.assertTrue(result["success"])
        self.assertEqual(result["column_axis"], AXIS_CATEGORIES)
        self.assertIn("column_axis_note", result)
        # No "earliest", "latest", "change" or "percentage change" between categories.
        self.assertEqual(result["series_summary"], [])
        self.assertEqual(result["chart_kind"], "none")
        self.assertIsNone(result["chart_spec"])
        self.assertFalse(result["chart_source_verified"])

    def test_published_values_stay_available(self):
        result = _query(_payload(self.CATEGORIES))
        values = {row["value"] for row in result["observations"]}
        self.assertEqual(values, {3000.0, 1400.0, 1600.0, 500.0, 240.0, 260.0})
        self.assertEqual(set(result["numeric_claims"]), values)
        self.assertNotIn(87.5, result["numeric_claims"])  # the old made-up percentage change

    def test_a_real_time_series_is_unchanged(self):
        result = _query(_payload({"Group A": [("2019", 100), ("2020", 110), ("2021", 121)]}),
                        max_series=1)
        self.assertEqual(result["column_axis"], AXIS_TIME)
        self.assertNotIn("column_axis_note", result)
        self.assertEqual(result["chart_kind"], "time_series")
        summary = result["series_summary"][0]
        self.assertEqual((summary["earliest_period"], summary["latest_period"]), ("2019", "2021"))
        self.assertEqual(summary["change_latest_minus_earliest"], 21.0)
        self.assertEqual(summary["percentage_change"], 21.0)


class CompareToolOnCategoryColumns(unittest.TestCase):
    def test_no_trend_chart_across_categories(self):
        payload = _payload({
            "Group A": [("Males", 1400), ("Females", 1600)],
            "Group B": [("Males", 240), ("Females", 260)],
        })
        frame_and_metadata = table_tools.retrieve_generic_singstat_table(
            "M000000", data_override=payload
        )
        with mock.patch.object(
            table_tools, "retrieve_generic_singstat_table", lambda table_id: frame_and_metadata
        ):
            result = table_tools.compare_singstat_series(
                "M000000", "Group A", "Group B", None, None, True
            )
        self.assertTrue(result["success"])
        self.assertEqual(result["column_axis"], AXIS_CATEGORIES)
        self.assertIsNone(result["chart_spec"])
        self.assertEqual(len(result["observations"]), 2)  # the values are still listed


if __name__ == "__main__":
    unittest.main()
