"""Single entry point for Sage request security, intent and scope gating.

Called once per question, as STEP 1 of ``core_secure.ask_sage``. It answers
one question: "may Sage work on this request at all?"

Where are the checks themselves? The real checks (injection patterns, the
intent classifier, the standard refusal messages) live in ``core_secure.py``.
That file imports this one, so this one cannot import it back (a circular
import). Instead the core hands them over as ONE object, GateChecks, built
once. This file only fixes the ORDER in which they run.

No model prompts or security policies live here: existing callbacks are used
in their original order. The caller owns all downstream discovery and evidence.

Used by: utils/core_secure.py.
Tested in: tests/test_claim_binding.py, tests/test_security.py.
"""

from dataclasses import dataclass
import re
from typing import Callable, Optional
from utils.request_security import prepare_request
from utils.intent_resolution import IntentResolutionError, resolve_request_intent
from utils.vocabulary import CLARIFICATION_TOPICS


@dataclass(frozen=True)
class RequestGateResult:
    """Outcome of the gate. If ``blocked_response`` is not None, return it to
    the user as-is and do nothing else; otherwise continue with the other fields."""

    question: str
    combined_input: Optional[str]
    intent_decision: Optional[dict]
    blocked_response: Optional[dict]


# Clarification is a user-experience response only. It cannot override the
# existing injection/security checks or mark a request in scope.
_TOPIC_HINTS = CLARIFICATION_TOPICS  # vocabulary lives in utils/vocabulary.py


def _clarification_if_ambiguous(question, reason, response):
    """A "did you mean Singapore's <topic>?" reply for a short question hinting at one topic, else
    the refusal."""
    if reason in ("intent_classification_failed", "security_blocked", "prompt_injection"):
        return response
    clean = re.sub(r"\s+", " ", question.strip().lower())
    # Never reinterpret an obvious unrelated question as a SingStat request.
    if not clean or len(clean.split()) > 12:
        return response
    hints = [
        label
        for label, words in _TOPIC_HINTS
        if any(re.search(r"(?<![a-z])" + re.escape(word) + r"(?![a-z])", clean) for word in words)
    ]
    if len(hints) != 1:
        return response
    result = dict(response)
    result["answer"] = (
        "Are you asking about Singapore's "
        + hints[0]
        + " statistics? If so, you can say what you would like to know "
        "(for example, how it has changed over time), or rephrase your question. "
        "I won't assume which figures you mean until your intention is clearer."
    )
    result["clarification_requested"] = True
    return result


_MSG_AI_UNAVAILABLE = (
    "Sage could not reach its AI service to read your question, so nothing was "
    "looked up. Your question was not rejected; please try again in a moment."
)


def _service_unavailable(response):
    """The refusal shown when the intent check itself FAILED (network, provider error).

    Still a blocked response (no analysis runs, nothing is shown as verified),
    but it no longer claims the question is outside Sage's scope.
    """
    result = dict(response)
    result["answer"] = _MSG_AI_UNAVAILABLE
    result["service_unavailable"] = True
    return result


class GateConfigurationError(TypeError):
    """A gate was built with something that is not a function (a programming error)."""


@dataclass(frozen=True)
class GateChecks:
    """The checks the gate runs, supplied by the caller (utils/core_secure.py).

    The gate used to take these as eleven separate keyword arguments. They
    always travel together and never change between questions, so they are
    one object now: the core builds it once and each call passes the question
    and its context only.

    Safety (step 1):
        sanitize(text) -> text              clean the question or raise ValueError
        is_injection(text) -> bool          high-confidence injection wording
        security_response(reason) -> dict   the fixed refusal for unsafe input
        combine(question, chat_history=..., combined_user_input=...) -> text
        on_injection()                      called when injection is refused (logging)
    Intent (step 2):
        validate(decision) -> dict | None   normalise a decision the page already made
        fast_classify(combined, question)   decision without a model call, or None
        ai_classify(combined) -> dict       decision from the model
        on_intent_error()                   called when classification failed (logging)
    Scope (step 3):
        out_of_scope(summary=..., reason=...) -> dict   the standard refusal
        on_out_of_scope()                   called when a request is refused (logging)
    """

    sanitize: Callable
    is_injection: Callable
    security_response: Callable
    combine: Callable
    on_injection: Optional[Callable]
    validate: Callable
    fast_classify: Callable
    ai_classify: Callable
    out_of_scope: Callable
    on_intent_error: Callable
    on_out_of_scope: Callable

    def __post_init__(self) -> None:
        """Refuse a gate with a missing check: every one of them is a safeguard."""
        for name, value in vars(self).items():
            if name == "on_injection" and value is None:
                continue  # optional: it only writes a log line
            if not callable(value):
                raise GateConfigurationError(f"gate check {name!r} must be a function")


def gate_request(question, *, chat_history, combined_user_input, intent_decision, checks):
    """Run the security and intent gates, with no discovery side effects.

    Order: (1) sanitise and look for injection in the new question and the
    whole conversation; (2) classify intent: reuse the caller's decision if
    valid, else a fast keyword rule for explicit table requests, else one AI
    call; (3) refuse out-of-scope requests, offering a clarification when a
    short question clearly hints at one statistics topic.
    """
    # (1) Input safety: sanitise text and block prompt/code injection.
    prepared = prepare_request(
        question,
        chat_history=chat_history,
        combined_user_input=combined_user_input,
        sanitize=checks.sanitize,
        is_injection=checks.is_injection,
        security_response=checks.security_response,
        combine=checks.combine,
        on_injection=checks.on_injection,
    )
    if prepared.blocked_response is not None:
        return RequestGateResult(
            prepared.question, prepared.combined_input, None, prepared.blocked_response
        )
    # (2) Is this a Singapore-statistics request, and which tables are relevant?
    try:
        decision = resolve_request_intent(
            intent_decision,
            prepared.combined_input,
            prepared.question,
            validate=checks.validate,
            fast_classify=checks.fast_classify,
            ai_classify=checks.ai_classify,
        )
    except IntentResolutionError:
        checks.on_intent_error()
        return RequestGateResult(
            prepared.question,
            prepared.combined_input,
            None,
            _service_unavailable(
                checks.out_of_scope(
                    summary="The current request could not be classified " "safely.",
                    reason="intent_classification_failed",
                )
            ),
        )
    # (3) Polite refusal (or clarification question) for anything else.
    if not decision['in_scope'] and decision.get('reason') == 'intent_classification_failed':
        # The page's own intent preview failed (see core_secure.preview_user_intent).
        return RequestGateResult(
            prepared.question,
            prepared.combined_input,
            decision,
            _service_unavailable(
                checks.out_of_scope(summary=decision['summary'],
                                    reason='intent_classification_failed')
            ),
        )
    if not decision['in_scope']:
        checks.on_out_of_scope()
        return RequestGateResult(
            prepared.question,
            prepared.combined_input,
            decision,
            _clarification_if_ambiguous(
                prepared.question,
                decision.get('reason', 'out_of_scope'),
                checks.out_of_scope(
                    summary=decision['summary'], reason=decision.get('reason', 'out_of_scope')
                ),
            ),
        )
    return RequestGateResult(prepared.question, prepared.combined_input, decision, None)
