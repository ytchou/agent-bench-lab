# DSBench data-analysis task review (to-do 0.9)

Reviewed 2026-07-30, all 40 task directories from `data_analysis/data.zip` (HuggingFace `liqiang888/DSBench`, 8 MB) + `data.json` ground truth (38 metadata entries).

## Verdict: suitable — no swap to InfiAgent-DABench needed

- **Structure:** each task dir has `introduction.txt` + `questionN.txt` files; 29/40 dirs include a real Excel workbook (ModelOff competition data). `data.json` carries per-question ground-truth answers.
- **Deterministic grading confirmed:** answers are choice letters ("D", "I") or exact numbers (1661626) — our ANSWER.txt protocol grades by normalized exact match / numeric tolerance. DSBench's own GPT-based checker (`compute_answer.py`) is NOT used — one of our critiques of prior work is judge-based grading, so we bypass it.
- **Tool-output-heavy confirmed:** workbook tasks force the agent to load multi-sheet Excel via pandas/openpyxl and print slices repeatedly — the L2 tools' (rtk/Headroom) claimed best case.
- **Eligibility rule (frozen logic, applied at pre-registration):** task dir must have ≥1 data workbook AND ground-truth metadata. Excludes theory-only MCQ sets (00000002, 00000036, 00000037, 00000042 …) — those test recall, not analysis. ~28 dirs qualify.

## Task unit

One run = (task dir, ONE selected question): workspace gets the workbook + `introduction.txt` + the single question; agent writes the answer to `ANSWER.txt`. Rationale: bounded trajectory length per run, one deterministic answer per row. Question selection at pre-registration: prefer numeric-answer questions (letter answers admit 1-in-N lucky guesses; where letters are used, record answer type so guess-rate can be reported).

## Caveats

- **License:** DSBench is "strictly for non-commercial use" — our public repo must NOT vendor the data; ship a download-and-extract script (HF URL + checksums) instead.
- Answer normalization must handle formats like `1,661,626`, `$1661626`, `1.66M` → the prompt instructs "digits only, no separators/units"; grader also strips `,` `$` `%`.
- 2 dirs lack `data.json` metadata (00000015, 00000040) — ineligible.

## Exit-gate task

`00000001` (2016-round-1-section-2-chip-off-the-old-block) / `question17`, ground truth `1661626` (numeric, workbook-based).
