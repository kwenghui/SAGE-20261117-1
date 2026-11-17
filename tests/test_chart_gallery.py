"""Which tables may be listed and charted in the gallery: fail closed (utils/chart_gallery.py)."""

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

from utils import chart_gallery  # noqa: E402

SOURCE = "https://tablebuilder.singstat.gov.sg/table/TS/M000000"


def _table(**changes):
    result = {
        "success": True,
        "table_id": "M000000",
        "table_complete": True,
        "source_url": SOURCE,
        "title": "A Table",
    }
    result.update(changes)
    return {"tool_results": [result]}


class IdentifiedTablesFailClosed(unittest.TestCase):
    def test_a_complete_official_table_is_listed_once(self):
        response = _table()
        response["tool_results"] *= 2
        self.assertEqual(
            chart_gallery.describe_identified_tables(response),
            [
                {
                    "table_id": "M000000",
                    "title": "A Table",
                    "source_url": SOURCE,
                    "is_crosstab": False,
                }
            ],
        )

    def test_anything_unverified_is_left_out(self):
        for changes in (
            {"success": False},
            {"table_complete": False},
            {"table_complete": None},
            {"source_url": "https://example.com/table/TS/M000000"},
            {"source_url": SOURCE.replace("M000000", "M000001")},  # another table's page
            {"table_id": "../etc"},
        ):
            with self.subTest(changes=changes):
                self.assertEqual(chart_gallery.describe_identified_tables(_table(**changes)), [])
        self.assertEqual(chart_gallery.describe_identified_tables("not a response"), [])

    def test_a_cross_table_page_is_recognised(self):
        page = SOURCE.replace("/TS/", "/CT/")
        found = chart_gallery.describe_identified_tables(_table(source_url=page))
        self.assertTrue(found[0]["is_crosstab"])

    def test_at_most_eight_tables(self):
        results = [
            {
                "success": True,
                "table_id": f"M00000{n}",
                "table_complete": True,
                "source_url": SOURCE.replace("M000000", f"M00000{n}"),
            }
            for n in range(1, 10)
        ] + [
            {
                "success": True,
                "table_id": f"M00001{n}",
                "table_complete": True,
                "source_url": SOURCE.replace("M000000", f"M00001{n}"),
            }
            for n in range(3)
        ]
        found = chart_gallery.describe_identified_tables({"tool_results": results})
        self.assertEqual(len(found), 8)


class ChartGalleryGuards(unittest.TestCase):
    def test_no_graphs_when_charts_are_suppressed_or_the_response_is_not_a_dict(self):
        self.assertEqual(chart_gallery.collect_verified_graphs(None), [])
        self.assertEqual(
            chart_gallery.collect_verified_graphs({"suppress_charts": True, **_table()}), []
        )
        self.assertEqual(chart_gallery.collect_verified_graphs({"tool_results": []}), [])

    def test_a_table_is_reloaded_only_for_a_well_formed_id_and_a_real_loader(self):
        loader = mock.Mock(side_effect=ValueError("not available"))
        empty = {"series": [], "charts": [], "total_chartable": 0, "limited": False}
        for bad_id in ("m000000", "../x", "", "M0"):
            with self.subTest(bad_id=bad_id):
                self.assertEqual(chart_gallery.load_table_gallery(bad_id, loader), [])
                self.assertEqual(chart_gallery.load_complete_table_series(bad_id, loader), empty)
        loader.assert_not_called()
        self.assertEqual(chart_gallery.load_table_gallery("M000000", None), [])
        # A loader failure gives nothing to draw, never an exception on the page.
        self.assertEqual(chart_gallery.load_table_gallery("M000000", loader), [])
        self.assertEqual(chart_gallery.load_complete_table_series("M000000", loader), empty)
        self.assertEqual(loader.call_count, 2)

    def test_total_rows_are_recognised_by_flag_or_by_their_own_label(self):
        for entry in (
            {"is_total": True},
            {"is_parent": True},
            {"series_name": "Total Residents"},
            {"leaf_name": "Overall"},
            {"series_name": "  Grand Total"},
            {"series_name": "All Items"},
        ):
            with self.subTest(entry=entry):
                self.assertTrue(chart_gallery.is_total_series(entry))
        for entry in (
            {"series_name": "Allied Health"},
            {"series_name": "Subtotal"},
            {"leaf_name": "Males", "series_name": "Total"},
            {},
        ):
            with self.subTest(entry=entry):
                self.assertFalse(chart_gallery.is_total_series(entry))

    def test_standard_period_labels_only(self):
        self.assertEqual(chart_gallery._period_order("2024"), (2024, 0))
        self.assertEqual(chart_gallery._period_order("2024 Mar"), (2024, 3))
        self.assertEqual(chart_gallery._period_order("2024 3Q"), (2024, 9))
        self.assertEqual(chart_gallery._period_order("2024 Q1"), (2024, 3))
        for odd in ("2017/18", "Mid-2024", "2024 Foo", "Total"):
            with self.subTest(odd=odd):
                self.assertIsNone(chart_gallery._period_order(odd))

    def test_nothing_is_selected_from_an_empty_table(self):
        self.assertEqual(chart_gallery.default_table_selection("any question", []), set())


if __name__ == "__main__":
    unittest.main()
