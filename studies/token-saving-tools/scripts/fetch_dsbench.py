"""DSBench data-analysis fetcher (to-do 0.13): download, checksum, extract, filter.

The benchmark data is non-commercial-licensed, so nothing here is ever vendored —
the cache dir is gitignored and this script is the only way to (re)materialise it.
Two artefacts are pulled from HF (`liqiang888/DSBench`, `data_analysis/`):
`data.zip` (per-case workbooks + introduction/question text) and `data.json`
(JSONL metadata: one dict per case with `id`, `name`, `questions`, `answers`).
Both SHA256s are printed and written to `checksums.json` so a later rerun can prove
it saw the same upstream bytes.

`load_eligible()` applies the frozen eligibility rule and is what
`select_main_tasks.py` imports; running this file just does the fetch + a summary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, NamedTuple

STUDY_ROOT = Path(__file__).resolve().parents[1]
LAB_ROOT = STUDY_ROOT.parents[1]
if str(LAB_ROOT) not in sys.path:
    sys.path.insert(0, str(LAB_ROOT))

from runner.spec import RunnerError  # noqa: E402

HF_BASE = "https://huggingface.co/datasets/liqiang888/DSBench/resolve/main/data_analysis"
DATA_ZIP_URL = f"{HF_BASE}/data.zip"
DATA_JSON_URL = f"{HF_BASE}/data.json"

DEFAULT_CACHE = STUDY_ROOT / "tasks" / ".dsbench_cache"
EXTRACT_DIRNAME = "extracted"
CHECKSUMS_NAME = "checksums.json"

WORKBOOK_SUFFIXES = (".xlsx", ".xls")
INTRODUCTION_NAME = "introduction.txt"
QUESTION_RE = re.compile(r"^question(\d+)\.txt$")
CASE_DIR_RE = re.compile(r"^\d{8}$")

# Frozen eligibility rule: an answer counts as numeric only if it is a bare number
# (thousands separators and a decimal point allowed). Percentages are excluded because
# the 0.5 absolute tolerance is meaningless against a percent-scaled answer.
NUMERIC_ANSWER_RE = re.compile(r"^-?[\d,.]+%?$")

_DOWNLOAD_CHUNK = 1 << 20


class NumericQuestion(NamedTuple):
    """One numeric-answer question of a case: its `questionN.txt` file and gold value."""

    number: int
    question_file: str
    answer: float


class EligibleCase(NamedTuple):
    """A DSBench case that clears the frozen eligibility rule."""

    dir_id: str
    name: str
    path: Path
    workbooks: list[str]
    numeric_questions: list[NumericQuestion]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_DOWNLOAD_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, dest: Path, force: bool = False) -> Path:
    """Fetch `url` to `dest`, skipping the transfer when the file is already cached."""
    if dest.is_file() and not force:
        print(f"cached: {dest.name} ({dest.stat().st_size} bytes)")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"downloading: {url}")
    try:
        with urllib.request.urlopen(url) as response, tmp.open("wb") as handle:
            shutil.copyfileobj(response, handle, _DOWNLOAD_CHUNK)
    except (urllib.error.URLError, OSError) as exc:
        tmp.unlink(missing_ok=True)
        raise RunnerError(f"download failed for {url}: {exc}") from exc
    tmp.replace(dest)
    return dest


def _extract(zip_path: Path, extract_root: Path, force: bool = False) -> Path:
    """Unzip `data.zip` once; the marker file keeps reruns cheap and idempotent."""
    marker = extract_root / ".extracted"
    if marker.is_file() and not force:
        print(f"already extracted: {extract_root}")
        return extract_root
    if force and extract_root.exists():
        shutil.rmtree(extract_root)
    extract_root.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extract_root)
    except (zipfile.BadZipFile, OSError) as exc:
        raise RunnerError(f"could not extract {zip_path}: {exc}") from exc
    marker.write_text("ok\n")
    return extract_root


def _parse_numeric_answer(answer: Any) -> float | None:
    """Gold answer -> float, or None when it is not a plain (non-percent) number."""
    if isinstance(answer, (int, float)) and not isinstance(answer, bool):
        return float(answer)
    if not isinstance(answer, str):
        return None
    text = answer.strip()
    if not text or not NUMERIC_ANSWER_RE.match(text):
        return None
    if text.endswith("%"):
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def load_metadata(cache_dir: Path) -> dict[str, dict[str, Any]]:
    """Parse `data.json` (JSONL) into {case id: metadata dict}."""
    path = cache_dir / "data.json"
    if not path.is_file():
        raise RunnerError(f"metadata not fetched yet: {path} (run fetch_dsbench.py)")
    meta: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RunnerError(f"{path}:{lineno} is not valid JSON: {exc}") from exc
            case_id = str(record.get("id", "")).strip()
            if not case_id:
                raise RunnerError(f"{path}:{lineno} has no 'id'")
            meta[case_id] = record
    if not meta:
        raise RunnerError(f"no metadata records in {path}")
    return meta


def _case_dirs(extract_root: Path) -> dict[str, Path]:
    """Locate the 8-digit case directories wherever the zip nested them."""
    found: dict[str, Path] = {}
    for path in extract_root.rglob("*"):
        if not path.is_dir() or not CASE_DIR_RE.match(path.name):
            continue
        # A case dir is the one holding the question text, not an intermediate.
        if any(QUESTION_RE.match(child.name) for child in path.iterdir() if child.is_file()):
            found[path.name] = path
    if not found:
        raise RunnerError(f"no DSBench case directories under {extract_root}")
    return found


def _question_files(case_path: Path) -> list[tuple[int, str]]:
    """`questionN.txt` files of a case, sorted by N."""
    numbered: list[tuple[int, str]] = []
    for child in case_path.iterdir():
        match = QUESTION_RE.match(child.name) if child.is_file() else None
        if match:
            numbered.append((int(match.group(1)), child.name))
    return sorted(numbered)


def _workbooks(case_path: Path) -> list[str]:
    """Spreadsheet inputs of a case; Excel lock files (`~$...`) are not data."""
    return sorted(
        child.name
        for child in case_path.iterdir()
        if child.is_file()
        and child.suffix.lower() in WORKBOOK_SUFFIXES
        and not child.name.startswith("~$")
    )


def load_eligible(cache_dir: Path = DEFAULT_CACHE) -> dict[str, EligibleCase]:
    """Cases clearing the frozen rule: >=1 workbook AND metadata AND >=1 numeric answer.

    Question files are paired with `answers[]` positionally — the i-th question file of
    a case (ordered by its number) is the i-th entry of the metadata lists.
    """
    extract_root = cache_dir / EXTRACT_DIRNAME
    if not extract_root.is_dir():
        raise RunnerError(f"DSBench not extracted yet: {extract_root} (run fetch_dsbench.py)")
    meta = load_metadata(cache_dir)
    eligible: dict[str, EligibleCase] = {}

    for dir_id, case_path in sorted(_case_dirs(extract_root).items()):
        record = meta.get(dir_id)
        if record is None:
            continue
        workbooks = _workbooks(case_path)
        if not workbooks:
            continue
        question_files = _question_files(case_path)
        answers = record.get("answers") or []
        if not isinstance(answers, list) or not question_files:
            continue
        if len(answers) != len(question_files):
            # Positional pairing is only sound when the two lists line up; a mismatched
            # case is dropped rather than risking a wrong gold answer.
            continue
        numeric = [
            NumericQuestion(number=number, question_file=name, answer=value)
            for (number, name), raw in zip(question_files, answers)
            if (value := _parse_numeric_answer(raw)) is not None
        ]
        if not numeric:
            continue
        eligible[dir_id] = EligibleCase(
            dir_id=dir_id,
            name=str(record.get("name", dir_id)),
            path=case_path,
            workbooks=workbooks,
            numeric_questions=numeric,
        )

    if not eligible:
        raise RunnerError(f"no eligible DSBench cases under {extract_root}")
    return eligible


def fetch(cache_dir: Path = DEFAULT_CACHE, force: bool = False) -> dict[str, str]:
    """Download both artefacts, record their SHA256s, extract the zip."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    zip_path = _download(DATA_ZIP_URL, cache_dir / "data.zip", force=force)
    json_path = _download(DATA_JSON_URL, cache_dir / "data.json", force=force)

    checksums = {
        "data.zip": {"url": DATA_ZIP_URL, "sha256": _sha256(zip_path), "bytes": zip_path.stat().st_size},
        "data.json": {"url": DATA_JSON_URL, "sha256": _sha256(json_path), "bytes": json_path.stat().st_size},
    }
    (cache_dir / CHECKSUMS_NAME).write_text(json.dumps(checksums, indent=2) + "\n")
    _extract(zip_path, cache_dir / EXTRACT_DIRNAME, force=force)
    return {name: entry["sha256"] for name, entry in checksums.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch + filter the DSBench data-analysis split")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE, help=f"default: {DEFAULT_CACHE}")
    parser.add_argument("--force", action="store_true", help="re-download and re-extract")
    args = parser.parse_args()

    try:
        digests = fetch(args.cache_dir, force=args.force)
        for name, digest in digests.items():
            print(f"sha256  {name:<10} {digest}")
        print(f"checksums written: {args.cache_dir / CHECKSUMS_NAME}")
        eligible = load_eligible(args.cache_dir)
    except RunnerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    total_numeric = sum(len(case.numeric_questions) for case in eligible.values())
    print(f"eligible cases: {len(eligible)}  numeric questions: {total_numeric}")
    print(f"{'CASE':<12}{'NUMERIC':>8}{'WORKBOOKS':>11}  NAME")
    for dir_id, case in sorted(eligible.items()):
        print(f"{dir_id:<12}{len(case.numeric_questions):>8}{len(case.workbooks):>11}  {case.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
