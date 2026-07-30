"""Activation detection: scan a session log for the arm's tool signatures, and count the
study's configured log flags (compaction, rate limiting, transport fallback) in the log."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from runner.agents import AgentResult
from runner.spec import RunSpec, StudyConfig

MAX_EVIDENCE_CHARS = 200


def detect_activation(
    spec: RunSpec, cfg: StudyConfig, session_log: Path | None
) -> tuple[bool | None, str | None]:
    """Return (activated, evidence). None means "unknown" — no signatures or no log.

    Signatures are configured per arm per agent in study.yaml; arms with none (baseline,
    and every codex arm until codex-side markers are identified) always report unknown.
    """
    signatures = cfg.activation_signatures(spec.agent, spec.arm)
    if not signatures:
        return None, None
    if session_log is None or not Path(session_log).is_file():
        return None, None

    with Path(session_log).open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            for signature in signatures:
                index = line.find(signature)
                if index != -1:
                    start = max(0, index - 40)
                    snippet = line[start : start + MAX_EVIDENCE_CHARS].strip()
                    return True, snippet[:MAX_EVIDENCE_CHARS]
    return False, None


def _count_flags(lines: Iterable[str], flags: Mapping[str, list[str]]) -> dict[str, int]:
    """Count, per flag, the lines containing any of its substrings — one pass over `lines`.

    A line matching two substrings of the same flag counts once: the unit is "a log line
    that shows this happened", not "a substring occurrence".
    """
    counts = {name: 0 for name in flags}
    for line in lines:
        for name, substrings in flags.items():
            if any(substring in line for substring in substrings):
                counts[name] += 1
    return counts


def scan_log_flags(
    spec: RunSpec, cfg: StudyConfig, agent_result: AgentResult
) -> dict[str, int]:
    """Count the study's `log_flags:` substrings in this run's log.

    Claude is scanned from its session JSONL; codex has no equally rich session log, so it
    is scanned from the raw stdout capture (already in memory from the driver). Returns {}
    when nothing is configured for the agent or when the log is unavailable — an empty dict
    means "not measured", a zero count means "measured and absent".
    """
    flags = cfg.log_flags(spec.agent)
    if not flags:
        return {}

    if spec.agent == "codex":
        return _count_flags((agent_result.get("stdout") or "").splitlines(), flags)

    session_log = agent_result.get("session_log")
    if not session_log or not Path(session_log).is_file():
        return {}
    with Path(session_log).open("r", encoding="utf-8", errors="replace") as handle:
        return _count_flags(handle, flags)
