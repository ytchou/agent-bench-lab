"""One-off repair: recompute all codex rows after the cached-subset accounting fix.

For each codex row (any phase), rebuild usage from the archived --json event
stream, reprice, and append the corrected row (append_row supersedes the old
one into superseded.jsonl, preserving history).
"""

import json
import sys
from pathlib import Path

LAB = Path.home() / "project/agent-bench-lab"
sys.path.insert(0, str(LAB))

from runner.accounting import extract_usage, load_prices, simulated_cost
from runner.spec import load_study
from runner import results as results_mod

CFG = load_study("token-saving-tools", repo_root=LAB)
STUDY = LAB / "studies/token-saving-tools"
RUNS = STUDY / "data/runs.jsonl"
RAW = STUDY / "data/raw"
PRICES = load_prices(LAB / "config/prices.toml")

rows = [json.loads(l) for l in RUNS.read_text().splitlines() if l.strip()]
fixed = 0
for row in rows:
    if row["agent"] != "codex":
        continue
    run_id = row["run_id"]
    stdout_file = RAW / run_id / "agent_stdout.txt"
    if not stdout_file.is_file():
        print(f"SKIP {run_id}: no archived stdout")
        continue
    events = []
    for line in stdout_file.read_text().splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    cmd = json.loads((RAW / run_id / "agent_cmd.json").read_text())
    argv = cmd if isinstance(cmd, list) else cmd.get("argv") or cmd.get("cmd")
    model = argv[argv.index("-m") + 1] if "-m" in argv else None
    usage = extract_usage("codex", {"parsed": events})
    old_cost = row["cost_usd_simulated"]
    new_cost = simulated_cost("codex", usage, PRICES, model)
    for k in ("tokens_fresh", "tokens_cache_write", "tokens_cache_write_1h",
              "tokens_cache_read", "tokens_out", "tokens_reasoning",
              "model_usage", "turns", "tool_calls"):
        if k in row and usage.get(k) is not None or k in ("model_usage",):
            row[k] = usage[k]
    row["cost_usd_simulated"] = new_cost
    results_mod.append_row(CFG, row)
    print(f"FIXED {run_id}: fresh={usage['tokens_fresh']:,} "
          f"peak={usage['model_usage'].get('context_peak_tokens', 0):,} "
          f"cost ${old_cost:.4f} -> ${new_cost:.4f}")
    fixed += 1
print(f"done: {fixed} codex rows recomputed")
