# Pre-registered analysis plan

**Status: DRAFT — immutable at the pre-registration tag.** Companion to `hypotheses.md` (predictions) — this file fixes *how* every number is computed before any treatment run exists. Calibration source: warm-up study, 2026-07-30 (40 sterile runs, data in this repo).

## 1. Design (frozen)

- **Arms:** Claude Code — baseline, caveman, ponytail, rtk, Headroom (5). Codex CLI — baseline, caveman, ponytail, Headroom (4; rtk has no Codex support). *Amendments A1–A2 (2026-07-30, pre-data, see `run-order.md`): rtk withdrawn — both agents run 4 arms; Holm family = 3 tools × 2 agents. Replication is uniform k=2 on every cell (supersedes the k=1 Claude cells of decision 1.8/§1 — replication only added, never removed).*
- **Efforts:** medium, xhigh — full factors on both agents.
- **Families:** ~12 SWE-bench Verified, ~12 DSBench, ~10 comprehension (exact IDs frozen in the task manifests at the tag).
- **Repeats (decision 1.8):** k=2 on all Codex cells; k=1 on Claude cells. *Flagged for the freeze (see §6): warm-up sterile data puts Claude-SWE rep noise at sigma 0.34 (from 4 pairs, one 2.5× divergent pair) — recommendation: extend k=2 to Claude SWE cells (+~120 runs) as variance insurance; final call is Patrick's at merge.*
- **Activation:** default install is the primary condition; per-run activation detection via frozen log signatures; force-enabled runs are a separate step-4 exploratory block.

## 2. Metrics

**Primary:** cost-of-pass per cell = (sum of simulated USD across the cell's runs) ÷ (number of successful runs). Simulated USD from the 4-class token accounting at the list prices in `config/prices.toml` (dated); Codex accounting uses the cached-subset semantics (fix of 2026-07-30) and per-turn-peak long-context tiering.

**Secondary (all pre-declared):** raw simulated USD per run; success rate; tokens by class; turns; tool calls; wall seconds; per-channel tool-output bytes (rtk/Headroom mechanism); `diff_lines_added` vs gold (ponytail mechanism); activation rate; (Codex cells, k=2) rep-flip rate and per-cell cost spread.

**Never used as outcomes:** any tool's self-reported savings counter.

## 3. Estimation

- **Same-task pairing.** Every treatment effect is estimated from within-task differences (treatment run vs baseline run, same agent, same effort, same task). Effects reported as the **median of per-task log-ratios**, exponentiated to a percentage.
- **Uncertainty:** 95% percentile bootstrap over tasks (10,000 resamples, seed 0 — `runner/analysis.py:bootstrap_ci_median`). Codex k=2 cells enter as the per-task mean of the two reps.
- **Suite-level first, family-level second.** Suite estimates are primary; family estimates are reported with their own CIs and labeled exploratory wherever the warm-up MDE exceeds the observed effect.
- **DSBench clustering.** The 12 DSBench tasks span 6 workbooks (frozen selection took every fresh numeric question). Tasks sharing a workbook are not independent, so DSBench family-level bootstrap resamples **workbooks** (clusters), not tasks; the effective family n is between 6 and 12 and family conclusions are labeled accordingly.
- **Multiple comparisons:** Holm correction across the primary per-tool cost tests within each agent (4 tests on Claude, 3 on Codex). Secondary/mechanism metrics are descriptive and uncorrected, labeled as such.
- **Quality verdict:** TOST equivalence on suite success-rate difference, margin ±10 percentage points (frozen by Patrick 2026-07-30), alpha 0.05.
- **Verdict rule per tool:** delivers-on-claim = Holm-significant cost saving at the predicted magnitude band AND TOST non-inferiority. Partial outcomes (real-but-smaller saving; saving with quality breach) are reported in exactly those words.

## 4. Discard rules (symmetric, frozen)

A run is `status=discard` (and rerun once) iff: agent process error/timeout (e.g. API 529), missing/unwritable workspace, grading-infrastructure failure (Docker down). Discards are never silently dropped: every discard row is kept, counted, and reported per arm. A task is excluded entirely (both sides of every pair) if its baseline fails in ≥50% of runs on both agents — recorded in an excluded-task ledger. Nothing else is excludable; wrong answers and expensive runs are data, not discards.

## 5. Power (from the sterile warm-up, frozen as context)

| Cell | sigma_log | Suite MDE at planned n |
|---|---|---|
| Claude comprehension / DSBench | 0.041 / 0.037 | — (exquisitely powered) |
| Claude SWE | 0.34 (4 pairs; uncertain) | drives suite MDE to 8.5–14.4% |
| Codex (pooled, k=2 effective) | 0.35→0.25 | ≈18–27% → vendor-scale only |

Claude ±10% detection is **borderline** under the pessimistic SWE sigma — hence the §1 recommendation. Codex hypotheses are registered at vendor scale only; smaller Codex effects are pre-declared undetectable.

## 6. Open items that must close at the tag

1. Patrick's call on k=2 for Claude-SWE cells (+~120 runs) vs accepting the 8.5–14.4% MDE range.
2. Task manifests frozen (select_main_tasks.py output committed).
3. Tool versions table re-pinned (`versions.md`) after arm setup completes.

## 7. What "no peeking" means operationally

Between the tag and the end of step 3, with-vs-without comparisons are not computed — the weekly integrity check (3.4) looks only at run counts, discard reasons, and version pins. The analysis notebook runs for the first time when the last main run is logged.
