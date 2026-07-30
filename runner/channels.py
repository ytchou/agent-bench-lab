"""Tool-output channel volume: how many bytes of tool output the agent read, per tool.

Mechanism metric — a tool that only compresses one channel (rtk compresses Bash output)
can only pay off in proportion to that channel's share of the run's tool output.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from runner.accounting import CODEX_TOOL_ITEM_TYPES
from runner.agents import AgentResult
from runner.spec import RunnerError

ChannelBytes = dict[str, int]
"""tool/channel name -> bytes of output text, plus the `_total` key."""

TOTAL_KEY = "_total"
UNKNOWN_TOOL = "_unknown"
CODEX_COMMAND_CHANNEL = "bash"
_CODEX_COMMAND_TYPE = "command_execution"
# codex has renamed this field across releases; first present wins.
_CODEX_OUTPUT_FIELDS = ("aggregated_output", "output", "stdout")


def _byte_len(value: str) -> int:
    return len(value.encode("utf-8"))


def _block_bytes(content: Any) -> int:
    """Byte length of one tool_result's `content` (a string, or a list of typed blocks)."""
    if isinstance(content, str):
        return _byte_len(content)
    if isinstance(content, list):
        # Shortcut: only text blocks are measured. Ceiling: an image/document block reads
        # as 0 bytes even though it costs tokens — upgrade path is to add its base64
        # payload length once a family actually produces image tool results.
        return sum(
            _byte_len(block.get("text") or "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return 0


def _add(channels: ChannelBytes, name: str, size: int) -> None:
    channels[name] = channels.get(name, 0) + size
    channels[TOTAL_KEY] = channels.get(TOTAL_KEY, 0) + size


def claude_channels(session_log: Path) -> ChannelBytes | None:
    """Sum tool_result bytes per tool name from a Claude session log, in one pass.

    tool_use blocks always precede the matching tool_result, so the id -> tool_name map
    can be built during the same scan instead of a second read of a multi-MB log.
    """
    if not session_log.is_file():
        return None

    tool_names: dict[str, str] = {}
    channels: ChannelBytes = {TOTAL_KEY: 0}
    with session_log.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            entry_type = entry.get("type")
            if entry_type not in ("assistant", "user"):
                continue
            content = (entry.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if entry_type == "assistant" and block.get("type") == "tool_use":
                    block_id = block.get("id")
                    if block_id:
                        tool_names[str(block_id)] = str(block.get("name") or UNKNOWN_TOOL)
                elif entry_type == "user" and block.get("type") == "tool_result":
                    name = tool_names.get(str(block.get("tool_use_id")), UNKNOWN_TOOL)
                    _add(channels, name, _block_bytes(block.get("content")))
    return channels


def _codex_item_output(item: dict[str, Any]) -> int:
    for field in _CODEX_OUTPUT_FIELDS:
        value = item.get(field)
        if isinstance(value, str):
            return _byte_len(value)
    return 0


def codex_channels(events: list[dict[str, Any]]) -> ChannelBytes | None:
    """Sum tool-output bytes per item type from codex's parsed `item.completed` events."""
    if not events:
        return None
    channels: ChannelBytes = {TOTAL_KEY: 0}
    for event in events:
        if event.get("type") != "item.completed":
            continue
        item = event.get("item") or {}
        item_type = item.get("type")
        if item_type not in CODEX_TOOL_ITEM_TYPES:
            continue
        name = (
            CODEX_COMMAND_CHANNEL if item_type == _CODEX_COMMAND_TYPE else str(item_type)
        )
        _add(channels, name, _codex_item_output(item))
    return channels


def tool_output_bytes(agent: str, result: AgentResult) -> ChannelBytes | None:
    """Per-channel tool-output bytes for one run; None when the logs are unavailable."""
    if agent == "claude":
        session_log = result.get("session_log")
        if not session_log:
            return None
        return claude_channels(Path(session_log))
    if agent == "codex":
        events = result.get("parsed") or []
        return codex_channels(events if isinstance(events, list) else [])
    raise RunnerError(f"no tool-output channel rules for agent '{agent}'")
