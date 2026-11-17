r"""Update Sage's search indexes in one run: the SQLite knowledge base, then the Chroma index.

    python tools/update_indexes.py                       # finish pending tables, then sync Chroma
    python tools/update_indexes.py --discover cpi hdb    # first look for new tables by keyword
    python tools/update_indexes.py --catalogue new.csv   # first add tables from a catalogue CSV
    python tools/update_indexes.py --tables M000000 ...  # re-read these tables (new periods/series)
    python tools/update_indexes.py --sqlite-only         # no Chroma, no OpenAI calls
    python tools/update_indexes.py --chroma-only         # only bring Chroma up to date
    python tools/update_indexes.py --dry-run             # say what WOULD be done; change nothing
    python tools/update_indexes.py --backup --optimize   # copy the database first; compact it after
    python tools/update_indexes.py --titles              # also refresh the title index afterwards

WHAT IT DOES, IN ORDER
    1. (--backup)     an online copy of the knowledge base beside it (SQLite backup API);
    2. (--catalogue / --discover)  add newly published tables to the catalogue (metadata only);
    3. tables         read every table whose series are not complete yet from the official
                      SingStat API, plus any named with --tables. One failing table never
                      stops the run; it is reported and stays "incomplete" for next time;
    4. Chroma         embed every knowledge-base record that is new or whose text changed,
                      in rounds, until none is left. Records already indexed are skipped;
    5. (--titles)     rebuild data/discovery_index.sqlite3 (the list of table titles) from the
                      updated knowledge base, so newly added tables appear in "which tables
                      cover X?" (tools/rebuild_discovery_index.py; the old file is kept);
       (--optimize)   merge and compact the keyword index (STOP SAGE FIRST: the file is rewritten);
    6. a summary of counts before and after.

It only calls what "python -m utils.kb" already offers (ingest, table, sync-chroma). That
command does one bounded batch per call (10 tables, 500 records) and always starts again
from the same first tables, so a few permanently failing tables at the front could block
everything behind them. This tool goes through every pending table once.

NEEDS
    * network access to tablebuilder.singstat.gov.sg (steps 2-3);
    * the OpenAI key for step 4, read like Sage does (OPENAI_API_KEY in the environment or
      in the Sage folder's .env). Embeddings cost money: about 100,000 short records is
      well under one US dollar with text-embedding-3-small;
    * run it from anywhere; paths are relative to the Sage folder. Sage may keep running
      during steps 1-4 (readers are not blocked), but restart it afterwards so it opens
      the updated index.

The embedding model of an existing index is recorded in the knowledge base. Adding
vectors from another model is refused (utils/kb.py): build a new index instead
(--chroma-dir NEW_FOLDER and an empty kb_vector_sync table).

Exit code: 0 = everything asked for was done; 1 = it ran but some tables or records
failed (see the summary); 2 = it could not start (bad arguments, missing key or file).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import utils.early_env  # noqa: E402,F401 - reads the Sage folder's .env before any setting is used
from tools.rebuild_discovery_index import DiscoveryIndexBuilder, RebuildError  # noqa: E402
from utils import kb  # noqa: E402
from utils.errors import SageError  # noqa: E402

#: Statuses that mean "this table's series are not (fully) in the knowledge base yet".
PENDING_STATUSES = ("not_started", "incomplete", "failed")
#: Records embedded per round of the Chroma step (each round is committed).
CHROMA_ROUND = 500
#: Stop the Chroma step after this many rounds without any record being indexed.
MAX_IDLE_ROUNDS = 2


class IndexUpdateError(Exception):
    """The update cannot start or continue; the message is written for the operator."""


@dataclass
class UpdateReport:
    """What one run did. Printed as the summary and used for the exit code."""

    before: dict = field(default_factory=dict)
    after: dict = field(default_factory=dict)
    backup: str | None = None
    tables_added: int = 0
    tables_done: list = field(default_factory=list)
    tables_failed: list = field(default_factory=list)  # (table ID, short reason)
    chroma_indexed: int = 0
    chroma_left: int | None = None
    chroma_error: str | None = None
    optimized: bool = False
    titles_written: int | None = None
    titles_error: str | None = None
    dry_run: bool = False

    @property
    def clean(self) -> bool:
        """True when nothing failed and nothing was left undone."""
        return not (
            self.tables_failed or self.chroma_error or self.chroma_left or self.titles_error
        )


class IndexUpdater:
    """Runs the update steps on one knowledge base and one Chroma folder.

    ``fetch`` (the official-API client), ``open_chroma`` (returns an object with
    ``sync(con, limit=...)``) and ``say`` (prints progress) can be replaced, so the
    steps can be tested without a network, a key or the Chroma library.
    """

    def __init__(
        self,
        db_path,
        chroma_dir,
        *,
        embedding: str = "openai",
        delay: float = 0.5,
        page_size: int = 250,
        observations: bool = False,
        dry_run: bool = False,
        fetch=None,
        open_chroma=None,
        say=print,
    ) -> None:
        """Remember the settings; nothing is opened or changed until a step runs."""
        self.db_path = Path(db_path)
        self.chroma_dir = Path(chroma_dir)
        self.embedding = embedding
        self.delay = max(0.0, float(delay))
        self.page_size = int(page_size)
        self.observations = bool(observations)
        self.dry_run = bool(dry_run)
        self.fetch = fetch or kb.request_json
        self._open_chroma = open_chroma or (lambda: kb.ChromaIndex(self.chroma_dir, self.embedding))
        self.say = say
        self.report = UpdateReport(dry_run=self.dry_run)

    # -- the database ------------------------------------------------------------------
    @contextlib.contextmanager
    def database(self):
        """An open knowledge base, closed afterwards; IndexUpdateError when it cannot be opened.

        A dry run must not create a database that does not exist yet.
        """
        if self.dry_run and not self.db_path.is_file():
            raise IndexUpdateError(f"Knowledge base not found: {self.db_path}")
        try:
            con = kb.open_db(self.db_path)
        except (sqlite3.Error, OSError) as error:
            raise IndexUpdateError(
                f"Cannot open {self.db_path}: {type(error).__name__}: {error}"
            ) from error
        try:
            yield con
        finally:
            con.close()

    def pending_tables(self, con) -> list:
        """IDs of the tables whose series are not complete yet, in ID order."""
        marks = ",".join("?" for _ in PENDING_STATUSES)
        rows = con.execute(
            f"SELECT table_id FROM kb_tables WHERE series_status IN ({marks}) "  # noqa: S608
            "ORDER BY table_id",
            PENDING_STATUSES,
        ).fetchall()
        return [row[0] for row in rows]

    # -- step 1: backup --------------------------------------------------------------------
    def backup(self) -> None:
        """Copy the knowledge base beside itself with SQLite's online backup (safe while in use)."""
        if not self.db_path.is_file():
            self.say("Backup skipped: there is no knowledge base yet.")
            return
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = self.db_path.with_name(f"{self.db_path.stem}.backup-{stamp}{self.db_path.suffix}")
        if self.dry_run:
            self.say(f"Would back up {self.db_path.name} to {target.name}")
            return
        try:
            with contextlib.closing(sqlite3.connect(self.db_path)) as source, contextlib.closing(
                sqlite3.connect(target)
            ) as copy:
                source.backup(copy)
        except (sqlite3.Error, OSError) as error:
            with contextlib.suppress(OSError):
                target.unlink()  # never leave a half-written copy that looks like a backup
            raise IndexUpdateError(
                f"Backup failed ({type(error).__name__}); nothing else was changed."
            ) from error
        self.report.backup = str(target)
        self.say(f"Backup written: {target}")

    # -- step 2: new tables ------------------------------------------------------------------
    def add_from_catalogue(self, con, csv_file) -> None:
        """Add the tables of a catalogue CSV (metadata only; they become pending tables)."""
        if self.dry_run:
            self.say(f"Would import the catalogue {csv_file}")
            return
        try:
            result = kb.import_catalogue_csv(con, csv_file)
        except (OSError, ValueError, KeyError, sqlite3.Error) as error:
            raise IndexUpdateError(
                f"Catalogue {csv_file} could not be imported: {type(error).__name__}: {error}"
            ) from error
        self.say(f"Catalogue imported: {json.dumps(result)}")

    def discover(self, con, keywords) -> None:
        """Ask the official catalogue for tables matching each keyword; add the ones reported."""
        for keyword in keywords:
            if self.dry_run:
                self.say(f"Would search the official catalogue for '{keyword}'")
                continue
            try:
                found = kb.discover_keyword(con, keyword, fetch=self.fetch)
            except (RuntimeError, ValueError, sqlite3.Error) as error:
                # One keyword failing (network, an odd reply) must not stop the others.
                self.say(f"  '{keyword}': failed ({type(error).__name__}: {str(error)[:120]})")
                continue
            self.report.tables_added += int(found or 0)
            self.say(f"  '{keyword}': {found} table record(s) reported")

    # -- step 3: tables --------------------------------------------------------------------
    def update_tables(self, con, extra_tables=()) -> None:
        """Read every pending table (and ``extra_tables``) from the official API, once each."""
        wanted = list(dict.fromkeys([*self._checked_ids(extra_tables), *self.pending_tables(con)]))
        if not wanted:
            self.say("Tables: nothing pending.")
            return
        self.say(f"Tables: {len(wanted)} to read from SingStat.")
        if self.dry_run:
            self.say("  (dry run) " + ", ".join(wanted[:20]) + (" ..." if len(wanted) > 20 else ""))
            return
        for position, table_id in enumerate(wanted, start=1):
            try:
                kb.inspect_table(
                    con,
                    table_id,
                    fetch=self.fetch,
                    page_limit=self.page_size,
                    include_observations=self.observations,
                )
            except KeyboardInterrupt:
                self.say("Stopped by the operator; tables read so far are saved.")
                raise
            except Exception as error:  # noqa: BLE001 - one failing table must not stop the run
                reason = f"{type(error).__name__}: {str(error)[:160]}"
                self.report.tables_failed.append((table_id, reason))
                self.say(f"  [{position}/{len(wanted)}] {table_id} FAILED - {reason}")
            else:
                self.report.tables_done.append(table_id)
                self.say(f"  [{position}/{len(wanted)}] {table_id} ok")
            if self.delay and position < len(wanted):
                time.sleep(self.delay)  # be polite to the official API

    def _checked_ids(self, table_ids) -> list:
        """Validated, upper-case table IDs; IndexUpdateError names the first bad one."""
        checked = []
        for raw in table_ids or ():
            try:
                checked.append(kb.safe_id(raw))
            except ValueError as error:
                raise IndexUpdateError(f"Not a SingStat table ID: {raw!r}") from error
        return checked

    # -- step 4: Chroma --------------------------------------------------------------------
    def records_to_embed(self, con) -> int:
        """How many knowledge-base records are new or changed since they were last embedded."""
        return con.execute(
            "SELECT COUNT(*) FROM kb_fts f LEFT JOIN kb_vector_sync v ON v.doc_id = f.doc_id "
            "WHERE v.doc_id IS NULL"
        ).fetchone()[0]

    def sync_chroma(self, con) -> None:
        """Embed new and changed records in rounds until none is left.

        A failure (no key, no network, another embedding model) ends this step
        with a clear message; the knowledge base itself is already updated and
        the next run continues where this one stopped.
        """
        if self.dry_run:
            self.say(
                f"Chroma: about {self.records_to_embed(con)} new record(s) would be embedded "
                "(changed records are found during the run)."
            )
            return
        try:
            index = self._open_chroma()
        except (SageError, ImportError, ValueError, RuntimeError, OSError) as error:
            self.report.chroma_error = f"{type(error).__name__}: {str(error)[:200]}"
            self.say(f"Chroma: could not open the index - {self.report.chroma_error}")
            return
        idle = 0
        while True:
            try:
                result = index.sync(con, limit=CHROMA_ROUND)
            except KeyboardInterrupt:
                self.say("Stopped by the operator; records embedded so far are saved.")
                raise
            except Exception as error:  # noqa: BLE001 - report and stop this step; nothing is lost
                self.report.chroma_error = f"{type(error).__name__}: {str(error)[:200]}"
                self.say(f"Chroma: stopped - {self.report.chroma_error}")
                return
            indexed, found = int(result.get("indexed", 0)), int(result.get("pending_in_batch", 0))
            self.report.chroma_indexed += indexed
            self.report.chroma_left = found - indexed
            if found == 0:
                self.report.chroma_left = 0
                self.say(
                    f"Chroma: up to date ({result.get('chroma_count')} records; "
                    f"{self.report.chroma_indexed} embedded in this run)."
                )
                return
            self.say(f"Chroma: embedded {self.report.chroma_indexed} so far ...")
            idle = idle + 1 if indexed == 0 else 0
            if idle >= MAX_IDLE_ROUNDS:
                self.report.chroma_error = "no progress in two rounds; stopped"
                self.say("Chroma: " + self.report.chroma_error)
                return

    # -- step 5: title index, optimise --------------------------------------------------------
    def rebuild_titles(self) -> None:
        """Rebuild the title index beside the knowledge base (see rebuild_discovery_index.py).

        A failure is reported and does not undo the steps already done.
        """
        target = self.db_path.with_name("discovery_index.sqlite3")
        builder = DiscoveryIndexBuilder(
            self.db_path, target, force=True, dry_run=self.dry_run, say=self.say
        )
        try:
            result = builder.run()
        except RebuildError as error:
            self.report.titles_error = str(error)
            self.say(f"Title index: not rebuilt - {error}")
            return
        self.report.titles_written = result.written
        if not result.verified:
            self.report.titles_error = "the new title index could not be read back completely"
        self.say(
            f"Title index: {result.written} table(s) "
            + ("would be written." if self.dry_run else "written.")
        )

    def optimize(self) -> None:
        """Merge and compact the keyword index (Sage must be stopped: the file is rewritten)."""
        if self.dry_run:
            self.say(f"Would optimise {self.db_path.name}")
            return
        try:
            with contextlib.closing(sqlite3.connect(self.db_path, timeout=5)) as con:
                con.execute("INSERT INTO kb_fts(kb_fts) VALUES('optimize')")
                con.commit()
                con.execute("ANALYZE")
                con.commit()
                con.execute("VACUUM")
        except sqlite3.Error as error:
            # Usually "database is locked": Sage is still running. Nothing is lost.
            self.say(
                f"Optimise skipped ({type(error).__name__}: {error}). Stop Sage and run "
                "python tools/optimize_indexes.py"
            )
            return
        self.report.optimized = True
        self.say("Keyword index optimised.")

    # -- all steps ---------------------------------------------------------------------------
    def run(
        self,
        *,
        backup: bool = False,
        catalogue=None,
        keywords=(),
        tables=(),
        sqlite_step: bool = True,
        chroma_step: bool = True,
        optimize: bool = False,
        titles: bool = False,
    ) -> UpdateReport:
        """Run the chosen steps in order and return the report (see the module docstring)."""
        if backup:
            self.backup()
        with self.database() as con:
            self.report.before = kb.counts(con)
            if sqlite_step:
                if catalogue:
                    self.add_from_catalogue(con, catalogue)
                if keywords:
                    self.say("Looking for new tables:")
                    self.discover(con, keywords)
                self.update_tables(con, tables)
            if chroma_step:
                self.sync_chroma(con)
            self.report.after = kb.counts(con)
        if titles:
            self.rebuild_titles()
        if optimize:
            self.optimize()
        return self.report


def summary(report: UpdateReport) -> str:
    """The closing summary: counts before and after, and anything that failed."""
    lines = ["", "SUMMARY" + (" (dry run: nothing was changed)" if report.dry_run else "")]
    for name in sorted(set(report.before) | set(report.after)):
        lines.append(f"  {name:28} {report.before.get(name, '-'):>10} -> "
                     f"{report.after.get(name, '-'):>10}")
    lines.append(f"  tables read this run         {len(report.tables_done):>10}")
    lines.append(f"  records embedded this run    {report.chroma_indexed:>10}")
    if report.backup:
        lines.append(f"  backup: {report.backup}")
    if report.tables_failed:
        lines.append(f"  {len(report.tables_failed)} table(s) FAILED and stay incomplete (run "
                     f"again later):")
        lines += [f"    {table_id}: {reason}" for table_id, reason in report.tables_failed[:25]]
        if len(report.tables_failed) > 25:
            lines.append(f"    ... and {len(report.tables_failed) - 25} more")
    if report.chroma_error:
        lines.append(f"  Chroma step did not finish: {report.chroma_error}")
    if report.titles_written is not None:
        lines.append(f"  tables in the title index    {report.titles_written:>10}")
    if report.titles_error:
        lines.append(f"  Title index: {report.titles_error}")
    if not report.dry_run and (report.tables_done or report.chroma_indexed):
        lines.append("  Restart Sage so it opens the updated index.")
    return "\n".join(lines)


def parse_arguments(argv):
    """The command line (see the module docstring)."""
    parser = argparse.ArgumentParser(
        description="Update the SQLite knowledge base and the Chroma index."
    )
    parser.add_argument("--db", default="data/knowledge_base.sqlite3")
    parser.add_argument("--chroma-dir", default="data/chroma_singstat")
    parser.add_argument("--embedding", choices=("openai", "local"), default="openai")
    parser.add_argument("--catalogue", metavar="CSV", help="add tables from a catalogue CSV first")
    parser.add_argument("--discover", nargs="+", default=[], metavar="KEYWORD")
    parser.add_argument("--tables", nargs="+", default=[], metavar="TABLE_ID")
    parser.add_argument("--observations", action="store_true", help="also store observations")
    parser.add_argument("--page-size", type=int, default=250)
    parser.add_argument("--delay", type=float, default=0.5, help="seconds between tables")
    parser.add_argument("--backup", action="store_true")
    parser.add_argument("--optimize", action="store_true", help="stop Sage first")
    parser.add_argument("--titles", action="store_true", help="also rebuild the title index")
    parser.add_argument("--dry-run", action="store_true")
    only = parser.add_mutually_exclusive_group()
    only.add_argument("--sqlite-only", action="store_true")
    only.add_argument("--chroma-only", action="store_true")
    arguments = parser.parse_args(argv)
    if not 1 <= arguments.page_size <= 2500:
        parser.error("--page-size must be between 1 and 2500")
    if arguments.chroma_only and (arguments.catalogue or arguments.discover or arguments.tables):
        parser.error("--chroma-only cannot be combined with --catalogue, --discover or --tables")
    return arguments


def _in_sage_folder(path) -> Path:
    """A path as given when absolute, else inside the Sage folder (not the current folder)."""
    path = Path(path).expanduser()
    return path if path.is_absolute() else ROOT / path


def main(argv=None) -> int:
    """Command line entry point; returns the exit code."""
    arguments = parse_arguments(sys.argv[1:] if argv is None else argv)
    updater = IndexUpdater(
        _in_sage_folder(arguments.db),
        _in_sage_folder(arguments.chroma_dir),
        embedding=arguments.embedding,
        delay=arguments.delay,
        page_size=arguments.page_size,
        observations=arguments.observations,
        dry_run=arguments.dry_run,
    )
    try:
        report = updater.run(
            backup=arguments.backup,
            catalogue=arguments.catalogue,
            keywords=arguments.discover,
            tables=arguments.tables,
            sqlite_step=not arguments.chroma_only,
            chroma_step=not arguments.sqlite_only,
            optimize=arguments.optimize,
            titles=arguments.titles,
        )
    except IndexUpdateError as error:
        print(f"ERROR: {error}")
        return 2
    except KeyboardInterrupt:
        print(summary(updater.report))
        return 1
    print(summary(report))
    return 0 if report.clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
