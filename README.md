# agent-bench-lab

A lab for measuring what AI coding-agent setups actually cost.

The instrument: a small runner that drives coding agents (Claude Code, Codex CLI) non-interactively on consumer subscriptions, records **four-class token accounting** (fresh input / cached input reads / cache writes / output) from session logs, computes simulated dollar cost at published API prices, and grades task outcomes deterministically (hidden tests, exact answers, reference-patch comparison).

Individual studies live under `studies/` and share the runner.

## Status

**Study #1 is PRE-REGISTERED** as of 2026-07-30 — tag [`prereg-v1`](https://github.com/ytchou/agent-bench-lab/releases/tag/prereg-v1) (commit `e9d8d71`). The hypotheses, analysis rules, task list, arm configurations, tool versions, and prices were frozen **before any treatment run**. Any later change to a frozen artifact is a visible commit with a stated reason. Main-experiment runs (~1,004) are next.

## Structure

```
runner/                       # the reusable engine (agent driving, token accounting, grading adapters)
docs/                         # instrument-level docs (runner design note, token accounting notes)
studies/
  token-saving-tools/         # study #1: do popular token-saving tools deliver their claims?
    docs/proposal.md          # study design snapshot (living copy in Notion)
    docs/hypotheses.md        # FROZEN: per-tool predictions + decision rule
    docs/analysis-plan.md     # FROZEN: statistics, discard rules, no-peeking protocol
    docs/freeze.md            # what was frozen, run plan, declared limitations
    tasks/                    # FROZEN: 34-task manifests (SWE-bench Verified, DSBench, comprehension)
    config/                   # FROZEN: arms, activation signatures, prices
    data/                     # per-run results + raw logs (CC-BY-4.0)
    notebooks/                # analysis
```

## Studies

| # | Study | Status |
|---|---|---|
| 1 | [token-saving-tools](studies/token-saving-tools/docs/proposal.md) — pre-registered benchmark of caveman, rtk, ponytail, Headroom on Claude Code + Codex CLI | Pre-registered (`prereg-v1`); main runs pending |

## License

Code is [MIT](LICENSE). Run data under `studies/*/data/` is [CC-BY-4.0](studies/token-saving-tools/data/LICENSE). Third-party benchmark datasets (SWE-bench, DSBench) are not redistributed and retain their own licenses.
