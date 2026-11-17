"""The shapes of a SingStat table ID, defined once.

Several modules each carried their own copy of these patterns, and the copies
had drifted apart. They now import the same three; each use states which one
it needs and why.

    API_TABLE_ID      what the official API accepts (letters+digits, or all digits);
                      used wherever an ID becomes part of a request URL.
    PAGE_TABLE_ID     one letter and 5-8 digits: an ID that has a Table Builder page.
    SERIES_TABLE_ID   the same, limited to the published families M, C and D.
    METADATA_TABLE_ID a page ID or an all-digit ID (knowledge-base builder, metadata).
    SERIES_TABLE_ID_IN_TEXT   a family ID found inside a longer text.

Used by: utils/bank_source_guard.py, utils/catalogue_listing.py,
utils/core_secure.py, utils/pipeline_audit.py, utils/rag_engine.py,
utils/request_preferences.py, utils/round_labels.py, utils/singstat_api.py,
utils/table_download.py, utils/tool_arguments.py,
tools/rebuild_discovery_index.py.
Tested in: tests/test_intent_and_sources.py.
"""

import re

API_TABLE_ID = re.compile(r"^(?:[A-Z][A-Z0-9]{1,18}[0-9][A-Z0-9]*|[0-9]{3,20})$")
PAGE_TABLE_ID = re.compile(r"[A-Z]\d{5,8}\Z")
SERIES_TABLE_ID = re.compile(r"[MCD]\d{5,8}\Z")
SERIES_TABLE_ID_ANY_CASE = re.compile(r"[MCD]\d{5,8}\Z", re.I)
#: What the knowledge-base builder and the metadata endpoint accept: a page ID, or an
#: all-digit ID. utils/singstat_api.py had its own copy that allowed exactly six digits
#: after the letter, so a metadata lookup refused IDs the rest of Sage accepts.
METADATA_TABLE_ID = re.compile(r"(?:[A-Z]\d{5,8}|\d{1,12})\Z")
#: A published-family ID (M, C or D) anywhere inside a longer text, e.g. a source label.
SERIES_TABLE_ID_IN_TEXT = re.compile(r"\b[MCD]\d{5,8}\b")
