"""Agent drivers: run claude / codex headlessly in a workspace and normalize their output."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from runner.spec import RunSpec, RunnerError, StudyConfig

AgentResult = dict[str, Any]
"""Common shape: status, discard_reason, exit_code, wall_s, stdout, stderr, parsed,
session_id, session_log, final_text."""


def _base_result(agent: str, cmd: list[str]) -> AgentResult:
    return {
        "agent": agent,
        "cmd": cmd,
        "status": "ok",
        "discard_reason": None,
        "exit_code": None,
        "wall_s": 0.0,
        "stdout": "",
        "stderr": "",
        "parsed": None,
        "session_id": None,
        "session_log": None,
        "final_text": None,
    }


def _discard(result: AgentResult, reason: str) -> AgentResult:
    result["status"] = "discard"
    result["discard_reason"] = reason
    return result


def _build_env(
    cfg: StudyConfig, spec: RunSpec, config_dir: Path, env_var: str
) -> dict[str, str]:
    """Host env + the arm's hermetic config dir. Never points at ~/.claude or ~/.codex."""
    # Shortcut: we inherit the host environment wholesale (PATH, keychain access, locale).
    # Ceiling: a stray ANTHROPIC_*/OPENAI_* var in the shell could alter a run — upgrade
    # path is an allow-list env once the campaign starts.
    env = os.environ.copy()
    env.pop("CLAUDE_CONFIG_DIR", None)
    env.pop("CODEX_HOME", None)
    env[env_var] = str(config_dir)
    agent_env = cfg.agent_cfg(spec.agent).get("env") or {}
    env.update({str(k): str(v) for k, v in agent_env.items()})
    env.update(
        {
            str(k): str(v)
            for k, v in cfg.effort_overrides(spec.agent, spec.effort)["env"].items()
        }
    )
    return env


def _preflight(
    spec: RunSpec, binary: str, config_dir: Path, wrapper: str | None = None
) -> str | None:
    """Return a discard reason when the run cannot start, else None."""
    if shutil.which(binary) is None:
        return f"missing_binary: '{binary}' not found on PATH"
    if wrapper and shutil.which(wrapper) is None:
        return (
            f"missing_binary: wrapper '{wrapper}' (arms.{spec.arm}.wrap) not found on PATH"
        )
    if not config_dir.is_dir():
        return (
            f"missing_config_dir: {config_dir} — create/install the '{spec.arm}' arm's "
            f"sandbox config for agent '{spec.agent}' before running it"
        )
    return None


def _execute(
    result: AgentResult, cmd: list[str], workspace: Path, env: dict[str, str], timeout_s: int
) -> AgentResult:
    """Run the agent process with stdin closed, captured output and a hard timeout."""
    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(workspace),
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        result["wall_s"] = round(time.monotonic() - started, 3)
        result["stdout"] = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        result["stderr"] = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return _discard(result, f"timeout after {timeout_s}s")

    result["wall_s"] = round(time.monotonic() - started, 3)
    result["exit_code"] = proc.returncode
    result["stdout"] = proc.stdout or ""
    result["stderr"] = proc.stderr or ""
    if proc.returncode != 0:
        _discard(result, f"nonzero_exit: {proc.returncode}: {result['stderr'].strip()[:300]}")
    return result


def _model_args(agent_cfg: dict[str, Any]) -> list[str]:
    model = agent_cfg.get("model")
    flag = agent_cfg.get("model_flag")
    if not model or not flag:
        return []
    return [str(flag), str(model)]


def _wrap_cmd(cmd: list[str], wrapper: str | None) -> list[str]:
    """Run the agent command under a wrapper binary: `<wrapper> wrap <agent> -- <args>`.

    Only the argv changes — cwd and env (CLAUDE_CONFIG_DIR / CODEX_HOME) are untouched,
    so a wrapped run still uses the arm's hermetic config dir.
    """
    if not wrapper:
        return cmd
    return [wrapper, "wrap", cmd[0], "--", *cmd[1:]]


def _glob_first(root: Path, pattern: str) -> Path | None:
    """Newest path under `root` matching `pattern`, or None."""
    if not root.exists():
        return None
    matches = sorted(root.glob(pattern))
    return matches[-1] if matches else None


def _parse_result_object(stdout: str) -> dict[str, Any] | None:
    """The CLI's JSON result object from stdout, or None.

    Wrapper arms (headroom wrap) print their own banner and arg echo to stdout
    around the CLI's result, so whole-stdout parsing rejects genuinely successful
    runs. Accept the LAST line that parses to a JSON object — the result line is
    the final thing the wrapped CLI writes.
    """
    whole = stdout.strip()
    if not whole:
        return None
    try:
        parsed = json.loads(whole)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    for line in reversed(whole.splitlines()):
        line = line.strip()
        if not (line.startswith("{") and line.endswith("}")):
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def run_claude(
    spec: RunSpec, cfg: StudyConfig, workspace: Path, prompt: str
) -> AgentResult:
    """Run `claude -p ... --output-format json` and parse its single JSON result object."""
    agent_cfg = cfg.agent_cfg(spec.agent)
    binary = str(agent_cfg.get("binary", "claude"))
    config_dir = cfg.config_dir(spec.agent, spec.arm)
    wrapper = cfg.arm_wrap(spec.arm)
    cmd = _wrap_cmd(
        [
            binary,
            "-p",
            prompt,
            "--output-format",
            "json",
            *_model_args(agent_cfg),
            *[str(a) for a in (agent_cfg.get("extra_args") or [])],
            *[str(a) for a in cfg.effort_overrides(spec.agent, spec.effort)["args"]],
            *cfg.extra_agent_args(spec.agent, spec.arm),
        ],
        wrapper,
    )
    result = _base_result(spec.agent, cmd)

    reason = _preflight(spec, binary, config_dir, wrapper)
    if reason:
        return _discard(result, reason)

    env = _build_env(cfg, spec, config_dir, "CLAUDE_CONFIG_DIR")
    _execute(result, cmd, workspace, env, cfg.agent_timeout_s)

    result["parsed"] = _parse_result_object(result["stdout"])
    parsed = result["parsed"]

    if parsed is None:
        if result["status"] == "ok":
            _discard(result, "unparsable_output: stdout was not a JSON object")
        return result

    result["session_id"] = parsed.get("session_id")
    result["final_text"] = parsed.get("result")
    if parsed.get("is_error"):
        _discard(result, f"agent_error: {str(parsed.get('result'))[:300]}")

    if result["session_id"]:
        # Munged-cwd dir name is derivable but brittle; glob by session id instead.
        found = _glob_first(config_dir / "projects", f"*/{result['session_id']}.jsonl")
        result["session_log"] = str(found) if found else None
    return result


def run_codex(
    spec: RunSpec, cfg: StudyConfig, workspace: Path, prompt: str
) -> AgentResult:
    """Run `codex exec --json` and parse its JSONL event stream."""
    agent_cfg = cfg.agent_cfg(spec.agent)
    binary = str(agent_cfg.get("binary", "codex"))
    config_dir = cfg.config_dir(spec.agent, spec.arm)
    wrapper = cfg.arm_wrap(spec.arm)
    cmd = _wrap_cmd(
        [
            binary,
            "exec",
            "--json",
            "--skip-git-repo-check",
            *_model_args(agent_cfg),
            *[str(a) for a in (agent_cfg.get("extra_args") or [])],
            *[str(a) for a in cfg.effort_overrides(spec.agent, spec.effort)["args"]],
            # Arm args sit before the prompt: `codex exec` takes the prompt positionally.
            *cfg.extra_agent_args(spec.agent, spec.arm),
            prompt,
        ],
        wrapper,
    )
    result = _base_result(spec.agent, cmd)

    reason = _preflight(spec, binary, config_dir, wrapper)
    if reason:
        return _discard(result, reason)

    env = _build_env(cfg, spec, config_dir, "CODEX_HOME")
    _execute(result, cmd, workspace, env, cfg.agent_timeout_s)

    events: list[dict[str, Any]] = []
    for line in result["stdout"].splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    result["parsed"] = events

    if not events:
        if result["status"] == "ok":
            _discard(result, "unparsable_output: no JSON events on stdout")
        return result

    for event in events:
        if event.get("type") == "thread.started" and event.get("thread_id"):
            result["session_id"] = event["thread_id"]
        if event.get("type") == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message":
                result["final_text"] = item.get("text") or item.get("content")

    if not any(event.get("type") == "turn.completed" for event in events):
        if result["status"] == "ok":
            _discard(result, "no_turn_completed: run produced no usage event")

    if result["session_id"]:
        found = _glob_first(
            config_dir / "sessions", f"*/*/*/rollout-*{result['session_id']}*.jsonl"
        )
        result["session_log"] = str(found) if found else None
    return result


def run_agent(
    spec: RunSpec, cfg: StudyConfig, workspace: Path, prompt: str
) -> AgentResult:
    """Dispatch to the driver for spec.agent."""
    drivers = {"claude": run_claude, "codex": run_codex}
    if spec.agent not in drivers:
        raise RunnerError(
            f"no driver for agent '{spec.agent}' (known: {', '.join(sorted(drivers))})"
        )
    return drivers[spec.agent](spec, cfg, workspace, prompt)
