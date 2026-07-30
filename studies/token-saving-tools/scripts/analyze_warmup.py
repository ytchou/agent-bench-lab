"""Warm-up analysis (to-do 1.6): rep-to-rep variability and the resulting tasks-per-family.

Thin caller over runner.analysis — every statistic lives in the instrument layer; this
script only selects the warm-up rows, lays out the tables and prints them.
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path
from typing import Any, Sequence

STUDY_ROOT = Path(__file__).resolve().parents[1]
LAB_ROOT = STUDY_ROOT.parents[1]
if str(LAB_ROOT) not in sys.path:
    sys.path.insert(0, str(LAB_ROOT))

from runner.analysis import (  # noqa: E402
    _pooled_rms,
    load_rows,
    repeat_variability,
    required_n_paired,
)
from runner.spec import RunnerError  # noqa: E402

RUNS = STUDY_ROOT / "data" / "runs.jsonl"
EFFECT = 0.10
MIN_TASKS_FOR_ESTIMATE = 2

HEADER = f"{'AGENT':<16}{'FAMILY':<16}{'RUNS':>6}{'REP_TASKS':>11}{'SUCCESS':>9}{'MED_VAL':>12}{'MED_CV':>9}{'MEAN_CV':>9}{'SIGMA_LOG':>11}{'N_PAIRS':>9}"


def _fmt(value: float | None, width: int, decimals: int) -> str:
    """Right-aligned number, or a padded dash when the statistic is undefined."""
    if value is None:
        return format("-", f">{width}")
    return format(value, f">{width}.{decimals}f")


def _median_value(stats: dict[str, Any]) -> float | None:
    """Median of every repeat value seen in a group (tasks with repeats only)."""
    values = [value for values in stats["task_values"].values() for value in values]
    return statistics.median(values) if values else None


def _success_counts(rows: Sequence[dict[str, Any]], group: dict[str, Any]) -> tuple[int, int]:
    matched = [
        row for row in rows if all(row.get(k) == v for k, v in group.items())
    ]
    return sum(1 for row in matched if row.get("success")), len(matched)


def _required_n(sigma_log: float | None, n_tasks: int) -> int | None:
    if sigma_log is None or sigma_log <= 0 or n_tasks < MIN_TASKS_FOR_ESTIMATE:
        return None
    return required_n_paired(sigma_log, effect=EFFECT)


def print_block(rows: Sequence[dict[str, Any]], value_key: str) -> None:
    print()
    print(f"== {value_key}: rep-to-rep variability by agent x family ==")
    print(HEADER)

    by_cell = repeat_variability(rows, group_by=("agent", "family"), value_key=value_key)
    notes: list[str] = []
    for key, stats in by_cell.items():
        agent, family = key
        ok, total = _success_counts(rows, stats["group"])
        n_tasks = stats["n_tasks_with_repeats"]
        required = _required_n(stats["sigma_log"], n_tasks)
        print(
            f"{str(agent):<16}{str(family):<16}{stats['n_rows']:>6}{n_tasks:>11}"
            f"{f'{ok}/{total}':>9}{_fmt(_median_value(stats), 12, 4)}"
            f"{_fmt(stats['median_cv'], 9, 3)}{_fmt(stats['mean_cv'], 9, 3)}"
            f"{_fmt(stats['sigma_log'], 11, 4)}{'-' if required is None else required:>9}"
        )
        if n_tasks < MIN_TASKS_FOR_ESTIMATE:
            notes.append(
                f"  note: {agent} x {family} has {n_tasks} task(s) with repeats"
                " - too few repeats to estimate"
            )

    for note in notes:
        print(note)

    print()
    print(f"-- {value_key}: pooled across agents, by family --")
    print(f"{'FAMILY':<16}{'REP_TASKS':>11}{'SIGMA_LOG':>11}{'N_PAIRS':>9}")
    # Pool the agent x family cells computed above rather than re-grouping on family
    # alone: with one rep per (agent, family, task), grouping by family would treat the
    # claude and codex runs of a task as repeats of each other and report between-agent
    # cost differences as run-to-run noise. Sigmas pool by root-mean-square (the same
    # equal-weight rule runner.analysis uses across tasks) and REP_TASKS takes the min
    # across cells, so a family only estimates when every contributing agent has repeats.
    by_family: dict[Any, dict[str, list[Any]]] = {}
    for (_agent, family), stats in by_cell.items():
        cell = by_family.setdefault(family, {"sigmas": [], "n_tasks": []})
        cell["n_tasks"].append(stats["n_tasks_with_repeats"])
        if stats["sigma_log"] is not None:
            cell["sigmas"].append(stats["sigma_log"])

    for family in sorted(by_family, key=str):
        cell = by_family[family]
        n_tasks = min(cell["n_tasks"]) if cell["n_tasks"] else 0
        sigma_log = _pooled_rms(cell["sigmas"])
        required = _required_n(sigma_log, n_tasks)
        print(
            f"{str(family):<16}{n_tasks:>11}{_fmt(sigma_log, 11, 4)}"
            f"{'-' if required is None else required:>9}"
        )
        if n_tasks < MIN_TASKS_FOR_ESTIMATE:
            print(
                f"  note: family {family} has {n_tasks} task(s) with repeats"
                " - too few repeats to estimate"
            )


def main() -> int:
    try:
        rows = load_rows(RUNS, phase="warmup", status="ok")
    except RunnerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not rows:
        print(f"no ok warm-up rows in {RUNS}")
        return 1

    print(f"warm-up rows (phase=warmup, status=ok): {len(rows)}  source: {RUNS}")
    print(
        f"N_PAIRS = paired tasks per family needed to detect a {EFFECT:.0%} cost"
        " difference (alpha=0.05, power=0.80, two-sided)"
    )
    print_block(rows, "cost_usd_simulated")
    print_block(rows, "wall_s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
