"""gap_analysis.py — Run the WhereToPublish data pipeline and identify metadata gaps.

This script:
1. Verifies that required external data files are present in the WhereToPublish project.
2. Downloads all Google Sheets tabs (every field slug in SHEET_TAB_NAMES) via the Sheets API.
3. Runs update_extracted.py then data_process.py (the existing WTP pipeline).
4. Reads the post-enrichment data/<slug>.csv for every tab.
5. Compares raw vs. enriched data across all tabs to identify what is still missing.
6. Produces agent/output/gap_report.json with per-journal gaps, priorities, and a summary.

The external data files (Scimago, DOAJ, OpenAPC, PCI, Dataverse) must already be present
in the WhereToPublish project's data_extraction/ directory. Run
    bash scripts/download_extraction.sh
from the WhereToPublish.github.io repository to populate them.

Run from the journal-metadata-enrichment/ project root:
    python3 agent/scripts/gap_analysis.py

Or with an explicit WTP repo path:
    python3 agent/scripts/gap_analysis.py --wtp-dir /path/to/WhereToPublish.github.io
"""

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path
from datetime import date, datetime

# Resolve the WTP scripts directory before importing sheets_client so we always
# use the canonical implementation in WhereToPublish.github.io/scripts/ rather
# than a duplicate copy inside the agent folder.
WTP_SCRIPTS = Path(__file__).parent.parent.parent / "WhereToPublish.github.io" / "scripts"
if str(WTP_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(WTP_SCRIPTS))
import sheets_client as _sheets_client

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = Path(".")
OUTPUT_DIR = PROJECT_ROOT / "agent" / "output"
DEFAULT_WTP_DIR = PROJECT_ROOT / "WhereToPublish.github.io"

# Files that must exist in data_extraction/ for the WTP pipeline to run correctly.
# These are managed by the WhereToPublish project (scripts/download_extraction.sh).
REQUIRED_EXTRACTION_FILES = [
    "openapc.csv.gz",
    "DOAJ.csv.gz",
    "scimagojr.csv.gz",
    "PCI_friendly.csv.gz",
    "APC_dataverse.txt.gz",
]

# Field priority weights — higher = more important to fill
FIELD_PRIORITIES = {
    "Business model":   ("medium", 3),
    "Publisher":        ("medium", 7),
    "Country":          ("medium", 6),
    "Website":          ("medium", 5),
    "APC Euros":        ("medium", 4),
    "Publisher type":   ("medium", 8),
    "Institution":      ("low",    0),
    "Institution type": ("low",    0),
    "e-ISSN":           ("high",   10),
    "p-ISSN":           ("high",   10),
    "ISSN-L":           ("high",   10),
}


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

def verify_extraction_data(wtp_dir: Path) -> None:
    """Crash with a clear error if any required extraction file is missing.

    These files are owned by the WhereToPublish project and must be downloaded
    separately using scripts/download_extraction.sh inside that repo.
    """
    data_extraction = wtp_dir / "data_extraction"
    missing = [f for f in REQUIRED_EXTRACTION_FILES if not (data_extraction / f).exists()]
    if missing:
        log(f"ERROR: Required extraction files not found in {display_path(data_extraction)}:")
        for f in missing:
            log(f"  missing: {f}")
        log("Run 'bash scripts/download_extraction.sh' from the WhereToPublish.github.io repo to populate them.")
        sys.exit(1)
    log(f"  Extraction data: all {len(REQUIRED_EXTRACTION_FILES)} required files present")


def download_all_sheets_csvs(wtp_dir: Path, credentials_path: Path | None = None) -> None:
    """Download every sheet tab to data_extracted/<slug>.csv."""
    data_extracted = wtp_dir / "data_extracted"
    data_extracted.mkdir(parents=True, exist_ok=True)

    log("Downloading all Google Sheets tabs via Sheets API ...")
    try:
        service = _sheets_client.get_sheets_service(credentials_path=credentials_path, readonly=True)
    except Exception as exc:
        log(f"ERROR: Could not authenticate with the Sheets API: {exc}")
        sys.exit(1)

    for slug, tab_name in _sheets_client.SHEET_TAB_NAMES.items():
        dest = data_extracted / f"{slug}.csv"
        try:
            rows = _sheets_client.download_tab_as_csv(service, tab_name, dest)
            log(f"  {tab_name}: {len(rows)} rows downloaded")
        except Exception as exc:
            log(f"ERROR: Could not download tab '{tab_name}': {exc}")
            sys.exit(1)


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

def run_pipeline(wtp_dir: Path) -> None:
    """Run update_extracted.py and data_process.py inside the WTP repo directory."""
    log("Running update_extracted.py ...")
    result = subprocess.run(
        [sys.executable, "scripts/update_extracted.py"],
        cwd=str(wtp_dir),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log(f"ERROR: update_extracted.py failed:\n{result.stderr[-2000:]}")
        sys.exit(1)
    log("  update_extracted.py: done")

    log("Running data_process.py ...")
    result = subprocess.run(
        [sys.executable, "scripts/data_process.py"],
        cwd=str(wtp_dir),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log(f"ERROR: data_process.py failed:\n{result.stderr[-2000:]}")
        sys.exit(1)
    log("  data_process.py: done")


# ---------------------------------------------------------------------------
# Gap analysis
# ---------------------------------------------------------------------------

def load_csv_as_dicts(path: Path) -> list[dict]:
    """Load a CSV file into a list of row dicts."""
    with open(path, encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))


def is_empty(val) -> bool:
    """True if value is None, empty string, or whitespace."""
    return val is None or str(val).strip() == ""


def compute_gaps(raw_row: dict, enriched_row: dict | None) -> list[dict]:
    """Return a list of gap dicts for a single journal."""
    gaps = []
    row = enriched_row if enriched_row else raw_row

    # If the journal already has at least one ISSN (from any source), skip all ISSN gaps.
    # The pipeline join can often recover the remaining ISSNs once one is known.
    issn_fields = {"e-ISSN", "p-ISSN", "ISSN-L"}
    any_issn_present = any(not is_empty(row.get(f, "")) for f in issn_fields)

    for field, (priority, weight) in FIELD_PRIORITIES.items():
        if field in issn_fields and any_issn_present:
            continue
        if is_empty(row.get(field, "")):
            gaps.append({
                "field": field,
                "current_value": "",
                "gap_type": "fill",
                "priority": priority,
                "priority_weight": weight,
            })

    # Detect journals with no Scimago match — prime candidates for alt_name suggestion.
    # This gets the highest priority_weight (12) so it is processed before Business model (10).
    if is_empty(row.get("Scimago Rank", "")) and is_empty(row.get("Scimago Quartile", "")):
        alt_journal_name = raw_row.get("Alternative journal name", "")
        gaps.append({
            "field": "Alternative journal name",
            "current_value": alt_journal_name if not is_empty(alt_journal_name) else "",
            "gap_type": "alt_name",
            "priority": "high",
            "priority_weight": 12,
            "note": (
                "Journal has no Scimago data. Providing the correct alternative journal name "
                "enables automatic enrichment with Rank, Quartile, H index, and Publisher."
            ),
        })

    return gaps


def _process_tab(slug: str, wtp_dir: Path) -> tuple[list[dict], list[str]]:
    """Load raw + enriched CSVs for one tab and return (journal entries with gaps, warnings).

    Returns a list of journal gap dicts (ready for the report) and a list of warning strings
    for any issue that should be logged but does not abort processing.
    """
    warnings: list[str] = []
    raw_path = wtp_dir / "data_extracted" / f"{slug}.csv"
    enriched_path = wtp_dir / "data" / f"{slug}.csv"

    if not raw_path.exists():
        warnings.append(f"Raw CSV not found, skipping tab '{slug}': {display_path(raw_path)}")
        return [], warnings
    if not enriched_path.exists():
        warnings.append(f"Enriched CSV not found, skipping tab '{slug}': {display_path(enriched_path)}")
        return [], warnings

    raw_rows = load_csv_as_dicts(raw_path)
    enriched_rows = load_csv_as_dicts(enriched_path)
    tab_name = _sheets_client.SHEET_TAB_NAMES.get(slug, slug)

    enriched_by_name: dict[str, dict] = {
        row.get("Journal", "").strip().lower(): row
        for row in enriched_rows
        if row.get("Journal", "").strip()
    }

    entries: list[dict] = []
    for raw_row in raw_rows:
        journal_name = raw_row.get("Journal", "").strip()
        if not journal_name:
            continue

        enriched_row = enriched_by_name.get(journal_name.lower())
        gaps = compute_gaps(raw_row, enriched_row)
        if not gaps:
            continue

        gaps.sort(key=lambda g: g["priority_weight"], reverse=True)
        for gap in gaps:
            gap.pop("priority_weight", None)

        # Prefer enriched ISSN values (pipeline may have filled them from Scimago/DOAJ/OpenAPC);
        # fall back to raw row when the enriched row is unavailable.
        row_for_issn = enriched_row if enriched_row else raw_row
        entries.append({
            "name": journal_name,
            "tab": tab_name,
            "website": enriched_row.get("Website", "") if enriched_row else raw_row.get("Website", ""),
            "current_publisher": enriched_row.get("Publisher", "") if enriched_row else "",
            "current_business_model": enriched_row.get("Business model", "") if enriched_row else "",
            "e_issn": row_for_issn.get("e-ISSN", ""),
            "p_issn": row_for_issn.get("p-ISSN", ""),
            "issn_l": row_for_issn.get("ISSN-L", ""),
            "gaps": gaps,
        })

    return entries, warnings


def build_gap_report(wtp_dir: Path) -> dict:
    """Compare raw Google Sheets data with enriched pipeline output across all tabs."""
    # Accumulate results; deduplicate journals by name (first occurrence wins).
    seen_names: set[str] = set()
    journals_with_gaps: list[dict] = []
    total_journals_seen = 0
    total_gaps = 0
    priority_counts: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    tabs_processed: list[str] = []

    for slug in _sheets_client.SHEET_TAB_NAMES:
        entries, warnings = _process_tab(slug, wtp_dir)
        for w in warnings:
            log(f"  WARNING: {w}")
        if not warnings:
            tabs_processed.append(_sheets_client.SHEET_TAB_NAMES[slug])

        # Count raw rows for total_journals (load directly here to stay accurate even if no gaps).
        raw_path = wtp_dir / "data_extracted" / f"{slug}.csv"
        if raw_path.exists():
            raw_rows = load_csv_as_dicts(raw_path)
            total_journals_seen += sum(1 for r in raw_rows if r.get("Journal", "").strip())

        for entry in entries:
            key = entry["name"].lower()
            if key in seen_names:
                continue  # deduplicate: same journal in multiple tabs → keep first occurrence
            seen_names.add(key)
            journals_with_gaps.append(entry)
            total_gaps += len(entry["gaps"])
            for gap in entry["gaps"]:
                priority_counts[gap["priority"]] = priority_counts.get(gap["priority"], 0) + 1

    journals_with_gaps.sort(key=lambda k: (
        0 if any(g["priority"] == "high" for g in k["gaps"])
        else 1 if any(g["priority"] == "medium" for g in k["gaps"])
        else 2,
        k["name"].lower(),
    ))

    field_gap_counts: dict[str, int] = {}
    for j in journals_with_gaps:
        for gap in j["gaps"]:
            field = gap["field"]
            field_gap_counts[field] = field_gap_counts.get(field, 0) + 1
    field_gap_counts = dict(sorted(field_gap_counts.items(), key=lambda x: x[1], reverse=True))

    return {
        "run_date": date.today().isoformat(),
        "wtp_dir": display_path(wtp_dir),
        "tabs": tabs_processed,
        "total_journals": total_journals_seen,
        "journals_with_gaps": len(journals_with_gaps),
        "total_gap_instances": total_gaps,
        "priority_summary": priority_counts,
        "field_gap_counts": field_gap_counts,
        "journals": journals_with_gaps,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Run the WTP pipeline and identify metadata gaps.")
    parser.add_argument(
        "--wtp-dir",
        type=Path,
        default=DEFAULT_WTP_DIR,
        help=f"Path to the WhereToPublish.github.io repo (default: {DEFAULT_WTP_DIR})",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip downloading all sheet tabs (use existing data_extracted/<slug>.csv files).",
    )
    parser.add_argument(
        "--skip-pipeline",
        action="store_true",
        help="Skip running update_extracted.py and data_process.py (use existing output).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_DIR / "gap_report.json",
        help="Where to write gap_report.json",
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

    wtp_dir: Path = args.wtp_dir

    if not wtp_dir.exists():
        log(f"ERROR: WTP directory not found: {display_path(wtp_dir)}")
        sys.exit(1)

    log("=== WhereToPublish Gap Analysis ===")
    log(f"WTP dir: {display_path(wtp_dir)}")
    log(f"Output : {display_path(args.output)}")

    verify_extraction_data(wtp_dir)

    if not args.skip_download:
        download_all_sheets_csvs(wtp_dir, credentials_path=args.credentials)
    else:
        log("Skipping sheet downloads (--skip-download set)")

    if not args.skip_pipeline:
        run_pipeline(wtp_dir)
    else:
        log("Skipping pipeline (--skip-pipeline set)")

    log("Building gap report ...")
    report = build_gap_report(wtp_dir)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    log(f"=== Gap Report Written: {display_path(args.output)} ===")
    log(f"  Tabs processed      : {len(report['tabs'])}")
    log(f"  Total journals      : {report['total_journals']}")
    log(f"  Journals with gaps  : {report['journals_with_gaps']}")
    log(f"  Total gap instances : {report['total_gap_instances']}")
    log(f"  Priority breakdown  : {report['priority_summary']}")
    log(f"  Top missing fields  : {dict(list(report['field_gap_counts'].items())[:5])}")


if __name__ == "__main__":
    main()
