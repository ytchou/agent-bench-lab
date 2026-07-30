"""RunSpec identity and study-config loading (studies/<study>/config/study.yaml)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

_UNSAFE = re.compile(r"[^A-Za-z0-9._]+")


class RunnerError(RuntimeError):
    """Any runner-level failure with a message meant for the operator."""


def _slug(value: str) -> str:
    """Make a value safe for a run_id / directory name."""
    return _UNSAFE.sub("-", value).strip("-")


@dataclass(frozen=True)
class RunSpec:
    """One run: an agent/arm/effort cell applied to one task of one family."""

    agent: str
    arm: str
    effort: str
    family: str
    task_id: str
    rep: int = 1
    phase: str = "main"

    def __post_init__(self) -> None:
        # Convention is main|warmup|gate; not enforced, so studies can add their own.
        if not isinstance(self.phase, str) or not self.phase.strip():
            raise RunnerError("phase must be a non-empty string")

    @property
    def run_id(self) -> str:
        head = "-".join(
            _slug(part)
            for part in (self.agent, self.arm, self.effort, self.family, self.task_id)
        )
        return f"{head}-r{self.rep}"


@dataclass(frozen=True)
class StudyConfig:
    """Parsed study.yaml plus the path layout it implies."""

    name: str
    repo_root: Path
    study_root: Path
    config_path: Path
    raw: dict[str, Any]

    # --- path helpers -------------------------------------------------

    def _resolve(self, value: str) -> Path:
        path = Path(value).expanduser()
        return path if path.is_absolute() else self.repo_root / path

    def _paths(self) -> dict[str, Any]:
        return self.raw.get("paths") or {}

    @property
    def workspaces_dir(self) -> Path:
        return self._resolve(self._paths().get("workspaces", "sandbox/workspaces"))

    @property
    def data_dir(self) -> Path:
        return self._resolve(
            self._paths().get("data", f"studies/{self.name}/data")
        )

    @property
    def runs_jsonl(self) -> Path:
        return self.data_dir / "runs.jsonl"

    @property
    def superseded_jsonl(self) -> Path:
        """Rows displaced by a rerun of the same run_id (history, never analyzed)."""
        return self.data_dir / "superseded.jsonl"

    @property
    def runs_csv(self) -> Path:
        return self.data_dir / "runs.csv"

    def raw_dir(self, run_id: str) -> Path:
        return self.data_dir / "raw" / run_id

    @property
    def prices_path(self) -> Path:
        return self._resolve(self._paths().get("prices", "config/prices.toml"))

    def workspace_dir(self, run_id: str) -> Path:
        return self.workspaces_dir / run_id

    # --- section accessors --------------------------------------------

    def agent_cfg(self, agent: str) -> dict[str, Any]:
        agents = self.raw.get("agents") or {}
        if agent not in agents:
            raise RunnerError(
                f"agent '{agent}' is not defined in {self.config_path} "
                f"(known: {', '.join(sorted(agents)) or 'none'})"
            )
        return agents[agent]

    def arm_cfg(self, arm: str) -> dict[str, Any]:
        arms = self.raw.get("arms") or {}
        if arm not in arms:
            raise RunnerError(
                f"arm '{arm}' is not defined in {self.config_path} "
                f"(known: {', '.join(sorted(arms)) or 'none'})"
            )
        return arms[arm] or {}

    def family_cfg(self, family: str) -> dict[str, Any]:
        families = self.raw.get("families") or {}
        if family not in families:
            raise RunnerError(
                f"family '{family}' is not defined in {self.config_path} "
                f"(known: {', '.join(sorted(families)) or 'none'})"
            )
        return families[family] or {}

    def families(self) -> list[str]:
        return list((self.raw.get("families") or {}).keys())

    def tasks_path(self, family: str) -> Path:
        family_cfg = self.family_cfg(family)
        tasks = family_cfg.get("tasks")
        if not tasks:
            raise RunnerError(
                f"family '{family}' has no 'tasks' path in {self.config_path}"
            )
        return self._resolve(tasks)

    def config_dir(self, agent: str, arm: str) -> Path:
        """Sandbox config dir for an (agent, arm) cell: base dir + the arm's suffix."""
        base = self.agent_cfg(agent).get("config_dir")
        if not base:
            raise RunnerError(
                f"agents.{agent}.config_dir missing in {self.config_path}"
            )
        suffix = (self.arm_cfg(arm).get("config_dir_suffix") or "")
        return self._resolve(f"{base}{suffix}")

    def effort_overrides(self, agent: str, effort: str) -> dict[str, Any]:
        """Return {'args': [...], 'env': {...}} for an effort level on one agent."""
        efforts = self.raw.get("efforts") or {}
        if effort not in efforts:
            raise RunnerError(
                f"effort '{effort}' is not defined in {self.config_path} "
                f"(known: {', '.join(sorted(efforts)) or 'none'})"
            )
        per_agent = (efforts[effort] or {}).get(agent) or {}
        return {
            "args": list(per_agent.get("args") or []),
            "env": dict(per_agent.get("env") or {}),
        }

    def arm_wrap(self, arm: str) -> str | None:
        """Name of the wrapper binary the arm runs the agent under, or None."""
        wrap = self.arm_cfg(arm).get("wrap")
        return str(wrap) if wrap else None

    def extra_agent_args(self, agent: str, arm: str) -> list[str]:
        """Per-arm extra CLI args appended to the agent's own command."""
        extra = (self.arm_cfg(arm).get("extra_agent_args") or {}).get(agent)
        return [str(a) for a in (extra or [])]

    def activation_signatures(self, agent: str, arm: str) -> list[str]:
        """Signatures whose presence in the session log proves the arm's tool activated."""
        signatures = (self.arm_cfg(arm).get("activation_signatures") or {}).get(agent)
        return list(signatures or [])

    def log_flags(self, agent: str) -> dict[str, list[str]]:
        """Top-level `log_flags:` narrowed to one agent: flag name -> substrings to count.

        Flags with no substrings for this agent are kept (they report 0) so the row's
        columns stay identical across agents.
        """
        configured = self.raw.get("log_flags") or {}
        return {
            str(name): [str(s) for s in ((per_agent or {}).get(agent) or [])]
            for name, per_agent in configured.items()
        }

    @property
    def agent_timeout_s(self) -> int:
        return int((self.raw.get("timeouts") or {}).get("agent_run_s", 1800))

    @property
    def git_timeout_s(self) -> int:
        return int((self.raw.get("timeouts") or {}).get("git_s", 600))

    @property
    def grading_timeout_s(self) -> int:
        return int((self.raw.get("timeouts") or {}).get("grading_s", 3600))


def load_study(name: str, repo_root: Path | None = None) -> StudyConfig:
    """Load studies/<name>/config/study.yaml into a StudyConfig."""
    root = (repo_root or REPO_ROOT).resolve()
    study_root = root / "studies" / name
    config_path = study_root / "config" / "study.yaml"
    if not config_path.is_file():
        raise RunnerError(f"study config not found: {config_path}")
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise RunnerError(f"study config must be a YAML mapping: {config_path}")
    return StudyConfig(
        name=name,
        repo_root=root,
        study_root=study_root,
        config_path=config_path,
        raw=raw,
    )
