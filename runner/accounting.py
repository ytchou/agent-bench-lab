"""Token accounting: extract the token classes per agent and price them from prices.toml."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

from runner.agents import AgentResult
from runner.spec import RunnerError

Usage = dict[str, Any]
"""tokens_fresh, tokens_cache_write, tokens_cache_write_1h, tokens_cache_read,
tokens_out, tokens_reasoning, model_usage, turns, tool_calls, cost_usd_cli_reported."""

# codex `item.completed` item types that represent the agent using a tool.
CODEX_TOOL_ITEM_TYPES = frozenset(
    {
        "command_execution",
        "file_change",
        "patch_apply",
        "mcp_tool_call",
        "web_search",
        "local_shell_call",
        "function_call",
    }
)

_PER_MILLION = 1_000_000


def _empty_usage() -> Usage:
    return {
        "tokens_fresh": 0,
        "tokens_cache_write": 0,
        "tokens_cache_write_1h": 0,
        "tokens_cache_read": 0,
        "tokens_out": 0,
        "tokens_reasoning": None,
        "model_usage": {},
        "turns": None,
        "tool_calls": None,
        "cost_usd_cli_reported": None,
    }


def count_claude_tool_calls(session_log: Path) -> int | None:
    """Count `tool_use` content blocks across assistant lines of a Claude session log."""
    if not session_log.is_file():
        return None
    count = 0
    with session_log.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") != "assistant":
                continue
            content = (entry.get("message") or {}).get("content")
            if isinstance(content, list):
                count += sum(
                    1
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "tool_use"
                )
    return count


def _claude_usage(result: AgentResult) -> Usage:
    usage = _empty_usage()
    parsed = result.get("parsed") or {}
    reported = parsed.get("usage") or {}
    cache_creation = reported.get("cache_creation") or {}

    usage["tokens_fresh"] = int(reported.get("input_tokens", 0) or 0)
    usage["tokens_cache_write"] = int(reported.get("cache_creation_input_tokens", 0) or 0)
    usage["tokens_cache_write_1h"] = int(
        cache_creation.get("ephemeral_1h_input_tokens", 0) or 0
    )
    usage["tokens_cache_read"] = int(reported.get("cache_read_input_tokens", 0) or 0)
    usage["tokens_out"] = int(reported.get("output_tokens", 0) or 0)
    # Claude does not report a reasoning class; leave it null rather than zero.
    usage["tokens_reasoning"] = None
    usage["model_usage"] = parsed.get("modelUsage") or {}
    usage["turns"] = parsed.get("num_turns")
    usage["cost_usd_cli_reported"] = parsed.get("total_cost_usd")

    session_log = result.get("session_log")
    if session_log:
        usage["tool_calls"] = count_claude_tool_calls(Path(session_log))
    return usage


def _codex_usage(result: AgentResult) -> Usage:
    """Normalize codex `turn.completed` usage events.

    Codex's `input_tokens` INCLUDES `cached_input_tokens` (codex-rs computes
    `non_cached_input() = input_tokens - cached_input()` and blends on that), so fresh
    is the non-cached remainder. Codex reports no per-model split, so `model_usage` is
    reused to carry `context_peak_tokens` — the largest single-turn `input_tokens`,
    which approximates the run's peak per-request context size for the long-context tier.
    """
    usage = _empty_usage()
    events = result.get("parsed") or []
    turns = 0
    tool_calls = 0
    reasoning = 0
    context_peak = 0

    for event in events:
        event_type = event.get("type")
        if event_type == "turn.completed":
            turns += 1
            reported = event.get("usage") or {}
            turn_input = int(reported.get("input_tokens", 0) or 0)
            turn_cached = int(reported.get("cached_input_tokens", 0) or 0)
            # cached is a subset of input; fresh is the non-cached remainder.
            usage["tokens_fresh"] += max(turn_input - turn_cached, 0)
            usage["tokens_cache_read"] += turn_cached
            # input_tokens (incl. cached) is that request's context size.
            context_peak = max(context_peak, turn_input)
            usage["tokens_cache_write"] += int(
                reported.get("cache_write_input_tokens", 0) or 0
            )
            usage["tokens_out"] += int(reported.get("output_tokens", 0) or 0)
            reasoning += int(reported.get("reasoning_output_tokens", 0) or 0)
        elif event_type == "item.completed":
            item = event.get("item") or {}
            if item.get("type") in CODEX_TOOL_ITEM_TYPES:
                tool_calls += 1

    usage["tokens_reasoning"] = reasoning
    usage["turns"] = turns or None
    usage["tool_calls"] = tool_calls
    # Codex reports no per-model split and no CLI-side cost estimate; model_usage carries
    # the peak per-turn context instead (see docstring).
    usage["model_usage"] = {"context_peak_tokens": context_peak}
    return usage


def extract_usage(agent: str, result: AgentResult) -> Usage:
    """Normalize one agent's raw output into the shared token-class dict."""
    extractors = {"claude": _claude_usage, "codex": _codex_usage}
    if agent not in extractors:
        raise RunnerError(f"no accounting rules for agent '{agent}'")
    return extractors[agent](result)


def load_prices(path: Path) -> dict[str, Any]:
    """Load the price table (USD per 1M tokens) from prices.toml."""
    if not path.is_file():
        raise RunnerError(f"price table not found: {path}")
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _claude_cost(usage: Usage, table: dict[str, Any], model: str | None) -> float | None:
    model_usage = usage.get("model_usage") or {}
    if not model_usage and model:
        model_usage = {
            model: {
                "inputTokens": usage["tokens_fresh"],
                "outputTokens": usage["tokens_out"],
                "cacheReadInputTokens": usage["tokens_cache_read"],
                "cacheCreationInputTokens": usage["tokens_cache_write"],
            }
        }

    if not model_usage:
        return None

    total = 0.0
    for model_id, counts in model_usage.items():
        prices = table.get(model_id)
        if not prices:
            # One unpriced model (a new helper model, a renamed id) would silently shrink
            # the run's cost, so the whole run goes null instead of partially priced.
            return None
        creation = int(counts.get("cacheCreationInputTokens", 0) or 0)
        # The 1h/5m split is only reported run-level, so it is attributed to the pinned
        # model. Ceiling: a helper model doing 1h caching would be priced at the 5m rate —
        # upgrade path is per-model splits if the CLI ever exposes them.
        one_hour = min(int(usage.get("tokens_cache_write_1h", 0) or 0), creation) if model_id == model else 0
        five_minute = creation - one_hour
        total += (
            int(counts.get("inputTokens", 0) or 0) * float(prices.get("input", 0.0))
            + five_minute * float(prices.get("cache_write_5m", 0.0))
            + one_hour * float(prices.get("cache_write_1h", 0.0))
            + int(counts.get("cacheReadInputTokens", 0) or 0)
            * float(prices.get("cache_read", 0.0))
            + int(counts.get("outputTokens", 0) or 0) * float(prices.get("output", 0.0))
        ) / _PER_MILLION
    return round(total, 6)


def _codex_cost(usage: Usage, table: dict[str, Any], model: str | None) -> float | None:
    prices = table.get(model or "")
    if not prices:
        return None

    threshold = int(prices.get("long_context_threshold_tokens", 0) or 0)
    fresh = usage["tokens_fresh"]
    # Long-context tier is a per-REQUEST rule upstream. Codex reports usage per turn, so
    # the tier is applied to the largest single-turn context (input_tokens incl. cached),
    # recorded as model_usage.context_peak_tokens. Ceiling: a run whose peak turn crosses
    # the threshold has its cheaper turns priced at the long rate too — upgrade path is
    # per-turn pricing. Pre-fix rows lack the key and fall back to the run total.
    context_peak = int(
        (usage.get("model_usage") or {}).get(
            "context_peak_tokens", fresh + usage["tokens_cache_read"]
        )
        or 0
    )
    long_context = bool(threshold) and context_peak > threshold
    input_rate = float(prices.get("input_long" if long_context else "input", 0.0))
    output_rate = float(prices.get("output_long" if long_context else "output", 0.0))
    # reasoning_output_tokens are a subset of output_tokens in OpenAI's accounting, so
    # they are recorded but not priced again.
    total = (
        fresh * input_rate
        + usage["tokens_cache_read"] * float(prices.get("cached_input", 0.0))
        + usage["tokens_cache_write"]
        * float(prices.get("cache_write", prices.get("input", 0.0)))
        + usage["tokens_out"] * output_rate
    ) / _PER_MILLION
    return round(total, 6)


def simulated_cost(
    agent: str, usage: Usage, prices: dict[str, Any], model: str | None
) -> float | None:
    """Price the token classes at list prices; None when the model has no price entry."""
    table = prices.get(agent) or {}
    if agent == "claude":
        return _claude_cost(usage, table, model)
    if agent == "codex":
        return _codex_cost(usage, table, model)
    raise RunnerError(f"no pricing rules for agent '{agent}'")
