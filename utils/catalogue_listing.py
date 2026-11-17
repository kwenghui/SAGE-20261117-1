"""Answer "which tables cover X?" from the local catalogue: table titles only, no figures.

Moved out of utils/core_secure.py unchanged (16 November 2026). These functions
need no AI model and no network: they read a local list of table titles
(data/discovery_index.sqlite3, or data/knowledge_base.sqlite3 when that file
is absent; both read-only) and return official table IDs, titles and Table
Builder page links. Nothing here is numeric evidence.

    CatalogueSource                which local file supplies the titles

    is_table_catalogue_request     is the question asking for a list of tables?
    list_local_singstat_tables     matching tables from the local index

Used by: utils/core_secure.py.
Tested in: tests/test_catalogue_listing.py, tests/test_core_layout.py,
tests/test_rebuild_discovery_index.py.
"""

import logging
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlparse

from utils.official_links import table_page_path
from utils.table_ids import PAGE_TABLE_ID
from utils.query_text import specific_words
from utils.vocabulary import expand_subject

APP_DIR = Path(__file__).resolve().parent.parent  # the Sage folder
logger = logging.getLogger("sage")


class CatalogueMissing(Exception):
    """No local file holds a list of table titles."""


class CatalogueUnreadable(Exception):
    """A catalogue file exists but could not be read (locked, damaged, another layout)."""


class CatalogueSource:
    """Where the list of official table titles is read from.

    Two local files can supply it, tried in this order:

        data/discovery_index.sqlite3   table ``docs``       (the title index)
        data/knowledge_base.sqlite3    table ``kb_tables``  (the search knowledge base)

    Only the first was used before. On a server without that file, "which
    tables cover X?" answered "the index is unavailable" although the knowledge
    base beside it, which the ordinary search was using at that moment, holds
    the same IDs, titles and links. Both are read-only here, and every row is
    still checked by the caller (ID shape, official link) before it is shown.

    A caller that names a file (``index_path``) gets that file only.
    """

    #: (file name, SQL returning table_id, title, source_url)
    KNOWN_FILES = (
        (
            'discovery_index.sqlite3',
            "SELECT DISTINCT table_id, title, source_url FROM docs WHERE "
            "doc_type IS NOT NULL ORDER BY table_id, title",
        ),
        (
            'knowledge_base.sqlite3',
            "SELECT table_id, title, source_url FROM kb_tables ORDER BY table_id, title",
        ),
    )

    def __init__(self, candidates) -> None:
        """``candidates`` is a list of (path, SQL) pairs, tried in order."""
        self.candidates = list(candidates)

    @classmethod
    def for_request(cls, index_path=None) -> "CatalogueSource":
        """The named file (read as a title index), or the Sage folder's known files."""
        if index_path is not None:
            return cls([(Path(index_path), cls.KNOWN_FILES[0][1])])
        return cls([(APP_DIR / 'data' / name, sql) for name, sql in cls.KNOWN_FILES])

    def read(self):
        """(number of tables, rows) from the first file that can be read.

        Raises CatalogueMissing when none of the files exists, and
        CatalogueUnreadable when files exist but none could be read (the cause
        is the last error). A file that fails is skipped for the next one.
        """
        failure = None
        for path, sql in self.candidates:
            if not path.is_file():
                continue
            connection = None
            try:
                # Read-only, no schema changes, no user text interpolated in SQL.
                connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
                rows = connection.execute(sql).fetchall()
            except (sqlite3.Error, OSError) as error:
                failure = error
                logger.warning(
                    'Catalogue file %s unreadable: %s', path.name, type(error).__name__
                )
                continue
            finally:
                if connection is not None:
                    connection.close()
            return len({row[0] for row in rows}), rows
        if failure is not None:
            raise CatalogueUnreadable('no catalogue file could be read') from failure
        raise CatalogueMissing('no catalogue file exists')


# Catalogue-only questions do not request statistical observations or charts.
# Resolve them from the local *metadata index*, never a numeric tool's payload.
def is_table_catalogue_request(question) -> bool:
    """Detect requests for lists of datasets/tables, not their observations."""
    q = str(question or '').casefold()
    return bool(
        re.search(r"\b(?:list|find|show|display|browse|enumerate|which|what|give|available)\b", q)
        and re.search(r"\b(?:tables?|datasets?)\b", q)
        and not re.search(
            r"\b(?:values?|figures?|statistics|graphs?|charts?|plots?|series|calculate)\b", q
        )
    )


def _catalogue_subject_patterns(question):
    """Title search terms for a "which tables cover X?" request (any subject).

    Uses the shared vocabulary expansion when the subject is a known everyday
    phrase, otherwise the question's own specific words. Suggestions only:
    a matching title is never an official definition or evidence.
    """
    # Returns ALTERNATIVES; a title matches when it contains every word of
    # at least one alternative.
    expanded = expand_subject(question)
    if expanded:
        return tuple((term,) for term in expanded)
    listing_words = {
        "list",
        "find",
        "show",
        "display",
        "browse",
        "enumerate",
        "which",
        "what",
        "give",
        "available",
        "tables",
        "table",
        "datasets",
        "dataset",
        "related",
        "about",
        "cover",
        "covers",
        "covering",
        # Ways of asking, not subjects: "which tables CONTAIN x", "tables that HAVE x".
        # Left in, they had to appear in a table's title too, so such a question
        # matched no table at all.
        "contain",
        "contains",
        "containing",
        "include",
        "includes",
        "including",
        "have",
        "has",
        "having",
        "hold",
        "holds",
        "mention",
        "mentions",
        "relate",
        "relates",
        "relating",
        "regarding",
        "concerning",
        "involve",
        "involves",
        "involving",
        "with",
        "there",
        "any",
        "some",
        "all",
        "that",
        "information",
        "info",
        "please",
        "singstat",
    }
    # Do not list the whole catalogue for a vague request: require real subject words.
    words = tuple(sorted(word for word in specific_words(question) if word not in listing_words))
    return (words,) if words else ()


def list_local_singstat_tables(question, index_path=None):
    """Return documented matching table metadata, clearly scoped to the local index.

    Read-only SQLite query: exact stored table IDs/titles and approved SingStat
    URLs only. No interpretation of table titles as numeric observations.
    """

    subject_patterns = _catalogue_subject_patterns(question)
    if not subject_patterns:
        return None
    try:
        total, rows = CatalogueSource.for_request(index_path).read()
    except CatalogueMissing:
        return {
            'answer': 'The local SingStat table-title index is unavailable. '
            'I cannot provide a complete list of matching official tables '
            'without an accessible catalogue.',
            'count': 0,
            'indexed_count': 0,
        }
    except CatalogueUnreadable as error:
        logger.warning('Catalogue index unavailable: %s', type(error.__cause__).__name__)
        return {
            'answer': 'The local SingStat table-title index could not be read. '
            'No table list was invented.',
            'count': 0,
            'indexed_count': 0,
        }
    approved = []
    for table_id, title, source_url in rows:
        if not (
            isinstance(table_id, str)
            and PAGE_TABLE_ID.fullmatch(table_id)
            and isinstance(title, str)
            and isinstance(source_url, str)
        ):
            continue
        parsed = urlparse(source_url)
        if (
            parsed.scheme != 'https'
            or parsed.hostname != 'tablebuilder.singstat.gov.sg'
            or parsed.path != table_page_path(table_id)
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            continue
        lowered_title = title.casefold()
        if any(
            all(word in lowered_title for word in alternative) for alternative in subject_patterns
        ):
            approved.append((table_id, title, source_url))
    # Different RAG metadata entries may refer to the same table. Never show duplicates.
    approved = list({row[0]: row for row in approved}.values())
    approved.sort(key=lambda row: row[0])
    subject = str(question).strip()[:80]
    lines = [
        f'## SingStat tables related to {subject}',
        '',
        f'Found **{len(approved)} matching tables** among **{total} tables in the local Sage '
        f'metadata index**.',
        '',
        '| Table ID | Official indexed title |',
        '|---|---|',
    ]
    for table_id, title, source_url in approved:
        safe_title = title.replace('|', '\\|').replace('\n', ' ')
        lines.append(f'| [{table_id}]({source_url}) | {safe_title} |')
    if not approved:
        lines.append('| — | No titles matched in this local index. |')
    lines.extend(
        [
            '',
            '**Coverage:** This is a list of matching table titles available in '
            'Sage’s local metadata index, **not a guaranteed exhaustive list** '
            'of every table in the live SingStat catalogue. '
            'A matching title does not guarantee that all statistical series '
            'or observations have been downloaded or verified.',
            '',
            'No graph is required for a table-discovery question.',
        ]
    )
    return {'answer': '\n'.join(lines), 'count': len(approved), 'indexed_count': total}
