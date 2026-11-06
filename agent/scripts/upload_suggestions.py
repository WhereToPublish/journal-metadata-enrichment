"""upload_suggestions.py — Upload Agent_suggestions.csv to the WhereToPublish Google Sheet.

Reads Agent_suggestions.csv and writes all rows to the 'Agent_suggestions' review
tab of the spreadsheet. Shares the review workflow of the disagreement report
(review_common in the parent WhereToPublish scripts/): a leading 'Status' column
(pending / approve / reject) is prepended for human review, new suggestions are
uploaded as 'pending', and each row records the exact data-tab cell to change
(data_tab / target_row / target_col) so the Google Apps Script applies approved
suggestions with a single verified cell write (no whole-tab scans). Any existing
content in that tab is replaced on each run.

The cell targets are resolved from the data_extracted CSVs downloaded during
the last gap analysis (they mirror the data tabs). The Apps Script re-verifies
each target cell before writing, so a stale target fails safely instead of
editing the wrong cell.

Usage:
    python3 agent/scripts/upload_suggestions.py \
        [--input agent/output/Agent_suggestions.csv] \
        [--credentials ~/.config/wheretopublish/google_service_account.json]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Resolve the WTP scripts directory before importing the shared modules so we
# always use the canonical implementations in the parent WhereToPublish repo's
# scripts/ (review_common itself imports sheets_client and libraries).
from enrichment_common import SUGGESTIONS_CSV_PATH, WTP_SCRIPTS_DIR

if str(WTP_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(WTP_SCRIPTS_DIR))
import review_common
from review_common import STATUS_PENDING, SUGGESTIONS_TAB
import sheets_client
from suggestions_io import dedupe_suggestions, load_suggestions, write_suggestions

# Review-tab column order, mirrored with the Disagreements tab: the shared
# leading columns (Status + apply-row schema + priority), the suggestion-specific
# context columns, and the shared target-cell columns last.
SHARED_LEADING_COLUMNS = ["journal", "field", "current_value", "suggested_value", "suggestion_type", "priority"]
SUGGESTION_CONTEXT_COLUMNS = ["confidence", "source_urls", "reasoning"]
TARGET_COLUMNS = ["data_tab", "target_row", "target_col"]
OUTPUT_HEADERS = [review_common.STATUS_COLUMN] + SHARED_LEADING_COLUMNS + SUGGESTION_CONTEXT_COLUMNS + TARGET_COLUMNS

# Review order of the uploaded rows: priority (high → medium → low, unknown last),
# then confidence (descending). Journals are kept as contiguous groups, ordered
# by their best suggestion, so a journal's rows are always reviewed together.
PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def suggestion_sort_key(row: dict) -> tuple:
    """Sort key of a single suggestion row: priority, then confidence desc."""
    priority = str(row.get("priority", "")).strip().lower()
    try:
        confidence = float(row.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    return (PRIORITY_ORDER.get(priority, 3), -confidence, str(row.get("journal", "")))


def sort_rows_grouped_by_journal(rows: list[dict]) -> list[dict]:
    """Sort suggestions by priority then confidence, keeping each journal's rows contiguous.

    Rows are sorted individually; journals then appear in the order of their best
    (highest priority, highest confidence) suggestion, and all rows of a journal
    are emitted together (still sorted by priority then confidence within the
    group).
    """
    by_journal: dict[str, list[dict]] = {}
    for row in sorted(rows, key=suggestion_sort_key):
        by_journal.setdefault(row.get("journal", ""), []).append(row)
    ordered: list[dict] = []
    for row in sorted(rows, key=suggestion_sort_key):
        journal = row.get("journal", "")
        if journal in by_journal:
            ordered.extend(by_journal.pop(journal))
    return ordered


def build_review_rows(rows: list[dict], data_extracted_dir: Path) -> list[list]:
    """Return the review-tab rows (header first) for the suggestion *rows*.

    Rows are sorted by priority then confidence and grouped per journal (see
    sort_rows_grouped_by_journal). Resolves the data-tab cell targeted by each
    suggestion from the data_extracted CSVs; unresolved journals (missing /
    ambiguous) get empty target columns and are flagged for manual handling by
    the Apps Script.
    """
    data_rows: list[list] = []
    for row in sort_rows_grouped_by_journal(rows):
        target = review_common.locate_journal_cell(data_extracted_dir, row.get("journal", ""), row.get("field", ""))
        data_rows.append(
            [STATUS_PENDING]
            + [row.get(col, "") for col in SHARED_LEADING_COLUMNS]
            + [row.get(col, "") for col in SUGGESTION_CONTEXT_COLUMNS]
            + [target[col] for col in TARGET_COLUMNS]
        )
    return [OUTPUT_HEADERS] + data_rows


def upload(input_csv: Path, credentials_path: Path | None, data_extracted_dir: Path) -> None:
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
    review_rows = build_review_rows(rows, data_extracted_dir)

    service = sheets_client.get_sheets_service(credentials_path=credentials_path, readonly=False)
    review_common.upload_review_tab(
        service=service, spreadsheet_id=sheets_client.SPREADSHEET_ID,
        tab_name=SUGGESTIONS_TAB, rows=review_rows,
    )

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
    parser.add_argument(
        "--data-extracted-dir",
        type=Path,
        default=WTP_SCRIPTS_DIR.parent / "data_extracted",
        help="Directory with the data tab CSVs used to resolve suggestion targets",
    )
    args = parser.parse_args()
    upload(args.input, args.credentials, args.data_extracted_dir)


if __name__ == "__main__":
    main()
