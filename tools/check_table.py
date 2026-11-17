"""Download one SingStat table exactly the way Sage does, and say why it fails.

    python tools/check_table.py M213881

Uses a TEMPORARY database, so your real data is never changed. Prints the number
of rows and periods on success, or Sage's exact completeness / download error.
Also shows what your real local database already holds for the table.
"""

from __future__ import annotations

import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _logged(fetch):
    """Wrap Sage's HTTP fetch so every official request and what it returned is printed."""

    def run(url, **kwargs):
        short = url.split("/api/table/", 1)[-1]
        started = time.monotonic()
        try:
            response = fetch(url, **kwargs)
        except Exception as exc:  # noqa: BLE001 - diagnostic tool: show it, then re-raise
            print(f"  [http] {short}\n         -> {type(exc).__name__}: {str(exc)[:160]}")
            raise
        note = f"HTTP {response.status_code}, {len(response.content):,} bytes"
        try:
            data = (response.json() or {}).get("Data") or {}
            records = data.get("records") if isinstance(data.get("records"), dict) else None
            if records is not None:  # the metadata manifest
                listed = records.get("row") or []
                with_id = sum(1 for r in listed if isinstance(r, dict) and r.get("seriesNo"))
                note += (f"; metadata total={records.get('total')}, rows listed={len(listed)}, "
                         f"with seriesNo={with_id}, frequency={records.get('frequency')!r}")
            elif isinstance(data.get("row"), list):
                rows = data["row"]
                cells = sum(len(r.get("columns") or []) for r in rows if isinstance(r, dict))
                ids = [str(r.get("seriesNo") or r.get("rowNo") or "") for r in rows[:1] + rows[-1:]]
                note += (f"; rows={len(rows)}, cells={cells:,}, Data.total={data.get('total')}, "
                         f"first/last id={ids}, updated={data.get('dataLastUpdated')!r}")
        except Exception as exc:  # noqa: BLE001
            note += f"; body not readable as a table ({type(exc).__name__})"
        print(f"  [http] {short}\n         -> {note} ({time.monotonic() - started:.1f} s)")
        return response

    return run


def main(argv) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    table_id = argv[1].strip().upper()
    print(f"Checking SingStat table {table_id}\n")
    try:
        import utils.store as store
        # Sage's own download door (it moved here from utils/core_secure.py, which
        # this tool still referred to and so crashed with AttributeError).
        import utils.official_source as source
    except Exception as exc:  # noqa: BLE001 - diagnostic tool
        print(f"Could not load Sage: {type(exc).__name__}: {exc}")
        return 1

    # 1) What the real database already has (read-only).
    try:
        real = source.get_local_store()
        age = real.table_cache_age_seconds(table_id)
        staged = real.staged_catalog(table_id) or []
        print(
            f"[info] Local database: "
            f"{'stored, ' + str(round(age / 3600, 1)) + ' h old' if age is not None else 'not stored'}"
            f"; staged series names: {len(staged)}"
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[info] Local database not readable: {type(exc).__name__}: {exc}")

    # 2) A fresh download into a temporary database, exactly as Sage fetches it.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        temp = store.LocalStore(str(Path(folder) / "check.sqlite3"))
        started = time.monotonic()
        try:
            # Same two callbacks as utils.official_source.safe_http_get: table pages
            # and the separate metadata manifest that proves completeness.
            payload = temp.get_or_fetch(
                table_id, _logged(source._live_http_get), fetch_metadata=_logged(source._live_http_get)
            )
        except store.UnverifiablePaginationError as exc:
            print(
                f"[FAIL] Download not verifiable as complete after "
                f"{time.monotonic() - started:.1f} s:"
            )
            print(f"       {exc}")
            print("       Sage refuses incomplete official data, so it answered without figures.")
            return 1
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] {type(exc).__name__} after {time.monotonic() - started:.1f} s: {exc}")
            print("".join(traceback.format_exc(limit=6)))
            return 1
        data = (payload or {}).get("Data") or {}
        rows = data.get("row") or []
        from utils.periods import period_sort_key

        periods = sorted(
            {str(c.get("key")) for r in rows for c in (r.get("columns") or []) if c.get("key")},
            key=period_sort_key,  # time order ("1961 Apr" is not the first period)
        )
        print(
            f"[OK  ] Downloaded and verified in {time.monotonic() - started:.1f} s: "
            f"{len(rows)} series, {len(periods)} periods"
        )
        if periods:
            print(f"       first periods: {periods[:3]}  last periods: {periods[-3:]}")
        print(f"       title: {data.get('title')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
