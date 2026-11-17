"""A table whose API pages are counted in OBSERVATIONS downloads in a few requests.

SingStat's table-data endpoint returns at most ``limit`` observations per call
for large monthly tables, cutting a series in the middle. Sage used to recover
with one request PER SERIES and was rate-limited (HTTP 429) on tables with
hundreds of series. The fake server below behaves like the real one.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from utils import store  # noqa: E402

TABLE = "M000000"


class _Response:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload
        self.content = json.dumps(payload).encode()
        self.url = "https://tablebuilder.singstat.gov.sg/"

    def raise_for_status(self):
        return None

    def json(self):
        return json.loads(self.content)


class _ObservationPagedServer:
    """30 monthly series of different lengths; ``limit``/``offset`` count observations."""

    def __init__(self, drop_last_cell=False):
        self.series = []
        for number in range(30):
            ident = "1" if number == 0 else f"1.{number:02d}"
            months = 250 - 5 * number
            self.series.append(
                {
                    "seriesNo": ident,
                    "rowText": f"Series {ident}",
                    "uoM": "Per Cent",
                    "columns": [
                        {"key": f"{2000 + m // 12} {m % 12 + 1:02d}", "value": str(m + number)}
                        for m in range(months)
                    ],
                }
            )
        self.total = sum(len(row["columns"]) for row in self.series)
        self.drop_last_cell = drop_last_cell
        self.table_requests = 0
        self.filtered_requests = 0

    def _data(self, rows):
        return {"Data": {"id": TABLE, "title": "Test table", "frequency": "Monthly",
                         "dataLastUpdated": "23/10/2026", "row": rows}}

    def fetch(self, url, **_kwargs):
        parsed = urlparse(url)
        if "/metadata/" in parsed.path:
            listed = [{"seriesNo": r["seriesNo"], "rowText": r["rowText"], "uoM": r["uoM"]}
                      for r in self.series]
            return _Response({"Data": {"records": {"id": TABLE, "total": self.total,
                                                   "frequency": "Monthly", "row": listed}}})
        query = parse_qs(parsed.query)
        if "seriesNoORrowNo" in query or "sortBy" in query:
            self.filtered_requests += 1
            raise AssertionError("per-series or sorted recovery should not be needed")
        self.table_requests += 1
        limit, offset = int(query["limit"][0]), int(query.get("offset", ["0"])[0])
        end = self.total - 1 if self.drop_last_cell else self.total
        rows, position = [], 0
        for row in self.series:
            start, stop = max(offset, position), min(offset + limit, position + len(row["columns"]), end)
            if start < stop:
                rows.append(dict(row, columns=row["columns"][start - position : stop - position]))
            position += len(row["columns"])
        return _Response(self._data(rows))


class ObservationPagedTables(unittest.TestCase):
    def _store(self):
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        return store.LocalStore(str(Path(folder.name) / "check.sqlite3"))

    def test_the_whole_table_is_read_in_a_few_pages_and_verified(self):
        server = _ObservationPagedServer()
        payload = self._store().get_or_fetch(TABLE, server.fetch, fetch_metadata=server.fetch)
        rows = payload["Data"]["row"]
        self.assertEqual(len(rows), 30)
        self.assertEqual(sum(len(r["columns"]) for r in rows), server.total)
        self.assertEqual([r["seriesNo"] for r in rows], [r["seriesNo"] for r in server.series])
        self.assertEqual(rows[7]["columns"], server.series[7]["columns"])  # a series cut by a page
        self.assertEqual(server.filtered_requests, 0)
        self.assertLessEqual(server.table_requests, server.total // 2500 + 2)

    def test_a_table_that_is_one_observation_short_is_still_refused(self):
        server = _ObservationPagedServer(drop_last_cell=True)
        with self.assertRaises(Exception):
            self._store().get_or_fetch(TABLE, server.fetch, fetch_metadata=server.fetch)


if __name__ == "__main__":
    unittest.main()
