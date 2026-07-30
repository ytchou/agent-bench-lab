"""DSBench family: copy data files into the workspace, match a numeric ANSWER.txt."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from runner.grading.base import GradeContext, GradeResult, read_answer_file, require
from runner.spec import RunnerError

TASK_ID_FIELD = "id"
ANSWER_FILE = "ANSWER.txt"
DATA_SUBDIR = "data"
DEFAULT_TOLERANCE = 1e-6
_MAX_DETAIL_CHARS = 500
# Last number in the file wins: agents that ignore the "only the number" instruction
# usually end with the answer. Ceiling: prose containing a trailing citation year would
# mis-parse — upgrade path is a strict single-token parse once compliance is measured.
_NUMBER = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


def _data_root(task: dict[str, Any], ctx: GradeContext) -> Path:
    task_id = str(task.get(TASK_ID_FIELD, "<unknown>"))
    override = task.get("data_root")
    if override:
        root = Path(override)
        return root if root.is_absolute() else ctx.study_root / root
    return ctx.tasks_dir / "dsbench_data" / task_id


def materialize(
    task: dict[str, Any], workspace_dir: Path, ctx: GradeContext
) -> None:
    """Copy the task's data files into <workspace>/data/."""
    task_id = str(task.get(TASK_ID_FIELD, "<unknown>"))
    data_files = task.get("data_files") or []
    if not data_files:
        raise RunnerError(f"dsbench task '{task_id}' lists no data_files")

    root = _data_root(task, ctx)
    if not root.is_dir():
        raise RunnerError(f"dsbench data dir not found for task '{task_id}': {root}")

    dest_root = workspace_dir / DATA_SUBDIR
    dest_root.mkdir(parents=True, exist_ok=True)
    for rel in data_files:
        source = root / rel
        if not source.is_file():
            raise RunnerError(
                f"dsbench task '{task_id}' data file not found: {source}"
            )
        dest = dest_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)


def _parse_number(text: str) -> float | None:
    matches = _NUMBER.findall(text.replace(",", ""))
    if not matches:
        return None
    try:
        return float(matches[-1])
    except ValueError:
        return None


def grade(
    task: dict[str, Any], workspace_dir: Path, ctx: GradeContext
) -> GradeResult:
    """Parse a float from ANSWER.txt and compare within absolute or relative tolerance."""
    task_id = str(task.get(TASK_ID_FIELD, "<unknown>"))
    expected = float(require(task, "answer_numeric", task_id))
    tolerance = float(task.get("tolerance", DEFAULT_TOLERANCE))

    raw = read_answer_file(workspace_dir, ANSWER_FILE)
    if raw is None:
        return {
            "success": False,
            "detail": {
                "reason": "answer_file_missing",
                "answer_file": str(workspace_dir / ANSWER_FILE),
            },
        }

    actual = _parse_number(raw)
    if actual is None:
        return {
            "success": False,
            "detail": {
                "reason": "no_number_in_answer",
                "actual_raw": raw.strip()[:_MAX_DETAIL_CHARS],
                "expected": expected,
            },
        }

    abs_err = abs(actual - expected)
    rel_err = abs_err / abs(expected) if expected != 0 else None
    # tolerance is ABSOLUTE only. Reusing it as a relative bound made large answers
    # absurdly lenient (tolerance 0.5 would accept a 50%-off answer). Relative
    # matching must be opted into per task via rel_tolerance.
    rel_tolerance = task.get("rel_tolerance")
    success = abs_err <= tolerance or (
        rel_tolerance is not None
        and rel_err is not None
        and rel_err <= float(rel_tolerance)
    )

    return {
        "success": success,
        "detail": {
            "expected": expected,
            "actual": actual,
            "tolerance": tolerance,
            "abs_error": abs_err,
            "rel_error": rel_err,
            "actual_raw": raw.strip()[:_MAX_DETAIL_CHARS],
        },
    }
