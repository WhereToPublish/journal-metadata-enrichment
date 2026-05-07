from __future__ import annotations

import csv
import json
from pathlib import Path
import re
from typing import Any

from enrichment_common import (
    ALLOWED_BUSINESS_MODELS,
    ALLOWED_FIELDS,
    ALLOWED_PRIORITIES,
    ALLOWED_SUGGESTION_TYPES,
    CSV_HEADERS,
)


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _read_csv_rows(path: Path) -> tuple[bool, list[list[str]]]:
    if not path.exists():
        return False, []
    with open(path, encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        return False, []
    has_header = rows[0] == CSV_HEADERS
    return has_header, rows[1:] if has_header else rows


def _normalize_existing_csv(path: Path) -> None:
    if not path.exists():
        return
    has_header, rows = _read_csv_rows(path)
    if has_header:
        return
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_HEADERS)
        writer.writerows(row for row in rows if row)


def init_suggestions_csv(path: Path) -> None:
    ensure_parent(path)
    if path.exists():
        _normalize_existing_csv(path)
        return
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_HEADERS)


def load_existing_keys(path: Path) -> set[tuple[str, str, str]]:
    init_suggestions_csv(path)
    with open(path, encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return {
            (
                row.get("journal", ""),
                row.get("field", ""),
                row.get("suggested_value", ""),
            )
            for row in reader
        }


def append_suggestions(path: Path, suggestions: list[dict[str, str]]) -> None:
    if not suggestions:
        return
    init_suggestions_csv(path)
    with open(path, "a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_HEADERS)
        writer.writerows(suggestions)


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"processed_journals": [], "summary": {"written": 0, "unresolved": 0, "errors": 0}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: dict[str, Any]) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def write_checkpoint(csv_path: Path, checkpoint_path: Path) -> None:
    ensure_parent(checkpoint_path)
    checkpoint_path.write_text(csv_path.read_text(encoding="utf-8"), encoding="utf-8")


def _coerce_source_urls(raw_value: Any) -> list[str]:
    if isinstance(raw_value, list):
        return [str(url).strip() for url in raw_value if str(url).strip()]
    if isinstance(raw_value, str) and raw_value.strip():
        return [part.strip() for part in raw_value.split("|") if part.strip()]
    return []


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.strip().lower())


def _names_equivalent_or_contained(left: str, right: str) -> bool:
    normalized_left = _normalize_name(left)
    normalized_right = _normalize_name(right)
    if not normalized_left or not normalized_right:
        return False
    return (
        normalized_left == normalized_right
        or normalized_left in normalized_right
        or normalized_right in normalized_left
    )


def sanitize_agent_result(
    journal_name: str,
    journal_gap: dict[str, Any],
    agent_result: dict[str, Any] | None,
    existing_keys: set[tuple[str, str, str]],
) -> tuple[str, list[dict[str, str]], str]:
    if not agent_result:
        return "error", [], "No JSON object could be parsed from the agent response."

    status = str(agent_result.get("status", "unresolved")).strip().lower()
    notes = str(agent_result.get("notes", "")).replace("\n", " ").strip()
    gap_lookup = {gap["field"]: gap for gap in journal_gap["gaps"]}
    allowed_fields = set(gap_lookup)
    current_publisher = str(journal_gap.get("current_publisher", "")).strip()
    current_business_model = str(journal_gap.get("current_business_model", "")).strip()
    current_institution = str(gap_lookup.get("Institution", {}).get("current_value", "")).strip()
    allowed_institution_types = {"Society", "Society/Association", "University", "Research Institute"}
    candidate_rows: list[dict[str, str]] = []
    local_keys = set(existing_keys)

    for item in agent_result.get("suggestions", []):
        field = str(item.get("field", "")).strip()
        suggested_value = str(item.get("suggested_value", "")).strip()
        if field not in ALLOWED_FIELDS or field not in allowed_fields or not suggested_value:
            continue

        try:
            confidence = float(item.get("confidence", 0))
        except (TypeError, ValueError):
            continue
        if not 0.55 <= confidence <= 1:
            continue

        suggestion_type = str(item.get("suggestion_type", "")).strip()
        priority = str(item.get("priority", "")).strip()
        if suggestion_type not in ALLOWED_SUGGESTION_TYPES or priority not in ALLOWED_PRIORITIES:
            continue

        if field == "Business model" and suggested_value not in ALLOWED_BUSINESS_MODELS:
            continue
        if field == "APC Euros":
            digits = "".join(ch for ch in suggested_value if ch.isdigit())
            if not digits:
                continue
            suggested_value = digits

        source_urls = _coerce_source_urls(item.get("source_urls", []))
        if not source_urls:
            continue

        reasoning = str(item.get("reasoning", "")).replace("\n", " ").strip()
        if not reasoning:
            continue

        current_value = str(item.get("current_value", "")).replace("\n", " ").strip()
        if not current_value:
            current_value = str(gap_lookup[field].get("current_value", "")).replace("\n", " ").strip()

        row = {
            "journal": journal_name,
            "field": field,
            "current_value": current_value,
            "suggested_value": suggested_value,
            "confidence": f"{confidence:.2f}",
            "source_urls": "|".join(source_urls),
            "reasoning": reasoning,
            "suggestion_type": suggestion_type,
            "priority": priority,
        }

        candidate_rows.append(row)

    filtered_rows: list[dict[str, str]] = []
    for row in candidate_rows:
        field = row["field"]
        suggested_value = row["suggested_value"]
        reasoning_lower = row["reasoning"].lower()

        if field == "Scimago Journal Title":
            if row["suggestion_type"] != "alt_name":
                continue
            if _normalize_name(suggested_value) == _normalize_name(journal_name):
                continue

        if field == "APC Euros" and suggested_value == "0":
            # Never accept APC=0 for subscription journals — it implies OA diamond in this schema
            if current_business_model == "Subscription":
                continue
            if not any(
                marker in reasoning_lower
                for marker in (
                    "no apc",
                    "no article processing charge",
                    "no article processing charges",
                    "zero apc",
                    "apc is 0",
                    "free to publish",
                    "no publication fee",
                    "no charge",
                    "does not charge",
                    "without apc",
                    "no article fee",
                    "not charged",
                    "waived",
                )
            ):
                continue

        if field == "Institution":
            if current_publisher and _names_equivalent_or_contained(suggested_value, current_publisher):
                continue

        filtered_rows.append(row)

    has_institution_value = bool(current_institution) or any(row["field"] == "Institution" for row in filtered_rows)

    cleaned_rows: list[dict[str, str]] = []
    for row in filtered_rows:
        if row["field"] == "Institution type":
            if row["suggested_value"] not in allowed_institution_types:
                continue
            if not has_institution_value:
                continue

        key = (row["journal"], row["field"], row["suggested_value"])
        if key in local_keys:
            continue
        local_keys.add(key)
        cleaned_rows.append(row)

    final_status = status if status in {"ok", "unresolved"} else "error"
    if final_status == "ok" and not cleaned_rows:
        final_status = "unresolved"
    return final_status, cleaned_rows, notes
