"""Run-to-run variance measurement and sample-size (power) calculation over runs.jsonl.

Study-agnostic: everything here works off a results-row list, so any study can point it
at its own runs.jsonl. Stdlib only (no numpy/scipy/pandas) to keep the instrument layer
dependency-free.
"""

from __future__ import annotations

import json
import math
import random
import statistics
from pathlib import Path
from typing import Any, Iterable, Sequence

from runner.spec import RunnerError

GroupKey = tuple[Any, ...]


def load_rows(
    runs_path: Path, *, phase: str | None = None, status: str | None = "ok"
) -> list[dict[str, Any]]:
    """Read runs.jsonl, keeping rows matching `phase` (None = all) and `status` (None = all)."""
    path = Path(runs_path)
    if not path.is_file():
        raise RunnerError(f"no results file: {path}")

    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RunnerError(f"{path}:{lineno} is not valid JSON: {exc}") from exc
            if phase is not None and row.get("phase") != phase:
                continue
            if status is not None and row.get("status") != status:
                continue
            rows.append(row)
    return rows


def _numeric(value: Any) -> float | None:
    """Coerce a cell to float, or None when it is missing / not a number."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _cv(values: Sequence[float]) -> float | None:
    """Coefficient of variation (sample stdev / mean) across repeats of one task."""
    mean = statistics.fmean(values)
    if mean == 0:
        return None
    return statistics.stdev(values) / abs(mean)


def _sigma_log(values: Sequence[float]) -> float | None:
    """Within-task sd on the log scale; undefined when any repeat is non-positive."""
    if any(value <= 0 for value in values):
        return None
    return statistics.stdev([math.log(value) for value in values])


def _pooled_rms(sigmas: Iterable[float]) -> float | None:
    """Pool per-task sigmas by root-mean-square (equal weight, equal rep counts)."""
    values = list(sigmas)
    if not values:
        return None
    return math.sqrt(statistics.fmean([sigma**2 for sigma in values]))


def repeat_variability(
    rows: Sequence[dict[str, Any]],
    *,
    group_by: Sequence[str] = ("agent", "family"),
    value_key: str = "cost_usd_simulated",
    task_key: str = "task_id",
) -> dict[GroupKey, dict[str, Any]]:
    """Measure rep-to-rep variability of `value_key` within each group.

    For every group (keyed by the tuple of `group_by` values) the tasks carrying >=2
    repeats with a non-null value are collected; each contributes a coefficient of
    variation and a log-scale sd. The group's `sigma_log` pools the per-task log sds by
    root-mean-square — the within-task noise term `required_n_paired` expects.
    """
    grouped: dict[GroupKey, dict[str, list[float]]] = {}
    counts: dict[GroupKey, int] = {}
    for row in rows:
        key: GroupKey = tuple(row.get(field) for field in group_by)
        counts[key] = counts.get(key, 0) + 1
        value = _numeric(row.get(value_key))
        if value is None:
            continue
        task = row.get(task_key)
        grouped.setdefault(key, {}).setdefault(task, []).append(value)

    summary: dict[GroupKey, dict[str, Any]] = {}
    for key in sorted(counts, key=lambda item: tuple(str(part) for part in item)):
        by_task = grouped.get(key, {})
        task_values = {
            task: values for task, values in by_task.items() if len(values) >= 2
        }
        task_cv: dict[Any, float] = {}
        task_sigma_log: dict[Any, float] = {}
        for task, values in task_values.items():
            cv = _cv(values)
            if cv is not None:
                task_cv[task] = cv
            sigma = _sigma_log(values)
            if sigma is not None:
                task_sigma_log[task] = sigma

        cvs = list(task_cv.values())
        summary[key] = {
            "group": dict(zip(group_by, key)),
            "n_rows": counts[key],
            "n_tasks": len(by_task),
            "n_tasks_with_repeats": len(task_values),
            "task_values": task_values,
            "task_cv": task_cv,
            "task_sigma_log": task_sigma_log,
            "median_cv": statistics.median(cvs) if cvs else None,
            "mean_cv": statistics.fmean(cvs) if cvs else None,
            "sigma_log": _pooled_rms(task_sigma_log.values()),
        }
    return summary


def required_n_paired(
    sigma_log: float, *, effect: float = 0.10, alpha: float = 0.05, power: float = 0.80
) -> int:
    """Pairs needed to detect a multiplicative `effect` in cost, paired design.

    Formula:
        n = ceil( ((z_{1-alpha/2} + z_{power}) * sqrt(2) * sigma_log / ln(1+effect))**2 )

    Assumptions: the outcome is analysed on the log scale, where a multiplicative effect
    of `effect` becomes an additive shift of ln(1+effect); both arms of a pair carry
    independent run noise of sd `sigma_log`, so the paired difference has sd
    sqrt(2)*sigma_log; normal approximation, two-sided test at level `alpha`.
    """
    if sigma_log <= 0:
        raise RunnerError("sigma_log must be positive")
    if effect <= 0:
        raise RunnerError("effect must be positive")
    if not 0 < alpha < 1:
        raise RunnerError("alpha must be in (0, 1)")
    if not 0 < power < 1:
        raise RunnerError("power must be in (0, 1)")

    normal = statistics.NormalDist()
    z_alpha = normal.inv_cdf(1 - alpha / 2)
    z_power = normal.inv_cdf(power)
    delta = math.log(1 + effect)
    return math.ceil((((z_alpha + z_power) * math.sqrt(2) * sigma_log) / delta) ** 2)


def bootstrap_ci_median(
    values: Sequence[float], *, n_boot: int = 10000, ci: float = 0.95, seed: int = 0
) -> tuple[float, float, float]:
    """Return (median, lo, hi) for the median, via a percentile bootstrap."""
    sample = [float(value) for value in values]
    if not sample:
        raise RunnerError("bootstrap_ci_median needs at least one value")
    if not 0 < ci < 1:
        raise RunnerError("ci must be in (0, 1)")

    point = statistics.median(sample)
    if len(sample) == 1:
        return point, point, point

    rng = random.Random(seed)
    size = len(sample)
    medians = sorted(
        statistics.median(rng.choices(sample, k=size)) for _ in range(n_boot)
    )
    lo_index = max(0, min(n_boot - 1, int((1 - ci) / 2 * n_boot)))
    hi_index = max(0, min(n_boot - 1, int((1 + ci) / 2 * n_boot) - 1))
    return point, medians[lo_index], medians[hi_index]
