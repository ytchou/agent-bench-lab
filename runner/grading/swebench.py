"""SWE-bench Verified family: clone at base_commit, diff the workspace, run the official harness."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from runner.grading.base import GradeContext, GradeResult, git, run_cmd, require, shallow_clone
from runner.spec import RunnerError

TASK_ID_FIELD = "instance_id"
MODEL_NAME = "agent-bench-lab"
DATASET_NAME = "princeton-nlp/SWE-bench_Verified"
PREDICTIONS_FILE = "predictions.jsonl"
PATCH_FILE = "model_patch.diff"
_MAX_DETAIL_CHARS = 2000
_DOCKER_PROBE_TIMEOUT_S = 10


def materialize(
    task: dict[str, Any], workspace_dir: Path, ctx: GradeContext
) -> None:
    """Fetch <repo> at base_commit into the workspace root (detached HEAD)."""
    instance_id = str(task.get(TASK_ID_FIELD, "<unknown>"))
    repo = require(task, "repo", instance_id)
    base_commit = require(task, "base_commit", instance_id)
    repo_url = repo if str(repo).startswith("http") else f"https://github.com/{repo}"
    shallow_clone(repo_url, str(base_commit), workspace_dir, timeout_s=ctx.timeout_s)


def build_model_patch(workspace_dir: Path, timeout_s: int) -> str:
    """Return the agent's changes as a unified diff, including newly created files."""
    # `add -N` registers untracked files with the index so `git diff` shows them.
    git(["add", "-N", "."], workspace_dir, timeout_s)
    return git(["diff"], workspace_dir, timeout_s).stdout


def diff_stats(patch: str) -> dict[str, int]:
    """Scope of a unified diff: distinct files touched, lines added, lines removed.

    Parsed from the diff text itself (no `git diff --stat` subprocess) so it also works on
    an archived patch during a regrade.
    """
    files: set[str] = set()
    added = 0
    removed = 0
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            parts = line.split(" ")
            if len(parts) >= 4:
                # Shortcut: take the post-image path, stripping the `b/` prefix. Ceiling:
                # a path containing a space (or a git-quoted path with escapes) splits
                # wrong and inflates files_changed — upgrade path is to read the
                # `+++ b/<path>` header with quoting rules if such repos appear.
                files.add(parts[-1].removeprefix("b/"))
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return {"files_changed": len(files), "lines_added": added, "lines_removed": removed}


def _attach_scope(
    detail: dict[str, Any], task: dict[str, Any], patch: str
) -> dict[str, Any]:
    """Add the model patch's diff stats, plus the task's gold stats when the dataset has them."""
    detail["diff_stats"] = diff_stats(patch)
    gold = task.get("gold_diff_stats")
    if isinstance(gold, dict):
        # Precomputed by the dataset-prep script from the instance's gold patch; never
        # recomputed here, so scope-delta analysis compares like with like.
        detail["gold_diff_stats"] = gold
    return detail


def _find_report(search_dirs: list[Path], instance_id: str) -> dict[str, Any] | None:
    """Locate the harness report JSON (its filename/location varies by version)."""
    for directory in search_dirs:
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if isinstance(payload, dict) and (
                "resolved_ids" in payload or instance_id in payload
            ):
                return {"path": str(path), "payload": payload}
    return None


def _resolved(report_payload: dict[str, Any], instance_id: str) -> bool | None:
    if "resolved_ids" in report_payload:
        if instance_id in (report_payload.get("resolved_ids") or []):
            return True
        if instance_id in (report_payload.get("unresolved_ids") or []):
            return False
        return None
    per_instance = report_payload.get(instance_id)
    if isinstance(per_instance, dict) and "resolved" in per_instance:
        return bool(per_instance["resolved"])
    return None


def docker_unavailable_reason() -> str | None:
    """Return why the Docker daemon is unusable, or None when `docker info` succeeds."""
    try:
        result = run_cmd(["docker", "info"], None, _DOCKER_PROBE_TIMEOUT_S)
    except FileNotFoundError:
        return "docker binary not found on PATH"
    except subprocess.TimeoutExpired:
        return f"`docker info` timed out after {_DOCKER_PROBE_TIMEOUT_S}s"
    if result.returncode != 0:
        return (result.stderr.strip() or result.stdout.strip() or "unknown")[
            :_MAX_DETAIL_CHARS
        ]
    return None


def run_harness(
    instance_id: str, predictions_path: Path, ctx: GradeContext
) -> GradeResult:
    """Evaluate an existing predictions.jsonl with the official harness (Docker-backed).

    Split out of grade() so `abl regrade` can re-run only this step against the
    predictions file archived by the original run.
    """
    # The harness spends ~8 minutes building images before it notices a dead daemon, so
    # probe first and skip the evaluation entirely (observed live during the exit gate).
    reason = docker_unavailable_reason()
    if reason is not None:
        return {
            "success": None,
            "detail": {
                "reason": "docker_unavailable",
                "docker_error": reason,
                "instance_id": instance_id,
            },
        }

    cmd = [
        sys.executable,
        "-m",
        "swebench.harness.run_evaluation",
        "--predictions_path",
        str(predictions_path),
        "--instance_ids",
        instance_id,
        "--run_id",
        ctx.run_id,
        "--dataset_name",
        DATASET_NAME,
        "--max_workers",
        "1",
    ]
    result = run_cmd(cmd, ctx.raw_dir, ctx.timeout_s)
    (ctx.raw_dir / "swebench_harness.log").write_text(
        f"$ {' '.join(cmd)}\nexit={result.returncode}\n\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}\n",
        encoding="utf-8",
    )

    report = _find_report(
        [ctx.raw_dir, Path.cwd() / "logs" / "run_evaluation" / ctx.run_id],
        instance_id,
    )
    if report is None:
        return {
            "success": None,
            "detail": {
                "reason": "harness_report_not_found",
                "harness_exit": result.returncode,
                "stderr_tail": result.stderr[-_MAX_DETAIL_CHARS:],
                "instance_id": instance_id,
            },
        }

    return {
        "success": _resolved(report["payload"], instance_id),
        "detail": {
            "instance_id": instance_id,
            "harness_exit": result.returncode,
            "report_path": report["path"],
        },
    }


def regrade(instance_id: str, ctx: GradeContext) -> GradeResult:
    """Re-run the harness for a finished run using its archived predictions.jsonl."""
    predictions_path = ctx.raw_dir / PREDICTIONS_FILE
    if not predictions_path.is_file():
        raise RunnerError(
            f"no {PREDICTIONS_FILE} archived for run '{ctx.run_id}': {predictions_path}"
        )
    try:
        return run_harness(instance_id, predictions_path, ctx)
    except Exception as exc:  # harness setup, Docker, dataset download — all non-fatal
        return _harness_error(exc, instance_id)


def _harness_error(exc: Exception, instance_id: str) -> GradeResult:
    return {
        "success": None,
        "detail": {
            "reason": "harness_error",
            "error": f"{type(exc).__name__}: {exc}"[:_MAX_DETAIL_CHARS],
            "instance_id": instance_id,
        },
    }


def grade(
    task: dict[str, Any], workspace_dir: Path, ctx: GradeContext
) -> GradeResult:
    """Write predictions and shell out to the official SWE-bench evaluation harness."""
    instance_id = str(require(task, TASK_ID_FIELD, "<unknown>"))
    try:
        patch = build_model_patch(workspace_dir, ctx.timeout_s)
        ctx.raw_dir.mkdir(parents=True, exist_ok=True)
        (ctx.raw_dir / PATCH_FILE).write_text(patch, encoding="utf-8")

        if not patch.strip():
            return {
                "success": False,
                "detail": _attach_scope(
                    {"reason": "empty_patch", "instance_id": instance_id}, task, patch
                ),
            }

        predictions_path = ctx.raw_dir / PREDICTIONS_FILE
        predictions_path.write_text(
            json.dumps(
                {
                    "instance_id": instance_id,
                    "model_name_or_path": MODEL_NAME,
                    "model_patch": patch,
                }
            )
            + "\n",
            encoding="utf-8",
        )

        result = run_harness(instance_id, predictions_path, ctx)
        result["detail"]["patch_bytes"] = len(patch)
        _attach_scope(result["detail"], task, patch)
        return result
    except Exception as exc:  # harness setup, Docker, dataset download — all non-fatal
        return _harness_error(exc, instance_id)
