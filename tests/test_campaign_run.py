"""Batch execution: retry queue, sterility abort, budgets, and the no-execution modes.

Every test injects a fake `execute_run`, so no agent, workspace or sandbox is ever touched.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from conftest import STUDY_NAME, make_tasks
from runner import cli
from runner.campaign import MAX_RETRIES, plan_cells, run_batch
from runner.sandbox import SterilityError
from runner.spec import RunSpec, RunnerError, StudyConfig

ARMS = ["baseline", "caveman", "ponytail", "headroom"]


def _campaign_study(raw: dict[str, Any]) -> None:
    raw["arms"] = {arm: {} for arm in ARMS}
    raw["efforts"] = {"medium": {"claude": {}, "codex": {}}}
    raw["families"] = {
        "comprehension": {"tasks": f"studies/{STUDY_NAME}/tasks/comprehension.json"}
    }
    raw["campaign"] = {
        "phase": "main",
        "efforts": ["medium"],
        "agents": ["claude"],
        "arms": list(ARMS),
        "families": ["comprehension"],
        "reps": {"claude": {"default": 1}},
    }


@pytest.fixture
def campaign_cfg(tmp_path: Path, make_study) -> StudyConfig:
    cfg = make_study(_campaign_study)
    make_tasks(tmp_path, "comprehension", ["c1", "c2", "c3"])
    return cfg


class FakeRunner:
    """Records every call and replays a scripted outcome per run_id."""

    def __init__(self, failures: dict[str, int] | None = None, raises: dict[str, Any] | None = None):
        self.calls: list[tuple[str, bool]] = []
        self.failures = dict(failures or {})
        self.raises = dict(raises or {})

    def __call__(self, cfg: StudyConfig, spec: RunSpec, force: bool = False) -> dict[str, Any]:
        self.calls.append((spec.run_id, force))
        error = self.raises.get(spec.run_id)
        if error is not None:
            raise error
        remaining = self.failures.get(spec.run_id, 0)
        if remaining:
            self.failures[spec.run_id] = remaining - 1
            return {
                "run_id": spec.run_id,
                "arm": spec.arm,
                "status": "discard",
                "discard_reason": "nonzero_exit: 1: API overloaded",
                "cost_usd_simulated": 0.1,
            }
        return {
            "run_id": spec.run_id,
            "arm": spec.arm,
            "status": "ok",
            "discard_reason": None,
            "cost_usd_simulated": 1.0,
        }

    @property
    def run_ids(self) -> list[str]:
        return [run_id for run_id, _ in self.calls]


def test_a_batch_runs_its_cells_in_plan_order(campaign_cfg) -> None:
    cells = plan_cells(campaign_cfg)[:2]
    runner = FakeRunner()

    report = run_batch(campaign_cfg, cells, execute=runner, log=lambda _: None)

    assert runner.run_ids == [spec.run_id for cell in cells for spec in cell.specs]
    assert (report.attempted, report.succeeded) == (8, 8)
    assert report.discarded == []
    assert report.spend_by_arm == {arm: 2.0 for arm in ARMS}


def test_a_failed_run_is_requeued_at_the_end_of_the_batch(campaign_cfg) -> None:
    cells = plan_cells(campaign_cfg)[:2]
    victim = cells[0].specs[1].run_id
    runner = FakeRunner(failures={victim: 1})

    report = run_batch(campaign_cfg, cells, execute=runner, log=lambda _: None)

    assert runner.run_ids[-1] == victim  # retried behind the whole batch, not in place
    assert runner.run_ids.count(victim) == 2
    assert runner.calls[-1][1] is True  # the retry recreates the failed workspace
    assert report.succeeded == 8
    assert [outcome.run_id for outcome in report.discarded] == [victim]
    assert report.exhausted == []


def test_a_run_is_retried_at_most_twice_then_flagged(campaign_cfg) -> None:
    cells = plan_cells(campaign_cfg)[:1]
    victim = cells[0].specs[0].run_id
    runner = FakeRunner(failures={victim: 99})

    report = run_batch(campaign_cfg, cells, execute=runner, log=lambda _: None)

    assert runner.run_ids.count(victim) == MAX_RETRIES + 1
    assert [outcome.run_id for outcome in report.exhausted] == [victim]
    assert report.succeeded == 3  # the cell's other arms still ran
    assert report.aborted_reason is None


def test_a_runner_error_is_treated_as_retryable_infrastructure(campaign_cfg) -> None:
    cells = plan_cells(campaign_cfg)[:1]
    victim = cells[0].specs[0].run_id
    runner = FakeRunner(raises={victim: RunnerError("docker daemon is not running")})

    report = run_batch(campaign_cfg, cells, execute=runner, log=lambda _: None)

    assert runner.run_ids.count(victim) == MAX_RETRIES + 1
    assert "docker" in report.exhausted[0].discard_reason


def test_a_sterility_failure_aborts_the_campaign_immediately(campaign_cfg) -> None:
    cells = plan_cells(campaign_cfg)[:2]
    victim = cells[0].specs[1].run_id
    runner = FakeRunner(raises={victim: SterilityError("baseline config dir is not sterile")})

    report = run_batch(campaign_cfg, cells, execute=runner, log=lambda _: None)

    assert runner.run_ids == [cells[0].specs[0].run_id, victim]  # nothing after the failure
    assert runner.run_ids.count(victim) == 1  # never retried
    assert "not sterile" in report.aborted_reason
    assert report.cells_skipped == 2


def test_the_time_budget_stops_at_a_cell_boundary(campaign_cfg) -> None:
    cells = plan_cells(campaign_cfg)[:3]
    ticks = iter([0.0] + [60.0 * (i + 1) for i in range(20)])
    runner = FakeRunner()

    report = run_batch(
        campaign_cfg,
        cells,
        execute=runner,
        max_minutes=1,
        clock=lambda: next(ticks),
        log=lambda _: None,
    )

    assert report.cells_completed == 1
    assert report.cells_skipped == 2
    assert runner.run_ids == [spec.run_id for spec in cells[0].specs]
    assert "time budget" in report.stopped_reason


def test_an_aborted_batch_still_reports_what_it_spent(campaign_cfg) -> None:
    cells = plan_cells(campaign_cfg)[:2]
    victim = cells[0].specs[1].run_id
    runner = FakeRunner(raises={victim: SterilityError("contaminated")})

    report = run_batch(campaign_cfg, cells, execute=runner, log=lambda _: None)

    assert report.spend_by_arm[cells[0].specs[0].arm] == 1.0
    assert report.attempted == 1


# --- CLI modes that must never execute --------------------------------


def _explode(*args: Any, **kwargs: Any) -> dict[str, Any]:
    raise AssertionError("the execution path must not be reached")


def test_dry_run_prints_the_ordered_ids_without_running(
    campaign_cfg, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(cli, "execute_run", _explode)

    code = cli.main(["campaign", "--dry-run", "--study", STUDY_NAME, "--max-runs", "5"])

    printed = capsys.readouterr().out.split()
    assert code == 0
    assert printed == [
        spec.run_id for cell in plan_cells(campaign_cfg)[:2] for spec in cell.specs
    ]


def test_status_prints_progress_without_running(campaign_cfg, monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "execute_run", _explode)

    code = cli.main(["campaign", "--status", "--study", STUDY_NAME])
    out = capsys.readouterr().out

    assert code == 0
    assert "progress: 0 of 12" in out
    assert "simulated spend" in out


def test_campaign_requires_a_mode(campaign_cfg) -> None:
    with pytest.raises(SystemExit):
        cli.main(["campaign", "--study", STUDY_NAME])
