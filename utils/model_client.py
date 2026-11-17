"""Per-request OpenAI model selection; avoids shared-global model changes.

Used by: app.py, ui/answer_cache.py, ui/sidebar.py, ui/startup.py,
utils/core_config.py.
Tested in: tests/test_model_client.py, tests/test_periods_and_settings.py,
tests/test_small_modules.py.
"""

import logging
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from utils.model_family import ModelFamily
from utils.settings import DEFAULT_CHAT_MODEL, env_flag
from utils.token_usage import api_operation

MODEL_OPTIONS = (
    DEFAULT_CHAT_MODEL,
    "gpt-5.1",
    "gpt-5",
    "gpt-5-mini",
    "gpt-5-nano",
    "gpt-4.1",
    "gpt-4.1-mini",
)
_ACTIVE_MODEL = ContextVar("sage_selected_request_model", default=None)
REASONING_EFFORTS = ("minimal", "low", "medium", "high")


def reasoning_effort(env=None):
    """SAGE_REASONING_EFFORT: optional speed/quality trade-off for GPT-5-family models.

    Unset (the default) leaves the model's own default. "low" answers noticeably sooner;
    "minimal" is fastest but weakest at choosing tables and tools. Unknown values are
    ignored. Which models take the setting is decided in utils/model_family.py.
    """
    env = os.environ if env is None else env
    value = str(env.get("SAGE_REASONING_EFFORT") or "").strip().lower()
    return value if value in REASONING_EFFORTS else None


_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}")


def allowed_models(env=None) -> tuple:
    """The models a question may use: the built-in list plus the operator's SAGE_CHAT_MODEL.

    A model named in SAGE_CHAT_MODEL used to be replaced silently by the first
    built-in one unless it was already in the list, so a newly released model
    could not be used without editing this file.

    With SAGE_LOCK_MODEL=1 the only model is SAGE_CHAT_MODEL (or the default).
    """
    env = os.environ if env is None else env
    configured = str(env.get("SAGE_CHAT_MODEL") or "").strip()
    if configured and not _MODEL_NAME.fullmatch(configured):
        configured = ""
    if env_flag("SAGE_LOCK_MODEL", False, env):
        # One model only: every answer comes from the same (ideally dated) snapshot, so
        # results are repeatable and the sidebar cannot switch to another model.
        return (configured or DEFAULT_CHAT_MODEL,)
    if configured and configured not in MODEL_OPTIONS:
        return (configured, *MODEL_OPTIONS)
    return MODEL_OPTIONS


#: A dated snapshot name ends with its date ("...-2026-08-07"): it never changes.
_DATED_SNAPSHOT = re.compile(r"-\d{4}-\d{2}-\d{2}\Z")


def model_pin_advice(env=None) -> str | None:
    """What still makes answers unrepeatable in this configuration, or None when pinned.

    The same question can only give the same answer over time when ONE model
    answers and that model never changes: SAGE_CHAT_MODEL names a dated
    snapshot and SAGE_LOCK_MODEL=1 refuses every other model. Which snapshot
    to use is the operator's choice, so Sage only reports what is missing
    (ui/startup.py logs it once per process); it never picks one.
    """
    env = os.environ if env is None else env
    configured = str(env.get("SAGE_CHAT_MODEL") or "").strip()
    missing = []
    if not configured:
        missing.append("SAGE_CHAT_MODEL is not set (the built-in default name is used)")
    elif not _MODEL_NAME.fullmatch(configured):
        missing.append("SAGE_CHAT_MODEL is not a valid model name and is ignored")
    elif not _DATED_SNAPSHOT.search(configured):
        missing.append("SAGE_CHAT_MODEL is not a dated snapshot, so the model behind it can change")
    if not env_flag("SAGE_LOCK_MODEL", False, env):
        missing.append("SAGE_LOCK_MODEL is not 1, so other models are not refused")
    if not missing:
        return None
    return "Answers are not repeatable over time: " + "; ".join(missing) + "."


@contextmanager
def selected_model_scope(model):
    """Keep concurrent Streamlit sessions isolated even during API failures."""
    if model not in allowed_models():
        raise ValueError("Unrecognised model")
    token = _ACTIVE_MODEL.set(model)
    try:
        yield
    finally:
        _ACTIVE_MODEL.reset(token)


class _ResponseAdapter:
    """Wraps ``client.responses`` so the model chosen for this request is used."""

    def __init__(self, wrapped) -> None:
        """Wrap the tracked ``responses`` endpoint."""
        self._wrapped = wrapped

    def create(self, **kwargs):
        """Override model only within the current request's selected scope."""
        stage = kwargs.pop("sage_stage", None)
        purpose = kwargs.pop("sage_purpose", None)
        model = _ACTIVE_MODEL.get()
        if model is not None:
            kwargs["model"] = model
        effort = reasoning_effort()
        if (
            effort
            and "reasoning" not in kwargs
            and ModelFamily.accepts_reasoning_effort(kwargs.get("model"))
        ):
            kwargs["reasoning"] = {"effort": effort}

        def call(arguments):
            """Make the API call, labelled with its task when a stage and purpose are given."""
            if stage is not None and purpose is not None:
                with api_operation(stage, purpose):
                    return self._wrapped.create(**arguments)
            return self._wrapped.create(**arguments)

        try:
            return call(kwargs)
        except Exception as error:  # noqa: BLE001 - only a rejected effort value is retried
            if not (
                effort
                and "reasoning" in kwargs
                and getattr(error, "status_code", None) == 400
                and "reasoning" in str(error).lower()
            ):
                raise
            # A setting must never break answers: drop it, retry once, say so in the log.
            logging.getLogger("sage").warning(
                "SAGE_REASONING_EFFORT=%r was rejected for model %s; retrying with the default.",
                effort,
                kwargs.get("model"),
            )
            return call({k: v for k, v in kwargs.items() if k != "reasoning"})


class SageModelClient:
    """Wrap the tracked OpenAI client, preserving usage tracking and embeddings."""

    def __init__(self, tracked_client) -> None:
        """Wrap the token-tracking client; ``responses`` gets Sage's labelling adapter."""
        self._client = tracked_client
        self.responses = _ResponseAdapter(tracked_client.responses)

    def with_options(self, **options):
        """Derived clients keep per-request model scoping and usage tracking."""
        return SageModelClient(self._client.with_options(**options))

    def __getattr__(self, key):
        """Every other attribute comes from the tracked client unchanged."""
        return getattr(self._client, key)
