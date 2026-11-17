"""Debug capture writes one record and never makes questions wait for each other
(ui/debug_capture.py)."""

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import _stand_ins  # noqa: E402

_stand_ins._install_streamlit_stand_in()

from ui import config as ui_config  # noqa: E402
from ui import debug_capture  # noqa: E402


class NoProcessWideLock(unittest.TestCase):
    def test_two_questions_can_be_captured_at_the_same_time(self):
        """The old capture held one lock for the whole answer: the second question waited."""
        inside, release, finished = threading.Event(), threading.Event(), threading.Event()

        def first_question():
            with debug_capture.capture_query_debug("first question"):
                inside.set()
                release.wait(5)

        def second_question():
            with debug_capture.capture_query_debug("second question"):
                finished.set()

        with mock.patch.object(debug_capture, "SAGE_FULL_DEBUG_LOG", False):
            first = threading.Thread(target=first_question)
            first.start()
            self.assertTrue(inside.wait(5))
            second = threading.Thread(target=second_question)
            second.start()
            self.assertTrue(finished.wait(5), "the second question waited for the first")
            release.set()
            first.join(5)
            second.join(5)
        self.assertFalse(hasattr(ui_config, "DEBUG_CONSOLE_LOCK"))

    def test_console_streams_are_left_alone(self):
        before = (sys.stdout, sys.stderr)
        with mock.patch.object(debug_capture, "SAGE_FULL_DEBUG_LOG", False):
            with debug_capture.capture_query_debug("question"):
                self.assertEqual((sys.stdout, sys.stderr), before)

    def test_the_thread_is_registered_only_while_answering(self):
        with mock.patch.object(debug_capture, "SAGE_FULL_DEBUG_LOG", False):
            with debug_capture.capture_query_debug("question"):
                self.assertIn(threading.get_ident(), debug_capture.ACTIVE_DEBUG_QUERY_THREADS)
        self.assertNotIn(threading.get_ident(), debug_capture.ACTIVE_DEBUG_QUERY_THREADS)


class RecordFailuresNeverReplaceTheAnswer(unittest.TestCase):
    def test_the_questions_own_error_is_not_hidden(self):
        with mock.patch.object(debug_capture, "SAGE_FULL_DEBUG_LOG", False):
            with self.assertRaises(KeyError):
                with debug_capture.capture_query_debug("question"):
                    raise KeyError("the question failed")

    def test_a_record_that_cannot_be_built_is_logged_not_raised(self):
        def broken(*_arguments):
            raise TypeError("malformed answer")

        with mock.patch.object(debug_capture, "_debug_record", broken), \
                mock.patch.object(debug_capture.LOGGER, "error") as logged:
            with debug_capture.capture_query_debug("question"):
                pass
        logged.assert_called_once()

    def test_one_record_is_written_when_full_logging_is_on(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "debug.txt"
            with mock.patch.object(debug_capture, "DEBUG_LOG_FILE", target), \
                    mock.patch.object(debug_capture, "SAGE_FULL_DEBUG_LOG", True):
                with debug_capture.capture_query_debug("How many residents?"):
                    print("console text during the question")
            record = target.read_text(encoding="utf-8")
        self.assertEqual(record.count("Question digest:"), 1)
        self.assertNotIn("How many residents?", record)
        self.assertNotIn("console text during the question", record)


if __name__ == "__main__":
    unittest.main()
