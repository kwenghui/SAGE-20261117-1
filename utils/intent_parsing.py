"""Reading a question's intent WITHOUT a model: text preparation and the fast path.

Moved out of utils/core_secure.py unchanged, so that file holds the tool loop
and this one holds the small, pure text functions the intent gate needs:

    build_combined_user_input(...)          every user turn as one bounded text
    _without_prompt_markers(text)           remove Sage's own section markers from user text
    _extract_json_object(text)              the first JSON object in a model reply
    _datasets_from_explicit_request(text)   table IDs the user typed (never a year)
    fast_intent_from_combined_user_input()  a decision for an explicit table ID, no model call
    _validated_intent_decision(decision)    normalise a decision the page already made

The model call itself (summarise_and_classify_user_intent) stays in
utils/core_secure.py, next to the client. Every function here raises
ValueError for input it cannot use; the request gate turns that into a safe
refusal. utils/core_secure.py imports these names, so ``core.<name>`` still works.

Pure: no model, network or Streamlit.

Used by: utils/core_secure.py.
Tested in: tests/test_intent_parsing.py.
"""

import json
import re

from utils.settings import env_flag
from utils.tool_arguments import normalize_singstat_table_id


# Maximum size of the full user-only conversation chain sent to the intent gate.
# The Streamlit UI passes every user turn as one plain string.  This generous
# limit prevents accidental unbounded growth while preserving normal sessions.
MAX_COMBINED_USER_INPUT_CHARS = 100000


MAX_INTENT_SUMMARY_CHARS = 2000


# Performance controls. Clear, explicit dataset requests take a deterministic
# local intent path; only ambiguous/context-dependent follow-ups need an extra
# model call. RAG is local by default because this application has only a small
# curated knowledge base.
FAST_INTENT_ENABLED = env_flag("SAGE_FAST_INTENT", True)


_PROMPT_MARKER = re.compile(
    r"(?i)\b(?:ADDITIONAL_)?(?:UNTRUSTED|VALIDATED)_[A-Z_]{0,60}?(?:START|END)\b"
)


def _without_prompt_markers(text) -> str:
    """Untrusted text with Sage's own section markers removed.

    The model is told that text between UNTRUSTED_..._START/END is data. A user
    who types an END marker could otherwise make their words look like they sit
    outside that section.
    """
    return _PROMPT_MARKER.sub("[marker removed]", str(text))


def build_combined_user_input(question, chat_history=None, combined_user_input=None):
    """Return every user turn as one chronological plain-text string.

    The Streamlit UI normally provides ``combined_user_input`` directly.  The
    fallback path reconstructs it from ``chat_history`` plus the current question
    so the command-line interface and direct function callers behave the same way.
    """
    if combined_user_input is not None:
        if not isinstance(combined_user_input, str):
            raise ValueError("Combined user input must be text.")
        combined = combined_user_input.replace("\x00", "").strip()
    else:
        user_turns = []
        for message in chat_history or []:
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                user_turns.append(content.strip())

        # The Streamlit UI may already have appended the current user turn to
        # history.  Add it only when it is not already the newest user message.
        if not user_turns or user_turns[-1] != question.strip():
            user_turns.append(question.strip())

        combined = "\n\n".join(
            f"USER INPUT {index}:\n{content}" for index, content in enumerate(user_turns, start=1)
        )

    if not combined:
        raise ValueError("Combined user input cannot be empty.")
    if len(combined) > MAX_COMBINED_USER_INPUT_CHARS:
        raise ValueError(
            "Conversation is too long to classify safely. Please clear the "
            "conversation and continue with the relevant request."
        )
    return combined


def _extract_json_object(text):
    """Parse the FIRST complete JSON object in model output; anything after it is ignored.

    Taking everything from the first "{" to the last "}" broke when the model
    added a remark containing a brace after the object, or returned two objects.
    The result must be an object (a list or a bare value is refused).
    """
    if not isinstance(text, str):
        raise ValueError("Intent classifier returned non-text output.")
    start = text.find("{")
    if start < 0:
        raise ValueError("Intent classifier did not return JSON.")
    data, _end = json.JSONDecoder().raw_decode(text, start)
    if not isinstance(data, dict):
        raise ValueError("Intent classifier did not return a JSON object.")
    return data


_CALENDAR_YEAR = re.compile(r"(?:18|19|20|21)\d{2}")


def _datasets_from_explicit_request(text):
    """Return SingStat table IDs the user typed explicitly (e.g. "Mxxxxxx").

    There is deliberately no keyword-to-table routing: questions that do not
    name a table are matched by the general discovery step, which treats every
    table the same way.
    """
    if not isinstance(text, str):
        return []
    matched = []
    # A lettered ID has one letter and 5-8 digits (utils/table_ids.py). Shorter tokens
    # such as "C19" or "M2020" are ordinary words, not tables.
    for token in re.findall(r"(?i)(?<![A-Za-z0-9])[MDC][0-9]{5,8}(?![A-Za-z0-9])", text):
        token = token.upper()
        if token not in matched:
            matched.append(token)
    # An all-digit ID counts only when the user labels it as one ("table 17581",
    # "dataset id: 17581"). The product name alone is not a label, and a calendar
    # year or the start of a range of years is never an ID: "the table 2015 to 2020"
    # and "SingStat 2024 figures" used to yield the tables "2015" and "2024".
    for found in re.finditer(
        r"(?i)\b(?:table|dataset)\s*(?:id|no\.?|number)?\s*[:#-]?\s*([0-9]{3,20})\b", text
    ):
        token = found.group(1)
        if _CALENDAR_YEAR.fullmatch(token):
            continue
        if token not in matched:
            matched.append(token)
    return matched


def fast_intent_from_combined_user_input(combined_user_input, latest_question):
    """Return a deterministic decision for explicit SingStat/table requests."""
    if (
        not FAST_INTENT_ENABLED
        or not isinstance(combined_user_input, str)
        or not combined_user_input.strip()
    ):
        return None
    if not isinstance(latest_question, str) or not latest_question.strip():
        return None
    relevant_datasets = _datasets_from_explicit_request(latest_question)
    # Only an explicit table ID takes the fast path. Naming the product or the source
    # is not a statistics request ("use Table Builder to draft my letter" was marked
    # in scope without any check): such questions go to the AI intent gate instead.
    if not relevant_datasets:
        return None
    return {
        "summary": " ".join(latest_question.strip().split())[:MAX_INTENT_SUMMARY_CHARS],
        "in_scope": True,
        "relevant_datasets": relevant_datasets,
        "reason": "Explicit SingStat table ID identified locally.",
        "classification_path": "local_fast_path",
    }


def _validated_intent_decision(intent_decision):
    """Normalise a UI-previewed Sage intent decision before reusing it."""
    if not isinstance(intent_decision, dict):
        return None
    summary = str(intent_decision.get("summary", "")).strip()[:MAX_INTENT_SUMMARY_CHARS]
    raw_datasets = intent_decision.get("relevant_datasets", [])
    if not isinstance(raw_datasets, list):
        raw_datasets = []
    datasets = []
    for dataset_id in raw_datasets:
        try:
            dataset_id = normalize_singstat_table_id(dataset_id)
        except ValueError:
            continue
        if dataset_id not in datasets:
            datasets.append(dataset_id)
    in_scope = intent_decision.get("in_scope") is True
    if not summary:
        summary = "The current request could not be summarised safely."
    return {
        "summary": summary,
        "in_scope": in_scope,
        "relevant_datasets": datasets if in_scope else [],
        "reason": str(intent_decision.get("reason", "Scope decision completed."))[:1000],
        "classification_path": str(intent_decision.get("classification_path", "reused_ui_preview"))[
            :100
        ],
    }
