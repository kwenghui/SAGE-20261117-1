"""Start-up and access: environment, OpenAI key, page look, sign-in.

Called at the top of app.main() on every run, in this order:
warn_if_theme_config_missing -> inject_theme_styles -> require_password ->
load_api_key -> ensure_app_path. Each is cheap after the first run of the
process (results are cached or guarded).

Access policy itself lives in utils/auth.py; credentials in utils/credentials.py.

Used by: app.py.
Tested in: tests/test_token_usage.py.
"""

import os
import threading
import sys
from pathlib import Path

from dotenv import load_dotenv
from streamlit.errors import StreamlitAPIException
import streamlit as st

from ui.config import APP_DIR, LOGGER
from utils import (
    auth as sage_auth,
    credentials as sage_credentials,
    process_state as _process_state,
)
from utils.model_client import model_pin_advice
from utils.token_usage import DAILY_BUDGET
from utils.sage_design import install_design_system


def load_environment_once() -> None:
    """Read .env a single time per process (it used to be re-read every rerun)."""
    # Process-wide flag: a flag stored in app.py is reset on every Streamlit rerun, which
    # made .env re-read on every click and let a second key replace the first.
    if not _process_state.first_time("dotenv_loaded"):
        return
    try:
        # only this folder's .env, never one found in a parent folder
        load_dotenv(APP_DIR / ".env")
    except (OSError, UnicodeError):
        LOGGER.warning(".env could not be read; continuing with the process environment")
    try:
        # Parsing st.secrets makes Streamlit publish root-level secrets (e.g.
        # Need_Password, SAGE_PASSWORD_HASH) as environment variables, so the
        # sign-in gate sees them on Streamlit Community Cloud as well.
        st.secrets.get("Need_Password")
    except (FileNotFoundError, OSError, StreamlitAPIException):
        pass  # no secrets.toml: .env / process environment only


def load_api_key():
    """Load the OpenAI credential into process memory and return it.

    Streamlit secrets are preferred, then the environment. The key is held by
    utils.credentials and removed from os.environ, so it is not inherited by
    child processes or captured in environment dumps. A malformed key raises
    CredentialError (fail closed) rather than being sent to the API.
    """
    load_environment_once()
    if sage_credentials.has_openai_api_key() and not (
        os.environ.get("OPENAI_API_KEY") or os.environ.get("KEY")
    ):
        return sage_credentials.get_openai_api_key()
    try:
        secrets_source = {name: st.secrets.get(name) for name in ("OPENAI_API_KEY", "KEY")}
    except (FileNotFoundError, OSError, StreamlitAPIException):
        secrets_source = None  # no secrets.toml is normal for .env development
    return sage_credentials.load_openai_api_key(secrets_source)


def ensure_app_path() -> None:
    """Ensure the application directory is available on Python's import path."""
    app_path = str(APP_DIR)
    if app_path not in sys.path:
        sys.path.insert(0, app_path)


def warn_if_theme_config_missing() -> None:
    """Log once if Streamlit did not load Sage's .streamlit/config.toml.

    Streamlit reads that file from the folder it is STARTED in, not the folder
    of app.py. Started elsewhere, widgets follow the device's dark mode (black
    dropdowns, red radio dots). The CSS covers most of it; this explains why.
    """
    if not _process_state.first_time("theme_and_security_check"):
        return  # once per server process, not once per click
    load_environment_once()  # the self-check must see .env and Streamlit secrets
    run_security_selfcheck()  # once per process, like the theme check
    log_model_pin_advice()
    try:
        base = st.get_option("theme.base")
    except Exception:  # noqa: BLE001 - diagnostics only
        return
    if base != "light":
        LOGGER.warning(
            "Sage's .streamlit/config.toml was not loaded (theme.base=%r). Start "
            "Streamlit "
            "from the Sage folder: cd <Sage folder> && streamlit run app.py",
            base,
        )


def log_model_pin_advice() -> None:
    """Say once per process, in the log, when the answering model is not pinned.

    Advice, not a fault: Sage answers either way. It is kept out of the
    security self-check on purpose, so "0 findings" still means what it did.
    """
    try:
        env = dict(os.environ)
        try:  # settings kept in Streamlit secrets count too
            for name, value in st.secrets.items():
                if isinstance(value, (str, int, float, bool)) and name not in env:
                    env[name] = str(value)
        except Exception:  # noqa: BLE001 - no secrets file is a normal state
            pass
        advice = model_pin_advice(env)
        if advice:
            LOGGER.info("%s See 'how to run.txt' (Model).", advice)
    except Exception as error:  # noqa: BLE001 - advice must never stop the app
        LOGGER.debug("Model pin advice skipped: %s", type(error).__name__)


def run_security_selfcheck() -> None:
    """Log Sage's own configuration findings once per process ("0 findings" when clean)."""
    try:
        from utils.security_selfcheck import run_checks, log_findings

        env = dict(os.environ)
        try:  # Streamlit secrets (e.g. Community Cloud "Secrets") count as configuration too
            for name, value in st.secrets.items():
                if isinstance(value, (str, int, float, bool)) and name not in env:
                    env[name] = str(value)
        except Exception:  # noqa: BLE001 - no secrets file is a normal state
            pass
        log_findings(run_checks(env, get_option=st.get_option), env)
    except Exception as error:  # noqa: BLE001 - a diagnostic must never stop the app
        LOGGER.warning("Security self-check could not run: %s", type(error).__name__)


_WARM_UP_STARTED = threading.Event()


def keep_daily_token_count_on_disk() -> None:
    """Keep today's token count in a file, so a restart does not reset the daily ceiling.

    SAGE_DAILY_TOKEN_FILE names the file (default data/daily_token_usage.sqlite3,
    relative to the Sage folder); "off" keeps the count in memory only. Workers
    that share the file share one ceiling.
    """
    raw = (os.getenv("SAGE_DAILY_TOKEN_FILE", "data/daily_token_usage.sqlite3") or "").strip()
    if raw.lower() in ("", "0", "off", "false", "no"):
        DAILY_BUDGET.keep_on_disk(None)
        return
    path = Path(raw).expanduser()
    DAILY_BUDGET.keep_on_disk(path if path.is_absolute() else Path(APP_DIR) / path)


def start_search_warm_up() -> None:
    """Open the search indexes in the background once per server process.

    The first question used to pay 2-3 s to open the semantic index; now that is
    done while the first visitor is still reading the page. No model call is made.
    Called from app.main AFTER the OpenAI key and the core are loaded (the semantic
    index needs the key to open); two separate warm-up threads used to overlap.
    """
    if _WARM_UP_STARTED.is_set():
        return
    _WARM_UP_STARTED.set()

    def work():
        """Run the warm-up; failures only mean the first question opens the indexes itself."""
        try:
            from utils.hybrid_discovery import warm_up, warm_up_index

            prepared = warm_up()
            prepared["semantic_query"] = warm_up_index()  # one tiny search, no AI call
            LOGGER.info("Search indexes warmed up: %s", prepared)
        except Exception as error:  # noqa: BLE001 - warm-up is an optimisation only
            LOGGER.info("Search warm-up skipped: %s", type(error).__name__)

    threading.Thread(target=work, name="sage-search-warm-up", daemon=True).start()


def inject_theme_styles() -> None:
    """Install the Sage Atelier responsive presentation layer only."""
    install_design_system()


# Access control. Policy lives in utils/auth.py; see SECURITY.md.
# The gate is ON unless Need_Password is explicitly 0/false/no/off, and there is
# no built-in password: SAGE_PASSWORD_HASH (preferred) must be set.
_PASSWORD_GATE = sage_auth.PasswordGate()


def require_password() -> None:
    """Stop the script at the sign-in form until the visitor is authenticated."""
    load_environment_once()
    sage_auth.require_access(st, _PASSWORD_GATE)  # password or SSO (SAGE_AUTH_MODE)


def render_sign_out() -> None:
    """Offer sign-out whenever the gate is active."""
    if _PASSWORD_GATE.enabled() and st.session_state.get(sage_auth.AUTH_KEY):
        if st.sidebar.button("Sign out", key="sage_sign_out", width="stretch"):
            sage_auth.sign_out(st, _PASSWORD_GATE)
            st.rerun()
