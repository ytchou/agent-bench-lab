"""Fresh per-run workspace creation and task materialization."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

from runner.grading import get_adapter
from runner.grading.base import GradeContext, git
from runner.sandbox import enforce_preconditions
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


def seed_workspace(spec: RunSpec, cfg: StudyConfig, workspace: Path) -> Path | None:
    """Copy the arm's project-local install into the workspace; return the seed used, or None.

    Always a real copy, never a symlink: the agent must see the tool as ordinary files it
    could have installed itself, and a symlink would leak the frozen seed into edits.
    """
    seed = cfg.workspace_seed(spec.agent, spec.arm)
    if seed is None:
        return None
    if not seed.is_dir():
        raise RunnerError(
            f"workspace seed for arm '{spec.arm}' on agent '{spec.agent}' is missing: "
            f"{seed} — freeze the tool's project-local install there first; an unseeded "
            f"run would silently measure the baseline under a treatment label"
        )
    shutil.copytree(seed, workspace, dirs_exist_ok=True, symlinks=False)
    print(f"[seed] {spec.run_id}: {seed} -> {workspace}", file=sys.stderr)
    return seed


def prepare_workspace(
    spec: RunSpec, cfg: StudyConfig, task: dict[str, Any], force: bool = False
) -> Path:
    """Create the workspace, materialize the task into it, then seed the arm's tool."""
    workspace = create_workspace(spec, cfg, force=force)
    ctx = grade_context(spec, cfg, timeout_s=cfg.git_timeout_s)
    get_adapter(spec.family).materialize(task, workspace, ctx)
    enforce_preconditions(spec, cfg, workspace)
    seed_workspace(spec, cfg, workspace)
    return workspace
