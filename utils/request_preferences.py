"""Normalize untrusted user-selected source preferences before discovery.

Used by: utils/core_secure.py.
"""

from __future__ import annotations
from utils.table_ids import PAGE_TABLE_ID

_TABLE_ID = PAGE_TABLE_ID  # one definition: utils/table_ids.py


def normalize_source_preference(preferred_table_id, user_selected_table) -> tuple:
    """Return (validated table ID or None, explicitly_selected flag).

    A preferred table is only a discovery hint, never verified evidence.
    """
    table_id = (
        preferred_table_id
        if isinstance(preferred_table_id, str) and _TABLE_ID.fullmatch(preferred_table_id)
        else None
    )
    return table_id, bool(user_selected_table and table_id)
