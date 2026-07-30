"""Family adapter registry and task-manifest loading."""

from __future__ import annotations

import json
from typing import Any

from runner.grading import comprehension, dsbench, swebench
from runner.grading.base import GradeContext, GradeResult, GradingAdapter
from runner.spec import RunnerError, StudyConfig

ADAPTERS: dict[str, Any] = {
    "comprehension": comprehension,
    "dsbench": dsbench,
    "swebench": swebench,
}

__all__ = [
    "ADAPTERS",
    "GradeContext",
    "GradeResult",
    "GradingAdapter",
    "get_adapter",
    "load_tasks",
    "find_task",
    "first_task_id",
    "task_id_of",
]


def get_adapter(family: str) -> Any:
    """Return the adapter module for a task family."""
    if family not in ADAPTERS:
        raise RunnerError(
            f"no grading adapter for family '{family}' "
            f"(known: {', '.join(sorted(ADAPTERS))})"
        )
    return ADAPTERS[family]


def load_tasks(cfg: StudyConfig, family: str) -> list[dict[str, Any]]:
    """Load and validate the task manifest for a family."""
    path = cfg.tasks_path(family)
    if not path.is_file():
        raise RunnerError(f"task manifest not found for family '{family}': {path}")
    try:
        tasks = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RunnerError(f"task manifest is not valid JSON: {path} ({exc})") from exc
    if not isinstance(tasks, list):
        raise RunnerError(f"task manifest must be a JSON list: {path}")
    return tasks


def task_id_of(family: str, task: dict[str, Any]) -> str:
    """Read a task's identifier using the family's id field."""
    field = get_adapter(family).TASK_ID_FIELD
    value = task.get(field)
    if not value:
        raise RunnerError(f"task in family '{family}' is missing '{field}': {task!r}")
    return str(value)


def find_task(cfg: StudyConfig, family: str, task_id: str) -> dict[str, Any]:
    """Find one task by id in a family's manifest."""
    tasks = load_tasks(cfg, family)
    for task in tasks:
        if task_id_of(family, task) == task_id:
            return task
    known = ", ".join(task_id_of(family, task) for task in tasks) or "none"
    raise RunnerError(
        f"task '{task_id}' not found in {cfg.tasks_path(family)} (known: {known})"
    )


def first_task_id(cfg: StudyConfig, family: str) -> str | None:
    """First task id in a family's manifest, or None when the manifest is empty."""
    tasks = load_tasks(cfg, family)
    return task_id_of(family, tasks[0]) if tasks else None
