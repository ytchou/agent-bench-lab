"""Task prompt construction: one uniform preamble plus a family-specific body."""

from __future__ import annotations

from typing import Any

from runner.grading.base import require
from runner.spec import RunnerError

PREAMBLE = (
    "You are working non-interactively on a benchmark task.\n"
    "Rules:\n"
    "- Work only inside the current directory; do not touch anything outside it.\n"
    "- Do not ask questions and do not wait for confirmation; nobody will answer.\n"
    "- If the task asks for an answer file, write it before you finish.\n"
    "- When the task is complete, stop.\n"
)

_ANSWER_FILE_RULE = (
    "Write your final answer into a file named ANSWER.txt in the root of the current "
    "directory. ANSWER.txt must contain ONLY the final answer — no explanation, no "
    "reasoning, no formatting, no code fences."
)


def build_prompt(family: str, task: dict[str, Any]) -> str:
    """Build the full prompt sent to the agent for one task."""
    if family == "comprehension":
        question = require(task, "question", str(task.get("id", "<unknown>")))
        body = (
            "The current directory contains a checked-out source repository.\n"
            f"Answer this question about it:\n\n{question}\n\n"
            f"{_ANSWER_FILE_RULE}"
        )
    elif family == "dsbench":
        question = require(task, "question", str(task.get("id", "<unknown>")))
        body = (
            "The current directory contains a `data/` folder with the data files for "
            "this task.\n"
            f"Answer this data-analysis question:\n\n{question}\n\n"
            f"{_ANSWER_FILE_RULE} The answer is a single number."
        )
    elif family == "swebench":
        instance_id = str(task.get("instance_id", "<unknown>"))
        problem = require(task, "problem_statement", instance_id)
        body = (
            "The current directory is a source repository with an open issue:\n\n"
            f"{problem}\n\n"
            "Fix the issue by modifying the repository in place. Do not write "
            "ANSWER.txt. Do not commit; leave your changes in the working tree."
        )
    else:
        raise RunnerError(f"no prompt template for family '{family}'")

    return f"{PREAMBLE}\n{body}\n"
