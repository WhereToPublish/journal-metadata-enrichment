from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
from typing import Any


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


class OpenClawRunner:
    def __init__(self, log_dir: Path, local: bool = False) -> None:
        self.log_dir = log_dir
        self.local = local
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _run_once(self, session_id: str, prompt: str, timeout_seconds: int = 3600) -> AgentRunResult:
        prompt_path = self.log_dir / f"{session_id}.prompt.txt"
        stdout_path = self.log_dir / f"{session_id}.stdout.json"
        stderr_path = self.log_dir / f"{session_id}.stderr.log"
        prompt_path.write_text(prompt, encoding="utf-8")

        command = [
            "openclaw",
            "agent",
            "--session-id",
            session_id,
            "--message",
            prompt,
            "--thinking",
            "off",
            "--json",
        ]
        if self.local:
            command.insert(2, "--local")

        try:
            process = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            timeout_note = f"openclaw agent timed out after {timeout_seconds}s"
            stderr_path.write_text(timeout_note, encoding="utf-8")
            stdout_path.write_text("", encoding="utf-8")
            synthetic = {"journal": "", "suggestions": [], "status": "unresolved", "notes": timeout_note}
            return AgentRunResult(
                session_id=session_id,
                stdout="",
                stderr=timeout_note,
                response_json=None,
                payload_text="",
                parsed_payload=synthetic,
            )

        stdout_path.write_text(process.stdout, encoding="utf-8")
        stderr_path.write_text(process.stderr, encoding="utf-8")

        response_json: dict[str, Any] | None = None
        if process.stdout.strip():
            try:
                response_json = json.loads(process.stdout)
            except json.JSONDecodeError:
                response_json = None

        payloads = []
        if response_json:
            payloads = response_json.get("result", response_json).get("payloads", [])
        payload_text = "\n".join(payload.get("text", "") for payload in payloads if payload.get("text"))
        parsed_payload = _extract_json_block(payload_text)

        return AgentRunResult(
            session_id=session_id,
            stdout=process.stdout,
            stderr=process.stderr,
            response_json=response_json,
            payload_text=payload_text,
            parsed_payload=parsed_payload,
        )

    def run(self, session_id: str, prompt: str, timeout_seconds: int = 3600, max_attempts: int = 2) -> AgentRunResult:
        # Build a compact retry prompt: strip the long instruction block from the original
        # prompt and keep only the Evidence JSON + a minimal schema instruction.
        # A full-size retry is strictly worse when the first attempt failed due to context overflow.
        evidence_marker = "\nEvidence:\n"
        evidence_start = prompt.rfind(evidence_marker)
        evidence_block = prompt[evidence_start:] if evidence_start != -1 else prompt
        retry_prompt = (
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

        result: AgentRunResult | None = None
        for attempt in range(1, max_attempts + 1):
            attempt_session_id = session_id if attempt == 1 else f"{session_id}-retry{attempt - 1}"
            attempt_prompt = prompt if attempt == 1 else retry_prompt
            result = self._run_once(attempt_session_id, attempt_prompt, timeout_seconds=timeout_seconds)
            if result.parsed_payload is not None:
                return result
        assert result is not None
        return result
