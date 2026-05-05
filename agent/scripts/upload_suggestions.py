"""upload_suggestions.py — Upload AI_Suggestions.csv to the WhereToPublish Google Sheet.

Reads AI_Suggestions.csv and writes all rows to the 'AI_Suggestions' tab of the
spreadsheet. A leading 'Approve?' checkbox column is prepended for human review.
Any existing content in that tab is replaced on each run.

Usage:
    python3 agent/scripts/upload_suggestions.py \
        [--input agent/output/AI_Suggestions.csv] \
        [--credentials ~/.config/wheretopublish/google_service_account.json]
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import sheets_client
from enrichment_common import SUGGESTIONS_CSV_PATH

SUGGESTIONS_TAB = "AI_suggestions"

# Column order in the AI_Suggestions tab (Approve? is first; the rest match CSV headers)
OUTPUT_HEADERS = [
    "Approve?",
    "journal",
    "field",
    "current_value",
    "suggested_value",
    "confidence",
    "source_urls",
    "reasoning",
    "suggestion_type",
    "priority",
]

CSV_FIELD_ORDER = OUTPUT_HEADERS[1:]  # same list minus 'Approve?'


def upload(input_csv: Path, credentials_path: Path | None) -> None:
    if not input_csv.exists():
        print(f"ERROR: input file not found: {input_csv}", file=sys.stderr)
        sys.exit(1)

    with open(input_csv, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)

    if not rows:
        print("No suggestions to upload — file is empty.")
        return

    print(f"Uploading {len(rows)} suggestion(s) from {input_csv} ...")

    service = sheets_client.get_sheets_service(credentials_path=credentials_path, readonly=False)

    sheets_client.get_or_create_tab(service, sheets_client.SPREADSHEET_ID, SUGGESTIONS_TAB)
    sheets_client.clear_tab(service, sheets_client.SPREADSHEET_ID, SUGGESTIONS_TAB)

    # Build data: header row + one row per suggestion (Approve? starts as FALSE)
    data: list[list] = [OUTPUT_HEADERS]
    for row in rows:
        data.append(
            [False]  # Approve? checkbox — starts unchecked
            + [row.get(col, "") for col in CSV_FIELD_ORDER]
        )

    sheets_client.write_rows(service, sheets_client.SPREADSHEET_ID, SUGGESTIONS_TAB, data)

    print(f"Done. {len(rows)} row(s) written to the '{SUGGESTIONS_TAB}' tab.")
    print(f"https://docs.google.com/spreadsheets/d/{sheets_client.SPREADSHEET_ID}/edit")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upload AI_Suggestions.csv to the WhereToPublish Google Sheet."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=SUGGESTIONS_CSV_PATH,
        help=f"Path to AI_Suggestions.csv (default: {SUGGESTIONS_CSV_PATH})",
    )
    parser.add_argument(
        "--credentials",
        type=Path,
        default=None,
        help=(
            "Path to service-account JSON key "
            "(default: GOOGLE_SERVICE_ACCOUNT_KEY env var or "
            "~/.config/wheretopublish/google_service_account.json)"
        ),
    )
    args = parser.parse_args()
    upload(args.input, args.credentials)


if __name__ == "__main__":
    main()
