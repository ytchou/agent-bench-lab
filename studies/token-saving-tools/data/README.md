# data

Per-run results and raw session logs.

- `runs.csv` — one row per run: agent, setup, effort, task, four token classes, simulated cost, success, turns, tool calls, wall time, activation, discard flag + reason
- `raw/` — session logs per run (the audit trail behind every row)

Warm-up (step 1) data is stored separately and never enters the final analysis.
