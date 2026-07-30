# Pinned versions (to-do 0.11)

Recorded 2026-07-30. **Re-pinned at the step-2 pre-registration freeze (2026-07-30)** — versions must not change mid-campaign.

## Agents

| Component | Version | Notes |
|---|---|---|
| Claude Code CLI | 2.1.220 | model pinned per-run: `--model claude-opus-5` |
| Codex CLI | 0.146.0 | model id `gpt-5.6-sol` — verified live in exit-gate + warm-up runs |

## Tools under test

| Tool | Version / pin | Install (into sandbox config ONLY, never personal config) |
|---|---|---|
| caveman | plugin cache `0d95a81d35a9` (repo commit `0d95a81d35a9f2d123a5e9430d1cfc43d55f1bb0`) — installed in `claude-config-caveman` | `CLAUDE_CONFIG_DIR=<arm-config> claude plugin marketplace add JuliusBrussee/caveman && claude plugin install caveman@caveman`. Codex: project-local `npx skills add JuliusBrussee/caveman -a codex` → frozen as `sandbox/workspace-seed-caveman-codex/` (runner seeds each workspace; skills confirmed in the session catalog) |
| ponytail | plugin **4.8.4** — installed in `claude-config-ponytail` | same marketplace flow (DietrichGebert/ponytail). Codex: seed `sandbox/workspace-seed-ponytail-codex/` |
| rtk | brew stable **0.44.1** — installed; hook + RTK.md in `claude-config-rtk` (verified: `rtk init -g` respects CLAUDE_CONFIG_DIR; personal `~/.claude` md5-identical before/after; app-global filters at `~/Library/Application Support/rtk/filters.toml`) | **No Codex CLI support** (absent from its `--agent` list) → Claude-only arm; Codex runs 4 arms |
| Headroom | 0.33.0 (`uv tool install "headroom-ai[all]"`) | Claude arm: `headroom wrap claude` (registers MCP server `"headroom"` — the activation marker). Codex arm: `headroom wrap codex` with `-c features.responses_websocket=false` — key VERIFIED live: suppresses the 5× ws-403 fallback |

## Task fixtures

| Fixture | Pin |
|---|---|
| Comprehension repo: pallets/click | tag 8.3.1, commit `1d038f270701498433cb432f54db89f95f07a845` |
| SWE-bench Verified | dataset `princeton-nlp/SWE-bench_Verified` — 12 instance ids frozen in `tasks/swebench.json` |
| DSBench | HF `liqiang888/DSBench` `data_analysis/data.zip` + `data.json` — SHA256 checksums recorded by `scripts/fetch_dsbench.py`; 12 task ids frozen in `tasks/dsbench.json` |

## Open items

All closed at the 2026-07-30 freeze: `gpt-5.6-sol` + effort flags verified live; ponytail 4.8.4 read from the arm's plugin.json; rtk sandbox scoping verified against an untouched `~/.claude`.
