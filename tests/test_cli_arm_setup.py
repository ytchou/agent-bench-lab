"""`abl arm-setup --verify`: exit code and table content over fabricated sandbox layouts."""

from __future__ import annotations

from pathlib import Path

from conftest import STUDY_NAME, make_claude_config, make_codex_home, make_seed_tree
from runner.cli import main
from runner.sandbox import check_all_arms


def _clean_layout(root: Path) -> None:
    """Every arm of the fixture study, set up exactly as the study declares it."""
    make_claude_config(root)
    make_claude_config(root, suffix="-caveman", plugins=["caveman"])
    make_codex_home(root)
    make_codex_home(root, suffix="-caveman")
    make_seed_tree(root, "caveman")


def _verify() -> int:
    return main(["arm-setup", "--verify", "--study", STUDY_NAME])


def test_verify_passes_on_a_clean_layout(tmp_path: Path, make_study, capsys) -> None:
    make_study()
    _clean_layout(tmp_path)

    assert _verify() == 0
    out = capsys.readouterr().out
    assert "FAIL" not in out
    assert out.count("PASS") == 4


def test_verify_returns_nonzero_on_a_contaminated_baseline(
    tmp_path: Path, make_study, capsys
) -> None:
    make_study()
    _clean_layout(tmp_path)
    make_claude_config(tmp_path, plugins=["caveman"])  # contaminate the baseline config

    assert _verify() == 1
    assert "FAIL" in capsys.readouterr().out


def test_verify_returns_nonzero_when_a_seed_tree_is_missing(
    tmp_path: Path, make_study, capsys
) -> None:
    make_study()
    _clean_layout(tmp_path)
    seed = tmp_path / "sandbox" / "workspace-seed-caveman-codex"
    (seed / "skills-lock.json").unlink()
    (seed / ".agents" / "skills" / "caveman" / "SKILL.md").unlink()
    (seed / ".agents" / "skills" / "caveman").rmdir()
    (seed / ".agents" / "skills").rmdir()

    assert _verify() == 1
    assert ".agents/skills" in capsys.readouterr().out


def test_verify_returns_nonzero_when_a_config_dir_is_missing(
    tmp_path: Path, make_study, capsys
) -> None:
    make_study()
    make_claude_config(tmp_path)  # only the claude baseline exists

    assert _verify() == 1
    assert "config dir missing" in capsys.readouterr().out


def test_verify_flags_a_foreign_tool_in_a_treatment_arm(
    tmp_path: Path, make_study, capsys
) -> None:
    make_study()
    _clean_layout(tmp_path)
    make_claude_config(tmp_path, suffix="-caveman", plugins=["ponytail"])

    assert _verify() == 1
    assert "foreign tool" in capsys.readouterr().out


def test_arms_marked_unsupported_for_an_agent_are_skipped(
    tmp_path: Path, make_study
) -> None:
    """An empty activation-signature list is how study.yaml says 'arm absent here' (rtk/codex)."""

    def add_claude_only_arm(raw: dict) -> None:
        raw["arms"]["rtk"] = {
            "config_dir_suffix": "-rtk",
            "activation_signatures": {"claude": ["rtk hook claude"], "codex": []},
        }

    cfg = make_study(add_claude_only_arm)
    _clean_layout(tmp_path)

    cells = {(check.agent, check.arm) for check in check_all_arms(cfg)}

    assert ("claude", "rtk") in cells
    assert ("codex", "rtk") not in cells
