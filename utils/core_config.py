"""The analytical core's shared settings: OpenAI client, model names, logger.

Imported by utils/core_secure.py and the modules split out of it, so that they
all use ONE client (with token accounting and budgets, see utils/token_usage.py)
and the same model choices.

The OpenAI key normally arrives from the page via utils.credentials (kept in
memory, never in os.environ). When the core runs on its own (command line),
the same helper falls back to OPENAI_API_KEY (or the legacy name KEY) from the
environment or the Sage folder's .env. Importing this module without a key
raises RuntimeError: nothing can run without one.

Used by: utils/core_secure.py, utils/rag_engine.py.
Tested in: tests/test_embedding_model_record.py.
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from utils.credentials import (
    get_openai_api_key,
    has_key_in_memory,
    scrub_key_from_environment,
)
from utils.kb import EMBEDDING_MODEL as _INDEX_EMBEDDING_MODEL
from utils.model_client import SageModelClient
from utils.settings import DEFAULT_CHAT_MODEL
from utils.session_security import bounded_int_env
from utils.token_usage import TrackedOpenAI

# Only the Sage folder's own .env. Without a path, load_dotenv() uses the first .env
# found walking UP from this file's folder (utils\, then the Sage folder, then the
# drive root), so a stray file elsewhere could silently supply an old key.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logger = logging.getLogger("sage")
APP_DIR = Path(__file__).resolve().parent.parent  # the Sage folder

API_KEY = get_openai_api_key()
if has_key_in_memory():
    # load_dotenv above put the key back into os.environ after the page had moved it
    # to process memory; remove it again so child processes and dumps never see it.
    # (Only Sage's own key: another program's KEY variable is left alone.)
    scrub_key_from_environment()
if not API_KEY:
    raise RuntimeError(
        "Missing OpenAI key. Add OPENAI_API_KEY=your_openai_api_key to the .env file."
    )

#: The one model client: every call is labelled, counted and budget-checked.
# One call may take SAGE_OPENAI_TIMEOUT_SECONDS (default 90; reasoning models with tools
# often need more than the old 45 s). ONE retry: three attempts of 45 s could outlast
# the whole question budget, and retried attempts are not counted by the budgets.
OPENAI_TIMEOUT_SECONDS = bounded_int_env("SAGE_OPENAI_TIMEOUT_SECONDS", 90, 15, 600)
client = SageModelClient(
    TrackedOpenAI(OpenAI(api_key=API_KEY, timeout=float(OPENAI_TIMEOUT_SECONDS), max_retries=1))
)

CHAT_MODEL = os.getenv("SAGE_CHAT_MODEL", DEFAULT_CHAT_MODEL)
# ONE definition (utils/kb.py): notes search and table search must use the index's model.
EMBEDDING_MODEL = _INDEX_EMBEDDING_MODEL


def _debug_print(*parts, **_ignored) -> None:
    """Developer diagnostics, sent to the DEBUG logger (never to stdout).

    They may paraphrase visitors' questions, so they follow the operator's
    logging configuration (pseudonymisation, retention) like other logs.
    """
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(" ".join(str(part) for part in parts))
