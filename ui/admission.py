"""Rate limits for a question, charged ONCE, before anything that costs money.

A typed question can trigger two paid stages: source discovery (one model
call that reads the question) and then the answer itself. The rate limits
used to be checked only at the start of the answer, so discovery ran before
them: every Submit bought a model call even when the limiter would have
refused the question a moment later.

QuestionAdmission is the one place that charges the limiters:

    admission = QuestionAdmission(st.session_state)
    admission.reserve(question)              before discovery: charge, keep a ticket
    admission.charge_unless_reserved(q)      before the answer: use the ticket, or charge

The ticket is a keyed digest of the question plus a time, held in the
visitor's own session. It lets the answer stage see that this exact question
was already charged a moment ago, so one question costs one unit of the limit
whether or not discovery ran, and whether or not the visitor had to choose a
table in between. It expires after TICKET_SECONDS and is used once.

Both methods raise utils.errors.RateLimitExceeded (its ``user_message`` is
written for visitors). Limits are per process, as before (see SECURITY.md).

Used by: ui/answer_pipeline.py.
Tested in: tests/test_admission.py.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time

from utils import auth as sage_auth
from utils.errors import RateLimitExceeded
from utils.session_security import CLIENT_LIMITER, LIMITER

LOGGER = logging.getLogger("sage_ui")

#: Session-state key of the security session identifier (owned by utils/auth.py).
SESSION_KEY = sage_auth.SECURITY_SESSION_KEY
#: Session-state key of the "already charged" ticket (owned by this module).
TICKET_KEY = "_sage_admission_ticket"
#: How long a ticket stays valid: long enough to choose a table, short enough
#: that it cannot be saved up.
TICKET_SECONDS = 600


class QuestionAdmission:
    """Charges the per-session and per-client rate limits for one question.

    ``state`` is the visitor's session state (any mutable mapping). The
    limiters, the client lookup and the clock can be replaced in tests.
    """

    def __init__(
        self,
        state,
        *,
        session_limiter=LIMITER,
        client_limiter=CLIENT_LIMITER,
        client_id=None,
        clock=time.monotonic,
    ) -> None:
        """Bind to one visitor's session; ``client_id`` is a callable returning the client key."""
        self.state = state
        self.session_limiter = session_limiter
        self.client_limiter = client_limiter
        self._client_id = client_id
        self.clock = clock

    # -- public -----------------------------------------------------------------------
    def reserve(self, question) -> None:
        """Charge the limits now and remember that this question was charged.

        Call before discovery. Raises RateLimitExceeded when over a limit; no
        ticket is kept in that case.
        """
        self._charge()
        self.state[TICKET_KEY] = (self._digest(question), self.clock())

    def charge_unless_reserved(self, question) -> None:
        """Charge the limits unless ``reserve`` already did so for this exact question.

        The ticket is removed either way, so it can never pay for two answers.
        Raises RateLimitExceeded when over a limit.
        """
        ticket = self.state.pop(TICKET_KEY, None) if TICKET_KEY in self.state else None
        if self._ticket_valid(ticket, question):
            return
        self._charge()

    # -- internals ----------------------------------------------------------------------
    def _ticket_valid(self, ticket, question) -> bool:
        """True for an unexpired ticket of this same question; anything malformed is refused."""
        try:
            digest, issued = ticket
            return (
                isinstance(digest, str)
                and secrets.compare_digest(digest, self._digest(question))
                and 0 <= self.clock() - float(issued) <= TICKET_SECONDS
            )
        except (TypeError, ValueError):
            return False  # no ticket, or not one of ours: charge normally

    def _session_id(self) -> str:
        """The server-generated session identifier (created on first use, never from the browser)."""
        session_id = self.state.get(SESSION_KEY)
        if not isinstance(session_id, str) or not session_id:
            session_id = secrets.token_urlsafe(32)
            self.state[SESSION_KEY] = session_id
        return session_id

    def _digest(self, question) -> str:
        """A digest of the question bound to this session (the text itself is not kept)."""
        text = " ".join(str(question or "").split())
        return hashlib.sha256((self._session_id() + "\x1f" + text).encode("utf-8")).hexdigest()

    def _client(self) -> str:
        """The pseudonymous client key, or UNKNOWN_CLIENT when it cannot be worked out."""
        if self._client_id is None:
            return sage_auth.UNKNOWN_CLIENT
        try:
            return str(self._client_id() or sage_auth.UNKNOWN_CLIENT)
        except (AttributeError, RuntimeError, KeyError, TypeError) as error:
            # Without an address the shared bucket must not be limited (see below).
            LOGGER.debug("Client address unavailable (%s)", type(error).__name__)
            return sage_auth.UNKNOWN_CLIENT

    def _charge(self) -> None:
        """Count one question against the session, then against the client address.

        A new tab is a new session, so the client is limited as well. An
        unknown address is a bucket shared by everyone and is never limited:
        one person could otherwise lock every visitor out.
        """
        try:
            self.session_limiter.check(self._session_id())
        except ValueError as error:
            # A malformed identifier is a defect, not a visitor's fault: refuse safely.
            LOGGER.error("Session identifier rejected by the rate limiter")
            raise RateLimitExceeded(
                "invalid session identifier",
                user_message="Please reload the page and try again.",
            ) from error
        client = self._client()
        if client != sage_auth.UNKNOWN_CLIENT:
            self.client_limiter.check(client)
