"""One question is charged against the rate limits once, before any paid step
(ui/admission.py), and source discovery uses the Sage folder's own index."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import _stand_ins  # noqa: E402

_stand_ins._install_streamlit_stand_in()

from ui import config as ui_config  # noqa: E402
from ui.admission import TICKET_KEY, TICKET_SECONDS, QuestionAdmission  # noqa: E402
from utils.errors import RateLimitExceeded  # noqa: E402
from utils.session_security import SessionRateLimiter  # noqa: E402


class _Clock:
    """A clock the test moves by hand."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _admission(state, *, limit=2, clock=None, client="client-" + "a" * 24):
    clock = clock or _Clock()
    return QuestionAdmission(
        state,
        session_limiter=SessionRateLimiter(limit=limit, clock=clock),
        client_limiter=SessionRateLimiter(limit=100, clock=clock, min_key_length=14),
        client_id=lambda: client,
        clock=clock,
    )


class ChargedOnce(unittest.TestCase):
    def test_discovery_then_answer_costs_one_unit(self):
        state = {}
        gate = _admission(state, limit=1)
        gate.reserve("How many residents?")          # before discovery
        gate.charge_unless_reserved("How many residents?")  # before the answer: not charged
        self.assertNotIn(TICKET_KEY, state)
        with self.assertRaises(RateLimitExceeded):
            gate.charge_unless_reserved("How many residents?")  # the ticket was used up

    def test_refused_before_discovery_when_over_the_limit(self):
        gate = _admission({}, limit=1)
        gate.reserve("first question")
        with self.assertRaises(RateLimitExceeded):
            gate.reserve("second question")

    def test_a_ticket_only_pays_for_its_own_question(self):
        state = {}
        gate = _admission(state, limit=2)
        gate.reserve("question one")
        gate.charge_unless_reserved("question two")  # charged: a different question
        with self.assertRaises(RateLimitExceeded):
            gate.charge_unless_reserved("question three")

    def test_a_ticket_expires(self):
        clock = _Clock()
        state = {}
        gate = _admission(state, limit=5, clock=clock)
        gate.reserve("question")
        clock.now += TICKET_SECONDS + 1
        self.assertFalse(gate._ticket_valid(state[TICKET_KEY], "question"))

    def test_a_forged_or_malformed_ticket_is_ignored(self):
        for forged in ("text", 5, ("digest",), ("digest", "soon"), None):
            state = {TICKET_KEY: forged}
            gate = _admission(state, limit=1)
            gate.charge_unless_reserved("question")  # charged normally, no crash
            with self.assertRaises(RateLimitExceeded):
                gate.charge_unless_reserved("question")

    def test_unknown_client_is_not_limited_as_one_person(self):
        clock = _Clock()
        shared = SessionRateLimiter(limit=1, clock=clock, min_key_length=14)
        for _ in range(3):  # three different sessions, no address
            QuestionAdmission({}, session_limiter=SessionRateLimiter(limit=5, clock=clock),
                              client_limiter=shared, client_id=lambda: "client-unknown",
                              clock=clock).reserve("question")

    def test_a_failing_client_lookup_does_not_stop_the_question(self):
        def broken():
            raise RuntimeError("no context")

        clock = _Clock()
        QuestionAdmission({}, session_limiter=SessionRateLimiter(limit=5, clock=clock),
                          client_limiter=SessionRateLimiter(limit=5, clock=clock),
                          client_id=broken, clock=clock).reserve("question")


class DiscoveryIndexPath(unittest.TestCase):
    def test_the_index_is_in_the_sage_folder_not_in_ui(self):
        self.assertEqual(ui_config.DISCOVERY_INDEX_FILE,
                         ROOT / "data" / "discovery_index.sqlite3")
        source = (ROOT / "ui" / "question_form.py").read_text(encoding="utf-8")
        self.assertIn("DISCOVERY_INDEX_FILE", source)
        self.assertNotIn("parent / 'data' / 'discovery_index.sqlite3'", source)

    def test_discovery_runs_inside_the_budget_after_the_rate_limit(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        phase = source[source.index("def render_answer_phase"):]
        self.assertLess(phase.index("with usage_scope()"), phase.index("_discovery_step(deferred)"))
        step = source[source.index("def _discovery_step"):source.index("def render_answer_phase")]
        self.assertLess(step.index(".reserve(question)"), step.index("run_deferred_discovery("))


if __name__ == "__main__":
    unittest.main()
