"""Derived SQLite star schema built from the study's results JSONLs (`abl db`).

Three tables: `audit_runs` (lossless, every revision of every run), `fact_runs` (one row
per current run, nested fields pivoted into columns) and `dim_task` (one row per task in
the family manifests). The file is a derived artifact — rebuilt from scratch every time.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from runner.spec import RunnerError, StudyConfig

_UNSAFE_COLUMN = re.compile(r"[^a-z0-9]+")

# Scalar row fields promoted to fact_runs columns, in output order.
_SCALAR_COLUMNS: list[tuple[str, str]] = [
    ("ts", "TEXT"),
    ("agent", "TEXT"),
    ("arm", "TEXT"),
    ("effort", "TEXT"),
    ("family", "TEXT"),
    ("task_id", "TEXT"),
    ("rep", "INTEGER"),
    ("phase", "TEXT"),
    ("status", "TEXT"),
    ("discard_reason", "TEXT"),
    ("success", "INTEGER"),
    ("tokens_fresh", "INTEGER"),
    ("tokens_cache_write", "INTEGER"),
    ("tokens_cache_write_1h", "INTEGER"),
    ("tokens_cache_read", "INTEGER"),
    ("tokens_out", "INTEGER"),
    ("tokens_reasoning", "INTEGER"),
    ("cost_usd_simulated", "REAL"),
    ("cost_usd_cli_reported", "REAL"),
    ("turns", "INTEGER"),
    ("tool_calls", "INTEGER"),
    ("wall_s", "REAL"),
    ("activated", "INTEGER"),
    ("activation_evidence", "TEXT"),
    ("session_id", "TEXT"),
]

_GRADER_COLUMNS: list[tuple[str, str]] = [
    ("grader_abs_error", "REAL"),
    ("grader_rel_error", "REAL"),
    ("diff_files_changed", "INTEGER"),
    ("diff_lines_added", "INTEGER"),
    ("diff_lines_removed", "INTEGER"),
]

_JSON_COLUMNS: list[tuple[str, str]] = [
    ("model_usage_json", "TEXT"),
    ("grader_json", "TEXT"),
    ("tool_output_bytes_json", "TEXT"),
    ("log_flags_json", "TEXT"),
]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _load_rows(cfg: StudyConfig) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (current rows, superseded rows). A missing superseded.jsonl is normal."""
    if not cfg.runs_jsonl.is_file():
        raise RunnerError(f"no results file yet: {cfg.runs_jsonl}")
    current = _read_jsonl(cfg.runs_jsonl)
    superseded = (
        _read_jsonl(cfg.superseded_jsonl) if cfg.superseded_jsonl.is_file() else []
    )
    return current, superseded


def _task_key(family: Any, task_id: Any) -> str:
    return f"{family}:{task_id}"


def _channel_column(channel: str) -> str:
    """tool_output_bytes key -> fact_runs column name ('_total' is the roll-up)."""
    if channel == "_total":
        return "tool_bytes_total"
    return "tool_bytes_" + _UNSAFE_COLUMN.sub("_", channel.lower()).strip("_")


def _flag_column(flag: str) -> str:
    return "flag_" + _UNSAFE_COLUMN.sub("_", flag.lower()).strip("_")


def _scalar(value: Any) -> Any:
    """Coerce a row value into something sqlite3 can bind."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return value


def _dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def _number(value: Any) -> Any:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _discover_columns(rows: list[dict[str, Any]]) -> tuple[dict[str, str], dict[str, str]]:
    """Map every tool_output_bytes channel / log_flags flag seen in the data to a column."""
    channels: dict[str, str] = {}
    flags: dict[str, str] = {}
    for row in rows:
        for channel in (row.get("tool_output_bytes") or {}):
            channels[channel] = _channel_column(channel)
        for flag in (row.get("log_flags") or {}):
            flags[flag] = _flag_column(flag)
    return channels, flags


def _create_table(conn: sqlite3.Connection, name: str, columns: list[tuple[str, str]],
                  extra: str = "") -> None:
    body = ", ".join(f"{column} {sql_type}" for column, sql_type in columns)
    if extra:
        body = f"{body}, {extra}"
    conn.execute(f"CREATE TABLE {name} ({body})")


def _insert(conn: sqlite3.Connection, table: str, columns: list[str],
            values: list[list[Any]]) -> None:
    placeholders = ", ".join("?" for _ in columns)
    conn.executemany(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})", values
    )


def _build_audit(
    conn: sqlite3.Connection,
    current: list[dict[str, Any]],
    superseded: list[dict[str, Any]],
) -> int:
    """Every revision of every run, lossless: the original dict re-serialized verbatim."""
    _create_table(
        conn,
        "audit_runs",
        [
            ("run_id", "TEXT NOT NULL"),
            ("revision", "INTEGER NOT NULL"),
            ("is_current", "INTEGER NOT NULL"),
            ("ts", "TEXT"),
            ("superseded", "INTEGER NOT NULL"),
            ("row_json", "TEXT NOT NULL"),
        ],
        extra="PRIMARY KEY (run_id, revision)",
    )

    history: dict[str, list[dict[str, Any]]] = {}
    for row in superseded:
        history.setdefault(str(row.get("run_id")), []).append(row)

    for rows in history.values():
        rows.sort(key=lambda row: str(row.get("ts") or ""))

    values: list[list[Any]] = []
    for row in current:
        run_id = str(row.get("run_id"))
        older = history.pop(run_id, [])
        for revision, old in enumerate(older, start=1):
            values.append([run_id, revision, 0, old.get("ts"), 1, _dumps(old)])
        # The current row is always the last revision, whatever its ts says.
        values.append([run_id, len(older) + 1, 1, row.get("ts"), 0, _dumps(row)])
    # Orphans: superseded rows whose run_id no longer appears in runs.jsonl.
    for run_id, older in history.items():
        for revision, old in enumerate(older, start=1):
            values.append([run_id, revision, 0, old.get("ts"), 1, _dumps(old)])

    _insert(
        conn,
        "audit_runs",
        ["run_id", "revision", "is_current", "ts", "superseded", "row_json"],
        values,
    )
    conn.execute("CREATE INDEX idx_audit_runs_run_id ON audit_runs (run_id)")
    return len(values)


def _build_fact(conn: sqlite3.Connection, current: list[dict[str, Any]]) -> int:
    channels, flags = _discover_columns(current)
    channel_columns = sorted(set(channels.values()))
    flag_columns = sorted(set(flags.values()))

    columns: list[tuple[str, str]] = (
        [("run_id", "TEXT PRIMARY KEY")]
        + _SCALAR_COLUMNS
        + [("task_key", "TEXT"), ("context_peak_tokens", "INTEGER")]
        + [(column, "INTEGER") for column in channel_columns]
        + [(column, "INTEGER") for column in flag_columns]
        + _GRADER_COLUMNS
        + _JSON_COLUMNS
    )
    _create_table(
        conn,
        "fact_runs",
        columns,
        extra="FOREIGN KEY (task_key) REFERENCES dim_task (task_key)",
    )

    names = [column for column, _ in columns]
    values: list[list[Any]] = []
    for row in current:
        model_usage = row.get("model_usage") or {}
        grader = row.get("grader") or {}
        diff_stats = grader.get("diff_stats") or {}
        tool_bytes = row.get("tool_output_bytes") or {}
        row_flags = row.get("log_flags") or {}

        record: dict[str, Any] = {"run_id": row.get("run_id")}
        for column, _ in _SCALAR_COLUMNS:
            record[column] = _scalar(row.get(column))
        record["task_key"] = _task_key(row.get("family"), row.get("task_id"))
        record["context_peak_tokens"] = _number(model_usage.get("context_peak_tokens"))

        # Claude ('Bash') and codex ('bash') name the same channel differently; they
        # normalize onto one column, so a row carrying both is summed rather than lost.
        for column in channel_columns:
            record[column] = None
        for channel, raw in tool_bytes.items():
            column = channels[channel]
            amount = _number(raw)
            if amount is not None:
                record[column] = (record[column] or 0) + amount
        for column in flag_columns:
            record[column] = None
        for flag, count in row_flags.items():
            record[flags[flag]] = _number(count)

        record["grader_abs_error"] = _number(grader.get("abs_error"))
        record["grader_rel_error"] = _number(grader.get("rel_error"))
        record["diff_files_changed"] = _number(diff_stats.get("files_changed"))
        record["diff_lines_added"] = _number(diff_stats.get("lines_added"))
        record["diff_lines_removed"] = _number(diff_stats.get("lines_removed"))

        record["model_usage_json"] = _dumps(model_usage)
        record["grader_json"] = _dumps(grader)
        record["tool_output_bytes_json"] = _dumps(tool_bytes)
        record["log_flags_json"] = _dumps(row_flags)

        values.append([record.get(name) for name in names])

    _insert(conn, "fact_runs", names, values)
    conn.execute("CREATE INDEX idx_fact_runs_task_key ON fact_runs (task_key)")
    conn.execute("CREATE VIEW v_ok_runs AS SELECT * FROM fact_runs WHERE status = 'ok'")
    return len(values)


def _manifest_task_id(entry: dict[str, Any]) -> str | None:
    """comprehension/dsbench key their tasks 'id', swebench keys them 'instance_id'."""
    task_id = entry.get("id") or entry.get("instance_id")
    return str(task_id) if task_id is not None else None


def _build_dim_task(
    conn: sqlite3.Connection,
    cfg: StudyConfig,
    gate_keys: set[str],
) -> int:
    _create_table(
        conn,
        "dim_task",
        [
            ("task_key", "TEXT PRIMARY KEY"),
            ("task_id", "TEXT NOT NULL"),
            ("family", "TEXT NOT NULL"),
            ("is_gate_task", "INTEGER NOT NULL"),
            ("repo", "TEXT"),
            ("base_commit", "TEXT"),
            ("answer_numeric", "REAL"),
            ("tolerance", "REAL"),
            ("question_chars", "INTEGER"),
            ("manifest_json", "TEXT NOT NULL"),
        ],
    )

    names = [
        "task_key", "task_id", "family", "is_gate_task", "repo", "base_commit",
        "answer_numeric", "tolerance", "question_chars", "manifest_json",
    ]
    values: list[list[Any]] = []
    for family in cfg.families():
        path = cfg.tasks_path(family)
        if not path.is_file():
            raise RunnerError(f"task manifest not found: {path}")
        with path.open("r", encoding="utf-8") as handle:
            entries = json.load(handle)
        if not isinstance(entries, list):
            raise RunnerError(f"task manifest must be a JSON list: {path}")
        for entry in entries:
            task_id = _manifest_task_id(entry)
            if task_id is None:
                raise RunnerError(f"task in {path} has no 'id' / 'instance_id'")
            key = _task_key(family, task_id)
            question = entry.get("question") or entry.get("problem_statement")
            values.append([
                key,
                task_id,
                family,
                int(key in gate_keys),
                entry.get("repo"),
                entry.get("base_commit"),
                _number(entry.get("answer_numeric")),
                _number(entry.get("tolerance")),
                len(question) if isinstance(question, str) else None,
                _dumps(entry),
            ])

    _insert(conn, "dim_task", names, values)
    return len(values)


def build_db(cfg: StudyConfig, out_path: Path) -> dict[str, int]:
    """Rebuild the derived SQLite database from the study's JSONLs. Returns row counts."""
    current, superseded = _load_rows(cfg)
    gate_keys = {
        _task_key(row.get("family"), row.get("task_id"))
        for row in current + superseded
        if row.get("phase") == "gate"
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.unlink(missing_ok=True)  # derived artifact: always a clean rebuild

    conn = sqlite3.connect(out_path)
    try:
        counts = {
            "dim_task": _build_dim_task(conn, cfg, gate_keys),
            "audit_runs": _build_audit(conn, current, superseded),
            "fact_runs": _build_fact(conn, current),
        }
        conn.commit()
    finally:
        conn.close()
    return counts
