"""Load the Sage folder's .env BEFORE any other Sage module reads a setting.

Several limits are read once, when their module is first imported: question
rate limits (utils/session_security.py), sign-in throttling (utils/auth.py),
parallel model calls (utils/request_limits.py) and the debug-log switch
(ui/config.py). app.py used to import those modules first and read .env
afterwards, so values written in .env were silently ignored and the built-in
defaults applied. Importing this module first fixes the order.

Only this folder's .env is read (never one in a parent folder), and values
already set in the real process environment are kept.

Used by: app.py, tools/update_indexes.py.
"""

from pathlib import Path

from dotenv import load_dotenv

_APP_DIR = Path(__file__).resolve().parent.parent


def load() -> bool:
    """Read .env now; False if it is missing or unreadable (the environment is then used)."""
    try:
        return bool(load_dotenv(_APP_DIR / ".env"))
    except (OSError, UnicodeError):
        return False


LOADED = load()  # once per process: modules are imported once
