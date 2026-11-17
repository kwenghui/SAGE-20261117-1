"""Plain-language labels for the rounds of the tool loop (token report, progress line).

Moved out of utils/core_secure.py unchanged. Each round the model either
answers or asks for tools; these functions turn that request into a short
label such as "Round 2 - Query <table ID>" and a one-sentence description.
They report observable tool choices only: approved table IDs, short series
phrases, never the model's reasoning or whole tool arguments.

    _analysis_round_task_label(round, calls)        the label of a round
    _analysis_round_decision_summary(calls, prior)  what the model chose next
    _sage_audit_table_title(table_id)               a title from local metadata, or None
    _sage_audit_table_label(table_id)               "ID - Title" (the ID alone if unknown)

Titles are read from the local index files only, read-only; a missing or
locked file gives the ID alone, never a guessed title. Malformed tool
arguments are treated as "no details" rather than raised: a label must never
stop an answer. utils/core_secure.py imports these names.

No model or network; reads local SQLite metadata only.

Used by: utils/core_secure.py.
Tested in: tests/test_intent_parsing.py.
"""

import json
import re
import sqlite3
from pathlib import Path

from utils.table_ids import SERIES_TABLE_ID, SERIES_TABLE_ID_ANY_CASE

APP_DIR = Path(__file__).resolve().parent.parent  # the Sage folder


def _analysis_round_task_label(round_number, function_calls) -> str:
    """A specific task label based on the model's recorded tool request.

    Reports observable tool choices, not the model's hidden reasoning.
    Only approved table IDs are rendered; arbitrary tool arguments are not.
    """
    if not function_calls:
        return f"Round {round_number} · Compose answer"
    action_names = {
        "inspect_singstat_table": "Inspect",
        "list_singstat_series": "List series",
        "query_singstat_table": "Query",
        "compare_singstat_series": "Compare series",
    }
    first = function_calls[0]
    name = str(getattr(first, "name", ""))
    action = action_names.get(name, "Run analytical tool")
    raw = getattr(first, "arguments", "")
    try:
        args = json.loads(raw) if isinstance(raw, str) else {}
    except (TypeError, ValueError):
        args = {}
    if not isinstance(args, dict):
        args = {}
    table_ids = []
    for key in ("table_id", "table_a_id", "table_b_id", "table_id_a", "table_id_b"):
        candidate = args.get(key)
        if isinstance(candidate, str) and SERIES_TABLE_ID_ANY_CASE.fullmatch(candidate):
            if candidate.upper() not in table_ids:
                table_ids.append(candidate.upper())
    if isinstance(args.get("table_ids"), list):
        for candidate in args["table_ids"]:
            if isinstance(candidate, str) and SERIES_TABLE_ID_ANY_CASE.fullmatch(candidate):
                if candidate.upper() not in table_ids:
                    table_ids.append(candidate.upper())
    if table_ids:
        action += " " + ", ".join(table_ids[:2])
    if len(function_calls) > 1:
        action += f" +{len(function_calls) - 1} tool(s)"
    return f"Round {round_number} · {action}"


def _sage_audit_table_title(table_id):
    """Resolve titles for UI diagnostics from existing official SQLite metadata only.

    Missing/locked metadata is shown as an ID, never replaced by a guessed title.
    """
    tid = str(table_id or '').strip().upper()
    if not SERIES_TABLE_ID.fullmatch(tid):
        return None
    for filename, query in (
        ('knowledge_base.sqlite3', 'SELECT title FROM kb_tables WHERE table_id=? LIMIT 1'),
        ('source_catalog.sqlite3', 'SELECT title FROM docs WHERE table_id=? LIMIT 1'),
    ):
        db = APP_DIR / 'data' / filename
        if not db.is_file():
            continue
        try:
            # Read-only access: never create a database as a side effect of diagnostics.
            connection = sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)
            try:
                match = connection.execute(query, (tid,)).fetchone()
            finally:
                connection.close()
            if match and isinstance(match[0], str) and match[0].strip():
                return re.sub(r'\s+', ' ', match[0]).strip()[:130]
        except (sqlite3.Error, OSError, ValueError):
            continue
    return None


def _sage_audit_table_label(table_id):
    """ "Mxxxxxx - Title" for audit and token reports (the ID alone if the title is unknown)."""
    tid = str(table_id or '').strip().upper()
    title = _sage_audit_table_title(tid)
    return f'{tid} - {title}' if title else tid


def _analysis_round_decision_summary(function_calls, prior_tool_results):
    """Return a short non-sensitive description of what the model chose next."""
    prior_count = len(prior_tool_results) if isinstance(prior_tool_results, list) else 0
    if not function_calls:
        if prior_count:
            return (
                f"No further analytical tool requested; compose the answer from "
                f"{prior_count} previously gathered tool result(s)."
            )
        return (
            "No analytical tool requested; compose the answer directly from the validated "
            "context."
        )

    labels = {
        "inspect_singstat_table": "inspect the official table structure and metadata",
        "list_singstat_series": "inspect the published series available in the official table",
        "query_singstat_table": "retrieve verified observations from the official table",
        "compare_singstat_series": "compare two official SingStat series",
    }

    pieces = []
    for call in function_calls[:3]:
        name = str(getattr(call, "name", "unknown tool"))
        action = labels.get(name, f"run {name}")
        details = []
        raw = getattr(call, "arguments", "")
        try:
            args = json.loads(raw) if isinstance(raw, str) else {}
        except (json.JSONDecodeError, TypeError, ValueError):
            args = {}
        if isinstance(args, dict):
            table_id = args.get("table_id")
            if isinstance(table_id, str) and table_id.strip():
                details.append(_sage_audit_table_label(table_id.strip()[:24]))
            table_ids = args.get("table_ids")
            if isinstance(table_ids, list):
                ids = [str(v).strip() for v in table_ids if str(v).strip()][:3]
                if ids:
                    details.append(
                        "tables " + ", ".join(_sage_audit_table_label(tid) for tid in ids)
                    )
            for key, label in (
                ("series_search", "series"),
                ("series_a_search", "series A"),
                ("series_b_search", "series B"),
                ("age_group", "age group"),
                ("sex", "sex"),
            ):
                value = args.get(key)
                if isinstance(value, str) and value.strip():
                    details.append(f"{label} '{value.strip()[:60]}'")
        suffix = f" ({'; '.join(details)})" if details else ""
        pieces.append(action + suffix)
    if len(function_calls) > 3:
        pieces.append(f"plus {len(function_calls) - 3} additional tool request(s)")
    if pieces:
        pieces[0] = pieces[0][:1].upper() + pieces[0][1:]
    return "; ".join(pieces) + f". Prior tool results available: {prior_count}."
