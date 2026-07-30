"""Grading adapter protocol plus the git/file helpers the family adapters share."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from runner.spec import RunnerError

GradeResult = dict[str, Any]
"""{"success": bool | None, "detail": {...}} — None means "could not determine"."""


@dataclass(frozen=True)
class GradeContext:
    """Everything an adapter may need beyond the task and the workspace.

    Passed to both materialize() and grade(): materialize needs `tasks_dir` to locate
    bundled task data, grade needs `raw_dir`/`run_id` to write harness artifacts.
    """

    run_id: str
    raw_dir: Path
    study_root: Path
    tasks_dir: Path
    timeout_s: int


class GradingAdapter(Protocol):
    """A family adapter (implemented as a module) that prepares and grades one task."""

    TASK_ID_FIELD: str

    def materialize(
        self, task: dict[str, Any], workspace_dir: Path, ctx: GradeContext
    ) -> None:
        """Put the task's inputs into an already-created, git-inited workspace."""

    def grade(
        self, task: dict[str, Any], workspace_dir: Path, ctx: GradeContext
    ) -> GradeResult:
        """Judge the finished workspace deterministically."""


def run_cmd(
    cmd: list[str], cwd: Path | None, timeout_s: int
) -> subprocess.CompletedProcess[str]:
    """Run a command with captured output, closed stdin and a mandatory timeout."""
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )


def git(args: list[str], cwd: Path, timeout_s: int) -> subprocess.CompletedProcess[str]:
    """Run a git command in `cwd`, raising RunnerError with stderr on failure."""
    result = run_cmd(["git", *args], cwd, timeout_s)
    if result.returncode != 0:
        raise RunnerError(
            f"git {' '.join(args)} failed in {cwd} "
            f"(exit {result.returncode}): {result.stderr.strip() or result.stdout.strip()}"
        )
    return result


def shallow_clone(repo_url: str, commit: str, dest: Path, timeout_s: int) -> None:
    """Fetch exactly `commit` from `repo_url` into the git repo at `dest` and check it out.

    Assumes `dest` is an existing (possibly empty) git repo — WorkspaceManager git-inits it,
    so the repo lands at the workspace root and `git diff` there yields the model patch.
    Shortcut: single shallow fetch of one sha; ceiling is servers with uploadpack.allowReachableSHA1
    disabled — upgrade path is a `--depth 50` branch fetch followed by checkout.
    """
    if not (dest / ".git").is_dir():
        git(["init", "-q"], dest, timeout_s)
    remotes = git(["remote"], dest, timeout_s).stdout.split()
    if "origin" in remotes:
        git(["remote", "set-url", "origin", repo_url], dest, timeout_s)
    else:
        git(["remote", "add", "origin", repo_url], dest, timeout_s)
    git(["fetch", "--depth", "1", "origin", commit], dest, timeout_s)
    git(["checkout", "-q", "--detach", "FETCH_HEAD"], dest, timeout_s)


def read_answer_file(workspace_dir: Path, filename: str = "ANSWER.txt") -> str | None:
    """Return the answer file's text, or None when the agent never wrote it."""
    path = workspace_dir / filename
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def require(task: dict[str, Any], key: str, task_label: str) -> Any:
    """Fetch a required task field or raise a message naming the task and field."""
    if key not in task or task[key] in (None, ""):
        raise RunnerError(f"task '{task_label}' is missing required field '{key}'")
    return task[key]
