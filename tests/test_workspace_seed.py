"""Workspace seeding: project-local tool installs must be copied into every fresh workspace."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import make_seed_tree
from runner.spec import RunSpec, RunnerError
from runner.workspace import seed_workspace


def _spec(agent: str = "codex", arm: str = "caveman") -> RunSpec:
    return RunSpec(
        agent=agent,
        arm=arm,
        effort="medium",
        family="comprehension",
        task_id="task-1",
    )


def test_seed_copies_tree_into_workspace(tmp_path: Path, make_study) -> None:
    cfg = make_study()
    make_seed_tree(tmp_path, "caveman", skills=["caveman", "caveman-commit"])
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "task.md").write_text("solve me", encoding="utf-8")

    used = seed_workspace(_spec(), cfg, workspace)

    assert used == tmp_path / "sandbox" / "workspace-seed-caveman-codex"
    assert (workspace / ".agents" / "skills" / "caveman" / "SKILL.md").is_file()
    assert (workspace / ".agents" / "skills" / "caveman-commit" / "SKILL.md").is_file()
    assert (workspace / "skills-lock.json").is_file()
    # Copied, never symlinked: the agent must see plain files it could have installed.
    assert not (workspace / ".agents" / "skills").is_symlink()
    # Materialized task files survive the copy.
    assert (workspace / "task.md").read_text(encoding="utf-8") == "solve me"


def test_seed_missing_path_raises(tmp_path: Path, make_study) -> None:
    cfg = make_study()
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(RunnerError) as excinfo:
        seed_workspace(_spec(), cfg, workspace)

    assert "workspace-seed-caveman-codex" in str(excinfo.value)
    assert not (workspace / ".agents").exists()


def test_no_seed_configured_is_a_no_op(tmp_path: Path, make_study) -> None:
    """The claude caveman arm installs as a plugin, so it declares no workspace seed."""
    cfg = make_study()
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    assert seed_workspace(_spec(agent="claude"), cfg, workspace) is None
    assert list(workspace.iterdir()) == []
