# runner

The reusable engine (built in study #1 to-do 0.7). Responsibilities:

- Given (agent, setup, effort, task): create a fresh isolated workspace, run the agent non-interactively (`claude -p` / `codex exec`) on a subscription, save session logs
- Extract four-class token counts (fresh input / cached input reads / cache writes / output) per run
- Record turns, tool calls, wall time, and whether the installed tool actually activated
- Invoke the study's grading adapter and emit one results row per run
- Batch scheduling around subscription usage caps, resumable after interruption

Language (Node vs Python) is decided during step-0 feasibility checks.
