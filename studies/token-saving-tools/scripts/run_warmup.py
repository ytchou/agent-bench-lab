"""Warm-up driver (to-do 1.2): sequential `abl run` loop over the warm-up pool.

Rep-major ordering: every task's rep 1 across all agents/families first, then rep 2.
This keeps a task's two repeats hours apart so provider-side prompt caching (5-min
TTL) cannot make rep 2 artificially cheaper than rep 1 — the whole point of the
repeats is to measure natural run-to-run variance.
Ceiling: single retry per run + 15-min back-off on any failure; a real scheduler
with cap-awareness is to-do 3.1.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

STUDY_ROOT = Path(__file__).resolve().parents[1]
LAB_ROOT = STUDY_ROOT.parents[1]
POOL = STUDY_ROOT / "config" / "warmup_pool.json"
RUNS = STUDY_ROOT / "data" / "runs.jsonl"
LOG = STUDY_ROOT / "data" / "warmup_driver.log"

RETRY_SLEEP_S = 900
MAX_ATTEMPTS = 2


def log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    with LOG.open("a") as handle:
        handle.write(line + "\n")


def done_run_ids() -> set[str]:
    if not RUNS.is_file():
        return set()
    ids = set()
    for raw in RUNS.read_text().splitlines():
        if not raw.strip():
            continue
        row = json.loads(raw)
        if row.get("phase") == "warmup" and row.get("status") == "ok":
            ids.add(row["run_id"])
    return ids


def main() -> int:
    pool = json.loads(POOL.read_text())
    specs = []
    for rep in pool["reps"]:
        for family, tasks in pool["families"].items():
            for task in tasks:
                for agent in pool["agents"]:
                    specs.append((agent, family, task, rep))

    done = done_run_ids()
    failures = []
    for i, (agent, family, task, rep) in enumerate(specs, 1):
        run_id = f"{agent}-{pool['arm']}-{pool['effort']}-{family}-{task}-r{rep}"
        if run_id in done:
            log(f"[{i}/{len(specs)}] SKIP (done) {run_id}")
            continue
        cmd = [
            "uv", "run", "abl", "run",
            "--agent", agent, "--arm", pool["arm"], "--effort", pool["effort"],
            "--family", family, "--task", task,
            "--rep", str(rep), "--phase", "warmup", "--force",
        ]
        for attempt in range(1, MAX_ATTEMPTS + 1):
            log(f"[{i}/{len(specs)}] RUN attempt {attempt} {run_id}")
            proc = subprocess.run(cmd, cwd=LAB_ROOT, capture_output=True, text=True)
            tail = (proc.stdout + proc.stderr)[-400:].replace("\n", " | ")
            if proc.returncode == 0:
                log(f"[{i}/{len(specs)}] OK {run_id}")
                break
            log(f"[{i}/{len(specs)}] FAIL rc={proc.returncode} {run_id} :: {tail}")
            if attempt < MAX_ATTEMPTS:
                log(f"sleeping {RETRY_SLEEP_S}s before retry")
                time.sleep(RETRY_SLEEP_S)
        else:
            failures.append(run_id)

    log(f"DONE: {len(specs) - len(failures)}/{len(specs)} ok, failures: {failures or 'none'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
