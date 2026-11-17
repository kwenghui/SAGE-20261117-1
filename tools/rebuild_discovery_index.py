r"""Rebuild data/discovery_index.sqlite3 (the list of official table titles) from the knowledge base.

    python tools/rebuild_discovery_index.py --dry-run     # say what WOULD be written; change
    nothing python tools/rebuild_discovery_index.py               # build it (refuses if the file
    exists) python tools/rebuild_discovery_index.py --force       # replace an existing file (it is
    kept
                                                          # beside the new one as a .backup-... copy)

WHY THIS TOOL EXISTS
    Sage reads the titles of official tables from data/discovery_index.sqlite3 for two
    things: answering "which tables cover X?" and offering a choice of tables for a typed
    question. Nothing in Sage creates that file, so once it is lost it stays lost. The
    knowledge base (data/knowledge_base.sqlite3) holds the same facts for every table it
    knows: the table ID, the official title and the Table Builder link. This tool copies
    them into a new title index.

WHAT IS WRITTEN
    One table, ``docs``, in the layout of Sage's other catalogue (utils/rag_catalog.py):
        id, table_id, title, source_label, source_url, text, keywords, doc_type, updated_at
    The two readers (utils/catalogue_listing.py, utils/discovery_controller.py) use only
    table_id, title, source_url and doc_type. One row per table, doc_type "table_metadata".

    The original file was not available when this tool was written, so the layout is
    reconstructed from the code that READS it. After building, the tool opens the new
    file with Sage's own reader and checks that every table written can be read back.

WHAT IS LEFT OUT (and counted in the summary)
    * an ID that is not a Table Builder page ID (one letter and 5-8 digits);
    * an empty title, or one longer than the reader accepts (350 characters).
    The link is always rebuilt from the ID as the official page address; a different link
    stored in the knowledge base is never copied.

SAFE TO RUN WHILE SAGE IS RUNNING
    The knowledge base is opened read-only. The new index is written to a temporary file
    and put in place in one step, so Sage never sees a half-written index. Restart Sage
    afterwards so cached "index missing" results are dropped.

Exit code: 0 = built (or dry run completed); 1 = nothing suitable to write, or the check
of the new file failed; 2 = it could not start (missing knowledge base, file exists).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from utils.discovery_controller import safe_catalogue_rows  # noqa: E402
from utils.official_links import table_page_url  # noqa: E402
from utils.table_ids import PAGE_TABLE_ID  # noqa: E402

#: The longest title the readers accept (utils/discovery_controller.safe_catalogue_rows).
MAX_TITLE_CHARS = 350
DOC_TYPE = "table_metadata"

SCHEMA = """
CREATE TABLE docs (
    id TEXT PRIMARY KEY, table_id TEXT NOT NULL, title TEXT NOT NULL,
    source_label TEXT NOT NULL, source_url TEXT NOT NULL,
    text TEXT NOT NULL, keywords TEXT NOT NULL, doc_type TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX idx_rag_table ON docs(table_id);
"""


class RebuildError(Exception):
    """The index cannot be rebuilt; the message is written for the operator."""


@dataclass
class RebuildReport:
    """What one run found and wrote."""

    tables_in_knowledge_base: int = 0
    written: int = 0
    skipped_id: list = field(default_factory=list)
    skipped_title: list = field(default_factory=list)
    readable_by_sage: int | None = None
    target: str = ""
    kept_old_file: str | None = None
    dry_run: bool = False

    @property
    def verified(self) -> bool:
        """True when Sage's own reader gets back exactly the tables that were written."""
        return self.dry_run or self.readable_by_sage == self.written


class DiscoveryIndexBuilder:
    """Builds the title index from the knowledge base (see the module docstring)."""

    def __init__(self, knowledge_base, target, *, force: bool = False, dry_run: bool = False,
                 say=print) -> None:
        """Remember the two files; nothing is opened or written until ``run``."""
        self.knowledge_base = Path(knowledge_base)
        self.target = Path(target)
        self.force = bool(force)
        self.dry_run = bool(dry_run)
        self.say = say
        self.report = RebuildReport(target=str(self.target), dry_run=self.dry_run)

    # -- reading ---------------------------------------------------------------------------
    def read_tables(self) -> list:
        """(table_id, title, theme, subject, frequency) of every table, read-only.

        RebuildError when the knowledge base is missing, unreadable or has no
        table list.
        """
        if not self.knowledge_base.is_file():
            raise RebuildError(f"Knowledge base not found: {self.knowledge_base}")
        try:
            with contextlib.closing(
                sqlite3.connect(self.knowledge_base.resolve().as_uri() + "?mode=ro", uri=True)
            ) as con:
                return con.execute(
                    "SELECT table_id, title, theme, subject, frequency FROM kb_tables "
                    "ORDER BY table_id"
                ).fetchall()
        except (sqlite3.Error, OSError) as error:
            raise RebuildError(
                f"Cannot read {self.knowledge_base.name}: {type(error).__name__}: {error}"
            ) from error

    def rows_to_write(self, tables) -> list:
        """The ``docs`` rows for the tables that pass the readers' own rules.

        Tables left out are recorded in the report, never silently dropped.
        """
        rows, seen = [], set()
        for table_id, title, theme, subject, frequency in tables:
            table_id = str(table_id or "").strip().upper()
            if not PAGE_TABLE_ID.fullmatch(table_id) or table_id in seen:
                self.report.skipped_id.append(table_id or "(empty)")
                continue
            clean_title = " ".join(str(title or "").split())
            if not clean_title or len(clean_title) > MAX_TITLE_CHARS:
                self.report.skipped_title.append(table_id)
                continue
            seen.add(table_id)
            # Always the official page address derived from the validated ID.
            url = table_page_url(table_id)
            described = [f"Official table identifier: {table_id}",
                         f"Published table title: {clean_title}"]
            extra = [str(value).strip() for value in (theme, subject, frequency)
                     if str(value or "").strip()]
            if extra:
                described.append("Catalogue description: " + "; ".join(extra))
            described.append(
                "This entry describes table metadata ONLY. It is not an official "
                "definition, explanatory note or numerical value."
            )
            unique_id = "RAG_" + hashlib.sha256(
                (table_id + "|" + DOC_TYPE + "|" + url).encode()
            ).hexdigest()[:24]
            rows.append((unique_id, table_id, clean_title, "SingStat " + table_id, url,
                         "\n".join(described), " ".join(extra)[:2000], DOC_TYPE))
        return rows

    # -- writing ---------------------------------------------------------------------------
    def write(self, rows) -> None:
        """Write the rows to a temporary file, then put it in place in one step."""
        temporary = self.target.with_name(self.target.name + ".part")
        try:
            self.target.parent.mkdir(parents=True, exist_ok=True)
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()
            with contextlib.closing(sqlite3.connect(temporary)) as con:
                con.executescript(SCHEMA)
                con.executemany(
                    "INSERT INTO docs (id, table_id, title, source_label, source_url, text, "
                    "keywords, doc_type) VALUES (?,?,?,?,?,?,?,?)",
                    rows,
                )
                con.commit()
            if self.target.exists():
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                kept = self.target.with_name(
                    f"{self.target.stem}.backup-{stamp}{self.target.suffix}"
                )
                self.target.replace(kept)
                self.report.kept_old_file = str(kept)
            temporary.replace(self.target)
        except (sqlite3.Error, OSError) as error:
            raise RebuildError(
                f"Could not write {self.target.name}: {type(error).__name__}: {error}"
            ) from error
        finally:
            with contextlib.suppress(OSError):
                temporary.unlink()  # only present when something failed

    def verify(self) -> None:
        """Open the new file with Sage's own reader and count what it accepts."""
        _rows, count = safe_catalogue_rows(self.target)
        self.report.readable_by_sage = int(count)

    # -- all steps ---------------------------------------------------------------------------
    def run(self) -> RebuildReport:
        """Read, check, write and verify; returns the report. Raises RebuildError."""
        if self.target.exists() and not (self.force or self.dry_run):
            raise RebuildError(
                f"{self.target} already exists. Use --force to replace it "
                "(the old file is kept beside it as a .backup-... copy)."
            )
        tables = self.read_tables()
        self.report.tables_in_knowledge_base = len(tables)
        rows = self.rows_to_write(tables)
        self.report.written = len(rows)
        if self.dry_run:
            return self.report
        if not rows:
            raise RebuildError(
                "The knowledge base has no table with a valid ID and title; nothing was written."
            )
        self.write(rows)
        self.verify()
        return self.report


def summary(report: RebuildReport) -> str:
    """The closing summary, including everything that was left out."""
    verb = "would be written" if report.dry_run else "written"
    lines = [
        "",
        "SUMMARY" + (" (dry run: nothing was changed)" if report.dry_run else ""),
        f"  tables in the knowledge base      {report.tables_in_knowledge_base:>8}",
        f"  tables {verb:<26} {report.written:>8}",
        f"  left out: not a page ID / repeated {len(report.skipped_id):>7}",
        f"  left out: no usable title         {len(report.skipped_title):>8}",
    ]
    for label, items in (("IDs left out", report.skipped_id),
                         ("no usable title", report.skipped_title)):
        if items:
            lines.append(f"    {label}: " + ", ".join(items[:15]) + (" ..." if len(items) > 15 else ""))
    if not report.dry_run:
        lines.append(f"  readable by Sage's own reader     {report.readable_by_sage:>8}")
        lines.append(f"  file: {report.target}")
        if report.kept_old_file:
            lines.append(f"  previous file kept as: {report.kept_old_file}")
        lines.append(
            "  CHECK FAILED: Sage reads back a different number of tables than were written."
            if not report.verified
            else "  Restart Sage so it uses the new index."
        )
    return "\n".join(lines)


def _in_sage_folder(path) -> Path:
    """A path as given when absolute, else inside the Sage folder (not the current folder)."""
    path = Path(path).expanduser()
    return path if path.is_absolute() else ROOT / path


def main(argv=None) -> int:
    """Command line entry point; returns the exit code."""
    parser = argparse.ArgumentParser(
        description="Rebuild data/discovery_index.sqlite3 from the knowledge base."
    )
    parser.add_argument("--db", default="data/knowledge_base.sqlite3")
    parser.add_argument("--out", default="data/discovery_index.sqlite3")
    parser.add_argument("--force", action="store_true", help="replace an existing index")
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args(sys.argv[1:] if argv is None else argv)
    builder = DiscoveryIndexBuilder(
        _in_sage_folder(arguments.db), _in_sage_folder(arguments.out),
        force=arguments.force, dry_run=arguments.dry_run,
    )
    try:
        report = builder.run()
    except RebuildError as error:
        print(f"ERROR: {error}")
        return 2
    print(summary(report))
    return 0 if report.verified and (report.written or report.dry_run) else 1


if __name__ == "__main__":
    raise SystemExit(main())
