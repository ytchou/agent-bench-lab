"""Shared fixtures: a throwaway study tree under tmp_path.

Every test builds its own sandbox layout from scratch — nothing here ever reads or writes
the real `sandbox/`, which holds credentials and the frozen arm installs.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

from runner import spec as spec_module
from runner.spec import StudyConfig, load_study

STUDY_NAME = "fixture-study"

BASE_STUDY: dict[str, Any] = {
    "paths": {
        "workspaces": "sandbox/workspaces",
        "data": f"studies/{STUDY_NAME}/data",
        "prices": "config/prices.toml",
    },
    "agents": {
        "claude": {"binary": "claude", "config_dir": "sandbox/claude-config"},
        "codex": {"binary": "codex", "config_dir": "sandbox/codex-home"},
    },
    "arms": {
        "baseline": {},
        "caveman": {
            "config_dir_suffix": "-caveman",
            "workspace_seed": {"codex": "sandbox/workspace-seed-caveman-codex"},
            "activation_signatures": {
                "claude": ["CAVEMAN MODE ACTIVE"],
                "codex": ["$caveman"],
            },
        },
    },
    "families": {
        "comprehension": {"tasks": f"studies/{STUDY_NAME}/tasks/comprehension.json"}
    },
}


@pytest.fixture
def make_study(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., StudyConfig]:
    """Write a study.yaml under tmp_path and load it, with tmp_path as the repo root."""

    def _make(mutate: Callable[[dict[str, Any]], None] | None = None) -> StudyConfig:
        raw = copy.deepcopy(BASE_STUDY)
        if mutate is not None:
            mutate(raw)
        config_path = tmp_path / "studies" / STUDY_NAME / "config" / "study.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
        # `abl` resolves the repo root from the module global; point it at the fixture tree
        # so CLI-level tests can run without a --repo-root flag.
        monkeypatch.setattr(spec_module, "REPO_ROOT", tmp_path)
        return load_study(STUDY_NAME, repo_root=tmp_path)

    return _make


def make_claude_config(root: Path, suffix: str = "", plugins: list[str] | None = None) -> Path:
    """Create a Claude config dir, optionally with plugins installed into it."""
    config_dir = root / "sandbox" / f"claude-config{suffix}"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "CLAUDE.md").write_text("", encoding="utf-8")
    (config_dir / "settings.json").write_text("{}", encoding="utf-8")
    for name in plugins or []:
        (config_dir / "plugins" / "cache" / name).mkdir(parents=True, exist_ok=True)
        manifest = config_dir / "plugins" / "installed_plugins.json"
        installed = json.loads(manifest.read_text(encoding="utf-8")) if manifest.is_file() else {
            "version": 2,
            "plugins": {},
        }
        installed["plugins"][f"{name}@{name}"] = [{"scope": "user", "version": "0"}]
        manifest.write_text(json.dumps(installed), encoding="utf-8")
    return config_dir


def make_codex_home(root: Path, suffix: str = "", tools: list[str] | None = None) -> Path:
    """Create a CODEX_HOME, optionally with tool artifacts installed into it."""
    config_dir = root / "sandbox" / f"codex-home{suffix}"
    (config_dir / "plugins" / "cache").mkdir(parents=True, exist_ok=True)
    # `.system` ships with a sterile CODEX_HOME and must never count as contamination.
    (config_dir / "skills" / ".system").mkdir(parents=True, exist_ok=True)
    (config_dir / "config.toml").write_text("", encoding="utf-8")
    for name in tools or []:
        (config_dir / "skills" / name).mkdir(parents=True, exist_ok=True)
    return config_dir


def make_seed_tree(root: Path, name: str, skills: list[str] | None = None) -> Path:
    """Create a frozen project-local skill seed tree (as `npx skills add` leaves it)."""
    seed = root / "sandbox" / f"workspace-seed-{name}-codex"
    for skill in skills or [name]:
        skill_dir = seed / ".agents" / "skills" / skill
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(f"# {skill}\n", encoding="utf-8")
    (seed / "skills-lock.json").write_text(json.dumps({"skills": skills or [name]}), encoding="utf-8")
    return seed
