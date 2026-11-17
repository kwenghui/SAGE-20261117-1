"""Instant answers for questions Sage has recently answered and verified.

A repeated question (same wording after normalising, same model, same chosen
table, same reasoning effort, same Sage code) is shown at once from this cache
instead of being worked out again: no model calls, no search, no download.

Safety rules:
* Only VERIFIED answers are kept (never refusals, errors or limited answers
  whose wording depends on a failure).
* An entry is used only while every official table it used still has the same
  SingStat "Data last updated" date in the local store, and for at most
  SAGE_ANSWER_CACHE_MINUTES (default 1440 = one day; 0 switches the cache off).
* An entry is used only by the Sage code that produced and checked it: the
  key includes a fingerprint of the program files (code_version), and the
  on-disk cache is emptied the first time a different version opens it. An
  answer accepted by an older answer check is therefore never shown after an
  upgrade. No release number has to be bumped for this to work.
* Serving a cached answer also checks the age of its tables' local copies and
  starts a background refresh of any that is older than CACHE_DURATION, so new
  SingStat figures are noticed even when every ask is answered from the cache.
* Follow-up questions that depend on earlier dialogue are never cached.
* Typed questions stay in memory only (they may be personal). Sample-bank
  questions, which are public text, are also kept on disk
  (data/answer_cache.sqlite3) so they stay instant after a restart.

Used by: ui/answer_pipeline.py.
Tested in: tests/test_answer_cache.py, tests/test_speed_features.py.
"""
import copy
import hashlib
import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

from utils.query_text import normalise_query
from utils.session_security import bounded_int_env

LOGGER = logging.getLogger("sage_ui")
_MEMORY: dict = {}
_LOCK = threading.Lock()
_MEMORY_LIMIT = 300
_DISK_LIMIT = 3000
_CODE_VERSION = None  # fingerprint of the running program files, worked out once
_CHECKED_DISKS: set = set()  # disk caches already matched to this code version


def cache_seconds() -> int:
    """How long an answer may be reused (0 = cache off)."""
    return 60 * bounded_int_env("SAGE_ANSWER_CACHE_MINUTES", 1440, 0, 7 * 24 * 60)


def code_version() -> str:
    """A fingerprint of the Sage program that produces and checks answers.

    A digest of the two release names and of every program file (app.py, ui/,
    utils/). It changes whenever the code changes, with or without a new
    release name, so an answer checked by older code is never reused. Worked
    out once per process (a few milliseconds); if a file cannot be read, the
    release names alone are used.
    """
    global _CODE_VERSION
    if _CODE_VERSION is not None:
        return _CODE_VERSION
    from ui.config import APP_DIR, SAGE_REQUIRED_CORE_RELEASE, SAGE_UI_RELEASE

    digest = hashlib.sha256(f"{SAGE_UI_RELEASE}\x1f{SAGE_REQUIRED_CORE_RELEASE}".encode("utf-8"))
    root = Path(APP_DIR)
    try:
        files = [root / "app.py", *sorted((root / "ui").glob("*.py")),
                 *sorted((root / "utils").glob("*.py"))]
        for path in files:
            digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\x1f")
            digest.update(path.read_bytes().replace(b"\r\n", b"\n"))  # same on Windows and Linux
    except OSError as error:
        LOGGER.info("Program files unreadable (%s); cache keyed by release names only",
                    type(error).__name__)
    with _LOCK:
        _CODE_VERSION = digest.hexdigest()[:32]
    return _CODE_VERSION


def cache_key(question, model, selected_table_id=None, bank_table_id=None,
              hinted_table_id=None) -> str:
    """A digest of everything that decides the answer (the text itself is not stored in it)."""
    from utils.model_client import reasoning_effort

    parts = [normalise_query(str(question)).casefold(), str(model or ""),
             str(selected_table_id or ""), str(bank_table_id or "")]
    if hinted_table_id:  # a discovery hint steers the analysis, so it is part of the key
        parts.append("hint:" + str(hinted_table_id))
    # The code that checks the answer and the model's reasoning setting also decide it.
    parts.append("code:" + code_version())
    parts.append("effort:" + str(reasoning_effort() or ""))
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _table_dates(response) -> dict:
    """{table ID: SingStat "Data last updated"} for every successful table in the answer."""
    from utils.official_source import data_last_updated
    ids = sorted({str(r.get("table_id")) for r in (response or {}).get("tool_results") or []
                  if isinstance(r, dict) and r.get("success") and r.get("table_id")})
    return {tid: data_last_updated(tid) for tid in ids}


def _disk_path() -> Path:
    from ui.config import APP_DIR
    return Path(APP_DIR) / "data" / "answer_cache.sqlite3"


def _disk():
    """The on-disk cache (created on first use)."""
    path = _disk_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=5)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS answers (key TEXT PRIMARY KEY, saved REAL, "
                    "entry TEXT NOT NULL)")
        _empty_if_other_version(con, str(path))
    except BaseException:
        con.close()
        raise
    return con


def _empty_if_other_version(con, path) -> None:
    """Empty a disk cache written by different Sage code (once per file and process).

    Old entries could no longer be found anyway (the key holds the code
    version); removing them keeps answers from an older answer check off the
    disk and the file small.
    """
    with _LOCK:
        if path in _CHECKED_DISKS:
            return
    version = code_version()
    with con:
        con.execute("CREATE TABLE IF NOT EXISTS cache_info (name TEXT PRIMARY KEY, value TEXT)")
        row = con.execute("SELECT value FROM cache_info WHERE name='code_version'").fetchone()
        if row is None or row[0] != version:
            removed = con.execute("DELETE FROM answers").rowcount
            con.execute("INSERT OR REPLACE INTO cache_info VALUES ('code_version', ?)", (version,))
            if removed:
                LOGGER.info("Answer cache emptied after a Sage update (%d saved answers)", removed)
    with _LOCK:
        _CHECKED_DISKS.add(path)


def _plain(value):
    """JSON form of a non-JSON value: numbers stay NUMBERS (numpy/pandas scalars), else text.

    Turning a numpy integer into text made a cached chart differ from a fresh one.
    """
    item = getattr(value, "item", None)
    if callable(item):
        try:
            converted = item()
        except (TypeError, ValueError):
            converted = None
        if isinstance(converted, (bool, int, float, str)):
            return converted
    return str(value)


def lookup(key: str):
    """A still-valid cached entry {"messages", "response", "saved"}, or None."""
    limit = cache_seconds()
    if not limit:
        return None
    with _LOCK:
        entry = _MEMORY.get(key)
    if entry is None and _disk_path().exists():
        try:
            con = _disk()
            try:
                row = con.execute("SELECT entry FROM answers WHERE key=?", (key,)).fetchone()
            finally:
                con.close()
            entry = json.loads(row[0]) if row else None
        except (sqlite3.Error, ValueError, OSError) as error:
            LOGGER.info("Answer cache unreadable (%s); answering normally", type(error).__name__)
            entry = None
    if entry is None:
        return None
    if time.time() - float(entry.get("saved") or 0) > limit:
        return None
    _refresh_old_copies(entry.get("table_dates"))
    if _table_dates(entry.get("response")) != entry.get("table_dates"):
        return None                      # SingStat published new data: work it out again
    # A private copy: the caller stores it in ONE visitor's session and changes it.
    return copy.deepcopy(entry)


def _refresh_old_copies(table_dates) -> None:
    """Start a background refresh of any official table copy older than CACHE_DURATION.

    A cached answer is shown without reading its tables, so nothing else would
    ever refresh them: the answer kept being served until it expired, even
    after SingStat published new figures. The refresh runs in the background
    (this lookup does not wait); once it lands, the "Data last updated" date
    differs and the cached answer is dropped on the next ask. Never online in
    offline mode, and never creates the store just to look.
    """
    try:
        import utils.store
        from utils import official_source as source

        if source.SAGE_OFFLINE_ONLY or not source.SAGE_STORE_ENABLED:
            return
        if source._LOCAL_STORE is None and not Path(
            utils.store.default_db_path(source.APP_DIR)
        ).exists():
            return
        local = source.get_local_store()
        for table_id in sorted(table_dates or {}):
            age = local.table_cache_age_seconds(str(table_id))
            if age is not None and age > source.CACHE_DURATION_SECONDS:
                source._schedule_background_refresh(str(table_id))
    except (sqlite3.Error, OSError, ValueError, KeyError, RuntimeError) as error:
        LOGGER.info("Stale-copy check skipped (%s)", type(error).__name__)


def store(key: str, messages, response, *, persist: bool) -> None:
    """Keep a verified answer for reuse (persist=True also keeps it on disk)."""
    if not cache_seconds():
        return
    entry = {"saved": time.time(), "messages": messages, "response": response,
             "table_dates": _table_dates(response)}
    try:
        entry = json.loads(json.dumps(entry, default=_plain))  # plain data only, no live objects
    except (TypeError, ValueError):
        return
    with _LOCK:
        _MEMORY[key] = entry
        while len(_MEMORY) > _MEMORY_LIMIT:
            _MEMORY.pop(next(iter(_MEMORY)))
    if not persist:
        return
    try:
        con = _disk()
        try:
            with con:
                con.execute("INSERT OR REPLACE INTO answers VALUES (?,?,?)",
                            (key, entry["saved"], json.dumps(entry)))
                # Expired answers are never served; do not keep them on disk either.
                con.execute("DELETE FROM answers WHERE saved < ?",
                            (entry["saved"] - cache_seconds(),))
                con.execute("DELETE FROM answers WHERE key NOT IN (SELECT key FROM answers "
                            "ORDER BY saved DESC LIMIT ?)", (_DISK_LIMIT,))
        finally:
            con.close()
    except (sqlite3.Error, OSError) as error:
        LOGGER.info("Answer cache not saved (%s)", type(error).__name__)


def clear() -> None:
    """Forget every cached answer (memory and disk)."""
    with _LOCK:
        _MEMORY.clear()
    if _disk_path().exists():
        try:
            con = _disk()
            try:
                with con:
                    con.execute("DELETE FROM answers")
            finally:
                con.close()
        except (sqlite3.Error, OSError):
            pass
