"""CLI: `abl run` (one run end-to-end), `abl gate` (exit gate sweep), `abl regrade`,
`abl export`, `abl db` (derived SQLite star schema)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runner.accounting import extract_usage, load_prices, simulated_cost
from runner.activation import detect_activation, scan_log_flags
from runner.agents import run_agent
from runner.channels import tool_output_bytes
from runner.db import build_db
from runner.grading import find_task, first_task_id, get_adapter, swebench
from runner.grading.base import GradeContext
from runner.prompts import build_prompt
from runner.results import append_row, archive_run, build_row, export_csv, find_row, rewrite_row
from runner.spec import RunSpec, RunnerError, StudyConfig, load_study
from runner.workspace import grade_context, prepare_workspace

DEFAULT_STUDY = "token-saving-tools"
REGRADABLE_FAMILY = "swebench"
_SUMMARY_COLUMNS = [
    ("run_id", 46),
    ("status", 8),
    ("success", 8),
    ("cost_usd_simulated", 12),
    ("tokens_out", 11),
    ("turns", 6),
    ("activated", 10),
]


def execute_run(cfg: StudyConfig, spec: RunSpec, force: bool = False) -> dict[str, Any]:
    """Run one (agent, arm, effort, family, task, rep) cell end-to-end and record the row."""
    adapter = get_adapter(spec.family)
    task = find_task(cfg, spec.family, spec.task_id)
    prompt = build_prompt(spec.family, task)

    workspace = prepare_workspace(spec, cfg, task, force=force)
    agent_result = run_agent(spec, cfg, workspace, prompt)
    archive_run(cfg, spec, agent_result, prompt)

    usage = extract_usage(spec.agent, agent_result)
    prices = load_prices(cfg.prices_path)
    model = cfg.agent_cfg(spec.agent).get("model")
    cost = simulated_cost(spec.agent, usage, prices, model)

    session_log = agent_result.get("session_log")
    activated, evidence = detect_activation(spec, cfg, session_log)
    channels = tool_output_bytes(spec.agent, agent_result)
    log_flags = scan_log_flags(spec, cfg, agent_result)

    if agent_result["status"] == "ok":
        grade = adapter.grade(
            task, workspace, grade_context(spec, cfg, cfg.grading_timeout_s)
        )
    else:
        grade = {
            "success": None,
            "detail": {"reason": "not_graded", "discard": agent_result["discard_reason"]},
        }

    row = build_row(
        spec,
        agent_result,
        usage,
        cost,
        grade,
        activated,
        evidence,
        tool_output_bytes=channels,
        log_flags=log_flags,
    )
    append_row(cfg, row)
    return row


def _print_summary(rows: list[dict[str, Any]]) -> None:
    header = "  ".join(name.ljust(width) for name, width in _SUMMARY_COLUMNS)
    print(header)
    print("-" * len(header))
    for row in rows:
        print(
            "  ".join(
                str(row.get(name)).ljust(width)[:width] for name, width in _SUMMARY_COLUMNS
            )
        )


def cmd_run(args: argparse.Namespace) -> int:
    cfg = load_study(args.study)
    spec = RunSpec(
        agent=args.agent,
        arm=args.arm,
        effort=args.effort,
        family=args.family,
        task_id=args.task,
        rep=args.rep,
        phase=args.phase,
    )
    row = execute_run(cfg, spec, force=args.force)
    print(json.dumps(row, indent=2, ensure_ascii=False))
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    """Exit gate: first task of every family × both agents, baseline arm, sequentially."""
    cfg = load_study(args.study)
    gate_cfg = cfg.raw.get("gate") or {}
    agents = list(gate_cfg.get("agents") or ["claude", "codex"])
    arm = str(gate_cfg.get("arm", "baseline"))
    effort = str(gate_cfg.get("effort", "medium"))

    rows: list[dict[str, Any]] = []
    for family in cfg.families():
        task_id = first_task_id(cfg, family)
        if task_id is None:
            print(
                f"[skip] family '{family}': task manifest is empty "
                f"({cfg.tasks_path(family)})",
                file=sys.stderr,
            )
            continue
        for agent in agents:
            spec = RunSpec(agent, arm, effort, family, task_id, rep=1, phase="gate")
            print(f"[run ] {spec.run_id}", file=sys.stderr)
            try:
                rows.append(execute_run(cfg, spec, force=args.force))
            except RunnerError as exc:
                print(f"[fail] {spec.run_id}: {exc}", file=sys.stderr)
                rows.append(
                    {
                        "run_id": spec.run_id,
                        "status": "error",
                        "success": None,
                        "cost_usd_simulated": None,
                        "tokens_out": None,
                        "turns": None,
                        "activated": None,
                    }
                )

    if not rows:
        print("gate produced no runs — fill the task manifests first", file=sys.stderr)
        return 1
    _print_summary(rows)
    return 0 if all(row.get("status") == "ok" for row in rows) else 1


def cmd_regrade(args: argparse.Namespace) -> int:
    """Re-run only the grade step of a finished swebench run, from its archived predictions."""
    cfg = load_study(args.study)
    row = find_row(cfg, args.run_id)
    family = row.get("family")
    if family != REGRADABLE_FAMILY:
        raise RunnerError(
            f"run '{args.run_id}' is family '{family}'; regrade only supports "
            f"'{REGRADABLE_FAMILY}' (other families grade from the workspace, which is "
            f"not preserved)"
        )

    ctx = GradeContext(
        run_id=args.run_id,
        raw_dir=cfg.raw_dir(args.run_id),
        study_root=cfg.study_root,
        tasks_dir=cfg.tasks_path(REGRADABLE_FAMILY).parent,
        timeout_s=cfg.grading_timeout_s,
    )
    grade = swebench.regrade(str(row.get("task_id")), ctx)

    row["success"] = grade.get("success")
    row["grader"] = {
        **(grade.get("detail") or {}),
        "regraded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    rewrite_row(cfg, row)
    print(json.dumps(row, indent=2, ensure_ascii=False))
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    cfg = load_study(args.study)
    path = export_csv(cfg)
    print(str(path))
    return 0


def cmd_db(args: argparse.Namespace) -> int:
    cfg = load_study(args.study)
    out_path = Path(args.out).expanduser() if args.out else cfg.data_dir / "runs.sqlite"
    counts = build_db(cfg, out_path)
    print(str(out_path))
    for table, count in counts.items():
        print(f"  {table}: {count}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="abl", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run one benchmark cell end-to-end")
    run.add_argument("--study", default=DEFAULT_STUDY)
    run.add_argument("--agent", required=True)
    run.add_argument("--arm", required=True)
    run.add_argument("--effort", required=True)
    run.add_argument("--family", required=True)
    run.add_argument("--task", required=True)
    run.add_argument("--rep", type=int, default=1)
    run.add_argument("--phase", default="main", help="analysis phase: main|warmup|gate")
    run.add_argument("--force", action="store_true", help="recreate an existing workspace")
    run.set_defaults(func=cmd_run)

    gate = sub.add_parser("gate", help="run the step-0 exit gate sweep")
    gate.add_argument("--study", default=DEFAULT_STUDY)
    gate.add_argument("--force", action="store_true")
    gate.set_defaults(func=cmd_gate)

    regrade = sub.add_parser(
        "regrade", help="re-grade one finished swebench run from its archived predictions"
    )
    regrade.add_argument("--study", default=DEFAULT_STUDY)
    regrade.add_argument("--run-id", dest="run_id", required=True)
    regrade.set_defaults(func=cmd_regrade)

    export = sub.add_parser("export", help="export runs.jsonl to runs.csv")
    export.add_argument("--study", default=DEFAULT_STUDY)
    export.set_defaults(func=cmd_export)

    db = sub.add_parser("db", help="build the derived SQLite database from runs.jsonl")
    db.add_argument("--study", default=DEFAULT_STUDY)
    db.add_argument("--out", default=None, help="output path (default: <data>/runs.sqlite)")
    db.set_defaults(func=cmd_db)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the `abl` console script."""
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except RunnerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
