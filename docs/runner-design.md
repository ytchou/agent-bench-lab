# Runner design note (to-do 0.6)

One page. What we borrowed, what we rejected, and the architecture.

## Lessons from prior harnesses

**Harbor + SkillsBench (JetBrains):** paired A/B trials on identical tasks; agents run inside per-task Docker sandboxes; real billing captured per trial; large n (80+ paired tasks) with sign tests. Borrowed: paired design, per-run billing-grade accounting, task packs with automated grading. Rejected: agents-in-Docker — our runs use *subscription* auth (Claude's is macOS-Keychain-scoped, Codex's is a host auth file), which containers break; and JetBrains' own data shows the harness works without it when the workspace protocol is uniform.

**stet.sh:** tasks replayed from real merged commits; run-level JSON with usage; multiple graders per attempt. Borrowed: reference-anchored grading, one-results-row-per-run. Rejected: n=2 repetitions, prompt-imitation treatments (we install the real tools), grader sprawl (8 unblinded dimensions → self-demoted "exploratory").

## Invariants (from the study design + feasibility findings)

1. **Uniform agent-side protocol.** The agent's environment is identical across all arms and all task families: same host (this Mac), same fresh workspace layout, same invocation wrapper, same sterile config pattern. Docker appears only in *grading*, after the agent finishes — it can never influence agent behavior.
2. **Hermetic per-arm configs.** Each arm gets its own sandbox config dir (`sandbox/claude-config-<arm>/`, `sandbox/codex-home-<arm>/`): baseline = sterile; treatment = sterile + exactly one tool, real install, pinned version. Personal `~/.claude` / `~/.codex` are never used (feasibility: personal config added ~29k contaminated tokens and caveman/ponytail-like personal rules).
3. **One results schema for every family.** Grading is a per-family adapter, but every run emits the same row. Family-specific logic never leaks into the runner core.
4. **Accounting from session logs only.** Four token classes (+ Codex `reasoning_output_tokens`, + Claude per-model splits — a Haiku helper bills alongside the main model). Tool self-reported "savings" are never read. Cost = classes × `prices.toml` (checked into the repo, dated).
5. **Everything on disk, resumable.** A run manifest tracks pending/done; the scheduler can stop at a rate cap and resume; raw session logs are archived per run id.

## Architecture

```
RunSpec (agent, arm, effort, family, task_id, rep)
  → WorkspaceManager   fresh dir sandbox/workspaces/<run_id>/, git init, family adapter materializes task
  → AgentDriver        claude -p --output-format json --model <pinned> | codex exec --json
                       env: CLAUDE_CONFIG_DIR / CODEX_HOME → arm's sandbox config; timeout; stdin closed
  → Accounting         parse run JSON + session JSONL → token classes, per-model, turns, tool calls, wall time
  → ActivationDetector scan session log for tool hook signatures (e.g. SessionStart "CAVEMAN MODE ACTIVE")
  → GradingAdapter     swebench: official Docker harness · dsbench: numeric match · comprehension: exact match
  → ResultsWriter      append row to data/runs.jsonl (+ runs.csv export); archive logs to data/raw/<run_id>/
```

Known agent quirks the drivers handle: Codex needs `--skip-git-repo-check` semantics satisfied (workspace is git-inited anyway) and stdin closed; Headroom-wrapped Codex needs HTTP transport forced (WebSocket → 403 off the proxy); Claude auth requires one-time interactive login per sandbox config dir (user action, documented).

## Results row (v1.2)

`run_id, ts, agent, arm, effort, family, task_id, rep, phase(main|warmup|gate), status(ok|discard), discard_reason, success, tokens_fresh, tokens_cache_write, tokens_cache_write_1h, tokens_cache_read, tokens_out, tokens_reasoning, model_usage(json), cost_usd_simulated, cost_usd_cli_reported, turns, tool_calls, tool_output_bytes(json), wall_s, activated, activation_evidence, log_flags(json), session_id, grader(json)`

Added in v1.1 — three mechanism metrics, all descriptive (nothing gates on them):

- `tool_output_bytes` — bytes of tool output the agent read, split by channel (`{"Bash": n, "Read": n, ..., "_total": n}` for claude; `{"bash": n, ...}` by codex item type). `null` when the log is unavailable. Answers "what share of tool output does a single-channel tool like rtk even touch?".
- `log_flags` — per-run count of log lines matching the study's top-level `log_flags:` substrings (`{"compaction": 0, "rate_limited": 0, "ws_fallback": 2}`). `{}` when nothing is configured or the log is missing; a `0` means measured-and-absent. Claude scans the session JSONL, codex the raw stdout capture.
- `diff_stats` — lives **inside `grader`** (swebench only), not as a top-level column: `{files_changed, lines_added, lines_removed}` parsed from the model patch text. When the task manifest carries a precomputed `gold_diff_stats`, it is copied into `grader.gold_diff_stats` unchanged so scope delta (agent patch vs gold patch) is comparable.

Added in v1.2 — `phase`: analysis-phase fence (gate = step-0 exit gate, warmup = step-1 calibration, main = pre-registered runs); analysis filters on it instead of timestamps.

## Out of scope for v0

Parallel runs (rate caps make sequential fine), retry logic beyond one re-queue on agent crash, the force-enabled arm plumbing (step 4), judge pipeline (step 4).
