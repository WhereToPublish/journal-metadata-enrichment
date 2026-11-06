"""omp_runtime.py — OMP (oh-my-pi) swarm run wrapper.

One non-interactive OMP run per batch:

    omp -p <prompt> --mode json --no-session --auto-approve --thinking off \
        --append-system-prompt @agent/prompt/system.md --max-time <seconds>

The OMP process plays the orchestrator: it spawns one parallel subagent per
journal (via its built-in `task` tool) and the subagents write their result
JSON files inside the mission directory.  Python never parses the model's
reply for suggestions — the result files on disk are the source of truth.
The JSONL event stream on stdout is kept only as a run artifact, from which
the final assistant message is extracted for the runner log.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess

from enrichment_common import PROJECT_ROOT, SYSTEM_PROMPT_PATH, SwarmRunResult

OMP_COMMAND = os.environ.get("JOURNALMIND_OMP_COMMAND", "omp")

DEFAULT_SWARM_TIMEOUT_SECONDS = 3600
DEFAULT_PROCESS_STOP_GRACE_SECONDS = 5.0
# Extra seconds granted on top of --max-time before we kill the process.
_KILL_GRACE_SECONDS = 30.0


def _assistant_texts_from_jsonl(stdout: str) -> list[str]:
    """Return the text of every completed assistant message in an OMP JSONL stream."""
    texts: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "message_end":
            continue
        message = event.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        blocks: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                blocks.append(str(block["text"]))
        if blocks:
            texts.append("\n".join(blocks))
    return texts


def _stop_process(process: subprocess.Popen[str],
                  grace_seconds: float = DEFAULT_PROCESS_STOP_GRACE_SECONDS) -> tuple[str, str]:
    try:
        return process.communicate(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=grace_seconds)
                break
            except subprocess.TimeoutExpired:
                continue
    try:
        return process.communicate(timeout=0.5)
    except (subprocess.TimeoutExpired, ValueError):
        return "", ""


class OMPSwarmRunner:
    """Runs one non-interactive OMP orchestrator session per batch."""

    def __init__(self, log_dir: Path,
                 timeout_seconds: int = DEFAULT_SWARM_TIMEOUT_SECONDS,
                 model: str | None = None) -> None:
        self.log_dir = log_dir
        # JOURNALMIND_MODEL overrides the model configured in OMP itself.
        self.model = model or os.environ.get("JOURNALMIND_MODEL") or None
        self.timeout_seconds = timeout_seconds
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def run(self, session_id: str, prompt: str) -> SwarmRunResult:
        artifacts = (
            self.log_dir / f"{session_id}.prompt.txt",
            self.log_dir / f"{session_id}.stdout.json",
            self.log_dir / f"{session_id}.stderr.log",
        )
        artifacts[0].write_text(prompt, encoding="utf-8")

        command = [
            OMP_COMMAND,
            "-p", prompt,
            "--mode", "json",
            "--no-session",
            "--auto-approve",
            "--thinking", "off",
            "--append-system-prompt", f"@{SYSTEM_PROMPT_PATH}",
            "--max-time", str(self.timeout_seconds),
        ]
        if self.model:
            command += ["--model", self.model]

        process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )

        timed_out = False
        try:
            stdout, stderr = process.communicate(
                timeout=self.timeout_seconds + _KILL_GRACE_SECONDS
            )
        except subprocess.TimeoutExpired:
            stdout, stderr = _stop_process(process)
            timed_out = True

        result = SwarmRunResult(
            session_id=session_id,
            stdout=stdout or "",
            stderr=(stderr or "") + (f"\nomp orchestrator timed out after {self.timeout_seconds}s" if timed_out else ""),
            final_message="",
            timed_out=timed_out,
        )
        texts = _assistant_texts_from_jsonl(result.stdout)
        result.final_message = texts[-1] if texts else ""

        artifacts[1].write_text(result.stdout, encoding="utf-8")
        artifacts[2].write_text(result.stderr, encoding="utf-8")
        return result
