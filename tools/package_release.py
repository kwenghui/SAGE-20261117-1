"""Build a release zip of Sage WITHOUT secrets, logs or machine-specific files.

    python tools/package_release.py ../SAGE-release.zip

Two rules decide what goes in, and both must agree:

1. ALLOW-LIST. In a git checkout, only files that git tracks are candidates
   (``git ls-files``). A file nobody committed (a stray key, a log, a trace)
   cannot reach the zip, whatever it is called. Without git (an unpacked
   copy), every file under the Sage folder is a candidate and rule 2 alone
   decides; the tool says which mode it used.
2. DENY-LIST. Anything matched by EXCLUDED is left out even if it is tracked:

   * secrets: .env (in any folder), secrets.toml, .sage_log_key, key and
     certificate files (*.pem, *.key, *.p12, *.pfx);
   * personal data: question logs and debug.txt INCLUDING their rotated and
     temporary copies (debug.txt.1, failed.txt.tmp), search traces, feedback;
   * machine state: caches, databases, coverage data, test progress,
     __pycache__, virtualenvs, other archives.

The earlier version had only a deny-list of exact names, so rotated logs
(debug.txt.1), a .env in a sub-folder, key files and reports/ traces holding
visitors' questions were packaged.

The script prints what it left out, so a reviewer can confirm nothing needed
was dropped and nothing private was added.
"""

import contextlib
import fnmatch
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Relative-path patterns (POSIX form) that must never be packaged. A pattern
#: without "/" is matched against the FILE NAME, so it applies in every folder.
EXCLUDED = (
    # --- secrets ---------------------------------------------------------------
    ".env",
    ".env.*",
    "secrets.toml",
    ".sage_log_key",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
    "service-account.json",
    # --- personal data: logs, their rotations and temporary copies --------------
    "debug.txt",
    "debug.txt.*",
    "questions/failed.txt",
    "questions/failed.txt.*",
    "questions/success.txt",
    "questions/success.txt.*",
    "feedback*",
    "*.log",
    "*.log.*",
    "logs/*",
    "reports/*trace*",  # search traces record the questions that were asked
    "reports/discovery_eval_*",
    # --- machine state ------------------------------------------------------------
    "data/*",  # databases, indexes and caches belong to one server
    "charts/*",
    "*.sqlite3",
    "*.sqlite3-*",
    "test_progress.json",
    "test_reset_once.json",
    "*/__pycache__/*",
    "__pycache__/*",
    "*.pyc",
    "*.pyo",
    ".ruff_cache/*",
    ".pytest_cache/*",
    ".mypy_cache/*",
    ".coverage",
    ".coverage.*",
    "htmlcov/*",
    ".venv/*",
    "venv/*",
    ".git/*",
    "*.tmp",
    "*.bak",
    "*.zip",
    "*.7z",
    "*.rar",
)
#: Exceptions to EXCLUDED (templates that are safe and needed).
ALWAYS_INCLUDED = (".env.example", ".streamlit/secrets.toml.example")


class PackagingError(Exception):
    """The release zip could not be built; the message is written for the operator."""


def is_excluded(relative):
    """True when ``relative`` (POSIX path) must not be packaged.

    A pattern containing "/" is matched against the whole path, and also
    against the path below any parent folder (so "data/*" protects
    "copy/data/x" as well). A pattern without "/" is matched against the file
    name alone, so ".env" or "*.key" is refused in every folder.
    """
    relative = str(relative).replace("\\", "/")
    if relative in ALWAYS_INCLUDED:
        return False
    name = relative.rsplit("/", 1)[-1]
    parts = relative.split("/")
    tails = ["/".join(parts[start:]) for start in range(len(parts))]
    for pattern in EXCLUDED:
        if "/" not in pattern:
            if fnmatch.fnmatch(name, pattern):
                return True
        elif any(fnmatch.fnmatch(tail, pattern) for tail in tails):
            return True
    return False


class ReleasePackager:
    """Chooses the files of a release and writes them to a zip.

    ``root`` is the Sage folder. ``run_git`` runs a git command and returns
    its output (replaceable in tests); it must raise OSError or
    subprocess.SubprocessError when git cannot be used.
    """

    GIT_TIMEOUT_SECONDS = 30

    def __init__(self, root=ROOT, *, run_git=None) -> None:
        """Package the folder ``root`` (default: the Sage folder this tool is in)."""
        self.root = Path(root).resolve()
        self._run_git = run_git or self._git_output
        self.mode = "unknown"  # "git" or "folder", set by candidates()

    # -- which files -------------------------------------------------------------------
    def _git_output(self, *arguments) -> str:
        """Output of ``git <arguments>`` run in the Sage folder."""
        completed = subprocess.run(  # noqa: S603 - fixed arguments, no shell
            ["git", *arguments],  # noqa: S607 - git from the operator's PATH
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=self.GIT_TIMEOUT_SECONDS,
            check=True,
        )
        return completed.stdout

    def tracked_files(self):
        """Files git tracks, as POSIX paths, or None when this is not a usable git checkout."""
        try:
            listing = self._run_git("ls-files", "-z")
        except (OSError, subprocess.SubprocessError):
            return None  # git missing, not a repository, or it timed out
        files = [item for item in listing.split("\0") if item]
        return files or None  # an empty repository gives no allow-list

    def candidates(self) -> list:
        """Relative POSIX paths that MAY be packaged (rule 1 of the module docstring)."""
        tracked = self.tracked_files()
        if tracked is not None:
            self.mode = "git"
            return sorted(path for path in tracked if (self.root / path).is_file())
        self.mode = "folder"
        try:
            return sorted(
                path.relative_to(self.root).as_posix()
                for path in self.root.rglob("*")
                if path.is_file()
            )
        except OSError as error:
            raise PackagingError(f"Cannot read the Sage folder: {type(error).__name__}") from error

    # -- writing -------------------------------------------------------------------------
    def build(self, target):
        """Write the zip; return (included, excluded) relative paths.

        The zip is written to a temporary file beside the target and renamed
        only when complete, so a failure never leaves a half-written release
        that looks finished. Raises PackagingError.
        """
        target = Path(target).resolve()
        if target.is_dir():
            raise PackagingError(f"{target} is a folder; give the name of the zip file.")
        temporary = target.with_name(target.name + ".part")
        skip = {target, temporary}
        included, excluded = [], []
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
                for relative in self.candidates():
                    path = self.root / relative
                    if path.resolve() in skip:
                        continue
                    if is_excluded(relative):
                        excluded.append(relative)
                        continue
                    archive.write(path, relative)
                    included.append(relative)
            if not included:
                raise PackagingError("Nothing to package: no file passed the release rules.")
            temporary.replace(target)
        except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
            raise PackagingError(
                f"Could not write {target.name}: {type(error).__name__}: {error}"
            ) from error
        finally:
            if temporary.exists():
                # If it cannot be removed, the unfinished ".part" file is left for the
                # operator to delete; it is never mistaken for a release (wrong name).
                with contextlib.suppress(OSError):
                    temporary.unlink()
        return included, excluded


def build(target):
    """Write the zip; return (included, excluded) relative paths (see ReleasePackager.build)."""
    return ReleasePackager().build(target)


def main(argv=None):
    """Command line entry point."""
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print(__doc__)
        return 2
    packager = ReleasePackager()
    try:
        included, excluded = packager.build(Path(args[0]))
    except PackagingError as error:
        print(f"ERROR: {error}")
        return 1
    source = (
        "files tracked by git"
        if packager.mode == "git"
        else "every file in the folder (not a git checkout: only the exclusion list protects you)"
    )
    print(f"Packaged {len(included)} files into {args[0]} from {source}.")
    shown = [p for p in excluded if "__pycache__" not in p and ".ruff_cache" not in p]
    print("Left out:", ", ".join(shown) if shown else "nothing besides caches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
