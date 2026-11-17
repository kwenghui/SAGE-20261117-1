"""Bound concurrent external model calls per Python worker.

This is a process-level safety guard, not distributed per-user rate limiting.

Used by: utils/token_usage.py.
"""

import threading
from contextlib import contextmanager

from utils.session_security import bounded_int_env


MAX_CALLS = bounded_int_env('SAGE_MAX_PARALLEL_MODEL_CALLS', 4, 1, 32)
WAIT_SECONDS = bounded_int_env('SAGE_MODEL_QUEUE_WAIT_SECONDS', 20, 1, 120)
_GATE = threading.BoundedSemaphore(MAX_CALLS)


from utils.errors import ModelCapacityError  # noqa: F401 - re-exported


@contextmanager
def model_call_slot():
    """Context manager: wait for one of the limited concurrent model-call slots."""
    acquired = _GATE.acquire(timeout=WAIT_SECONDS)
    if not acquired:
        raise ModelCapacityError('Sage is busy processing other questions. Please retry shortly.')
    try:
        yield
    finally:
        _GATE.release()
