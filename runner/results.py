"""Results row assembly, archival of raw artifacts, runs.jsonl append and CSV export."""

from __future__ import annotations

import csv
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runner.accounting import Usage
from runner.agents import AgentResult
from runner.spec import RunSpec, RunnerError, StudyConfig

COLUMNS: list[str] = [
    "run_id",
    "ts",
    "agent",
    "arm",
    "effort",
    "family",
    "task_id",
    "rep",
    "phase",
    "status",
    "discard_reason",
    "success",
    "tokens_fresh",
    "tokens_cache_write",
    "tokens_cache_write_1h",
    "tokens_cache_read",
    "tokens_out",
    "tokens_reasoning",
    "model_usage",
    "cost_usd_simulated",
    "cost_usd_cli_reported",
    "turns",
    "tool_calls",
    "tool_output_bytes",
    "wall_s",
    "activated",
    "activation_evidence",
    "log_flags",
    "session_id",
    "grader",
]

_JSON_COLUMNS = ("model_usage", "tool_output_bytes", "log_flags", "grader")


def build_row(
    spec: RunSpec,
    agent_result: AgentResult,
    usage: Usage,
    cost_usd_simulated: float | None,
    grade: dict[str, Any],
    activated: bool | None,
    activation_evidence: str | None,
    tool_output_bytes: dict[str, int] | None = None,
    log_flags: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Assemble the v1.2 results row for one run."""
    return {
        "run_id": spec.run_id,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "agent": spec.agent,
        "arm": spec.arm,
        "effort": spec.effort,
        "family": spec.family,
        "task_id": spec.task_id,
        "rep": spec.rep,
        "phase": spec.phase,
        "status": agent_result["status"],
        "discard_reason": agent_result["discard_reason"],
        "success": grade.get("success"),
        "tokens_fresh": usage["tokens_fresh"],
        "tokens_cache_write": usage["tokens_cache_write"],
        "tokens_cache_write_1h": usage["tokens_cache_write_1h"],
        "tokens_cache_read": usage["tokens_cache_read"],
        "tokens_out": usage["tokens_out"],
        "tokens_reasoning": usage["tokens_reasoning"],
        "model_usage": usage["model_usage"],
        "cost_usd_simulated": cost_usd_simulated,
        "cost_usd_cli_reported": usage["cost_usd_cli_reported"],
        "turns": usage["turns"],
        "tool_calls": usage["tool_calls"],
        "tool_output_bytes": tool_output_bytes,
        "wall_s": agent_result["wall_s"],
        "activated": activated,
        "activation_evidence": activation_evidence,
        "log_flags": log_flags or {},
        "session_id": agent_result["session_id"],
        "grader": grade.get("detail") or {},
    }


def archive_run(
    cfg: StudyConfig, spec: RunSpec, agent_result: AgentResult, prompt: str
) -> Path:
    """Copy the agent's stdout/stderr, the prompt and the session log into data/raw/<run_id>/."""
    raw_dir = cfg.raw_dir(spec.run_id)
    raw_dir.mkdir(parents=True, exist_ok=True)

    (raw_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    (raw_dir / "agent_stdout.txt").write_text(agent_result["stdout"], encoding="utf-8")
    (raw_dir / "agent_stderr.txt").write_text(agent_result["stderr"], encoding="utf-8")
    (raw_dir / "agent_cmd.json").write_text(
        json.dumps(
            {
                "cmd": agent_result["cmd"],
                "exit_code": agent_result["exit_code"],
                "status": agent_result["status"],
                "discard_reason": agent_result["discard_reason"],
                "wall_s": agent_result["wall_s"],
                "session_id": agent_result["session_id"],
                "session_log": agent_result["session_log"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    session_log = agent_result.get("session_log")
    if session_log and Path(session_log).is_file():
        shutil.copy2(session_log, raw_dir / "session.jsonl")
    return raw_dir


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file, returning [] when it does not exist yet."""
    if not path.is_file():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    """Rewrite a JSONL file atomically (temp file + rename) so a crash cannot truncate it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(path)


def _replace_by_run_id(
    rows: list[dict[str, Any]], row: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (rows with `row` in place of every same-run_id row, the displaced rows)."""
    run_id = row.get("run_id")
    kept: list[dict[str, Any]] = []
    displaced: list[dict[str, Any]] = []
    replaced = False
    for existing in rows:
        if existing.get("run_id") != run_id:
            kept.append(existing)
            continue
        displaced.append(existing)
        if not replaced:
            kept.append(row)
            replaced = True
    if not replaced:
        kept.append(row)
    return kept, displaced


def append_row(cfg: StudyConfig, row: dict[str, Any]) -> Path:
    """Write one results row to runs.jsonl, superseding any earlier row with the same id.

    A `--force` rerun re-uses the run_id, so a blind append would leave two rows for one
    cell. The old row keeps its slot's history in data/superseded.jsonl instead; only the
    newest row per run_id survives in runs.jsonl.
    """
    path = cfg.runs_jsonl
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_rows = _read_jsonl(path)
    if not any(existing.get("run_id") == row.get("run_id") for existing in existing_rows):
        _append_jsonl(path, [row])
        return path

    kept, displaced = _replace_by_run_id(existing_rows, row)
    _append_jsonl(
        cfg.superseded_jsonl, [{**old, "superseded_at": _now()} for old in displaced]
    )
    # Shortcut: full rewrite on every rerun. Ceiling is a runs.jsonl too large to hold in
    # memory (~10^6 rows); upgrade path is a keyed store or a compaction pass.
    _write_jsonl(path, kept)
    return path


def rewrite_row(cfg: StudyConfig, row: dict[str, Any]) -> Path:
    """Replace an existing row in place (regrade); the old row is not kept."""
    path = cfg.runs_jsonl
    rows = load_rows(cfg)
    if not any(existing.get("run_id") == row.get("run_id") for existing in rows):
        raise RunnerError(f"run_id '{row.get('run_id')}' not found in {path}")
    kept, _ = _replace_by_run_id(rows, row)
    _write_jsonl(path, kept)
    return path


def load_rows(cfg: StudyConfig) -> list[dict[str, Any]]:
    """Read every row from runs.jsonl."""
    path = cfg.runs_jsonl
    if not path.is_file():
        raise RunnerError(f"no results file yet: {path}")
    return _read_jsonl(path)


def find_row(cfg: StudyConfig, run_id: str) -> dict[str, Any]:
    """Return the row for one run_id, or raise when it is absent."""
    for row in load_rows(cfg):
        if row.get("run_id") == run_id:
            return row
    raise RunnerError(f"run_id '{run_id}' not found in {cfg.runs_jsonl}")


def export_csv(cfg: StudyConfig) -> Path:
    """Write runs.jsonl out as runs.csv, JSON-encoding the nested columns."""
    rows = load_rows(cfg)
    path = cfg.runs_csv
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            flat = dict(row)
            for column in _JSON_COLUMNS:
                flat[column] = json.dumps(flat.get(column) or {}, ensure_ascii=False)
            writer.writerow({column: flat.get(column) for column in COLUMNS})
    return path
