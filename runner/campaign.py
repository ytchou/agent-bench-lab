"""Campaign scheduling for the main experiment: `abl campaign`.

The run order is pre-registered in `studies/<study>/docs/run-order.md` and is not a
tuning knob. Outermost to innermost it is:

1. **Tier** — every medium-effort run before any xhigh run.
2. **Agent slice** — inside a tier, the Claude slice first (checkpoint 1a).
3. **Rep-major** — every rep-1 run of a slice before any rep-2 run, so replicates never
   land inside the provider's 5-minute prompt-cache TTL.
4. **Task-complete cells** — all arms of one (effort, agent, family, task, rep) are
   scheduled adjacently, so a batch boundary can never leave a task with treatment runs
   and no baseline.
5. **Arm rotation** — the starting arm rotates per task, so no arm is systematically
   first and time-of-day API drift spreads across arms instead of confounding one.

State lives in the study's `runs.jsonl`, not in a side database: a run is done iff a row
with its run_id is present with `status == "ok"`. Resume after an interruption is
therefore automatic, and re-running the same command is idempotent.

Everything above `run_batch` is a pure function of (study config, results file), so the
order, the resume set and the batch budget are testable without launching an agent.
"""

from __future__ import annotations

import json
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from runner.grading import load_tasks, task_id_of
from runner.sandbox import SterilityError
from runner.spec import RunSpec, RunnerError, StudyConfig

MAX_RETRIES = 2
"""Infrastructure retries per run, after the first attempt (analysis-plan discard rule)."""

NO_PEEKING_NOTE = (
    "raw per-arm totals only — no with-tool vs baseline comparison is computed "
    "before the campaign ends (no-peeking protocol, analysis-plan.md)"
)

ExecuteFn = Callable[..., dict[str, Any]]
"""`execute_run`-shaped callable: (cfg, spec, force=bool) -> results row."""


# --- configuration ----------------------------------------------------


@dataclass(frozen=True)
class CampaignConfig:
    """The `campaign:` block of study.yaml: which cells exist and in what order."""

    phase: str
    efforts: tuple[str, ...]
    agents: tuple[str, ...]
    arms: tuple[str, ...]
    families: tuple[str, ...]
    reps: dict[str, Any]
    exclude_tasks: dict[str, tuple[str, ...]]

    def reps_for(self, agent: str, family: str) -> int:
        """Replicate count for one (agent, family) cell — k=1 unless the study says more."""
        per_agent = self.reps.get(agent) or {}
        per_family = (per_agent.get("families") or {}).get(family)
        value = per_family if per_family is not None else per_agent.get("default", 1)
        count = int(value)
        if count < 1:
            raise RunnerError(
                f"campaign.reps for agent '{agent}' / family '{family}' is {count}; "
                f"a scheduled cell needs at least one replicate"
            )
        return count

    def excluded(self, family: str) -> set[str]:
        return set(self.exclude_tasks.get(family) or ())


def _require_list(raw: dict[str, Any], key: str, fallback: Iterable[str]) -> tuple[str, ...]:
    values = tuple(str(value) for value in (raw.get(key) or fallback))
    if not values:
        raise RunnerError(f"campaign.{key} is empty; nothing to schedule")
    return values


def campaign_config(cfg: StudyConfig) -> CampaignConfig:
    """Read and validate `campaign:` from study.yaml, defaulting to the study's own lists.

    Every name is checked against the study's own `agents:` / `arms:` / `efforts:` /
    `families:` blocks here rather than at run time, so a typo in the campaign block fails
    before the first agent launches instead of 200 runs in.
    """
    raw = cfg.raw.get("campaign") or {}
    if not isinstance(raw, dict):
        raise RunnerError(f"campaign: must be a mapping in {cfg.config_path}")

    campaign = CampaignConfig(
        phase=str(raw.get("phase", "main")),
        efforts=_require_list(raw, "efforts", (cfg.raw.get("efforts") or {}).keys()),
        agents=_require_list(raw, "agents", (cfg.raw.get("agents") or {}).keys()),
        arms=_require_list(raw, "arms", (cfg.raw.get("arms") or {}).keys()),
        families=_require_list(raw, "families", cfg.families()),
        reps=dict(raw.get("reps") or {}),
        exclude_tasks={
            str(family): tuple(str(task_id) for task_id in (ids or []))
            for family, ids in (raw.get("exclude_tasks") or {}).items()
        },
    )

    for agent in campaign.agents:
        cfg.agent_cfg(agent)
    for arm in campaign.arms:
        cfg.arm_cfg(arm)
    for family in campaign.families:
        cfg.family_cfg(family)
    for effort in campaign.efforts:
        cfg.effort_overrides(campaign.agents[0], effort)
    return campaign


# --- ordering ---------------------------------------------------------


@dataclass(frozen=True)
class TaskCell:
    """All arms of one (effort, agent, family, task, rep): the campaign's atomic unit.

    Batches are cut between cells and never inside one, which is what makes rule 4 of
    run-order.md ("a stop at any batch boundary orphans no treatment run") mechanical.
    """

    effort: str
    agent: str
    family: str
    task_id: str
    rep: int
    specs: tuple[RunSpec, ...]

    @property
    def label(self) -> str:
        return f"{self.effort}/{self.agent}/{self.family}/{self.task_id}/r{self.rep}"

    def without(self, run_ids: set[str]) -> "TaskCell":
        """Same cell with the given run_ids dropped (used to resume a half-done cell)."""
        kept = tuple(spec for spec in self.specs if spec.run_id not in run_ids)
        return TaskCell(self.effort, self.agent, self.family, self.task_id, self.rep, kept)


def slice_tasks(cfg: StudyConfig, campaign: CampaignConfig) -> list[tuple[str, str]]:
    """(family, task_id) pairs of the main experiment, in family then manifest order.

    Tasks spent on the step-0 gate and the step-1 warm-up are listed in
    `campaign.exclude_tasks` and dropped here, so the main experiment runs the fresh
    subset of each manifest only.
    """
    tasks: list[tuple[str, str]] = []
    for family in campaign.families:
        excluded = campaign.excluded(family)
        seen = set()
        for task in load_tasks(cfg, family):
            task_id = task_id_of(family, task)
            seen.add(task_id)
            if task_id not in excluded:
                tasks.append((family, task_id))
        unknown = sorted(excluded - seen)
        if unknown:
            raise RunnerError(
                f"campaign.exclude_tasks.{family} names tasks that are not in "
                f"{cfg.tasks_path(family)}: {', '.join(unknown)}"
            )
    if not tasks:
        raise RunnerError("the campaign schedules no tasks (everything is excluded)")
    return tasks


def _rotate(arms: Sequence[str], offset: int) -> tuple[str, ...]:
    """Arms with the starting position advanced by `offset` (rule 5)."""
    pivot = offset % len(arms)
    return tuple(arms[pivot:]) + tuple(arms[:pivot])


def plan_cells(cfg: StudyConfig, campaign: CampaignConfig | None = None) -> list[TaskCell]:
    """The whole campaign as ordered cells — the pre-registered order, in full."""
    campaign = campaign or campaign_config(cfg)
    tasks = slice_tasks(cfg, campaign)

    cells: list[TaskCell] = []
    for effort in campaign.efforts:                                  # 1. tier
        for agent in campaign.agents:                                # 2. agent slice
            max_rep = max(campaign.reps_for(agent, family) for family, _ in tasks)
            for rep in range(1, max_rep + 1):                        # 3. rep-major
                for index, (family, task_id) in enumerate(tasks):    # 4. task-complete
                    if rep > campaign.reps_for(agent, family):
                        continue
                    specs = tuple(
                        RunSpec(
                            agent=agent,
                            arm=arm,
                            effort=effort,
                            family=family,
                            task_id=task_id,
                            rep=rep,
                            phase=campaign.phase,
                        )
                        for arm in _rotate(campaign.arms, index)     # 5. arm rotation
                    )
                    cells.append(TaskCell(effort, agent, family, task_id, rep, specs))
    return cells


def plan_specs(cfg: StudyConfig, campaign: CampaignConfig | None = None) -> list[RunSpec]:
    """Every run of the campaign, flattened into the order it will be executed in."""
    return [spec for cell in plan_cells(cfg, campaign) for spec in cell.specs]


# --- state (derived from runs.jsonl) ----------------------------------


def _read_rows(path: Path) -> list[dict[str, Any]]:
    """Rows of a results JSONL file, or [] when the campaign has not written one yet."""
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def campaign_rows(cfg: StudyConfig, campaign: CampaignConfig) -> list[dict[str, Any]]:
    """Live results rows of this campaign's phase (the source of truth for resume)."""
    return [row for row in _read_rows(cfg.runs_jsonl) if row.get("phase") == campaign.phase]


def historical_rows(cfg: StudyConfig, campaign: CampaignConfig) -> list[dict[str, Any]]:
    """Live rows plus superseded ones: every attempt that ever cost money or was discarded."""
    superseded = [
        row for row in _read_rows(cfg.superseded_jsonl) if row.get("phase") == campaign.phase
    ]
    return campaign_rows(cfg, campaign) + superseded


def completed_run_ids(cfg: StudyConfig, campaign: CampaignConfig) -> set[str]:
    """Run ids that count as done: a live row of this phase with status ok."""
    return {
        str(row.get("run_id"))
        for row in campaign_rows(cfg, campaign)
        if row.get("status") == "ok" and row.get("run_id")
    }


def pending_cells(cells: Sequence[TaskCell], done: set[str]) -> list[TaskCell]:
    """The plan minus what is already done, with fully finished cells dropped."""
    remaining = [cell.without(done) for cell in cells]
    return [cell for cell in remaining if cell.specs]


def take_cells(cells: Sequence[TaskCell], max_runs: int | None = None) -> list[TaskCell]:
    """The next batch under a run budget, cut only at cell (task) boundaries.

    The budget is a floor, not a ceiling: the cell that crosses it is finished rather than
    split, because half a cell is exactly the orphaned-treatment state rule 4 forbids.
    """
    if max_runs is None:
        return list(cells)
    if max_runs <= 0:
        return []
    taken: list[TaskCell] = []
    total = 0
    for cell in cells:
        taken.append(cell)
        total += len(cell.specs)
        if total >= max_runs:
            break
    return taken


# --- status report ----------------------------------------------------


@dataclass(frozen=True)
class GroupStatus:
    """Progress of one (tier, agent, arm, family) group of the plan."""

    effort: str
    agent: str
    arm: str
    family: str
    done: int
    remaining: int
    discarded: int


@dataclass(frozen=True)
class StatusReport:
    """`abl campaign --status`: plan vs. state, with raw per-arm spend and no comparisons."""

    total: int
    done: int
    discarded: int
    groups: list[GroupStatus]
    spend_by_arm: dict[str, float]

    @property
    def remaining(self) -> int:
        return self.total - self.done


def status_report(cfg: StudyConfig, campaign: CampaignConfig | None = None) -> StatusReport:
    """Compare the pre-registered plan against runs.jsonl, per (tier, agent, arm, family).

    Spend is the raw sum of `cost_usd_simulated` per arm over every attempt ever made
    (live plus superseded rows) — an arm's cost is what it cost, including discards.
    Deliberately no ratios or differences between arms: see NO_PEEKING_NOTE.
    """
    campaign = campaign or campaign_config(cfg)
    specs = plan_specs(cfg, campaign)
    planned = {spec.run_id for spec in specs}
    done = completed_run_ids(cfg, campaign) & planned

    discarded_by_group: dict[tuple[str, str, str, str], int] = {}
    spend_by_arm: dict[str, float] = {arm: 0.0 for arm in campaign.arms}
    for row in historical_rows(cfg, campaign):
        if row.get("run_id") not in planned:
            continue
        arm = str(row.get("arm"))
        cost = row.get("cost_usd_simulated")
        if arm in spend_by_arm and isinstance(cost, (int, float)):
            spend_by_arm[arm] += float(cost)
        if row.get("status") != "ok":
            key = (str(row.get("effort")), str(row.get("agent")), arm, str(row.get("family")))
            discarded_by_group[key] = discarded_by_group.get(key, 0) + 1

    counters: dict[tuple[str, str, str, str], list[int]] = {}
    order: list[tuple[str, str, str, str]] = []
    for spec in specs:
        key = (spec.effort, spec.agent, spec.arm, spec.family)
        if key not in counters:
            counters[key] = [0, 0]
            order.append(key)
        counters[key][0 if spec.run_id in done else 1] += 1

    groups = [
        GroupStatus(*key, done=counters[key][0], remaining=counters[key][1],
                    discarded=discarded_by_group.get(key, 0))
        for key in order
    ]
    return StatusReport(
        total=len(specs),
        done=len(done),
        discarded=sum(discarded_by_group.values()),
        groups=groups,
        spend_by_arm={arm: round(total, 6) for arm, total in spend_by_arm.items()},
    )


# --- execution --------------------------------------------------------


@dataclass(frozen=True)
class RunOutcome:
    """What one attempt at one run produced, as read back from its results row."""

    run_id: str
    arm: str
    attempt: int
    status: str
    discard_reason: str | None
    cost_usd_simulated: float | None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass
class BatchReport:
    """End-of-batch accounting: attempts, discards, wall time, raw per-arm spend."""

    attempted: int = 0
    succeeded: int = 0
    outcomes: list[RunOutcome] = field(default_factory=list)
    exhausted: list[RunOutcome] = field(default_factory=list)
    cells_completed: int = 0
    cells_skipped: int = 0
    wall_s: float = 0.0
    spend_by_arm: dict[str, float] = field(default_factory=dict)
    cumulative_spend_by_arm: dict[str, float] = field(default_factory=dict)
    progress_done: int = 0
    progress_total: int = 0
    stopped_reason: str = "batch complete"
    aborted_reason: str | None = None

    @property
    def discarded(self) -> list[RunOutcome]:
        return [outcome for outcome in self.outcomes if not outcome.ok]


def _stderr(message: str) -> None:
    print(message, file=sys.stderr)


def _outcome(spec: RunSpec, attempt: int, row: dict[str, Any]) -> RunOutcome:
    cost = row.get("cost_usd_simulated")
    return RunOutcome(
        run_id=spec.run_id,
        arm=spec.arm,
        attempt=attempt,
        status=str(row.get("status")),
        discard_reason=row.get("discard_reason"),
        cost_usd_simulated=float(cost) if isinstance(cost, (int, float)) else None,
    )


def _attempt(
    cfg: StudyConfig, spec: RunSpec, attempt: int, execute: ExecuteFn
) -> RunOutcome:
    """Run one spec once. A retry recreates the workspace the failed attempt left behind.

    A sterility failure is re-raised: a contaminated config dir poisons every run after it,
    so it must stop the campaign rather than be retried.
    """
    try:
        row = execute(cfg, spec, force=attempt > 1)
    except SterilityError:
        raise
    except RunnerError as exc:
        # No results row exists (the run never reached the agent), so the report is the
        # only record of this failure; the retry will write a real row if it gets that far.
        return RunOutcome(
            run_id=spec.run_id,
            arm=spec.arm,
            attempt=attempt,
            status="error",
            discard_reason=str(exc),
            cost_usd_simulated=None,
        )
    return _outcome(spec, attempt, row)


def run_batch(
    cfg: StudyConfig,
    cells: Sequence[TaskCell],
    *,
    execute: ExecuteFn,
    campaign: CampaignConfig | None = None,
    max_minutes: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    log: Callable[[str], None] = _stderr,
) -> BatchReport:
    """Execute one batch of cells in order, retrying infrastructure failures at the end.

    Time budget is checked only between cells, so a batch never stops mid-task. Failed runs
    are re-queued behind the whole batch (never immediately: an overloaded API needs the
    gap) and given at most MAX_RETRIES further attempts before being flagged and skipped.
    """
    campaign = campaign or campaign_config(cfg)
    report = BatchReport(spend_by_arm={arm: 0.0 for arm in campaign.arms})
    deadline = None if max_minutes is None else clock() + max_minutes * 60.0
    retries: deque[tuple[RunSpec, int]] = deque()
    started = clock()

    def record(outcome: RunOutcome) -> None:
        report.attempted += 1
        report.outcomes.append(outcome)
        if outcome.ok:
            report.succeeded += 1
        if outcome.cost_usd_simulated is not None and outcome.arm in report.spend_by_arm:
            report.spend_by_arm[outcome.arm] += outcome.cost_usd_simulated

    def attempt(spec: RunSpec, number: int) -> bool:
        """Run once, record it, and queue a retry when it failed. True when it succeeded."""
        log(f"[run ] {spec.run_id} (attempt {number})")
        outcome = _attempt(cfg, spec, number, execute)
        record(outcome)
        if outcome.ok:
            return True
        log(f"[fail] {spec.run_id}: {outcome.discard_reason}")
        if number <= MAX_RETRIES:
            retries.append((spec, number + 1))
        else:
            report.exhausted.append(outcome)
            log(f"[flag] {spec.run_id}: giving up after {number} attempts — continuing")
        return False

    try:
        for index, cell in enumerate(cells):
            for spec in cell.specs:
                attempt(spec, 1)
            report.cells_completed += 1
            if deadline is not None and clock() >= deadline:
                report.cells_skipped = len(cells) - index - 1
                report.stopped_reason = (
                    f"time budget reached after {max_minutes} min "
                    f"({report.cells_skipped} cell(s) left for the next batch)"
                )
                break

        while retries:
            spec, number = retries.popleft()
            attempt(spec, number)
    except SterilityError as exc:
        report.aborted_reason = str(exc)
        report.stopped_reason = "aborted: sterility gate failed"
        report.cells_skipped = max(len(cells) - report.cells_completed, 0)

    report.wall_s = round(clock() - started, 3)
    after = status_report(cfg, campaign)
    report.progress_done = after.done
    report.progress_total = after.total
    report.cumulative_spend_by_arm = after.spend_by_arm
    report.spend_by_arm = {arm: round(total, 6) for arm, total in report.spend_by_arm.items()}
    return report


# --- rendering --------------------------------------------------------

_STATUS_COLUMNS = [
    ("tier", 6),
    ("agent", 7),
    ("arm", 10),
    ("family", 14),
    ("done", 6),
    ("remain", 7),
    ("discard", 8),
]


def _table(columns: Sequence[tuple[str, int]], rows: Iterable[Sequence[Any]]) -> list[str]:
    header = "  ".join(name.ljust(width) for name, width in columns)
    lines = [header, "-" * len(header)]
    for row in rows:
        lines.append(
            "  ".join(
                str(value).ljust(width)[:width] for value, (_, width) in zip(row, columns)
            )
        )
    return lines


def _spend_lines(title: str, spend: dict[str, float]) -> list[str]:
    return [title] + [f"  {arm.ljust(10)} {total:>12.2f}" for arm, total in spend.items()]


def format_status(report: StatusReport) -> str:
    """Render `--status`: plan vs. state, raw per-arm spend, overall progress."""
    lines = _table(
        _STATUS_COLUMNS,
        (
            (g.effort, g.agent, g.arm, g.family, g.done, g.remaining, g.discarded)
            for g in report.groups
        ),
    )
    lines.append("")
    lines += _spend_lines("simulated spend (USD, all attempts):", report.spend_by_arm)
    lines.append("")
    lines.append(
        f"progress: {report.done} of {report.total} run(s) done, "
        f"{report.remaining} remaining, {report.discarded} discarded attempt(s)"
    )
    lines.append(f"note: {NO_PEEKING_NOTE}")
    return "\n".join(lines)


def format_batch_report(report: BatchReport) -> str:
    """Render the end-of-batch report: attempts, discards with reasons, spend, progress."""
    lines = [
        f"batch: {report.attempted} attempt(s), {report.succeeded} ok, "
        f"{len(report.discarded)} discarded, {len(report.exhausted)} flagged",
        f"cells: {report.cells_completed} completed, {report.cells_skipped} deferred",
        f"wall:  {report.wall_s:.1f}s",
    ]
    if report.discarded:
        lines.append("discards:")
        lines += [
            f"  {outcome.run_id} (attempt {outcome.attempt}): {outcome.discard_reason}"
            for outcome in report.discarded
        ]
    if report.exhausted:
        lines.append("flagged (retries exhausted, left for a later batch):")
        lines += [f"  {outcome.run_id}: {outcome.discard_reason}" for outcome in report.exhausted]
    lines.append("")
    lines += _spend_lines("simulated spend this batch (USD):", report.spend_by_arm)
    lines += _spend_lines("cumulative simulated spend (USD):", report.cumulative_spend_by_arm)
    lines.append("")
    lines.append(f"stopped: {report.stopped_reason}")
    if report.aborted_reason:
        lines.append(f"ABORTED: {report.aborted_reason}")
    lines.append(
        f"progress: {report.progress_done} of {report.progress_total} run(s) done"
    )
    lines.append(f"note: {NO_PEEKING_NOTE}")
    return "\n".join(lines)
