"""Comprehension family: clone a repo, ask one question, match ANSWER.txt against a key."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from runner.grading.base import (
    GradeContext,
    GradeResult,
    read_answer_file,
    require,
    shallow_clone,
)
from runner.spec import RunnerError

TASK_ID_FIELD = "id"
ANSWER_FILE = "ANSWER.txt"
_WHITESPACE = re.compile(r"\s+")
_MAX_DETAIL_CHARS = 500


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip().casefold()


def materialize(
    task: dict[str, Any], workspace_dir: Path, ctx: GradeContext
) -> None:
    """Fetch the task's repo at its pinned commit into the workspace root."""
    task_id = str(task.get(TASK_ID_FIELD, "<unknown>"))
    repo_url = require(task, "repo_url", task_id)
    commit = require(task, "repo_commit", task_id)
    shallow_clone(repo_url, commit, workspace_dir, timeout_s=ctx.timeout_s)


def grade(
    task: dict[str, Any], workspace_dir: Path, ctx: GradeContext
) -> GradeResult:
    """Compare ANSWER.txt to the answer key under the task's match rule."""
    task_id = str(task.get(TASK_ID_FIELD, "<unknown>"))
    expected = str(require(task, "answer", task_id))
    rule = str(task.get("match", "exact")).lower()

    raw = read_answer_file(workspace_dir, ANSWER_FILE)
    if raw is None:
        return {
            "success": False,
            "detail": {
                "reason": "answer_file_missing",
                "answer_file": str(workspace_dir / ANSWER_FILE),
                "match": rule,
            },
        }

    actual_norm = _normalize(raw)
    expected_norm = _normalize(expected)

    if rule == "exact":
        success = actual_norm == expected_norm
    elif rule == "contains":
        success = expected_norm in actual_norm
    elif rule == "regex":
        success = re.search(expected, raw, re.IGNORECASE | re.MULTILINE) is not None
    else:
        raise RunnerError(
            f"task '{task_id}' has unknown match rule '{rule}' "
            "(expected exact|contains|regex)"
        )

    return {
        "success": success,
        "detail": {
            "match": rule,
            "expected": expected[:_MAX_DETAIL_CHARS],
            "actual": raw.strip()[:_MAX_DETAIL_CHARS],
        },
    }
