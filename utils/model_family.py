"""Which OpenAI models are REASONING models? Decided once, here.

Two places need to know:

* utils/token_usage.py: with provider-side storage off (the default), the
  reasoning items a model returns must come back with their encrypted content,
  or the next round of the tool loop is refused by the API;
* utils/model_client.py: only reasoning models take SAGE_REASONING_EFFORT.

Each used to carry its own list of name prefixes ("gpt-5", "o1", "o3", "o4").
A model released under a new name (the next GPT generation, a later o-series
model) matched neither list: answers would then fail on the second tool round
with storage off, and the effort setting would be ignored without a word.

ModelFamily recognises the naming PATTERN instead of a fixed list:

    gpt-5, gpt-5.1, gpt-6, gpt-10-...      GPT generation 5 or later
    o1, o3-mini, o4, o5-...                 the o-series

Earlier chat models (gpt-4.1, gpt-4o, ...) are not reasoning models. A name
that cannot be read is treated as NOT a reasoning model and logged once, so a
typo never adds request options a model may reject.

Pure: no model, network or Streamlit.

Used by: utils/model_client.py, utils/token_usage.py.
Tested in: tests/test_model_family.py.
"""

from __future__ import annotations

import logging
import re

LOG = logging.getLogger("sage")


class ModelNameError(ValueError):
    """A model name was not text, or was empty."""


class ModelFamily:
    """Reads what kind of model a name such as "gpt-5.1" refers to."""

    #: GPT generation 5 or later ("gpt-5", "gpt-5.1", "gpt-6-mini", "gpt-10").
    _GPT_REASONING = re.compile(r"gpt-(?:[5-9]|[1-9]\d+)(?![0-9])", re.I)
    #: The o-series ("o1", "o3-mini", "o4", "o5-preview").
    _O_SERIES = re.compile(r"o[1-9]\d*(?![0-9a-z])", re.I)
    _warned: set = set()

    @classmethod
    def _clean(cls, model) -> str:
        """The trimmed name; ModelNameError when there is none."""
        if not isinstance(model, str) or not model.strip():
            raise ModelNameError("model name must be non-empty text")
        return model.strip()

    @classmethod
    def is_reasoning(cls, model) -> bool:
        """True for a reasoning model (see the module docstring). Never raises."""
        try:
            name = cls._clean(model)
        except ModelNameError:
            marker = repr(type(model).__name__)
            if marker not in cls._warned:
                cls._warned.add(marker)
                LOG.warning("Unreadable model name (%s); treated as a non-reasoning model", marker)
            return False
        return bool(cls._GPT_REASONING.match(name) or cls._O_SERIES.match(name))

    @classmethod
    def accepts_reasoning_effort(cls, model) -> bool:
        """True when SAGE_REASONING_EFFORT may be sent to this model.

        The same models as ``is_reasoning``. A value one of them rejects is
        dropped and the call retried once (utils/model_client.py), so being
        inclusive here cannot break an answer.
        """
        return cls.is_reasoning(model)
