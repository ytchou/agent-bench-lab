# agent-bench-lab

A lab for measuring what AI coding-agent setups actually cost.

The instrument: a small runner that drives coding agents (Claude Code, Codex CLI) non-interactively on consumer subscriptions, records **four-class token accounting** (fresh input / cached input reads / cache writes / output) from session logs, computes simulated dollar cost at published API prices, and grades task outcomes deterministically (hidden tests, exact answers, reference-patch comparison).

Individual studies live under `studies/` and share the runner.

## Status

**Step 0 — feasibility.** Verifying subscription automation, token-log parsing, and grading adapters before any real runs.

## Structure

```
runner/                       # the reusable engine (agent driving, token accounting, grading adapters)
docs/                         # instrument-level docs (runner design note, token accounting notes)
studies/
  token-saving-tools/         # study #1: do popular token-saving tools deliver their claims?
    docs/proposal.md          # frozen study design (snapshot; living copy in Notion)
    tasks/                    # task manifests (SWE-bench Verified, DSBench, comprehension questions)
    data/                     # per-run results + raw logs
    notebooks/                # analysis
```

## Studies

| # | Study | Status |
|---|---|---|
| 1 | [token-saving-tools](studies/token-saving-tools/docs/proposal.md) — pre-registered benchmark of caveman, rtk, ponytail, Headroom on Claude Code + Codex CLI | Step 0: feasibility |

## License

To be chosen at pre-registration (study #1, to-do 2.1), before the repo goes public.
