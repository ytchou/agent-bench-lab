# Token accounting — feasibility findings (2026-07-30)

Both agents run headless on subscriptions and expose per-run token counts. Verified with one-message runs.

## Claude Code (`claude -p`)

- **Command:** `claude -p "<prompt>" --output-format json` → single JSON result object
- **Run-level usage (all four classes, directly):** `usage.input_tokens` (fresh), `usage.cache_creation_input_tokens` (cache writes, split into `ephemeral_1h` / `ephemeral_5m` — pricing differs), `usage.cache_read_input_tokens`, `usage.output_tokens`. Also `total_cost_usd` (CLI's own simulation — cross-check against our own price math, don't trust blindly), `num_turns`, `session_id`, `modelUsage` (exact model + per-model split)
- **Session log:** `~/.claude/projects/<munged-cwd>/<session_id>.jsonl` — per-turn `usage` objects on each assistant message; verified identical to the run-level totals on a 1-turn run
- **Verified sample:** input 2 / cache-write 28,987 (all 1h) / cache-read 7,373 / output 4 → CLI-reported $0.2937

## Codex CLI (`codex exec`)

- **Command:** `codex exec --json --skip-git-repo-check "<prompt>"` (cwd must be a git repo or pass the skip flag; close stdin or it waits) → JSONL events on stdout
- **Run-level usage:** `turn.completed` event → `usage.input_tokens`, `usage.cached_input_tokens`, `usage.cache_write_input_tokens`, `usage.output_tokens`, **`usage.reasoning_output_tokens`** — a fifth class Claude doesn't report; billed as output and directly moderated by reasoning effort, so record it separately
- **Cached is a SUBSET of input:** `input_tokens` already includes `cached_input_tokens` (codex-rs: `non_cached_input() = input_tokens - cached_input()`, and `blended_total()` prices the non-cached remainder). Our fresh class is therefore `input − cached`, clamped at 0; counting them as disjoint double-counts the cached tokens.
- **Long-context tier:** a per-request rule, so it fires on the peak single-turn context — `max(input_tokens)` across `turn.completed` events, recorded as `context_peak_tokens` in `model_usage` (codex has no per-model split, so that field is otherwise unused) — not on the run total.
- **Session log:** `~/.codex/sessions/YYYY/MM/DD/rollout-<timestamp>-<thread_id>.jsonl`
- **Verified sample:** input 26,009 / cached 0 / cache-write 0 / output 5 / reasoning 0

## Confound caught: personal config contamination (MUST FIX before any real run)

Both agents injected the user's personal global config into the "plain" run:

- Claude Code: `~/.claude/CLAUDE.md` + skills roster + memory (~29k tokens of cache-write on an empty task)
- Codex: `~/.codex/AGENTS.md` global instructions visible in the rollout log

Consequences if unfixed: the baseline is not vanilla; personal rules overlap with treatment mechanisms (the user's own instructions contain terse-output and minimal-code directives — i.e., caveman/ponytail-like behavior in EVERY arm); config drift mid-campaign changes the baseline.

**Runner requirement:** hermetic config per run —

- Claude Code: `CLAUDE_CONFIG_DIR` pointed at a sterile, version-pinned config dir (empty CLAUDE.md, no skills, no memory); pin `--model claude-opus-5` explicitly (default inherited the user's `[1m]` 1M-context setting, which changes pricing and cache behavior)
- Codex: `CODEX_HOME` (or `-c` overrides) pointed at a sterile config; pin model + effort explicitly
- Treatment arms install their tool into the sterile config, never into the user's real one (also protects the user's daily setup from the experiment)

## Sandbox (hermetic config) — verified 2026-07-30

- `sandbox/claude-config/` via `CLAUDE_CONFIG_DIR`: baseline cache-write 29k → 5,764; requires one-time interactive `/login` inside the config dir (auth is config-dir-scoped)
- `sandbox/codex-home/` via `CODEX_HOME`: baseline input 26,009 → 15,496; requires `auth.json` copied in by the user (credentials — agent must not touch)
- `sandbox/` is gitignored (holds credentials)
- Claude runs also bill a Haiku helper model — record `modelUsage` per-model splits, not just totals
- Treatment installs go into the sandbox config only, one tool per arm: `CLAUDE_CONFIG_DIR=<sandbox> claude plugin install <tool>@<tool>` verified for caveman + ponytail

## Activation detection — verified 2026-07-30 (Claude Code)

Session JSONL contains machine-readable markers; no prose-judging needed:

- caveman: `hookEvent: "SessionStart"` attachment "CAVEMAN MODE ACTIVE — level: full" + per-turn `hook_additional_context` entries
- ponytail: SessionStart rules attachment ("Ponytail governs what you build…")
- Runner logs activation per run by scanning attachments for each tool's hook signature
- Codex-side detection: deferred to treatment-arm setup

## Headroom on Codex — verified 2026-07-30

- `headroom wrap codex -- exec --json …` (headroom 0.33.0 via `uv tool install "headroom-ai[all]"`) completes under subscription auth → **Codex grid keeps 5 setups**
- Caveat: Codex's WebSocket transport gets 403 from the proxy, falls back to HTTPS after 5 retries (~10s) — force HTTP transport in Codex config for Headroom arms
- Headroom-wrapped baseline input 18,881 vs 15,496 sterile — quantify Headroom's own overhead in the warm-up

## Still open

- Cross-check CLI-reported cost vs our own price-table math on a multi-turn run
- Codex-side activation detection for caveman (per-session `/caveman` on Codex — different mechanism than Claude hooks)
