# Study #1 — Do Token-Saving Tools Deliver? A Pre-Registered Benchmark

> Snapshot of the study design frozen 2026-07-30. The **living copy** (with to-do tracker, experiment log, and decision log) is the Notion page "Token-Saving Tools: A Pre-Registered Benchmark — Research Proposal". The immutable pre-registration will be a tagged commit in this repo (to-do 2.5).

## Background

Popular tools claim 50–90% token savings for AI coding agents: caveman (94k★, claims −65% output tokens), rtk (74k★, claims −60–90%), ponytail (91k★, claims −54% code). Independent re-tests found the claims mostly collapse:

- **JetBrains (2026-07, Claude Code, ~80 paired tasks, real billing):** caveman −65% → −8.5%; rtk −60–90% → **+7.6% more expensive** (p=0.004); ponytail −54% → −15.4% (only real cost win: −10.3%)
- **stet.sh (2026-07, Codex, 10 tasks × 2 reps):** no tool saved tokens in both runs — but underpowered (n=2) and used prompt imitations, not real installs
- **codepointer (500-session replay):** rtk-style savings shrink to <4% of total spend

Key mechanism: these tools are *behavioral interventions*. Compressing one surface (command output, prose, patch size) changes the agent's behavior — often more commands, more turns, more cache re-reads. The bill is for the whole trajectory.

**Our gap:** real installs + powered n + pre-registration + blinded quality grading + cross-harness (no rigorous Codex data exists) + Headroom (63k★, never independently tested).

## Research questions

1. Does each tool reduce cost-per-solved-task vs the plain agent?
2. Do JetBrains' findings replicate on a different task set and model?
3. Do findings transfer to Codex CLI with real installs?
4. Does reasoning effort moderate the effect? (rtk flipped +7.6% → +0.1% across effort levels)
5. Do savings depend on task type (software engineering / data analysis / short comprehension)?
6. Are savings bought with worse code?

Per tool, two pre-frozen reference points: the vendor claim and the JetBrains measurement.

## Design

**Setups (5):** plain agent (control), caveman, ponytail, rtk, Headroom. Cut: magic-compact (127★, headless-hostile), claude-mem (multi-session; future study).

**Factors:** 2 agents (Claude Code + Opus 5; Codex CLI + GPT-5.6 Sol) × 2 efforts (medium, xhigh). Activation: default install is the main condition; forced-activation is a best-case side slice (JetBrains: ponytail self-activated zero times passively).

**Tasks (3 families, ~34):**
- ~12 SWE-bench Verified bug fixes (hidden tests + human reference patch; ponytail's predicted best case)
- ~12 DSBench data-analysis tasks (exact numeric answers; rtk/Headroom's predicted best case — tool-output-heavy)
- ~10 self-authored comprehension questions (exact answers; overhead-dominated short regime)

Excluded task types: ML engineering (MLE-bench — cost/GPU/noisy grading), design (subjective grading only), Terminal-Bench (considered; no reference patches).

**Size:** ~680 main runs + ~150 warm-up/extras, on subscriptions, spread over 3–4 weeks.

## Measurement

- **Cost:** four token classes per run (fresh input / cached reads / cache writes / output) from session logs → simulated dollars at API list prices. Never a single token number; never a tool's own counter.
- **Success:** deterministic only (tests pass / numeric match / exact match). Cost and success read together.
- **Quality guardrail:** hidden tests; scope comparison vs the human reference patch; and a 3–4-trait AI-scored review with bias controls (diff-only, arm-blinded, two judges from different model families, human-calibrated on ~30 hand-scored samples — demoted to informational if calibration fails).
- **Verdict rule:** a tool delivers only if cost savings are significant AND quality is non-inferior within a pre-set margin.

## Analysis

Same-task paired differences; medians + bootstrap uncertainty ranges; sample size from warm-up variance (power to detect a 10% cost delta); multiple-comparison correction; pre-registered directional predictions per quality trait; discard rules fixed up front and applied symmetrically.

## Phases

0. Feasibility checks + runner build (exit: one task per family end-to-end on both agents with correct token accounting)
1. Warm-up: ~40 baseline runs → variance → final n
2. Pre-registration freeze (public tagged commit in this repo)
3. Main experiment (~680 runs)
4. Extras: forced-activation slice; judge calibration
5. Analysis → long-form article + this repo public

## Deliverables

- Long-form article on ytchou's portfolio (research-paper structure, plain-English tool explainers)
- This repo, public from pre-registration: frozen plan, task manifests, runner, raw per-run data, notebooks
- Notion page as the living tracker
