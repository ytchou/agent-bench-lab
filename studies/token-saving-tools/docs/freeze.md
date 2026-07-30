# Pre-registration freeze summary (step 2)

Assembled 2026-07-30. **The tag that follows the merge of this document is the pre-registration**: after it, `hypotheses.md`, `analysis-plan.md`, the task manifests, `study.yaml`, `prices.toml`, and `versions.md` are immutable — any later change must be a visible new commit with a stated reason.

## What is frozen, and where

| Artifact | File | Content |
|---|---|---|
| Hypotheses | `docs/hypotheses.md` | Per-tool magnitude-band predictions, G1–G4, decision rule, prediction grid |
| Analysis rules | `docs/analysis-plan.md` | Cost-of-pass primary; paired median log-ratios + seeded bootstrap; Holm; TOST ±10pp; DSBench cluster bootstrap; discard rules; no-peeking protocol |
| Task list | `tasks/*.json` | 34 tasks: 12 SWE (8 repos, difficulty 4/6/2, gold_diff_stats) · 12 DSBench (all fresh numeric questions, 6 workbooks) · 10 comprehension |
| Arms & signatures | `config/study.yaml` | 5 Claude arms / 4 Codex arms (rtk: no Codex support); every activation signature live-verified 2026-07-30 |
| Versions | `docs/versions.md` | Agents, tools (caveman 0d95a81, ponytail 4.8.4, rtk 0.44.1, headroom 0.33.0), fixtures |
| Prices | `config/prices.toml` | List prices dated 2026-07-30, incl. long-context tier |

## Run plan (from the frozen design)

- Claude: 5 arms × 2 efforts × 34 tasks, k=1 except **k=2 on SWE cells** (adopted with analysis-plan merge) ≈ 340 + 120 = **460 runs**
- Codex: 4 arms × 2 efforts × 34 tasks × **k=2** ≈ **544 runs**
- Total main experiment ≈ **1,004 runs**, scheduled around subscription caps (~3–4 weeks)
- Step-4 extras (force-enabled, judge calibration) budgeted separately (~80 runs)

## Design deltas registered since the proposal

1. rtk is Claude-only (no Codex support in v0.44.1) — Codex has 4 arms.
2. Codex caveman/ponytail install project-locally; workspaces are seeded from frozen `sandbox/workspace-seed-*` trees; activation is expected rare and is itself a registered outcome (G3).
3. Codex hypotheses registered at vendor scale only (warm-up power analysis).
4. DSBench = 12 tasks over 6 workbooks with cluster bootstrap (the fresh-numeric pool is exhausted — zero selection freedom).

## Known limitations declared at freeze

- Claude-SWE rep-noise sigma (0.34) estimated from 4 warm-up pairs — mitigated by k=2 on those cells.
- Codex per-turn `input_tokens` may be cumulative (peak readings to 918k); dashboard cross-check (1.7a) still open — if it lands after freeze, any accounting correction is applied symmetrically to all arms and recorded as a visible commit.
- Codex skill-use detection (`$caveman`/`$ponytail`) misses silent agent-initiated use; refined detection may be added in 3.0 but the registered signature stays authoritative for the primary analysis.

## Remaining before main runs (step 3, not frozen)

Runner workspace-seed support · baseline sterility precheck · batch scheduler (3.1) · `abl arm-setup` automation (3.0).
