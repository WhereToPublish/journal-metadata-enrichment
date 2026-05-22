from __future__ import annotations

import csv
import json
import sys
from typing import Any
from enrichment_common import *

if str(WTP_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(WTP_SCRIPTS_DIR))

from libraries import norm_name


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def read_csv_rows(path: Path) -> tuple[bool, list[list[str]]]:
    if not path.exists():
        return False, []
    with open(path, encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        return False, []
    has_header = rows[0] == CSV_HEADERS
    return has_header, rows[1:] if has_header else rows


def normalize_existing_csv(path: Path) -> None:
    if not path.exists():
        return
    has_header, rows = read_csv_rows(path)
    if has_header:
        return
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_HEADERS)
        writer.writerows(row for row in rows if row)


def init_suggestions_csv(path: Path) -> None:
    ensure_parent(path)
    if path.exists():
        normalize_existing_csv(path)
        return
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_HEADERS)


def normalize_suggestion_row(row: dict[str, Any]) -> dict[str, str]:
    return {header: str(row.get(header, "") or "") for header in CSV_HEADERS}


def load_suggestions(path: Path) -> list[dict[str, str]]:
    init_suggestions_csv(path)
    with open(path, encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [normalize_suggestion_row(row) for row in reader]


def load_existing_keys(path: Path) -> set[tuple[str, str, str]]:
    return {
        (
            row["journal"],
            row["field"],
            row["suggested_value"],
        )
        for row in load_suggestions(path)
    }


def parse_confidence(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def dedupe_suggestions(
    rows: list[dict[str, Any]], key_fields: tuple[str, ...] = ("journal", "field")
) -> tuple[list[dict[str, str]], int]:
    selected_rows: dict[tuple[str, ...], dict[str, Any]] = {}

    for index, raw_row in enumerate(rows):
        row = normalize_suggestion_row(raw_row)
        key = tuple(row.get(field, "") for field in key_fields)
        if not any(key):
            continue

        confidence = parse_confidence(row.get("confidence", "0"))
        existing = selected_rows.get(key)
        if existing is None:
            selected_rows[key] = {
                "first_index": index,
                "confidence": confidence,
                "row": row,
            }
            continue

        if confidence > existing["confidence"]:
            existing["confidence"] = confidence
            existing["row"] = row

    deduped_rows = [
        item["row"]
        for item in sorted(selected_rows.values(), key=lambda item: item["first_index"])
    ]
    return deduped_rows, len(rows) - len(deduped_rows)


def write_suggestions(path: Path, suggestions: list[dict[str, Any]]) -> None:
    ensure_parent(path)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_HEADERS)
        writer.writeheader()
        writer.writerows(normalize_suggestion_row(row) for row in suggestions)


def append_suggestions(path: Path, suggestions: list[dict[str, str]]) -> None:
    if not suggestions:
        return
    deduped_rows, _ = dedupe_suggestions(load_suggestions(path) + suggestions)
    write_suggestions(path, deduped_rows)


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


def coerce_source_urls(raw_value: Any) -> list[str]:
    if isinstance(raw_value, list):
        return [str(url).strip() for url in raw_value if str(url).strip()]
    if isinstance(raw_value, str) and raw_value.strip():
        return [part.strip() for part in raw_value.split("|") if part.strip()]
    return []


def normalize_name(value: str) -> str:
    return norm_name(value)


def names_equivalent_or_contained(left: str, right: str) -> bool:
    normalized_left = normalize_name(left)
    normalized_right = normalize_name(right)
    if not normalized_left or not normalized_right:
        return False
    return (
            normalized_left == normalized_right
            or normalized_left in normalized_right
            or normalized_right in normalized_left
    )


def sanitize_agent_result(journal_name: str, journal_gap: dict[str, Any], agent_result: dict[str, Any] | None,
                          existing_keys: set[tuple[str, str, str]]) -> tuple[str, list[dict[str, str]], str]:
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

        source_urls = coerce_source_urls(item.get("source_urls", []))
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

        if field == "Alternative journal name":
            if row["suggestion_type"] != "alt_name":
                continue
            # Require higher confidence for alt_name — a wrong name actively breaks the pipeline join.
            if float(row["confidence"]) < 0.70:
                continue
            if normalize_name(suggested_value) == normalize_name(journal_name):
                continue

        if field in ("e-ISSN", "p-ISSN", "ISSN-L"):
            # ISSN must be formatted as XXXX-XXXX (last char may be X as check digit)
            if not re.match(r"^\d{4}-[\dX][\dX][\dX][\dX]$", suggested_value):
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
            if current_publisher and names_equivalent_or_contained(suggested_value, current_publisher):
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
    if cleaned_rows:
        # If we wrote any valid suggestions, always mark the journal as resolved —
        # the model sometimes returns "unresolved" when only partially resolving gaps.
        final_status = "ok"
    elif final_status == "ok":
        # Model said ok but all suggestions were filtered out — treat as unresolved.
        final_status = "unresolved"
    return final_status, cleaned_rows, notes
