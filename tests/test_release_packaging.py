"""Release zips must never contain secrets, logs or machine state."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import subprocess  # noqa: E402
import tempfile  # noqa: E402
import zipfile  # noqa: E402

from tools.package_release import PackagingError, ReleasePackager, is_excluded  # noqa: E402


class ReleasePackaging(unittest.TestCase):
    def test_secrets_and_personal_data_are_left_out(self):
        for path in (
            ".env",
            ".env.local",
            ".streamlit/secrets.toml",
            ".sage_log_key",
            "debug.txt",
            "questions/failed.txt",
            "questions/success.txt",
            "data/app_cache.sqlite3",
            "utils/__pycache__/x.pyc",
        ):
            with self.subTest(path=path):
                self.assertTrue(is_excluded(path))

    def test_source_and_templates_are_kept(self):
        for path in (
            "app.py",
            "ui/answer_view.py",
            "utils/core_secure.py",
            ".env.example",
            ".streamlit/secrets.toml.example",
            "questions/questions.txt",
            "assets/sage_theme.css",
        ):
            with self.subTest(path=path):
                self.assertFalse(is_excluded(path))

    def test_rotated_logs_nested_secrets_and_traces_are_left_out(self):
        for path in (
            "debug.txt.1",
            "questions/failed.txt.1",
            "questions/success.txt.tmp",
            "sub/.env",
            "deploy/.env.production",
            "server.key",
            "certs/site.pem",
            "reports/search_function_trace.jsonl",
            "logs/app.log",
            ".ruff_cache/0.6.9/123",
            ".coverage",
            "old/SAGE-backup.zip",
            "copy/data/app_cache.sqlite3",
        ):
            with self.subTest(path=path):
                self.assertTrue(is_excluded(path))

    def test_windows_separators_are_understood(self):
        self.assertTrue(is_excluded("questions\\failed.txt"))
        self.assertFalse(is_excluded("ui\\answer_view.py"))


class ReleasePackagerTests(unittest.TestCase):
    """The packager itself, on a small temporary folder."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "sage"
        (self.root / "utils").mkdir(parents=True)
        (self.root / "app.py").write_text("print('page')\n", encoding="utf-8")
        (self.root / "utils" / "x.py").write_text("X = 1\n", encoding="utf-8")
        (self.root / ".env").write_text("OPENAI_API_KEY=secret\n", encoding="utf-8")
        (self.root / "debug.txt.1").write_text("old log\n", encoding="utf-8")
        (self.root / "stray_notes.txt").write_text("not committed\n", encoding="utf-8")
        self.target = Path(self.tmp.name) / "release.zip"

    def tearDown(self):
        self.tmp.cleanup()

    def names(self):
        with zipfile.ZipFile(self.target) as archive:
            return sorted(archive.namelist())

    def test_git_checkout_packages_only_tracked_files(self):
        tracked = "app.py\0utils/x.py\0.env\0"  # even a tracked secret is refused
        packager = ReleasePackager(self.root, run_git=lambda *a: tracked)
        included, excluded = packager.build(self.target)
        self.assertEqual(packager.mode, "git")
        self.assertEqual(self.names(), ["app.py", "utils/x.py"])
        self.assertEqual(excluded, [".env"])
        self.assertNotIn("stray_notes.txt", included)  # nobody committed it

    def test_without_git_the_exclusion_list_decides(self):
        def no_git(*arguments):
            raise subprocess.CalledProcessError(128, "git")

        packager = ReleasePackager(self.root, run_git=no_git)
        packager.build(self.target)
        self.assertEqual(packager.mode, "folder")
        self.assertEqual(self.names(), ["app.py", "stray_notes.txt", "utils/x.py"])

    def test_a_zip_inside_the_folder_never_contains_itself(self):
        inside = self.root / "out.zip"
        ReleasePackager(self.root, run_git=lambda *a: "").build(inside)
        with zipfile.ZipFile(inside) as archive:
            self.assertNotIn("out.zip", archive.namelist())
            self.assertNotIn("out.zip.part", archive.namelist())

    def test_failures_are_reported_and_leave_no_half_written_zip(self):
        packager = ReleasePackager(self.root, run_git=lambda *a: ".env\0")
        with self.assertRaises(PackagingError):
            packager.build(self.target)  # nothing passed the rules
        self.assertFalse(self.target.exists())
        self.assertFalse(self.target.with_name("release.zip.part").exists())
        with self.assertRaises(PackagingError):
            packager.build(self.root)  # a folder is not a zip name


if __name__ == "__main__":
    unittest.main()
