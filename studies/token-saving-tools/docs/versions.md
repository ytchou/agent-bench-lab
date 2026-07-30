# Pinned versions (to-do 0.11)

Recorded 2026-07-30. Re-verify (and re-pin) at pre-registration; versions must not change mid-campaign.

## Agents

| Component | Version | Notes |
|---|---|---|
| Claude Code CLI | 2.1.220 | model pinned per-run: `--model claude-opus-5` |
| Codex CLI | 0.146.0 | model: GPT-5.6 Sol (exact model id recorded at first pinned run) |

## Tools under test

| Tool | Version / pin | Install (into sandbox config ONLY, never personal config) |
|---|---|---|
| caveman | repo commit `0d95a81d35a9f2d123a5e9430d1cfc43d55f1bb0`; plugin via marketplace | `CLAUDE_CONFIG_DIR=<arm-config> claude plugin marketplace add JuliusBrussee/caveman && claude plugin install caveman@caveman` (verified). Codex: `npx skills add JuliusBrussee/caveman -a codex` (per-session `/caveman` — activation mode differs; record) |
| ponytail | plugin via marketplace (version = marketplace state at install; record plugin.json version at arm setup) | `CLAUDE_CONFIG_DIR=<arm-config> claude plugin marketplace add DietrichGebert/ponytail && claude plugin install ponytail@ponytail` (verified) |
| rtk | brew stable 0.44.1 (not yet installed) | `brew install rtk`; hook wiring via `rtk init` pointed at the arm config dir — verify hook lands in sandbox, not `~/.claude` |
| Headroom | 0.33.0 (`uv tool install "headroom-ai[all]"`, installed) | Claude arm: `headroom wrap claude`; Codex arm: `headroom wrap codex` — force HTTP transport for Codex (WebSocket → 403 fallback noise, verified) |

## Task fixtures

| Fixture | Pin |
|---|---|
| Comprehension repo: pallets/click | tag 8.3.1, commit `1d038f270701498433cb432f54db89f95f07a845` |
| SWE-bench Verified | dataset `princeton-nlp/SWE-bench_Verified` (instance ids frozen at pre-registration) |
| DSBench | pin dataset commit when adapter lands (to-do 0.9) |

## Open items

- Exact GPT-5.6 Sol model identifier + effort flag mapping (both CLIs) — fill during exit gate
- ponytail plugin version string — read from sandbox plugin.json when the ponytail arm config is created
- rtk hook scoping check — rtk's installer may assume `~/.claude`; must confirm `--config-dir`-style scoping before the rtk arm exists
