"""Sandbox sterility prechecks and per-arm setup verification.

An arm is only interpretable if its sandbox is what the study says it is: baseline =
nothing installed, treatment = exactly one tool. A contaminated baseline already cost
this study 24 reruns, so the checks here are hard errors raised *before* the agent
launches, never warnings.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from runner.spec import RunSpec, RunnerError, StudyConfig

BASELINE_ARM = "baseline"


class SterilityError(RunnerError):
    """A run was refused because its config dir or workspace is contaminated.

    Its own type (not a bare RunnerError) because the batch scheduler must treat it
    differently from every other failure: contamination is not transient, so the run is
    never retried and the campaign stops instead of poisoning the cells after it.
    """

SKILLS_TREE = ".agents/skills"
"""Project-local skill install (npx skills add) — must only exist in a workspace after seeding."""

CODEX_STOCK_ARTIFACTS = {"openai-curated-remote"}
"""Codex CLI's own curated remote-plugin catalog — self-populates in every CODEX_HOME
(verified present in the sterile baseline that produced the clean warm-up runs), so it
is part of the stock environment, not an installed tool."""


def _child_names(path: Path) -> list[str]:
    """Visible child names of a directory ([] when absent); dotted entries are agent-owned."""
    if not path.is_dir():
        return []
    return sorted(child.name for child in path.iterdir() if not child.name.startswith("."))


def _claude_plugins(config_dir: Path) -> list[str]:
    """Plugin names installed into a CLAUDE_CONFIG_DIR (empty list = sterile)."""
    names = set(_child_names(config_dir / "plugins" / "cache"))
    names.update(_child_names(config_dir / "plugins" / "marketplaces"))
    manifest = config_dir / "plugins" / "installed_plugins.json"
    if manifest.is_file():
        try:
            raw = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # An unreadable manifest is contamination, never "nothing installed".
            return sorted(names | {manifest.name})
        names.update(str(key) for key in ((raw or {}).get("plugins") or {}))
    return sorted(names)


def _codex_tools(config_dir: Path) -> list[str]:
    """Tool artifacts installed into a CODEX_HOME (empty list = sterile)."""
    return sorted(
        (set(_child_names(config_dir / "plugins" / "cache")) - CODEX_STOCK_ARTIFACTS)
        | set(_child_names(config_dir / "skills"))
    )


def installed_tools(agent: str, config_dir: Path) -> list[str]:
    """Tools installed into one agent's config dir, however that agent installs them."""
    readers = {"claude": _claude_plugins, "codex": _codex_tools}
    if agent not in readers:
        raise RunnerError(
            f"no sterility reader for agent '{agent}' "
            f"(known: {', '.join(sorted(readers))})"
        )
    return readers[agent](config_dir)


def foreign_tools(agent: str, arm: str, config_dir: Path) -> list[str]:
    """Installed tools not named after `arm`. For baseline nothing matches, so all are foreign."""
    return [name for name in installed_tools(agent, config_dir) if arm not in name.lower()]


# --- run prechecks ----------------------------------------------------


def config_problems(cfg: StudyConfig, agent: str, arm: str) -> list[str]:
    """Sterility complaints about the (agent, arm) config dir; empty means clean.

    Every arm — not just baseline — is held to the set of tools it declares: baseline
    expects nothing installed, a treatment arm expects only its own tool. Anything else
    in the config dir contaminates the cell, so it is a complaint here (raised before the
    agent launches) rather than something only `check_arm` notices after the fact.
    """
    config_dir = cfg.config_dir(agent, arm)
    intruders = foreign_tools(agent, arm, config_dir)
    if arm == BASELINE_ARM:
        problems = [
            f"baseline config dir is not sterile: '{name}' is installed under {config_dir}"
            for name in intruders
        ]
        seed = cfg.workspace_seed(agent, arm)
        if seed is not None:
            problems.append(
                f"baseline arm declares a workspace_seed ({seed}); baseline must run unseeded"
            )
        return problems
    return [
        f"foreign tool '{name}' is installed under {config_dir} "
        f"(arm '{arm}' expects only its own tool)"
        for name in intruders
    ]


def workspace_problems(workspace: Path) -> list[str]:
    """Complaints about a freshly materialized workspace, checked before it is seeded."""
    tree = workspace / SKILLS_TREE
    if tree.exists():
        return [f"workspace already carries a skill install before seeding: {tree}"]
    return []


def enforce_preconditions(spec: RunSpec, cfg: StudyConfig, workspace: Path) -> None:
    """Refuse a contaminated run before the agent launches, naming every offending path."""
    problems = config_problems(cfg, spec.agent, spec.arm) + workspace_problems(workspace)
    if problems:
        raise SterilityError(
            f"sterility precheck failed for {spec.run_id}:\n"
            + "\n".join(f"  - {problem}" for problem in problems)
        )


# --- arm-setup verification -------------------------------------------


@dataclass(frozen=True)
class ArmCheck:
    """Verdict on one (agent, arm) cell's sandbox prerequisites."""

    agent: str
    arm: str
    problems: list[str]

    @property
    def ok(self) -> bool:
        return not self.problems


def arm_applies(cfg: StudyConfig, agent: str, arm: str) -> bool:
    """False when the study declares the arm does not exist for this agent.

    Convention from study.yaml: a treatment arm listing an empty `activation_signatures`
    list for an agent (e.g. rtk on codex) is marked as unsupported there.
    """
    if arm == BASELINE_ARM:
        return True
    signatures = cfg.arm_cfg(arm).get("activation_signatures")
    if signatures is None:
        return True
    return bool((signatures or {}).get(agent))


def _tool_presence_problems(
    cfg: StudyConfig, agent: str, arm: str, config_dir: Path
) -> list[str]:
    """A treatment arm must show its one tool, installed the way the arm declares it."""
    wrapper = cfg.arm_wrap(arm)
    if wrapper:
        if shutil.which(wrapper) is None:
            return [f"arm wrapper '{wrapper}' is not on PATH"]
        return []

    seed = cfg.workspace_seed(agent, arm)
    if seed is not None:
        if not seed.is_dir():
            return [f"workspace seed tree is missing: {seed}"]
        if not (seed / SKILLS_TREE).is_dir():
            return [f"workspace seed has no {SKILLS_TREE} tree: {seed}"]
        return []

    # Config-dir install: a plugin named after the arm, or an arm-named file (rtk's RTK.md).
    if any(arm in name.lower() for name in installed_tools(agent, config_dir)):
        return []
    if any(arm in child.name.lower() for child in config_dir.iterdir()):
        return []
    return [f"no '{arm}' install found under {config_dir}"]


def check_arm(cfg: StudyConfig, agent: str, arm: str) -> ArmCheck:
    """Verify one arm's sandbox prerequisites without installing or changing anything."""
    config_dir = cfg.config_dir(agent, arm)
    if not config_dir.is_dir():
        return ArmCheck(agent, arm, [f"config dir missing: {config_dir}"])

    # config_problems already rejects anything the arm does not declare; check_arm adds
    # only the other half — that the arm's own tool is actually present.
    problems = config_problems(cfg, agent, arm)
    if arm == BASELINE_ARM:
        return ArmCheck(agent, arm, problems)

    problems += _tool_presence_problems(cfg, agent, arm, config_dir)
    return ArmCheck(agent, arm, problems)


def check_all_arms(cfg: StudyConfig) -> list[ArmCheck]:
    """Verify every arm of every agent the study defines, skipping unsupported cells."""
    agents = list((cfg.raw.get("agents") or {}).keys())
    arms = list((cfg.raw.get("arms") or {}).keys())
    return [
        check_arm(cfg, agent, arm)
        for agent in agents
        for arm in arms
        if arm_applies(cfg, agent, arm)
    ]
