"""Pure generic SingStat payload normalization; no HTTP or model calls.

Used by: utils/chart_gallery.py, utils/cube_slice.py, utils/table_tools.py.
Tested in: tests/test_data_layer.py, tests/test_flagged_values.py.
"""

import pandas as pd


def normalize_generic_table(table_id, data, *, year_from_period, coerce_value, source_url) -> tuple:
    """Flatten an official table payload into one row per (series, period) with typed values."""
    records = []
    for row in data.get("row", []):
        series_no = str(row.get("seriesNo", "")).strip()
        series_name = str(row.get("rowText", "")).strip() or f"Series {series_no}"
        unit = str(row.get("uoM", row.get("unit", "")) or "").strip()
        for column in row.get("columns", []) or []:
            period = str(column.get("key", "")).strip()
            raw_value = column.get("value")
            records.append(
                {
                    "table_id": table_id,
                    "series_no": series_no,
                    "series_name": series_name,
                    "unit": unit,
                    "period": period,
                    "year": year_from_period(period),
                    "value_raw": raw_value,
                    "value": coerce_value(raw_value),
                }
            )
    df = pd.DataFrame(records)
    if df.empty:
        raise ValueError(f"No observations returned for SingStat table {table_id}.")
    metadata = {
        "table_id": table_id,
        "title": str(data.get("title", "")).strip(),
        "theme": str(data.get("theme", "")).strip(),
        "frequency": str(data.get("frequency", "")).strip(),
        "data_source": str(data.get("datasource", data.get("dataSource", ""))).strip(),
        "source_url": source_url(table_id),
        "retrieval_source": (
            "Validated official data.gov.sg mirror stored in SQLite"
            if data.get("verified_mirror")
            else "SingStat Table Builder / SQLite"
        ),
        "mirror_url": data.get("mirror_url"),
        "table_complete": not bool(data.get("staged_single_series")),
    }
    if data.get("staged_single_series"):
        metadata["retrieval_source"] = (
            "Official SingStat row saved in SQLite staging (PARTIAL " "TABLE)"
        )
        metadata["coverage_warning"] = (
            "Exactly one retrieved official series is available. The complete "
            "table, other series and latest reporting period are not verified. "
            "No table-wide inference or automatic graph is permitted."
        )
    return df, metadata


# --- Cells published with a flag ---------------------------------------------------
#: Plain "no value" markers: these are missing cells, not flagged figures.
_NO_VALUE = frozenset({"", "na", "n.a.", "nil", "-", "\u2013", "..", "..."})
MAX_FLAGGED_CELLS = 60


def flagged_text(raw_value, value) -> str | None:
    """The published text of a cell that carries a flag ("12.3p", "(4.5)", "<5"), else None.

    A flagged cell has a figure in it but is not a plain number, so Sage has
    no numeric value for it (``value`` is missing). It is shown exactly as
    published and is never calculated with or charted. Plain "no value"
    markers such as "-" or "na" are not flagged cells.
    """
    if raw_value is None or isinstance(raw_value, (int, float)) or not pd.isna(value):
        return None
    text = " ".join(str(raw_value).split())
    if text.casefold() in _NO_VALUE or not any(ch.isdigit() for ch in text):
        return None
    return text[:40]


def flagged_cells(rows, *, limit: int = MAX_FLAGGED_CELLS) -> list:
    """Flagged cells among ``rows`` (a normalised frame), as published, at most ``limit``.

    Each is {"series_name", "series_no", "unit", "period", "published"}. Frames
    without the official text column ("value_raw") have none.
    """
    if rows is None or "value_raw" not in getattr(rows, "columns", ()):
        return []
    found = []
    for row in rows[rows["value"].isna()].itertuples(index=False):
        published = flagged_text(row.value_raw, row.value)
        if published is None:
            continue
        found.append(
            {
                "series_name": str(row.series_name),
                "series_no": str(row.series_no),
                "unit": str(row.unit),
                "period": str(row.period),
                "published": published,
            }
        )
        if len(found) >= limit:
            break
    return found
