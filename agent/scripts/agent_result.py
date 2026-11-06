"""agent_result.py — the shared Pydantic result contract for journal research.

Both agent runtimes produce one result per journal:

- the Mistral runtime returns the answer as an Agents SDK structured output
  validated against `AgentResult` (`output_type=AgentResult`);
- the OMP runtime has each subagent write a result JSON file, and Python now
  validates that file against the *same* `AgentResult` model when collecting it
  (this replaces the previous loose dict handling).

The models also carry the lenient coercions the old dict-based code performed
per item (strings cast from scalars, confidence parsed from strings, pipe-joined
source URL lists), so hand-written OMP results that are merely untidy — not
broken — still validate. `parse_result_file` skips only the suggestions that do
not validate, keeping the per-suggestion resilience of the old code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator


def _coerce_str(value: Any) -> str:
    return "" if value is None else str(value)


def _coerce_confidence(value: Any) -> float:
    """Parse confidence numbers; anything else fails this suggestion."""
    return float(value)


def _coerce_source_urls(value: Any) -> list[str]:
    """Accept a list of URLs or a pipe-joined single string (old OMP habit)."""
    if isinstance(value, list):
        return [str(url).strip() for url in value if str(url).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split("|") if part.strip()]
    return []


class Suggestion(BaseModel):
    """One metadata suggestion for one journal field."""

    field: str
    suggested_value: str
    current_value: str = ""
    confidence: float = Field(..., ge=0.0, le=1.0)
    source_urls: list[str] = []
    reasoning: str
    suggestion_type: str = "fill"
    priority: str = "high"

    @field_validator(
        "field", "suggested_value", "current_value", "reasoning",
        "suggestion_type", "priority", mode="before",
    )
    @classmethod
    def _cast_str(cls, value: Any) -> str:
        return _coerce_str(value)

    @field_validator("source_urls", mode="before")
    @classmethod
    def _cast_source_urls(cls, value: Any) -> list[str]:
        return _coerce_source_urls(value)

    _cast_confidence = field_validator("confidence", mode="before")(_coerce_confidence)


class AgentResult(BaseModel):
    """Result for one journal — same schema as the per-journal result files."""

    journal: str
    suggestions: list[Suggestion] = []
    status: str = "ok"
    notes: str = ""

    @field_validator("journal", "status", "notes", mode="before")
    @classmethod
    def _cast_str(cls, value: Any) -> str:
        return _coerce_str(value)


def unresolved_result(journal: str, notes: str) -> AgentResult:
    """The normal fallback result when evidence is weak, blocked, or the run failed."""
    return AgentResult(journal=journal, suggestions=[], status="unresolved", notes=notes)


def result_from_payload(payload: Any) -> AgentResult | None:
    """Validate a raw decoded result payload into an AgentResult.

    Suggestions that do not validate are skipped (mirroring the old lenient
    per-suggestion handling); a payload without a journal name is rejected.
    A missing or empty status defaults to ``unresolved``, as before.
    """
    if not isinstance(payload, dict):
        return None
    journal = str(payload.get("journal", "") or "").strip()
    if not journal:
        return None

    suggestions: list[Suggestion] = []
    raw_suggestions = payload.get("suggestions")
    if isinstance(raw_suggestions, list):
        for item in raw_suggestions:
            try:
                suggestions.append(Suggestion.model_validate(item))
            except Exception:
                continue  # one broken suggestion never poisons the whole result

    status = str(payload.get("status", "") or "").strip().lower() or "unresolved"
    notes = str(payload.get("notes", "") or "")
    return AgentResult(
        journal=journal,
        suggestions=suggestions,
        status=status,
        notes=notes,
    )


def parse_result_file(result_file: Path) -> AgentResult | None:
    """Load and validate one per-journal result JSON file for either runtime."""
    try:
        payload = json.loads(Path(result_file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return result_from_payload(payload)


def write_result_file(result_file: Path, result: AgentResult) -> None:
    """Persist a validated result in the mission results-file contract."""
    path = Path(result_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
