"""Sterility prechecks: a contaminated arm must fail before any tokens are spent."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import make_claude_config, make_codex_home, make_seed_tree
from runner.sandbox import config_problems, enforce_preconditions, workspace_problems
from runner.spec import RunSpec, RunnerError


def _spec(agent: str, arm: str) -> RunSpec:
    return RunSpec(
        agent=agent,
        arm=arm,
        effort="medium",
        family="comprehension",
        task_id="task-1",
    )


def test_baseline_rejects_config_dir_with_a_plugin(tmp_path: Path, make_study) -> None:
    cfg = make_study()
    config_dir = make_claude_config(tmp_path, plugins=["caveman"])

    problems = config_problems(cfg, "claude", "baseline")

    assert problems, "an installed plugin must break baseline sterility"
    assert str(config_dir) in problems[0]
    assert "caveman" in problems[0]


def test_baseline_accepts_a_clean_config_dir(tmp_path: Path, make_study) -> None:
    cfg = make_study()
    make_claude_config(tmp_path)

    assert config_problems(cfg, "claude", "baseline") == []


def test_codex_baseline_ignores_agent_owned_dotted_entries(
    tmp_path: Path, make_study
) -> None:
    cfg = make_study()
    make_codex_home(tmp_path)

    assert config_problems(cfg, "codex", "baseline") == []


def test_codex_baseline_rejects_an_installed_skill(tmp_path: Path, make_study) -> None:
    cfg = make_study()
    config_dir = make_codex_home(tmp_path, tools=["caveman"])

    problems = config_problems(cfg, "codex", "baseline")

    assert len(problems) == 1
    assert str(config_dir) in problems[0]


def test_baseline_rejects_a_declared_workspace_seed(tmp_path: Path, make_study) -> None:
    def add_seed(raw: dict) -> None:
        raw["arms"]["baseline"] = {"workspace_seed": {"codex": "sandbox/seed-oops"}}

    cfg = make_study(add_seed)
    make_codex_home(tmp_path)

    problems = config_problems(cfg, "codex", "baseline")

    assert any("workspace_seed" in problem for problem in problems)


def test_treatment_config_accepts_exactly_its_own_tool(
    tmp_path: Path, make_study
) -> None:
    """A treatment arm is not held to *baseline* sterility — its own tool is expected."""
    cfg = make_study()
    make_claude_config(tmp_path, suffix="-caveman", plugins=["caveman"])

    assert config_problems(cfg, "claude", "caveman") == []


def test_treatment_config_rejects_a_foreign_tool(tmp_path: Path, make_study) -> None:
    cfg = make_study()
    config_dir = make_claude_config(
        tmp_path, suffix="-caveman", plugins=["caveman", "ponytail"]
    )

    problems = config_problems(cfg, "claude", "caveman")

    # The cache dir and the manifest entry are both reported; neither may name the arm's
    # own tool, and every complaint has to name the path an operator must clean.
    assert problems
    assert all(problem.startswith("foreign tool 'ponytail") for problem in problems)
    assert all(str(config_dir) in problem for problem in problems)


def test_stale_skills_tree_in_workspace_is_a_problem(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / ".agents" / "skills" / "caveman").mkdir(parents=True)

    problems = workspace_problems(workspace)

    assert len(problems) == 1
    assert ".agents/skills" in problems[0]


def test_enforce_preconditions_refuses_a_contaminated_run(
    tmp_path: Path, make_study
) -> None:
    cfg = make_study()
    make_claude_config(tmp_path, plugins=["ponytail"])
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(RunnerError) as excinfo:
        enforce_preconditions(_spec("claude", "baseline"), cfg, workspace)

    assert "sterility precheck failed" in str(excinfo.value)
    assert "ponytail" in str(excinfo.value)


def test_enforce_preconditions_passes_a_clean_seeded_run(
    tmp_path: Path, make_study
) -> None:
    cfg = make_study()
    make_codex_home(tmp_path, suffix="-caveman")
    make_seed_tree(tmp_path, "caveman")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    enforce_preconditions(_spec("codex", "caveman"), cfg, workspace)


def test_enforce_preconditions_passes_a_treatment_arm_with_its_own_tool(
    tmp_path: Path, make_study
) -> None:
    cfg = make_study()
    make_claude_config(tmp_path, suffix="-caveman", plugins=["caveman"])
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    enforce_preconditions(_spec("claude", "caveman"), cfg, workspace)


def test_enforce_preconditions_refuses_a_treatment_arm_with_a_foreign_tool(
    tmp_path: Path, make_study
) -> None:
    """Two tools in one cell makes the arm uninterpretable — fail before tokens are spent."""
    cfg = make_study()
    config_dir = make_claude_config(
        tmp_path, suffix="-caveman", plugins=["caveman", "ponytail"]
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(RunnerError) as excinfo:
        enforce_preconditions(_spec("claude", "caveman"), cfg, workspace)

    message = str(excinfo.value)
    assert "sterility precheck failed" in message
    assert "ponytail" in message
    assert str(config_dir) in message


def test_enforce_preconditions_still_checks_the_workspace_for_a_treatment_arm(
    tmp_path: Path, make_study
) -> None:
    cfg = make_study()
    make_claude_config(tmp_path, suffix="-caveman", plugins=["caveman"])
    workspace = tmp_path / "workspace"
    (workspace / ".agents" / "skills" / "caveman").mkdir(parents=True)

    with pytest.raises(RunnerError) as excinfo:
        enforce_preconditions(_spec("claude", "caveman"), cfg, workspace)

    assert ".agents/skills" in str(excinfo.value)


def test_codex_stock_catalog_is_not_an_installed_tool(tmp_path):
    from runner.sandbox import installed_tools

    home = tmp_path / "codex-home"
    (home / "plugins" / "cache" / "openai-curated-remote" / "github").mkdir(parents=True)
    assert installed_tools("codex", home) == []

    (home / "plugins" / "cache" / "caveman").mkdir(parents=True)
    assert installed_tools("codex", home) == ["caveman"]


def test_wrapped_stdout_result_extraction():
    from runner.agents import _parse_result_object

    plain = '{"is_error": false, "session_id": "abc"}'
    assert _parse_result_object(plain) == {"is_error": False, "session_id": "abc"}

    banner = (
        "  == HEADROOM WRAP: CLAUDE ==\n"
        "  Proxy ready on http://127.0.0.1:8787\n"
        "  Extra args: -p {not json\n"
        + plain
        + "\n"
    )
    assert _parse_result_object(banner) == {"is_error": False, "session_id": "abc"}

    assert _parse_result_object("") is None
    assert _parse_result_object("banner only, no json") is None
    assert _parse_result_object('["a", "json", "array"]') is None
