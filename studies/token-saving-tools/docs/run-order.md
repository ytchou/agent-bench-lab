# Step-3 run order and amendment A1 (rtk withdrawal)

Registered 2026-07-30, **before any main (phase 3) run**. This document does two things:
records amendment A1 to the pre-registration, and fixes the campaign run order and
stop rule so that any early termination is provably resource-driven, not result-driven.

## Amendment A1 — rtk arm withdrawn from the study

**Decision (Patrick, 2026-07-30, pre-data):** the rtk arm is removed from the study
entirely. Zero rtk runs exist at the time of this decision (warm-up was baseline-only),
so the withdrawal cannot be data-driven.

**Reason:** rtk v0.44.1 has no Codex CLI support, making it the only asymmetric arm.
Dropping it yields a fully symmetric 4-arm × 2-agent design (baseline, caveman,
ponytail, Headroom) and cuts the campaign from ~1,004 to **912 runs**.

**Effects on frozen artifacts (text left intact; this amendment governs):**

- `hypotheses.md`: **H-RT-1..4 are withdrawn untested** — the article reports rtk as
  "excluded: no Codex support," with no empirical verdict. G2's rtk mention reduces to
  Headroom as the sole tool-output compressor. H-HR-1's comparative clause ("Headroom
  outperforms rtk") is untestable and void; its magnitude band (−5% to −20%) stands.
- `analysis-plan.md`: Claude arms 5 → 4; the Holm family shrinks accordingly
  (3 tools × 2 agents instead of 4 + 3).
- `config/study.yaml`: the `rtk` arm block remains for the record but is **not
  scheduled**; `versions.md` rtk pin stands as documentation of the exclusion basis.
- `freeze.md` run plan: superseded by the totals below.

## Run plan (912 runs)

| Tier | Content | Claude | Codex | Tier total | Cumulative |
|---|---|---|---|---|---|
| 1 | medium effort, 4 arms, 34 tasks | 184 | 272 | 456 | 50% |
| 2 | xhigh effort, 4 arms, 34 tasks | 184 | 272 | 456 | 100% |

Per-agent replication (frozen at prereg, unchanged): Claude k=1 except k=2 on SWE
cells → 46 runs/arm; Codex k=2 on all cells → 68 runs/arm.

Within Tier 1, the Claude slice runs first (checkpoint 1a, 184 runs): if the campaign
is halted after 1a, the surviving article scope is "3 tools on Claude Code at default
effort," still a complete paired experiment.

## Ordering rules (apply within every tier)

1. **Task-complete slices:** all arms of an (agent, effort, task) cell are run in the
   same batch window, so every completed task carries its baseline pair. A stop at any
   batch boundary orphans no treatment run.
2. **Rep-major ordering** (as in warm-up): all rep-1 runs of a slice before any rep-2
   run, defeating the 5-minute prompt-cache TTL between replicates.
3. **Arm interleaving:** within a slice, arms rotate per task rather than running one
   arm's full task list, so time-of-day API drift spreads across arms instead of
   confounding one.

## Stop rule (pre-declared)

An early stop or scope trim may be decided **only on time, subscription-cap, or
infrastructure grounds — never on observed results** (the no-peeking protocol in
`analysis-plan.md` makes result-driven stopping impossible in any case: no
with-vs-without comparison is computed before the campaign ends). Tiers or slices not
run are reported as "not run (resource constraint)" and their hypotheses as untested.
