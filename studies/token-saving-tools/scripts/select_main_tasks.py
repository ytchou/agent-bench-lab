"""Main-study task selection (to-do 0.13): deterministic, idempotent, rerunnable.

Every rule here is frozen at pre-registration — no sampling, no seeds, no "pick a nice
one". Rerunning the script on the same upstream data must reproduce byte-identical
manifests, and the warm-up/gate entries already in those manifests are never touched:
main-study entries are appended after them and deduped by id.

DSBench: eligible cases (see `fetch_dsbench.load_eligible`) minus the four already used
in warm-up/gate; every numeric-answer question of every remaining case is a task
(`<case>-q<N>`), sorted by (case id, question number) and capped at 12. The fresh pool
happens to hold exactly 12, but the cap keeps the rule total. Inputs are staged into
`tasks/dsbench_data/<case>-q<N>/`.

SWE-bench: `princeton-nlp/SWE-bench_Verified` test split, filtered on problem-statement
length and gold-patch size, then filled per difficulty bucket by round-robin over repos
(alphabetical by org prefix) taking the first-alphabetical eligible instance each pass —
maximum repo diversity with zero randomness.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

STUDY_ROOT = Path(__file__).resolve().parents[1]
LAB_ROOT = STUDY_ROOT.parents[1]
if str(LAB_ROOT) not in sys.path:
    sys.path.insert(0, str(LAB_ROOT))

from runner.spec import RunnerError  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_dsbench import (  # noqa: E402
    DEFAULT_CACHE,
    EligibleCase,
    NumericQuestion,
    load_eligible,
)

TASKS_DIR = STUDY_ROOT / "tasks"
DSBENCH_MANIFEST = TASKS_DIR / "dsbench.json"
SWEBENCH_MANIFEST = TASKS_DIR / "swebench.json"
DSBENCH_DATA_DIR = TASKS_DIR / "dsbench_data"
INTRODUCTION_NAME = "introduction.txt"

# Already spent on warm-up + gate; excluded so the main study is a fresh sample.
USED_DSBENCH_DIRS = frozenset({"00000001", "00000005", "00000010", "00000019"})
USED_SWEBENCH_INSTANCES = frozenset(
    {
        "pallets__flask-5014",
        "psf__requests-1766",
        "django__django-11141",
        "scikit-learn__scikit-learn-10844",
        "sympy__sympy-12096",
    }
)

DSBENCH_TASK_COUNT = 12
DSBENCH_TOLERANCE = 0.5

SWEBENCH_DATASET = "princeton-nlp/SWE-bench_Verified"
SWEBENCH_SPLIT = "test"
PROBLEM_MIN_CHARS = 500
PROBLEM_MAX_CHARS = 4000
PATCH_MAX_LINES = 60

# Frozen bucket plan: 12 instances skewed to the short/medium end of the difficulty
# labels, because the study measures token cost, not ceiling capability.
DIFFICULTY_QUOTAS: tuple[tuple[str, int], ...] = (
    ("<15 min fix", 4),
    ("15 min - 1 hour", 6),
    ("1-4 hours", 2),
)

# Every org prefix in SWE-bench Verified, alphabetical — the round-robin order.
REPO_ORDER: tuple[str, ...] = (
    "astropy",
    "django",
    "matplotlib",
    "mwaskom",
    "psf",
    "pydata",
    "pylint-dev",
    "pytest-dev",
    "scikit-learn",
    "sphinx-doc",
    "sympy",
)


def _load_manifest(path: Path) -> list[dict[str, Any]]:
    """Existing manifest entries (warm-up/gate) — preserved verbatim."""
    if not path.is_file():
        return []
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RunnerError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(entries, list):
        raise RunnerError(f"task manifest must be a JSON list: {path}")
    return entries


def _entry_id(entry: dict[str, Any]) -> str:
    task_id = entry.get("id") or entry.get("instance_id")
    if not task_id:
        raise RunnerError(f"manifest entry has no 'id' / 'instance_id': {entry}")
    return str(task_id)


def _merge(existing: Sequence[dict[str, Any]], selected: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Append selected entries after the existing ones, skipping ids already present."""
    seen = {_entry_id(entry) for entry in existing}
    merged = list(existing)
    for entry in selected:
        if _entry_id(entry) in seen:
            continue
        seen.add(_entry_id(entry))
        merged.append(entry)
    return merged


def _write_manifest(path: Path, entries: Sequence[dict[str, Any]]) -> bool:
    """Write only when the bytes change, so reruns are true no-ops."""
    payload = json.dumps(list(entries), indent=1)
    if path.is_file() and path.read_text(encoding="utf-8") == payload:
        return False
    path.write_text(payload, encoding="utf-8")
    return True


# --- DSBench ---------------------------------------------------------------


def _compose_question(introduction: str, question: str) -> str:
    """Prompt text as frozen in the warm-up manifest: header, intro, blank gap, question."""
    return f"INTRODUCTION\n{introduction.strip()}\n\n\n{question.strip()}"


def select_dsbench(cache_dir: Path, dry_run: bool = False) -> list[dict[str, Any]]:
    """First 12 unused eligible cases, lowest-numbered numeric question each."""
    eligible = load_eligible(cache_dir)
    candidates = [case for dir_id, case in sorted(eligible.items()) if dir_id not in USED_DSBENCH_DIRS]

    # The fresh pool is dir-poor but question-rich, so every numeric question of a kept
    # dir is its own task. Tasks from one dir share a workbook — the grader is
    # per-question, but treat the clustering as a known dependence when analysing.
    pairs = sorted(
        (
            (case, question)
            for case in candidates
            for question in case.numeric_questions
        ),
        key=lambda pair: (pair[0].dir_id, pair[1].number),
    )

    selected: list[dict[str, Any]] = []
    for case, question in pairs:
        if len(selected) == DSBENCH_TASK_COUNT:
            break
        entry = _stage_dsbench_case(case, question, dry_run=dry_run)
        if entry is not None:
            selected.append(entry)

    if len(selected) < DSBENCH_TASK_COUNT:
        raise RunnerError(
            f"only {len(selected)} stageable DSBench questions, need {DSBENCH_TASK_COUNT}"
        )
    return selected


def _stage_dsbench_case(
    case: EligibleCase, question: NumericQuestion, dry_run: bool = False
) -> dict[str, Any] | None:
    """Copy one question's inputs into `dsbench_data/<case>-q<N>/` and build its entry."""
    intro_path = case.path / INTRODUCTION_NAME
    question_path = case.path / question.question_file
    if not intro_path.is_file() or not question_path.is_file():
        print(f"skip {case.dir_id}: missing {INTRODUCTION_NAME} or {question.question_file}")
        return None

    task_id = f"{case.dir_id}-q{question.number}"
    dest = DSBENCH_DATA_DIR / task_id
    data_files = [*case.workbooks, INTRODUCTION_NAME, question.question_file]
    if not dry_run:
        dest.mkdir(parents=True, exist_ok=True)
        for name in data_files:
            shutil.copyfile(case.path / name, dest / name)

    return {
        "id": task_id,
        "data_files": data_files,
        "question": _compose_question(
            intro_path.read_text(encoding="utf-8", errors="replace"),
            question_path.read_text(encoding="utf-8", errors="replace"),
        ),
        "answer_numeric": question.answer,
        "tolerance": DSBENCH_TOLERANCE,
    }


# --- SWE-bench -------------------------------------------------------------


def gold_diff_stats(patch: str) -> dict[str, int]:
    """Files/lines touched by the gold patch (unified diff; `+++`/`---` are headers)."""
    files_changed = added = removed = 0
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            files_changed += 1
        elif line.startswith("+++") or line.startswith("---"):
            continue
        elif line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return {"files_changed": files_changed, "lines_added": added, "lines_removed": removed}


def _is_eligible_swebench(row: dict[str, Any]) -> bool:
    instance_id = str(row.get("instance_id", ""))
    if instance_id in USED_SWEBENCH_INSTANCES:
        return False
    problem = str(row.get("problem_statement") or "")
    if not PROBLEM_MIN_CHARS < len(problem) < PROBLEM_MAX_CHARS:
        return False
    patch = str(row.get("patch") or "")
    return 0 < len(patch.splitlines()) < PATCH_MAX_LINES


def _load_swebench_rows() -> list[dict[str, Any]]:
    try:
        from datasets import load_dataset
    except ImportError as exc:  # optional dep: only the swebench family needs it
        raise RunnerError("the `datasets` package is required for SWE-bench selection") from exc
    try:
        dataset = load_dataset(SWEBENCH_DATASET, split=SWEBENCH_SPLIT)
    except Exception as exc:  # network / HF auth / dataset-script failures
        raise RunnerError(f"could not load {SWEBENCH_DATASET}: {exc}") from exc
    return [dict(row) for row in dataset]


def _round_robin(pools: dict[str, list[dict[str, Any]]], quota: int) -> list[dict[str, Any]]:
    """One instance per repo per pass, orgs in `REPO_ORDER`, until the quota fills."""
    chosen: list[dict[str, Any]] = []
    while len(chosen) < quota:
        progressed = False
        for org in REPO_ORDER:
            if len(chosen) == quota:
                break
            pool = pools.get(org)
            if not pool:
                continue
            chosen.append(pool.pop(0))
            progressed = True
        if not progressed:  # every repo exhausted before the quota filled
            break
    return chosen


def select_swebench() -> list[dict[str, Any]]:
    """Fill each difficulty bucket by repo round-robin over the eligible instances."""
    rows = [row for row in _load_swebench_rows() if _is_eligible_swebench(row)]

    selected: list[dict[str, Any]] = []
    for difficulty, quota in DIFFICULTY_QUOTAS:
        pools: dict[str, list[dict[str, Any]]] = {}
        bucket = [row for row in rows if str(row.get("difficulty", "")).strip() == difficulty]
        for row in sorted(bucket, key=lambda item: str(item["instance_id"])):
            org = str(row.get("repo", "")).split("/")[0]
            if org in REPO_ORDER:
                pools.setdefault(org, []).append(row)
        chosen = _round_robin(pools, quota)
        if len(chosen) < quota:
            raise RunnerError(
                f"difficulty '{difficulty}': only {len(chosen)} eligible instances, need {quota}"
            )
        selected.extend(
            {
                "instance_id": str(row["instance_id"]),
                "repo": str(row["repo"]),
                "base_commit": str(row["base_commit"]),
                "problem_statement": str(row["problem_statement"]),
                "difficulty": difficulty,
                "gold_diff_stats": gold_diff_stats(str(row.get("patch") or "")),
            }
            for row in chosen
        )
    return selected


# --- driver ----------------------------------------------------------------


def _print_table(title: str, rows: Iterable[tuple[str, str]]) -> None:
    print(f"\n{title}")
    for left, right in rows:
        print(f"  {left:<38}{right}")


def _dsbench_rows(entries: Sequence[dict[str, Any]]) -> list[tuple[str, str]]:
    """Summary grouped by case dir, so the shared-workbook clustering is visible."""
    by_dir: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        by_dir.setdefault(str(entry["id"]).split("-q")[0], []).append(entry)

    rows: list[tuple[str, str]] = []
    for dir_id, group in by_dir.items():
        workbooks = [name for name in group[0]["data_files"] if not name.endswith(".txt")]
        rows.append((f"{dir_id}  ({len(group)} tasks)", ", ".join(workbooks)))
        rows.extend(
            (f"  {entry['id']}", f"answer={entry['answer_numeric']:g}") for entry in group
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Select the main-study DSBench + SWE-bench tasks")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE, help=f"default: {DEFAULT_CACHE}")
    parser.add_argument("--family", choices=("dsbench", "swebench", "both"), default="both")
    parser.add_argument("--dry-run", action="store_true", help="select and print, write nothing")
    args = parser.parse_args()

    try:
        if args.family in ("dsbench", "both"):
            dsbench = select_dsbench(args.cache_dir, dry_run=args.dry_run)
            merged = _merge(_load_manifest(DSBENCH_MANIFEST), dsbench)
            changed = False if args.dry_run else _write_manifest(DSBENCH_MANIFEST, merged)
            _print_table(
                f"dsbench: {len(dsbench)} selected, {len(merged)} total in manifest"
                f" ({'unchanged' if not changed else 'written'})",
                _dsbench_rows(dsbench),
            )

        if args.family in ("swebench", "both"):
            swebench = select_swebench()
            merged = _merge(_load_manifest(SWEBENCH_MANIFEST), swebench)
            changed = False if args.dry_run else _write_manifest(SWEBENCH_MANIFEST, merged)
            _print_table(
                f"swebench: {len(swebench)} selected, {len(merged)} total in manifest"
                f" ({'unchanged' if not changed else 'written'})",
                (
                    (
                        entry["instance_id"],
                        f"{entry['difficulty']:<16}"
                        f"files={entry['gold_diff_stats']['files_changed']}"
                        f" +{entry['gold_diff_stats']['lines_added']}"
                        f" -{entry['gold_diff_stats']['lines_removed']}",
                    )
                    for entry in swebench
                ),
            )
    except RunnerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print("\ndry run: no manifests written, no DSBench data staged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
