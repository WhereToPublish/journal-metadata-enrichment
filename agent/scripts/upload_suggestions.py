"""upload_suggestions.py — Upload Agent_suggestions.csv to the WhereToPublish Google Sheet.

Reads Agent_suggestions.csv and writes all rows to the 'Agent_suggestions' tab of the
spreadsheet. A leading 'Status' column (pending / approve / reject) is prepended for
human review. New suggestions are uploaded with 'pending' status.
Any existing content in that tab is replaced on each run.

Usage:
    python3 agent/scripts/upload_suggestions.py \
        [--input agent/output/Agent_suggestions.csv] \
        [--credentials ~/.config/wheretopublish/google_service_account.json]
"""

from __future__ import annotations
from typing import Any
import argparse
import sys
from pathlib import Path

# Resolve the WTP scripts directory before importing sheets_client so we always
# use the canonical implementation in WhereToPublish.github.io/scripts/ rather
# than a duplicate copy inside the agent folder.
WTP_SCRIPTS = Path(__file__).parent.parent.parent / "WhereToPublish.github.io" / "scripts"
if str(WTP_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(WTP_SCRIPTS))
import sheets_client
from enrichment_common import SUGGESTIONS_CSV_PATH
from suggestions_io import dedupe_suggestions, load_suggestions, write_suggestions

SUGGESTIONS_TAB = "Agent_suggestions"

# Column order in the Agent_suggestions tab (Status is first; the rest match CSV headers)
OUTPUT_HEADERS = [
    "Status",
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

CSV_FIELD_ORDER = OUTPUT_HEADERS[1:]  # same list minus 'Status'


def get_or_create_tab(service: Any, spreadsheet_id: str, tab_name: str) -> None:
    """Ensure a tab with the given name exists; create it if it doesn't."""
    spreadsheet = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    existing_titles = {s["properties"]["title"] for s in spreadsheet["sheets"]}
    if tab_name not in existing_titles:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": tab_name}}}]},
        ).execute()


def clear_tab(service: Any, spreadsheet_id: str, tab_name: str) -> None:
    """Clear all content from a tab."""
    service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=f"{tab_name}!A1:Z",
    ).execute()


def upload(input_csv: Path, credentials_path: Path | None) -> None:
    if not input_csv.exists():
        print(f"ERROR: input file not found: {input_csv}", file=sys.stderr)
        sys.exit(1)

    rows = load_suggestions(input_csv)
    rows, dropped_rows = dedupe_suggestions(rows)
    if dropped_rows:
        write_suggestions(input_csv, rows)
        print(f"Dropped {dropped_rows} duplicate suggestion(s) from {input_csv} before upload.")

    if not rows:
        print("No suggestions to upload — file is empty.")
        return

    print(f"Uploading {len(rows)} suggestion(s) from {input_csv} ...")

    service = sheets_client.get_sheets_service(credentials_path=credentials_path, readonly=False)

    get_or_create_tab(service, sheets_client.SPREADSHEET_ID, SUGGESTIONS_TAB)
    clear_tab(service, sheets_client.SPREADSHEET_ID, SUGGESTIONS_TAB)

    # Build data: header row + one row per suggestion (Status starts as 'pending')
    data: list[list] = [OUTPUT_HEADERS]
    for row in rows:
        data.append(
            ["pending"]  # Status — starts as pending for human review
            + [row.get(col, "") for col in CSV_FIELD_ORDER]
        )

    sheets_client.write_rows(service, sheets_client.SPREADSHEET_ID, SUGGESTIONS_TAB, data)

    print(f"Done. {len(rows)} row(s) written to the '{SUGGESTIONS_TAB}' tab.")
    print(f"https://docs.google.com/spreadsheets/d/{sheets_client.SPREADSHEET_ID}/edit")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upload Agent_suggestions.csv to the WhereToPublish Google Sheet."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=SUGGESTIONS_CSV_PATH,
        help=f"Path to Agent_suggestions.csv (default: {SUGGESTIONS_CSV_PATH})",
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
