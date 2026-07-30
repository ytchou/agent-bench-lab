"""Campaign ordering, resume state and batch budgets — pure functions, no agent runs.

The ordering assertions here are the executable form of `docs/run-order.md`: if one of
them fails, the campaign is no longer running in the order that was pre-registered.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from conftest import STUDY_NAME, make_tasks
from runner.campaign import (
    campaign_config,
    completed_run_ids,
    format_status,
    pending_cells,
    plan_cells,
    plan_specs,
    slice_tasks,
    status_report,
    take_cells,
)
from runner.spec import RunnerError, load_study

REAL_REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_STUDY = "token-saving-tools"

ARMS = ["baseline", "caveman", "ponytail", "headroom"]


def _campaign_study(raw: dict[str, Any]) -> None:
    """Fixture study shaped like the real one: 2 tiers, 2 agents, 4 arms, 2 families."""
    raw["arms"] = {arm: {} for arm in ARMS}
    raw["arms"]["rtk"] = {}  # present in the study, deliberately not scheduled
    raw["efforts"] = {
        "medium": {"claude": {}, "codex": {}},
        "xhigh": {"claude": {}, "codex": {}},
    }
    raw["families"] = {
        "comprehension": {"tasks": f"studies/{STUDY_NAME}/tasks/comprehension.json"},
        "swebench": {"tasks": f"studies/{STUDY_NAME}/tasks/swebench.json"},
    }
    raw["campaign"] = {
        "phase": "main",
        "efforts": ["medium", "xhigh"],
        "agents": ["claude", "codex"],
        "arms": list(ARMS),
        "families": ["comprehension", "swebench"],
        "reps": {
            "claude": {"default": 1, "families": {"swebench": 2}},
            "codex": {"default": 2},
        },
        "exclude_tasks": {"comprehension": ["c3"]},
    }


@pytest.fixture
def campaign_cfg(tmp_path: Path, make_study):
    cfg = make_study(_campaign_study)
    make_tasks(tmp_path, "comprehension", ["c1", "c2", "c3"])
    make_tasks(tmp_path, "swebench", ["s1", "s2"], id_field="instance_id")
    return cfg


def _write_rows(cfg, rows: list[dict[str, Any]]) -> None:
    path = cfg.runs_jsonl
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


def _row(run_id: str, **overrides: Any) -> dict[str, Any]:
    agent, arm, effort, family, *rest = run_id.split("-")
    row = {
        "run_id": run_id,
        "agent": agent,
        "arm": arm,
        "effort": effort,
        "family": family,
        "task_id": "-".join(rest[:-1]),
        "rep": int(rest[-1].lstrip("r")),
        "phase": "main",
        "status": "ok",
        "discard_reason": None,
        "cost_usd_simulated": 1.0,
    }
    row.update(overrides)
    return row


# --- the plan ---------------------------------------------------------


def test_excluded_tasks_are_dropped_from_the_slice(campaign_cfg) -> None:
    tasks = slice_tasks(campaign_cfg, campaign_config(campaign_cfg))

    assert tasks == [
        ("comprehension", "c1"),
        ("comprehension", "c2"),
        ("swebench", "s1"),
        ("swebench", "s2"),
    ]


def test_excluding_an_unknown_task_is_an_error(tmp_path: Path, make_study) -> None:
    def mutate(raw: dict[str, Any]) -> None:
        _campaign_study(raw)
        raw["campaign"]["exclude_tasks"] = {"comprehension": ["nope"]}

    cfg = make_study(mutate)
    make_tasks(tmp_path, "comprehension", ["c1", "c2", "c3"])
    make_tasks(tmp_path, "swebench", ["s1", "s2"], id_field="instance_id")

    with pytest.raises(RunnerError, match="nope"):
        slice_tasks(cfg, campaign_config(cfg))


def test_plan_size_follows_the_replication_policy(campaign_cfg) -> None:
    specs = plan_specs(campaign_cfg)

    # per tier: claude (4 tasks + 2 swe reps) x 4 arms = 24; codex 8 cells x 4 arms = 32.
    assert len(specs) == 2 * (24 + 32)
    assert {spec.phase for spec in specs} == {"main"}


def test_the_withdrawn_arm_is_never_scheduled(campaign_cfg) -> None:
    assert "rtk" not in {spec.arm for spec in plan_specs(campaign_cfg)}
    assert set(campaign_cfg.raw["arms"]) >= {"rtk"}  # still declared, just not scheduled


def test_every_medium_run_precedes_every_xhigh_run(campaign_cfg) -> None:
    efforts = [spec.effort for spec in plan_specs(campaign_cfg)]
    boundary = efforts.index("xhigh")

    assert efforts == ["medium"] * boundary + ["xhigh"] * (len(efforts) - boundary)


def test_the_claude_slice_runs_first_in_each_tier(campaign_cfg) -> None:
    specs = plan_specs(campaign_cfg)

    for effort in ("medium", "xhigh"):
        agents = [spec.agent for spec in specs if spec.effort == effort]
        assert set(agents[: agents.index("codex")]) == {"claude"}
        assert set(agents[agents.index("codex") :]) == {"codex"}


def test_rep_major_ordering_within_every_slice(campaign_cfg) -> None:
    specs = plan_specs(campaign_cfg)

    for effort in ("medium", "xhigh"):
        for agent in ("claude", "codex"):
            reps = [
                spec.rep for spec in specs if spec.effort == effort and spec.agent == agent
            ]
            assert reps == sorted(reps), (effort, agent)


def test_all_arms_of_a_task_are_scheduled_adjacently(campaign_cfg) -> None:
    specs = plan_specs(campaign_cfg)
    positions: dict[tuple, list[int]] = {}
    for index, spec in enumerate(specs):
        key = (spec.effort, spec.agent, spec.family, spec.task_id, spec.rep)
        positions.setdefault(key, []).append(index)

    for key, indices in positions.items():
        assert len(indices) == len(ARMS), key
        assert indices == list(range(indices[0], indices[0] + len(ARMS))), key


def test_the_starting_arm_rotates_across_tasks(campaign_cfg) -> None:
    cells = plan_cells(campaign_cfg)
    first_slice = [cell for cell in cells if cell.effort == "medium" and cell.agent == "claude"]
    starts = [cell.specs[0].arm for cell in first_slice[: len(ARMS)]]

    assert len(set(starts)) == len(ARMS)
    assert starts == ARMS  # offset = task index, so the rotation is deterministic


def test_run_ids_are_deterministic_and_unique(campaign_cfg) -> None:
    specs = plan_specs(campaign_cfg)
    run_ids = [spec.run_id for spec in specs]

    assert len(set(run_ids)) == len(run_ids)
    assert "claude-baseline-medium-comprehension-c1-r1" in run_ids
    assert plan_specs(campaign_cfg) == specs  # regeneration is byte-for-byte the same order


# --- the registered plan of the real study ----------------------------


def test_the_real_study_plan_matches_the_pre_registration() -> None:
    """Read-only check against the frozen config (amendments A1-A2): 1,088 runs, 34 tasks, no rtk."""
    cfg = load_study(REAL_STUDY, repo_root=REAL_REPO_ROOT)
    campaign = campaign_config(cfg)
    specs = plan_specs(cfg, campaign)

    assert len(slice_tasks(cfg, campaign)) == 34
    assert len(specs) == 1088
    assert "rtk" not in {spec.arm for spec in specs}
    for effort in ("medium", "xhigh"):
        tier = [spec for spec in specs if spec.effort == effort]
        assert len(tier) == 544
        assert len([spec for spec in tier if spec.agent == "claude"]) == 272
        assert len([spec for spec in tier if spec.agent == "codex"]) == 272


# --- resume -----------------------------------------------------------


def test_resume_excludes_exactly_the_runs_recorded_ok(campaign_cfg) -> None:
    cells = plan_cells(campaign_cfg)
    first = cells[0].specs
    _write_rows(
        campaign_cfg,
        [
            _row(first[0].run_id),
            _row(first[1].run_id, status="discard", discard_reason="nonzero_exit: 1"),
            _row(cells[5].specs[0].run_id),
        ],
    )

    done = completed_run_ids(campaign_cfg, campaign_config(campaign_cfg))
    remaining = {
        spec.run_id for cell in pending_cells(cells, done) for spec in cell.specs
    }

    assert done == {first[0].run_id, cells[5].specs[0].run_id}
    assert remaining == {spec.run_id for cell in cells for spec in cell.specs} - done


def test_resume_ignores_rows_from_another_phase(campaign_cfg) -> None:
    spec = plan_cells(campaign_cfg)[0].specs[0]
    _write_rows(campaign_cfg, [_row(spec.run_id, phase="warmup")])

    assert completed_run_ids(campaign_cfg, campaign_config(campaign_cfg)) == set()


def test_missing_results_file_means_nothing_is_done(campaign_cfg) -> None:
    assert completed_run_ids(campaign_cfg, campaign_config(campaign_cfg)) == set()


def test_a_finished_cell_disappears_from_the_pending_list(campaign_cfg) -> None:
    cells = plan_cells(campaign_cfg)
    done = {spec.run_id for spec in cells[0].specs}

    pending = pending_cells(cells, done)

    assert len(pending) == len(cells) - 1
    assert pending[0].task_id == cells[1].task_id


# --- batch budget -----------------------------------------------------


def test_the_run_budget_cuts_only_at_task_boundaries(campaign_cfg) -> None:
    cells = plan_cells(campaign_cfg)

    batch = take_cells(cells, max_runs=5)

    assert len(batch) == 2  # the cell that crosses the budget is finished, not split
    assert sum(len(cell.specs) for cell in batch) == 8
    assert all(len(cell.specs) == len(ARMS) for cell in batch)


def test_an_exact_budget_stops_on_the_boundary(campaign_cfg) -> None:
    batch = take_cells(plan_cells(campaign_cfg), max_runs=len(ARMS))

    assert len(batch) == 1


def test_no_budget_takes_the_whole_plan(campaign_cfg) -> None:
    cells = plan_cells(campaign_cfg)

    assert take_cells(cells, None) == list(cells)
    assert take_cells(cells, 10_000) == list(cells)
    assert take_cells(cells, 0) == []


# --- status -----------------------------------------------------------


def test_status_counts_done_remaining_and_discarded(campaign_cfg) -> None:
    cells = plan_cells(campaign_cfg)
    first = cells[0].specs
    _write_rows(
        campaign_cfg,
        [
            _row(first[0].run_id, cost_usd_simulated=2.0),
            _row(first[1].run_id, status="discard", discard_reason="timeout after 1800s"),
        ],
    )

    report = status_report(campaign_cfg)
    group = next(
        g
        for g in report.groups
        if (g.effort, g.agent, g.arm, g.family)
        == (first[0].effort, first[0].agent, first[0].arm, first[0].family)
    )

    assert report.total == 112
    assert report.done == 1
    assert report.remaining == 111
    assert report.discarded == 1
    assert group.done == 1
    assert group.remaining + group.done == 2  # c1 + c2 for this (tier, agent, arm, family)


def test_status_reports_raw_per_arm_spend_without_comparisons(campaign_cfg) -> None:
    cells = plan_cells(campaign_cfg)
    _write_rows(
        campaign_cfg,
        [
            _row(cells[0].specs[0].run_id, cost_usd_simulated=2.0),
            _row(cells[0].specs[1].run_id, cost_usd_simulated=0.5),
        ],
    )

    report = status_report(campaign_cfg)
    text = format_status(report)

    assert report.spend_by_arm[cells[0].specs[0].arm] == 2.0
    assert report.spend_by_arm[cells[0].specs[1].arm] == 0.5
    assert set(report.spend_by_arm) == set(ARMS)
    # The report carries per-arm totals only; nothing derived from two arms at once.
    assert not any(
        word in text.lower() for word in ("ratio", "saving", "reduction", "delta", "%")
    )
    assert "no-peeking" in text
