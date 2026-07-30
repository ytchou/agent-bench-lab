# Pre-registered hypotheses

> **Amendment A1 (2026-07-30, pre-data — see `run-order.md`):** the rtk arm is
> withdrawn from the study (no Codex support; zero rtk runs existed at decision time).
> H-RT-1..4 are untested, and H-HR-1's "outperforms rtk" clause is void. The frozen
> text below is preserved unchanged.

**Status: DRAFT — becomes immutable at the pre-registration tag (to-do 2.5).**
Written 2026-07-30, before any treatment run. Every prediction below is falsifiable and graded in the final article exactly as stated; the analysis rules live in `analysis-plan.md`.

## Decision rule (applies to every tool)

A tool **delivers on its claim** only if BOTH hold on the pre-registered primary metric (cost-of-pass = simulated USD ÷ success rate, same-task paired vs baseline):

1. **Cost:** statistically significant saving (paired bootstrap CI excluding 0, Holm-corrected across tools) of at least the magnitude band predicted below;
2. **Quality:** suite success rate non-inferior to baseline within **10 percentage points** (TOST, margin frozen here).

Power context, frozen from the warm-up study: the **Claude arm** can detect ≈10–15% suite-wide cost effects; the **Codex arm** only ≈18–27% — therefore all Codex hypotheses test *vendor-scale* claims (≥50%) only, and Codex effects smaller than ~20% are declared undetectable in advance, not reported as nulls.

## Scope facts fixed by arm setup (2026-07-30)

- **rtk has no Codex CLI support** (not in its supported-agent list) → rtk is a Claude-only arm; Codex runs 4 arms.
- On Codex, caveman and ponytail install **project-locally** as skills (`.agents/skills/`); passive activation is expected to be rare-to-zero — that activation rate is itself a registered outcome (G3).
- Headroom wraps both agents (verified live on Codex under subscription auth).

## Global hypotheses

| ID | Prediction | Grades as |
|---|---|---|
| **G1** | No tool achieves its vendor-claimed savings magnitude on cost-of-pass on any agent | Supported if every vendor-magnitude test fails |
| **G2 (family moderation)** | Savings, where they exist, concentrate in each tool's mechanism-predicted family: output-style tools (caveman, ponytail) → short/chat-heavy and code-writing tasks respectively; tool-output compressors (rtk, Headroom) → data-analysis tasks | Per-family interaction, exploratory-labeled if underpowered |
| **G3 (activation gap)** | Default-install activation < 100% everywhere; Codex skill-based installs activate in <10% of runs | From per-run activation detection |
| **G4 (effort moderation)** | Relative savings shrink at xhigh vs medium effort (JetBrains: rtk +7.6% → +0.1%) | Direction of effort × arm interaction |

## Per-tool hypotheses

### caveman (94k★) — L1 output-style

| Reference | Number |
|---|---|
| Vendor claim | −65% output tokens |
| JetBrains measured | −8.5% cost (claim collapsed) |
| Own README concedes | +1–1.5k instruction tokens/message; "net-negative on already-terse workloads" |

- **H-CM-1 (Claude, suite):** modest real cost saving, −5% to −20% cost-of-pass; vendor's −65% NOT delivered.
- **H-CM-2 (family):** largest saving on comprehension (short, chat-heavy); ≈0 or negative on SWE-bench. *(Prior: the accidental contaminated-vs-sterile comparison showed −21.6% comprehension / −0.4% SWE with caveman+ponytail stacked — recorded before this freeze, not evidence, but this prediction is consistent with it.)*
- **H-CM-3 (Codex):** passive activation ≈0 (skill install) → measured effect ≈0; vendor claim not delivered.
- **H-CM-4 (quality):** success non-inferior within margin (terser replies, same answers).

### ponytail (91k★) — L1 output-style (code minimalism)

| Reference | Number |
|---|---|
| Vendor claim | −54% code written |
| JetBrains measured | −15.4% tokens / −10.3% cost, p=0.004 — the only confirmed win; but 0 passive activations when merely installed |

- **H-PT-1 (Claude, suite):** the JetBrains result replicates in direction: cost-of-pass saving −5% to −20%, concentrated in SWE.
- **H-PT-2 (mechanism):** `diff_lines_added` drops vs baseline on SWE tasks (the "less code" mechanism is visible in our scope metrics).
- **H-PT-3 (activation):** Claude plugin activates passively in >50% of runs (its rules attach at session start); Codex skill ≈0 (G3).
- **H-PT-4 (quality risk):** of all tools, ponytail is the most likely to breach the 10pp margin on SWE (under-engineering risk); we predict it stays *within* margin but flag this as the study's most informative quality test.

### rtk (74k★) — L2 tool-output compression (Bash channel only)

| Reference | Number |
|---|---|
| Vendor claim | −60–90% |
| JetBrains measured | **+7.6% more expensive** (self-counter simultaneously claimed 96M tokens "saved") |
| codepointer replay | real-spend savings <4% |

- **H-RT-1 (Claude, suite):** no significant cost-of-pass saving; CI includes 0. Vendor claim not delivered.
- **H-RT-2 (mechanism ceiling):** rtk's addressable surface (Bash-channel bytes ÷ total tool-output bytes, from our per-channel metrics) averages <30% — the claim is structurally impossible at full effectiveness.
- **H-RT-3 (family):** best showing on DSBench (tool-output-heavy) but still not significant.
- **H-RT-4:** Codex arm does not exist (no support) — recorded as a finding about the ecosystem, not a test.

### Headroom (63k★) — L2 tool-output compression (all channels; never independently tested)

| Reference | Number |
|---|---|
| Vendor claim | no single number marketed; positioned as rtk-class savings at full coverage |
| Prior tests | none (our novel contribution) |

- **H-HR-1 (Claude, suite):** small-to-moderate real saving, −5% to −20% cost-of-pass — full-channel coverage fixes rtk's structural ceiling, so Headroom outperforms rtk.
- **H-HR-2 (family):** largest saving on DSBench; smallest on comprehension (little tool output to compress).
- **H-HR-3 (Codex):** the only treatment expected to show a *detectable* Codex effect if any does; tested at vendor scale (≥50%) and predicted NOT to reach it.
- **H-HR-4 (overhead):** wrapper overhead (proxy latency, its own prompt additions) keeps net savings well under the compression ratio it reports about itself — self-reported metrics are recorded but never used as outcomes.

## Prediction grid (the article's scorecard)

| Tool | Claude comprehension | Claude DSBench | Claude SWE | Codex (vendor-scale only) |
|---|---|---|---|---|
| caveman | **save** | ~0 | ~0/negative | ≈0 (no activation) |
| ponytail | ~0 | ~0 | **save** | ≈0 (no activation) |
| rtk | ~0 | ~0 (best case, still null) | ~0 | no arm |
| Headroom | ~0 | **save** | small save | < vendor scale |

Every cell is graded ✓/✗ in the article; wrong predictions get printed, not buried.
