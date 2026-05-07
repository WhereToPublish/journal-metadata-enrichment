"""sheets_client.py — Shared Google Sheets API helper for the journal-metadata-enrichment project.

Provides authenticated access to the WhereToPublish spreadsheet using a service account.
Credentials are resolved from the GOOGLE_SERVICE_ACCOUNT_KEY environment variable or the
default path ~/.config/wheretopublish/google_service_account.json.
"""

from __future__ import annotations

import csv
import io
import os
from pathlib import Path
from typing import Any

SPREADSHEET_ID = "1PRXViyQlo5ZMjpCJ_XpcHfsnZEJmmdCiXjnkazMyua8"

DEFAULT_CREDENTIALS_PATH = Path(
    os.environ.get(
        "GOOGLE_SERVICE_ACCOUNT_KEY",
        os.path.expanduser("~/.config/wheretopublish/google_service_account.json"),
    )
)

# Map field slugs (used by CLI/gap_analysis) to Google Sheets tab names
SHEET_TAB_NAMES: dict[str, str] = {
    "generalist":                  "Generalists",
    "anatomy_physiology":          "Anatomy & Physiology",
    "cancer":                      "Cancer",
    "development":                 "Development",
    "ecology_evolution":           "Ecology & Evolution",
    "genetics_genomics":           "Genetics & Genomics",
    "immunology":                  "Immunology",
    "molecular_cellular_biology":  "Molecular & Cellular Biology",
    "neurosciences":               "Neurosciences",
    "plants":                      "Plants",
}

# Scopes ─ use readonly when only downloading; use full when uploading too
_SCOPE_READONLY = "https://www.googleapis.com/auth/spreadsheets.readonly"
_SCOPE_READWRITE = "https://www.googleapis.com/auth/spreadsheets"


def get_sheets_service(
    credentials_path: Path | None = None,
    readonly: bool = True,
) -> Any:
    """Return an authenticated Google Sheets API v4 service resource.

    Args:
        credentials_path: Path to the service-account JSON key file.
            Defaults to DEFAULT_CREDENTIALS_PATH.
        readonly: When True, requests only the spreadsheets.readonly scope.
            Set False when the caller needs write access (e.g. upload_suggestions).
    """
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds_path = credentials_path or DEFAULT_CREDENTIALS_PATH
    if not creds_path.exists():
        raise FileNotFoundError(
            f"Google service-account credentials not found: {creds_path}\n"
            "Set GOOGLE_SERVICE_ACCOUNT_KEY or place the key at the default path."
        )

    scope = _SCOPE_READONLY if readonly else _SCOPE_READWRITE
    creds = service_account.Credentials.from_service_account_file(
        str(creds_path),
        scopes=[scope],
    )
    return build("sheets", "v4", credentials=creds)


def download_tab_as_csv(
    service: Any,
    tab_name: str,
    dest_path: Path,
    spreadsheet_id: str = SPREADSHEET_ID,
) -> list[list[str]]:
    """Download a single tab from the spreadsheet and write it as a CSV file.

    Args:
        service: Authenticated Sheets API service (from get_sheets_service).
        tab_name: The exact name of the tab as shown in Google Sheets.
        dest_path: Local path where the CSV file will be written.
        spreadsheet_id: Google Sheets spreadsheet ID (default: WhereToPublish).

    Returns:
        The rows as a list-of-lists (including the header row).
    """
    result = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=spreadsheet_id,
            range=tab_name,
            valueRenderOption="FORMATTED_VALUE",
            dateTimeRenderOption="FORMATTED_STRING",
        )
        .execute()
    )
    rows: list[list[str]] = result.get("values", [])

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dest_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerows(rows)

    return rows


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


def write_rows(
    service: Any,
    spreadsheet_id: str,
    tab_name: str,
    rows: list[list[Any]],
    value_input_option: str = "USER_ENTERED",
) -> None:
    """Write a list of rows to a tab starting at A1."""
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{tab_name}!A1",
        valueInputOption=value_input_option,
        body={"values": rows},
    ).execute()


def load_suggestion_keys_from_tabs(
    service: Any,
    tab_names: list[str],
    spreadsheet_id: str = SPREADSHEET_ID,
) -> set[tuple[str, str, str]]:
    """Read (journal, field, suggested_value) triples from the given sheet tabs.

    Used before enrichment runs to avoid re-suggesting values already present in
    AI_suggestions or AI_suggestions_processed.  Missing or empty tabs are silently
    skipped so the caller degrades gracefully when those tabs do not yet exist.

    Args:
        service: Authenticated Sheets API service (readonly is sufficient).
        tab_names: List of tab names to read from.
        spreadsheet_id: Google Sheets spreadsheet ID (default: WhereToPublish).

    Returns:
        A set of (journal, field, suggested_value) tuples.
    """
    keys: set[tuple[str, str, str]] = set()
    for tab_name in tab_names:
        try:
            result = (
                service.spreadsheets()
                .values()
                .get(
                    spreadsheetId=spreadsheet_id,
                    range=tab_name,
                    valueRenderOption="FORMATTED_VALUE",
                )
                .execute()
            )
        except Exception:
            # Tab does not exist or API error — skip gracefully
            continue

        rows = result.get("values", [])
        if len(rows) < 2:
            continue

        header = rows[0]
        try:
            journal_col = header.index("journal")
            field_col = header.index("field")
            value_col = header.index("suggested_value")
        except ValueError:
            # Header structure not recognised — skip this tab
            continue

        for row in rows[1:]:
            # Pad short rows
            padded = row + [""] * max(0, max(journal_col, field_col, value_col) + 1 - len(row))
            journal = padded[journal_col].strip()
            field = padded[field_col].strip()
            suggested_value = padded[value_col].strip()
            if journal and field and suggested_value:
                keys.add((journal, field, suggested_value))

    return keys
