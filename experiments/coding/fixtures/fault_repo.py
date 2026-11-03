"""Build a deterministic two-repository Git fixture for Coding TRD §22.4.

The command `python experiments/coding/fixtures/fault_repo.py` creates the
history in a temporary directory, checks every fault case, and exits 0.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


AUTHOR = "EviEpoch Fixture"
EMAIL = "fixture@eviepoch.local"
ORIGIN_A = "git@example.com:org/service.git"
ORIGIN_B = "git@example.com:other/service.git"


def _git(repo: Path, *args: str, when: str | None = None) -> str:
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": AUTHOR,
            "GIT_AUTHOR_EMAIL": EMAIL,
            "GIT_COMMITTER_NAME": AUTHOR,
            "GIT_COMMITTER_EMAIL": EMAIL,
            "GIT_AUTHOR_DATE": when or "2026-01-01T00:00:00+0000",
            "GIT_COMMITTER_DATE": when or "2026-01-01T00:00:00+0000",
        }
    )
    completed = subprocess.run(
        [
            "git",
            "-c", "core.autocrlf=false",
            "-c", "user.name=" + AUTHOR,
            "-c", "user.email=" + EMAIL,
            "-c", "core.editor=true",
            *args,
        ],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed\n{completed.stdout}\n{completed.stderr}")
    return completed.stdout.strip()


def _write(repo: Path, relative: str, content: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.replace("\r\n", "\n").encode("utf-8"))


def _commit(repo: Path, message: str, when: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message, when=when)


def _check_base() -> str:
    return """import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pkg.handler import parse_name
assert parse_name(" a ") == "a"
"""


def _check_null() -> str:
    return """import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pkg.handler import parse_name
assert parse_name(" a ") == "a"
try:
    parse_name(None)
except ValueError:
    sys.exit(0)
sys.exit(1)
"""


def _check_renamed() -> str:
    return """import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pkg.handler import parse_input
assert parse_input(" a ") == "a"
try:
    parse_input(None)
except ValueError:
    sys.exit(0)
sys.exit(1)
"""


def _init(repo: Path) -> None:
    repo.mkdir(parents=True)
    _git(repo, "init", "-b", "main")


def _run_check(repo: Path) -> int:
    completed = subprocess.run(
        [sys.executable, "tests/check_handler.py"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    return completed.returncode


def build(root: Path) -> dict[str, Path]:
    repo_a = root / "org-service"
    repo_b = root / "other-service"
    _init(repo_a)
    _init(repo_b)
    _git(repo_a, "remote", "add", "origin", ORIGIN_A)
    _git(repo_b, "remote", "add", "origin", ORIGIN_B)

    _write(repo_a, "pkg/__init__.py", "")
    _write(repo_a, "pkg/handler.py", "def parse_name(value):\n    return value.strip()\n")
    _write(repo_a, "tests/check_handler.py", _check_base())
    _commit(repo_a, "c1 base parse_name", "2026-01-01T00:00:00+0000")

    _write(repo_a, "tests/check_handler.py", _check_null())
    _commit(repo_a, "c2 expect ValueError on null", "2026-01-01T01:00:00+0000")
    c2 = _git(repo_a, "rev-parse", "HEAD")

    _git(repo_a, "branch", "fork", c2)
    _git(repo_a, "checkout", "-b", "attempt", c2)
    _write(
        repo_a,
        "pkg/handler.py",
        "def parse_name(value):\n    if value is None:\n        return \"\"\n    return value.strip()\n",
    )
    _commit(repo_a, "c3 fixed the null crash", "2026-01-01T02:00:00+0000")
    _write(
        repo_a,
        "pkg/handler.py",
        "def parse_name(value):\n    if value is None:\n        raise ValueError(\"null\")\n    return value.strip()\n",
    )
    _commit(repo_a, "c4 raise ValueError for null", "2026-01-01T03:00:00+0000")
    _write(
        repo_a,
        "pkg/handler.py",
        "def parse_input(value):\n    if value is None:\n        raise ValueError(\"null\")\n    return value.strip()\n",
    )
    _write(repo_a, "tests/check_handler.py", _check_renamed())
    _commit(repo_a, "c5 rename parse_name to parse_input", "2026-01-01T04:00:00+0000")
    c5 = _git(repo_a, "rev-parse", "HEAD")

    _git(repo_a, "checkout", "fork")
    _write(repo_a, "pkg/notes.py", "NOTE = \"other module\"\n")
    _commit(repo_a, "c6 fork adds notes only", "2026-01-01T05:00:00+0000")

    _git(repo_a, "checkout", "main")
    _git(repo_a, "merge", "--no-ff", "--no-edit", "attempt", when="2026-01-01T06:00:00+0000")
    _git(repo_a, "revert", "--no-edit", c5, when="2026-01-01T07:00:00+0000")

    _write(repo_b, "pkg/__init__.py", "")
    _write(repo_b, "pkg/handler.py", "def parse_name(value):\n    return value\n")
    _write(
        repo_b,
        "tests/check_handler.py",
        "import pathlib, sys\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))\n"
        "from pkg.handler import parse_name\n"
        "assert parse_name(\" a \") == \" a \"\n",
    )
    _commit(repo_b, "b1 other repository same path", "2026-01-01T00:00:00+0000")
    return {"repo_a": repo_a, "repo_b": repo_b}


def _subject_map(repo: Path) -> dict[str, str]:
    raw = _git(repo, "log", "--all", "--format=%H%x09%s")
    found: dict[str, str] = {}
    for line in raw.splitlines():
        sha, subject = line.split("\t", 1)
        found[subject] = sha
    return found


def _show(repo: Path, sha: str, path: str) -> str:
    return _git(repo, "show", f"{sha}:{path}")


def _tree(repo: Path, sha: str) -> str:
    return _git(repo, "rev-parse", f"{sha}^{{tree}}")


def verify(built: dict[str, Path]) -> None:
    repo_a = built["repo_a"]
    repo_b = built["repo_b"]
    commits = _subject_map(repo_a)
    required = [
        "c1 base parse_name",
        "c2 expect ValueError on null",
        "c3 fixed the null crash",
        "c4 raise ValueError for null",
        "c5 rename parse_name to parse_input",
        "c6 fork adds notes only",
    ]
    missing = [subject for subject in required if subject not in commits]
    if missing:
        raise RuntimeError(f"missing commits: {missing}")
    c2, c3, c4, c5, c6 = (commits[subject] for subject in required[1:])
    merge = _git(repo_a, "log", "main", "--merges", "--format=%H").splitlines()[0]
    parents = _git(repo_a, "rev-parse", f"{merge}^1", f"{merge}^2").splitlines()
    if parents != [c2, c5]:
        raise RuntimeError(f"merge parents {parents} are not c2 then c5")
    if _tree(repo_a, merge) != _tree(repo_a, c5):
        raise RuntimeError("merge tree differs from the attempt tip")
    rollback = _git(repo_a, "rev-parse", "main")
    if _show(repo_a, rollback, "pkg/handler.py") != _show(repo_a, c4, "pkg/handler.py"):
        raise RuntimeError("rollback did not restore the pre-rename handler")
    if _subject_map(repo_a)["c6 fork adds notes only"] != c6:
        raise RuntimeError("fork commit was not retained")
    if "def parse_name" not in _show(repo_a, c6, "pkg/handler.py"):
        raise RuntimeError("fork changed the handler it was meant to leave alone")

    blob_a = _git(repo_a, "rev-parse", f"{c4}:pkg/handler.py")
    blob_b = _git(repo_b, "rev-parse", "HEAD:pkg/handler.py")
    if blob_a == blob_b:
        raise RuntimeError("the two repositories share one handler blob")
    if _git(repo_a, "remote", "get-url", "origin") != ORIGIN_A:
        raise RuntimeError("repo A origin changed")
    if _git(repo_b, "remote", "get-url", "origin") != ORIGIN_B:
        raise RuntimeError("repo B origin changed")
    if _run_check(repo_b) != 0:
        raise RuntimeError("other repository check failed")

    renamed = _show(repo_a, c5, "pkg/handler.py")
    previous = _show(repo_a, c4, "pkg/handler.py")
    if "def parse_input" not in renamed or "def parse_name" in renamed:
        raise RuntimeError("c5 did not rename the symbol")
    if "def parse_name" not in previous or "def parse_input" in previous:
        raise RuntimeError("c4 does not still expose parse_name")

    _git(repo_a, "checkout", "--detach", c3)
    if _run_check(repo_a) == 0:
        raise RuntimeError("the claimed fix at c3 passed its test")
    _git(repo_a, "checkout", "--detach", c4)
    if _run_check(repo_a) != 0:
        raise RuntimeError("the successful fix at c4 failed its test")
    _git(repo_a, "checkout", "--detach", c2)
    if _run_check(repo_a) == 0:
        raise RuntimeError("c2 was expected to fail")
    if _tree(repo_a, c2) == _tree(repo_a, c4):
        raise RuntimeError("c2 and c4 have the same tree")

    diff_path = repo_a.parent / "c4_c5.diff"
    with diff_path.open("wb") as handle:
        completed = subprocess.run(
            ["git", "-c", "core.autocrlf=false", "diff", "--binary", c4, c5],
            cwd=repo_a,
            stdout=handle,
            stderr=subprocess.PIPE,
            check=False,
        )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.decode("utf-8", "replace"))
    _git(repo_a, "checkout", "--detach", c4)
    _git(repo_a, "apply", "--whitespace=nowarn", str(diff_path))
    for relative in ("pkg/handler.py", "tests/check_handler.py"):
        got = _git(repo_a, "hash-object", relative)
        expect = _git(repo_a, "rev-parse", f"{c5}:{relative}")
        if got != expect:
            raise RuntimeError(f"diff round-trip mismatch for {relative}")

    _git(repo_a, "checkout", "--detach", rollback)
    handler = repo_a / "pkg" / "handler.py"
    handler.write_bytes(handler.read_bytes() + b"# dirty overlay\n")
    status = _git(repo_a, "status", "--porcelain")
    if "pkg/handler.py" not in status:
        raise RuntimeError("dirty overlay is not visible")
    if _git(repo_a, "rev-parse", "HEAD") != rollback:
        raise RuntimeError("dirty overlay moved HEAD")


def main() -> int:
    if shutil.which("git") is None:
        print("git is required", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="coding-fault-") as directory:
        built = build(Path(directory))
        verify(built)
        print(json.dumps({"ok": True, "cases": 9}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
