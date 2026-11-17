"""Live progress lines while a question is answered ("Reading SingStat Mxxxxxx ...").

The analysis calls ``report("...")`` at each step; the page decides how to show it
(``progress_sink``). Without a sink, reports are ignored, so the analysis works the
same in tests, tools and the command line. A failing display never stops an answer.

Only plain, short text is reported: step names and official table titles, never
figures (those appear only after they are verified). Every line ends with "…".

Used by: app.py, ui/answer_pipeline.py, utils/core_secure.py,
utils/hybrid_discovery.py.
Tested in: tests/test_speed_features.py.
"""
import logging
from contextlib import contextmanager
from contextvars import ContextVar

_SINK: ContextVar = ContextVar("sage_progress_sink", default=None)
LOGGER = logging.getLogger("sage_ui")


@contextmanager
def progress_sink(show):
    """While inside the block, ``report(text)`` calls ``show(text)``."""
    token = _SINK.set(show)
    try:
        yield
    finally:
        _SINK.reset(token)


def report(text) -> None:
    """Show one progress line, if a page is listening (never raises)."""
    show = _SINK.get()
    if show is None:
        return
    try:
        show(_with_ellipsis(" ".join(str(text).split())[:220]))
    except Exception:  # noqa: BLE001 - a progress line must never stop an answer
        LOGGER.debug("Progress line could not be shown", exc_info=True)


def _with_ellipsis(text: str) -> str:
    """Every step ends with exactly one "…" (no "....", "….", ";…" or ":…")."""
    return text.rstrip(" .…;:,") + "…" if text else text

