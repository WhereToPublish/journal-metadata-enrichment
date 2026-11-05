"""omp_runtime.py — OMP (oh-my-pi) agent wrapper.

Each journal is researched by one non-interactive OMP run on the host:

    omp -p <prompt> --mode json --no-session --auto-approve --thinking off \
        --append-system-prompt @agent/prompt/system.md --max-time <seconds>

OMP is already installed on the host and plugged to an LLM (see
~/.omp/agent/config.yml); no Docker container and no local Ollama model are
involved anymore.  The JSONL event stream printed on stdout is parsed to
recover the final assistant text, from which the suggestion JSON object is
extracted.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import signal
import subprocess
from typing import Any

from enrichment_common import PROJECT_ROOT, SYSTEM_PROMPT_PATH

OMP_COMMAND = os.environ.get("JOURNALMIND_OMP_COMMAND", "omp")

DEFAULT_AGENT_TIMEOUT_SECONDS = 900
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_PROCESS_STOP_GRACE_SECONDS = 5.0
# Extra seconds granted on top of --max-time before we kill the process.
_KILL_GRACE_SECONDS = 30.0


def _strip_json_line_comments(text: str) -> str:
    """Remove // line comments from JSON-like text, preserving // inside strings."""
    result: list[str] = []
    in_string = False
    escape = False
    i = 0
    while i < len(text):
        c = text[i]
        if escape:
            result.append(c)
            escape = False
        elif c == "\\" and in_string:
            result.append(c)
            escape = True
        elif c == '"':
            in_string = not in_string
            result.append(c)
        elif not in_string and c == "/" and i + 1 < len(text) and text[i + 1] == "/":
            # Skip to end of line (but keep the newline itself)
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        else:
            result.append(c)
        i += 1
    return "".join(result)


def _extract_json_block(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None

    # Strip a leading fenced code block (```json ... ```)
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else stripped
        stripped = stripped.rsplit("```", 1)[0].strip()

    # Remove // line comments that LLMs sometimes add inside JSON objects
    stripped = _strip_json_line_comments(stripped)

    # Scan every `{` position and return the first valid JSON dict that
    # looks like the expected agent-response schema.  Using raw_decode avoids
    # the "first-{ to last-}" heuristic that breaks when the text contains
    # multiple JSON objects (e.g. inline tool results followed by the final
    # answer).
    decoder = json.JSONDecoder()
    best: dict[str, Any] | None = None
    idx = 0
    while True:
        start = stripped.find("{", idx)
        if start == -1:
            break
        try:
            obj, _ = decoder.raw_decode(stripped, start)
            if isinstance(obj, dict):
                if "suggestions" in obj and "status" in obj:
                    return obj
                if best is None:
                    best = obj
        except json.JSONDecodeError:
            pass
        idx = start + 1
    return best


@dataclass
class AgentRunResult:
    session_id: str
    stdout: str
    stderr: str
    payload_text: str
    parsed_payload: dict[str, Any] | None


@dataclass(frozen=True)
class RunArtifacts:
    prompt_path: Path
    stdout_path: Path
    stderr_path: Path


def _build_retry_prompt(prompt: str, previous_response: str = "") -> str:
    evidence_marker = "\nEvidence:\n"
    evidence_start = prompt.rfind(evidence_marker)
    evidence_block = prompt[evidence_start:] if evidence_start != -1 else prompt

    previous_section = ""
    if previous_response.strip():
        truncated = previous_response.strip()[:3000]
        previous_section = (
            "\nYour previous analysis was:\n"
            "---\n"
            + truncated
            + "\n---\n\n"
            "Using the findings above, "
        )
    else:
        previous_section = "Based on the evidence below, "

    return (
        "You must return exactly one JSON object and nothing else.\n"
        "Do not fetch any additional URLs. Do not explain your reasoning outside the JSON.\n"
        "If you could not find reliable evidence for a field, omit that field from suggestions.\n"
        "If no field has reliable evidence, return: "
        '{"journal": "<name>", "suggestions": [], "status": "unresolved", "notes": "<brief reason>"}\n\n'
        "JSON schema (return this exact shape):\n"
        "{\n"
        '  "journal": string,\n'
        '  "suggestions": [{"field": string, "current_value": string, "suggested_value": string, '
        '"confidence": number, "source_urls": string[], "reasoning": string, '
        '"suggestion_type": string, "priority": string}],\n'
        '  "status": "ok" | "unresolved",\n'
        '  "notes": string\n'
        "}\n"
        + previous_section
        + "return a JSON object with your findings.\n"
        + evidence_block
    )


def _build_omp_command(
    prompt: str,
    timeout_seconds: int,
    model: str | None,
) -> list[str]:
    command = [
        OMP_COMMAND,
        "-p", prompt,
        "--mode", "json",
        "--no-session",
        "--auto-approve",
        "--thinking", "off",
        "--append-system-prompt", f"@{SYSTEM_PROMPT_PATH}",
        "--max-time", str(timeout_seconds),
    ]
    if model:
        command += ["--model", model]
    return command


def _build_run_artifacts(log_dir: Path, session_id: str) -> RunArtifacts:
    return RunArtifacts(
        prompt_path=log_dir / f"{session_id}.prompt.txt",
        stdout_path=log_dir / f"{session_id}.stdout.json",
        stderr_path=log_dir / f"{session_id}.stderr.log",
    )


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
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=grace_seconds)
            except subprocess.TimeoutExpired:
                pass

    stdout = ""
    stderr = ""
    try:
        stdout, stderr = process.communicate(timeout=0.1)
    except subprocess.TimeoutExpired:
        pass
    return stdout, stderr


def _build_timeout_result(session_id: str, stdout: str, stderr: str,
                          timeout_seconds: int) -> AgentRunResult:
    timeout_note = f"omp agent timed out after {timeout_seconds}s"
    timed_out_stderr = f"{stderr}\n{timeout_note}".strip()
    synthetic: dict[str, Any] = {
        "journal": "",
        "suggestions": [],
        "status": "unresolved",
        "notes": timeout_note,
    }
    return AgentRunResult(
        session_id=session_id,
        stdout=stdout,
        stderr=timed_out_stderr,
        payload_text="",
        parsed_payload=synthetic,
    )


class OMPRunner:
    """Runs one OMP (oh-my-pi) non-interactive session per journal."""

    def __init__(self, log_dir: Path,
                 timeout_seconds: int = DEFAULT_AGENT_TIMEOUT_SECONDS,
                 max_attempts: int = DEFAULT_MAX_ATTEMPTS,
                 model: str | None = None) -> None:
        self.log_dir = log_dir
        # JOURNALMIND_MODEL overrides the model configured in OMP itself.
        self.model = model or os.environ.get("JOURNALMIND_MODEL") or None
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _run_once(self, session_id: str, prompt: str,
                  timeout_seconds: int | None = None) -> AgentRunResult:
        effective_timeout_seconds = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        artifacts = _build_run_artifacts(self.log_dir, session_id)
        artifacts.prompt_path.write_text(prompt, encoding="utf-8")

        process = subprocess.Popen(
            _build_omp_command(prompt, effective_timeout_seconds, self.model),
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )

        try:
            stdout, stderr = process.communicate(
                timeout=effective_timeout_seconds + _KILL_GRACE_SECONDS
            )
            timed_out = False
        except subprocess.TimeoutExpired:
            stdout, stderr = _stop_process(process)
            timed_out = True

        if timed_out:
            result = _build_timeout_result(session_id, stdout, stderr, effective_timeout_seconds)
        else:
            result = AgentRunResult(
                session_id=session_id,
                stdout=stdout,
                stderr=stderr,
                payload_text="",
                parsed_payload=None,
            )

        artifacts.stdout_path.write_text(result.stdout, encoding="utf-8")
        artifacts.stderr_path.write_text(result.stderr, encoding="utf-8")

        if not timed_out:
            texts = _assistant_texts_from_jsonl(result.stdout)
            if texts:
                result.payload_text = texts[-1]
                result.parsed_payload = _extract_json_block(result.payload_text)

        return result

    def run(self, session_id: str, prompt: str,
            timeout_seconds: int | None = None,
            max_attempts: int | None = None) -> AgentRunResult:
        effective_timeout_seconds = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        effective_max_attempts = self.max_attempts if max_attempts is None else max_attempts

        result: AgentRunResult | None = None
        for attempt in range(1, effective_max_attempts + 1):
            attempt_session_id = session_id if attempt == 1 else f"{session_id}-retry{attempt - 1}"
            if attempt == 1:
                attempt_prompt = prompt
            else:
                attempt_prompt = _build_retry_prompt(
                    prompt,
                    previous_response=result.payload_text if result is not None else "",
                )
            result = self._run_once(attempt_session_id, attempt_prompt, timeout_seconds=effective_timeout_seconds)
            if result.parsed_payload is not None:
                return result
        assert result is not None
        return result
