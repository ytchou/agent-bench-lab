# agent-bench-lab — Architecture

How the pieces fit together. For per-decision rationale see `runner-design.md`; for token-extraction specifics see `token-accounting.md`. This doc is the map.

## The two-layer split

The repo enforces one structural rule: **the instrument never knows what study it is running.**

```
Layer 1 — INSTRUMENT (reusable)          Layer 2 — STUDY (one per research question)
┌────────────────────────────┐           ┌─────────────────────────────────────┐
│ runner/    the engine      │  reads    │ studies/<name>/config/study.yaml    │
│ config/    price tables    │◄──────────│ studies/<name>/tasks/*.json         │
│ docs/      instrument docs │  writes   │ studies/<name>/data/  (runs, raw)   │
│ sandbox/   agent configs   │──────────►│ studies/<name>/docs/  (study docs)  │
└────────────────────────────┘           └─────────────────────────────────────┘
```

Everything study-specific — which tools, which tasks, which models, which activation signatures — lives in the study's YAML and JSON. The runner core contains zero references to caveman, SWE-bench, or token-saving anything. **Test of success:** study #2 should require only new Layer-2 files.

## One run, end to end

A "run" is the atomic unit: one agent, one setup (arm), one effort level, one task. Every run flows through the same six stages regardless of task family:

```
RunSpec ──► Workspace ──► AgentDriver ──► Accounting ──► Activation ──► Grading ──► Results
(what to    (fresh git    (subprocess:    (token classes (did the tool  (family     (one row,
 run)        dir, task     claude -p /     → simulated $) actually       adapter)    same schema
             materialized) codex exec)                    fire?)                     every family)
```

| Stage | Module | Contract |
|---|---|---|
| Spec | `runner/spec.py` | Immutable description of the run + loader for study.yaml. `run_id` is deterministic: `<agent>-<arm>-<effort>-<family>-<task>-r<rep>` |
| Workspace | `runner/workspace.py` | Fresh directory under `sandbox/workspaces/<run_id>/`, git-inited; asks the family adapter to *materialize* the task into it (clone a repo, copy a workbook) |
| Agent driver | `runner/agents.py` | Runs the agent as a subprocess with env pointed at the arm's sandbox config. Knows the two CLIs' invocation shapes and quirks; optionally wraps the command (`headroom wrap …`). Timeouts and failures become *discard rows*, never exceptions |
| Accounting | `runner/accounting.py` | Parses run output + session logs into four token classes (plus Codex `reasoning`, plus Claude per-model splits) and prices them from `config/prices.toml`. Unpriceable → `null`, never a silent 0 |
| Activation | `runner/activation.py` | Scans the session log for the tool's hook signatures (declared per-arm in study.yaml). Returns true/false/unknown + evidence snippet |
| Grading | `runner/grading/*` | One adapter per family behind a two-method contract: `materialize(task, workspace)` and `grade(task, workspace) → {success, detail}` |
| Results | `runner/results.py` | Appends one row to `data/runs.jsonl`; archives all raw artifacts to `data/raw/<run_id>/`. Rerunning a run_id supersedes the old row into `superseded.jsonl` — at most one live row per run_id, full history kept |

## The three load-bearing design decisions

**1. Uniform agent-side protocol; containers only after the agent finishes.**
The agent's world is identical across every arm and family: same host, same workspace shape, same invocation wrapper. Docker appears only inside the SWE-bench grading adapter, *after* the agent is done — so grading infrastructure can never influence agent behavior (the confound the study design forbids). This is also why agents run on the host at all: subscription auth (Keychain / auth files) doesn't survive containerization.

**2. Hermetic per-arm sandbox configs.**
`sandbox/claude-config[-<arm>]/` and `sandbox/codex-home[-<arm>]/` are sealed agent homes: baseline = sterile, treatment = sterile + exactly one tool, really installed, version-pinned. The runner refuses to fall back to `~/.claude`/`~/.codex` (a missing config dir is a discard, not a fallback). Reason: personal config injected ~29k tokens and caveman-like personal rules into "plain" runs during feasibility. `sandbox/` is gitignored — it holds credentials.

**3. Adapters make families comparable without pretending they're the same.**
Families differ in what a task *is* (a repo to fix, a workbook to analyze, a question to answer) and how success is judged (hidden tests in Docker, numeric tolerance, exact match). The adapter contract confines those differences to two functions; everything upstream (workspace, driver, accounting) and downstream (results schema, analysis) is family-blind. The uniform ANSWER.txt protocol for non-SWE families keeps grading deterministic — no AI judge anywhere in the primary metrics.

## Control surface

`abl` (CLI, `runner/cli.py`):

- `abl run --study S --agent A --arm R --effort E --family F --task T` — one run, prints the row
- `abl gate --study S` — the step-0 exit gate: first task of each family × both agents × baseline
- `abl regrade --study S --run-id ID` — re-run only the grading stage from archived artifacts (SWE-bench; exists because Docker died mid-grade once)
- `abl export --study S` — runs.jsonl → runs.csv

Planned (Notion to-dos): `abl arm-setup` (3.0 — create a treatment arm's sandbox + install its tool + verify signatures), the batch scheduler (3.1 — rate-cap-aware, resumable), dataset prep scripts (0.13), analysis library (1.6/5.0), judge pipeline (4.4).

## Failure philosophy

Runs never throw away information: every failure mode (timeout, agent error, missing config, Docker down, unparsable output) becomes a row with `status: discard` and a machine-readable `discard_reason`. Discard rules are symmetric across arms and will be frozen at pre-registration. Tool self-reported metrics are never read — all accounting comes from the agents' own session logs, cross-checked against the CLI's billing math (verified to the penny on Claude).

## Repo map

```
agent-bench-lab/
├── runner/               # Layer 1: the engine (family-blind, study-blind)
│   └── grading/          #   family adapters (the only family-aware code)
├── config/prices.toml    # dated price table; analysis refuses unpriced models
├── docs/                 # instrument docs (this file, runner-design, token-accounting)
├── sandbox/              # gitignored: per-arm agent configs + per-run workspaces
└── studies/
    └── token-saving-tools/
        ├── config/study.yaml   # agents, arms, efforts, families, signatures
        ├── tasks/*.json        # task manifests (regenerable via 0.13 scripts)
        ├── docs/               # proposal snapshot, versions, dsbench review
        └── data/               # runs.jsonl + raw/<run_id>/ + superseded.jsonl
```
