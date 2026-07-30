"""Fresh per-run workspace creation and task materialization."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from runner.grading import get_adapter
from runner.grading.base import GradeContext, git
from runner.spec import RunSpec, RunnerError, StudyConfig


def create_workspace(spec: RunSpec, cfg: StudyConfig, force: bool = False) -> Path:
    """Create sandbox/workspaces/<run_id>/ as an empty git repo."""
    workspace = cfg.workspace_dir(spec.run_id)
    if workspace.exists():
        if not force:
            raise RunnerError(
                f"workspace already exists: {workspace} (pass --force to recreate it)"
            )
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    git(["init", "-q"], workspace, cfg.git_timeout_s)
    return workspace


def grade_context(spec: RunSpec, cfg: StudyConfig, timeout_s: int) -> GradeContext:
    """Build the context object handed to a family adapter."""
    return GradeContext(
        run_id=spec.run_id,
        raw_dir=cfg.raw_dir(spec.run_id),
        study_root=cfg.study_root,
        tasks_dir=cfg.tasks_path(spec.family).parent,
        timeout_s=timeout_s,
    )


def prepare_workspace(
    spec: RunSpec, cfg: StudyConfig, task: dict[str, Any], force: bool = False
) -> Path:
    """Create the workspace and let the family adapter materialize the task into it."""
    workspace = create_workspace(spec, cfg, force=force)
    ctx = grade_context(spec, cfg, timeout_s=cfg.git_timeout_s)
    get_adapter(spec.family).materialize(task, workspace, ctx)
    return workspace
