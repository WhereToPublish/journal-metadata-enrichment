from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from typing import Any, cast


DEFAULT_OPENCLAW_TIMEOUT_SECONDS = 900
DEFAULT_OPENCLAW_MAX_ATTEMPTS = 2
DEFAULT_POLL_INTERVAL_SECONDS = 1.0
DEFAULT_PROCESS_STOP_GRACE_SECONDS = 2.0


def _extract_json_block(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else stripped
        stripped = stripped.rsplit("```", 1)[0].strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    candidate = stripped[start:end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


@dataclass
class AgentRunResult:
    session_id: str
    stdout: str
    stderr: str
    response_json: dict[str, Any] | None
    payload_text: str
    parsed_payload: dict[str, Any] | None


@dataclass(frozen=True)
class RunArtifacts:
    prompt_path: Path
    stdout_path: Path
    stderr_path: Path
    session_path: Path


def _build_retry_prompt(prompt: str) -> str:
    evidence_marker = "\nEvidence:\n"
    evidence_start = prompt.rfind(evidence_marker)
    evidence_block = prompt[evidence_start:] if evidence_start != -1 else prompt
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
        + evidence_block
    )


def _resolve_openclaw_state_dir() -> Path:
    configured = os.environ.get("OPENCLAW_STATE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".openclaw"


def _build_run_artifacts(log_dir: Path, state_dir: Path, session_id: str) -> RunArtifacts:
    return RunArtifacts(
        prompt_path=log_dir / f"{session_id}.prompt.txt",
        stdout_path=log_dir / f"{session_id}.stdout.json",
        stderr_path=log_dir / f"{session_id}.stderr.log",
        session_path=state_dir / "agents" / "main" / "sessions" / f"{session_id}.jsonl",
    )


def _extract_session_payload(session_file: Path) -> tuple[str, dict[str, Any] | None] | None:
    if not session_file.exists():
        return None

    payload_text = ""
    try:
        lines = session_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        if event.get("type") != "message":
            continue
        message = cast(dict[str, Any] | None, event.get("message"))
        if not isinstance(message, dict):
            continue
        message_data = message
        if cast(str | None, message_data.get("role")) != "assistant":
            continue
        if cast(str | None, message_data.get("stopReason")) != "stop":
            continue

        content = cast(list[Any] | None, message_data.get("content"))
        if not isinstance(content, list):
            continue

        text_blocks: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_data = cast(dict[str, Any], block)
            text = cast(str | None, block_data.get("text"))
            if text:
                text_blocks.append(text)

        if text_blocks:
            payload_text = "\n".join(block for block in text_blocks if block)

    if not payload_text:
        return None
    return payload_text, _extract_json_block(payload_text)


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


def _parse_response_json(stdout: str) -> dict[str, Any] | None:
    if not stdout.strip():
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return None


def _extract_cli_payload(response_json: dict[str, Any] | None) -> tuple[str, dict[str, Any] | None]:
    if not response_json:
        return "", None

    result = response_json.get("result", response_json)
    if not isinstance(result, dict):
        return "", None
    result_data = cast(dict[str, Any], result)

    raw_payloads = cast(list[Any] | None, result_data.get("payloads"))
    if not isinstance(raw_payloads, list):
        return "", None

    payload_text_parts: list[str] = []
    for raw_payload in raw_payloads:
        if not isinstance(raw_payload, dict):
            continue
        payload_data = cast(dict[str, Any], raw_payload)
        text = cast(str | None, payload_data.get("text"))
        if text:
            payload_text_parts.append(text)

    payload_text = "\n".join(payload_text_parts)
    return payload_text, _extract_json_block(payload_text)


def _build_timeout_result(session_id: str, stdout: str, stderr: str,
                          timeout_seconds: int) -> AgentRunResult:
    timeout_note = f"openclaw agent timed out after {timeout_seconds}s"
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
        response_json=None,
        payload_text="",
        parsed_payload=synthetic,
    )


def _wait_for_process(process: subprocess.Popen[str], session_path: Path,
                      timeout_seconds: int) -> tuple[str, str, str, dict[str, Any] | None, bool]:
    deadline = time.monotonic() + timeout_seconds
    session_payload_text = ""
    session_parsed_payload: dict[str, Any] | None = None

    while True:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            return stdout, stderr, session_payload_text, session_parsed_payload, False

        session_payload = _extract_session_payload(session_path)
        if session_payload is not None:
            session_payload_text, session_parsed_payload = session_payload
            if session_parsed_payload is not None:
                stdout, stderr = _stop_process(process)
                return stdout, stderr, session_payload_text, session_parsed_payload, False

        if time.monotonic() >= deadline:
            stdout, stderr = _stop_process(process)
            if session_parsed_payload is None:
                session_payload = _extract_session_payload(session_path)
                if session_payload is not None:
                    session_payload_text, session_parsed_payload = session_payload
            return stdout, stderr, session_payload_text, session_parsed_payload, session_parsed_payload is None

        time.sleep(DEFAULT_POLL_INTERVAL_SECONDS)


class OpenClawRunner:
    def __init__(self, log_dir: Path,
                 timeout_seconds: int = DEFAULT_OPENCLAW_TIMEOUT_SECONDS,
                 max_attempts: int = DEFAULT_OPENCLAW_MAX_ATTEMPTS) -> None:
        self.log_dir = log_dir
        self.state_dir = _resolve_openclaw_state_dir()
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.log_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _build_command(session_id: str, prompt: str, timeout_seconds: int) -> list[str]:
        return [
            "openclaw",
            "agent",
            "--local",
            "--session-id",
            session_id,
            "--message",
            prompt,
            "--thinking",
            "off",
            "--json",
            "--timeout",
            str(timeout_seconds),
        ]

    def _run_once(self, session_id: str, prompt: str,
                  timeout_seconds: int | None = None) -> AgentRunResult:
        effective_timeout_seconds = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        artifacts = _build_run_artifacts(self.log_dir, self.state_dir, session_id)
        artifacts.prompt_path.write_text(prompt, encoding="utf-8")

        process = subprocess.Popen(
            self._build_command(session_id, prompt, effective_timeout_seconds),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )

        stdout, stderr, session_payload_text, session_parsed_payload, timed_out = _wait_for_process(
            process,
            artifacts.session_path,
            effective_timeout_seconds,
        )

        if timed_out:
            timeout_result = _build_timeout_result(session_id, stdout, stderr, effective_timeout_seconds)
            artifacts.stdout_path.write_text(timeout_result.stdout, encoding="utf-8")
            artifacts.stderr_path.write_text(timeout_result.stderr, encoding="utf-8")
            return timeout_result

        artifacts.stdout_path.write_text(stdout, encoding="utf-8")
        artifacts.stderr_path.write_text(stderr, encoding="utf-8")

        response_json = _parse_response_json(stdout)
        payload_text, parsed_payload = _extract_cli_payload(response_json)
        if parsed_payload is None and session_parsed_payload is not None:
            payload_text = session_payload_text
            parsed_payload = session_parsed_payload

        return AgentRunResult(
            session_id=session_id,
            stdout=stdout,
            stderr=stderr,
            response_json=response_json,
            payload_text=payload_text,
            parsed_payload=parsed_payload,
        )

    def run(self, session_id: str, prompt: str,
            timeout_seconds: int | None = None,
            max_attempts: int | None = None) -> AgentRunResult:
        effective_timeout_seconds = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        effective_max_attempts = self.max_attempts if max_attempts is None else max_attempts
        retry_prompt = _build_retry_prompt(prompt)

        result: AgentRunResult | None = None
        for attempt in range(1, effective_max_attempts + 1):
            attempt_session_id = session_id if attempt == 1 else f"{session_id}-retry{attempt - 1}"
            attempt_prompt = prompt if attempt == 1 else retry_prompt
            result = self._run_once(attempt_session_id, attempt_prompt, timeout_seconds=effective_timeout_seconds)
            if result.parsed_payload is not None:
                return result
        assert result is not None
        return result
