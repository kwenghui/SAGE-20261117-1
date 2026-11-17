"""Make the local search index faster to read (run after rebuilding or updating it).

    python tools/optimize_indexes.py               # data/knowledge_base.sqlite3
    python tools/optimize_indexes.py path\\to\\knowledge_base.sqlite3

* merges the keyword index's many small segments into one (FTS5 'optimize'):
  fewer pages to read per search;
* refreshes SQLite's statistics (ANALYZE) so it picks the best lookup order;
* compacts the file (VACUUM).

Stop Sage first (the file is rewritten). Takes about a minute; nothing is lost.
"""
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main(argv) -> int:
    """Optimise one knowledge-base file and print the size before and after."""
    path = Path(argv[0]) if argv else ROOT / "data" / "knowledge_base.sqlite3"
    if not path.is_file():
        print(f"Not found: {path}")
        return 1
    before, started = path.stat().st_size, time.monotonic()
    con = sqlite3.connect(path)
    try:
        con.execute("INSERT INTO kb_fts(kb_fts) VALUES('optimize')")
        con.commit()
        con.execute("ANALYZE")
        con.commit()
        con.execute("VACUUM")
    finally:
        con.close()
    print(f"{path.name}: {before / 1e6:.0f} MB -> {path.stat().st_size / 1e6:.0f} MB "
          f"in {time.monotonic() - started:.0f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
