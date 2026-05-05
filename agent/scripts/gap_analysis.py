"""gap_analysis.py — Run the WhereToPublish data pipeline and identify metadata gaps.

This script:
1. Downloads the Genetics & Genomics sheet from the public Google Sheets export.
2. Downloads external sources (Scimago, DOAJ, OpenAPC, Dataverse) if not already present.
3. Runs update_extracted.py then data_process.py (the existing WTP pipeline).
4. Reads the post-enrichment data/genetics_genomics.csv.
5. Compares raw vs. enriched data to identify what is still missing.
6. Produces agent/output/gap_report.json with per-journal gaps, priorities, and a summary.

Run from the journal-metadata-enrichment/ project root:
    python3 agent/scripts/gap_analysis.py

Or with an explicit WTP repo path:
    python3 agent/scripts/gap_analysis.py --wtp-dir /path/to/WhereToPublish.github.io
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path
from datetime import date, datetime

import sheets_client as _sheets_client

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = Path(".")
OUTPUT_DIR = PROJECT_ROOT / "agent" / "output"

DEFAULT_WTP_DIR = PROJECT_ROOT / "WhereToPublish.github.io"

URL_OPENAPC = "https://github.com/OpenAPC/openapc-de/raw/refs/heads/master/data/apc_de.csv"
URL_DOAJ = "https://doaj.org/csv"
URL_SCIMAGO = "https://www.scimagojr.com/journalrank.php?out=xls"
URL_PCI = "https://docs.google.com/spreadsheets/d/1UF3z_brMq-cJt0nbVactbcNPm5U8YsC6vx1GdmzBfxU/export?format=csv"
# Dataverse: dataset DOI 10.7910/DVN/CR1MMV — fetch file listing via API then download first file
URL_DATAVERSE_API = "https://dataverse.harvard.edu/api/datasets/:persistentId/?persistentId=doi:10.7910/DVN/CR1MMV"

# Field priority weights — higher = more important to fill
FIELD_PRIORITIES = {
    "Business model": ("high",  10),
    "Publisher":      ("high",  9),
    "Country":        ("medium", 6),
    "Website":        ("medium", 5),
    "APC Euros":      ("medium", 4),   # only medium; highly dependent on Business model
    "Publisher type": ("medium", 3),
    "Institution":    ("low",    2),
    "Institution type": ("low",  1),
}

# Fields enriched by external sources (if still empty after enrichment → pipeline join failed)
ENRICHMENT_FIELDS = {"Business model", "Publisher", "Country", "Website", "APC Euros",
                     "Scimago Rank", "Scimago Quartile", "H index"}


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def _http_get(url: str, dest: Path, label: str) -> None:
    """Download url → dest using urllib (stdlib). Creates parent dirs as needed."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
        dest.write_bytes(data)
        log(f"  {label}: {len(data) // 1024:,} KB saved")
    except Exception as e:
        log(f"  WARNING: failed to download {label}: {e}")


def _wget(url: str, dest: Path, label: str, extra_args: list[str] | None = None) -> bool:
    """Download url → dest using wget. Returns True on success."""
    import shutil
    if not shutil.which("wget"):
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["wget", "--quiet", f'--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
           "-O", str(dest)] + (extra_args or []) + [url]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0 and dest.exists() and dest.stat().st_size > 1000:
        log(f"  {label}: {dest.stat().st_size // 1024:,} KB saved via wget")
        return True
    log(f"  WARNING: wget failed for {label} (rc={result.returncode})")
    return False


def _create_stub_csv_gz(dest: Path, header: str, label: str) -> None:
    """Create an empty (header-only) gzipped CSV so the pipeline can run without this source."""
    import gzip as gz
    dest.parent.mkdir(parents=True, exist_ok=True)
    with gz.open(dest, "wb") as f:
        f.write((header + "\n").encode("utf-8"))
    log(f"  {label}: created stub (header-only) at {dest.name} — pipeline will skip enrichment from this source")


def ensure_extraction_data(wtp_dir: Path) -> None:
    """Download external data sources if not already present (they are large; skip if fresh)."""
    import gzip as gzip_module
    import shutil

    data_extraction = wtp_dir / "data_extraction"
    data_extraction.mkdir(parents=True, exist_ok=True)

    # ── OpenAPC ────────────────────────────────────────────────────────────
    dest = data_extraction / "openapc.csv.gz"
    if dest.exists():
        log(f"  OpenAPC: already present ({dest.stat().st_size // (1024*1024):.1f} MB), skipping")
    else:
        raw = dest.with_suffix("")
        _http_get(URL_OPENAPC, raw, "OpenAPC")
        if raw.exists():
            log("  Compressing OpenAPC ...")
            with open(raw, "rb") as f_in, gzip_module.open(dest, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            raw.unlink()
            log(f"  OpenAPC: compressed to {dest.name}")

    # ── DOAJ ───────────────────────────────────────────────────────────────
    dest = data_extraction / "DOAJ.csv.gz"
    if dest.exists():
        log(f"  DOAJ: already present ({dest.stat().st_size // (1024*1024):.1f} MB), skipping")
    else:
        raw = dest.with_suffix("")
        _http_get(URL_DOAJ, raw, "DOAJ")
        if raw.exists():
            log("  Compressing DOAJ ...")
            with open(raw, "rb") as f_in, gzip_module.open(dest, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            raw.unlink()
            log(f"  DOAJ: compressed to {dest.name}")

    # ── Scimago — tries wget first (Scimago blocks urllib with 403) ────────
    dest = data_extraction / "scimagojr.csv.gz"
    if dest.exists():
        log(f"  Scimago: already present ({dest.stat().st_size // (1024*1024):.1f} MB), skipping")
    else:
        raw = dest.with_suffix("")
        log("Downloading Scimago → scimagojr.csv (using wget) ...")
        ok = _wget(URL_SCIMAGO, raw, "Scimago")
        if not ok:
            # Try urllib as fallback (may still get 403)
            _http_get(URL_SCIMAGO, raw, "Scimago")
        if raw.exists() and raw.stat().st_size > 10000:
            log("  Compressing Scimago ...")
            with open(raw, "rb") as f_in, gzip_module.open(dest, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            raw.unlink()
            log(f"  Scimago: compressed to {dest.name}")
        else:
            if raw.exists():
                raw.unlink()
            log("  WARNING: Scimago download failed. Creating stub file.")
            log("  NOTE: Scimago blocks automated downloads. Run scripts/download_extraction.sh")
            log("        from the WhereToPublish.github.io repo manually, or place scimagojr.csv.gz")
            log(f"        at: {dest}")
            # Stub with ALL columns expected by update_extracted.py so the pipeline can load it
            _create_stub_csv_gz(
                dest,
                "Rank;Sourceid;Title;Type;Issn;SJR;SJR Best Quartile;H index;"
                "Total Docs. (2023);Total Docs. (3years);Total Refs.;Total Cites (3years);"
                "Citable Docs. (3years);Cites / Doc. (2years);Ref. / Doc.;Country;Region;"
                "Publisher;Coverage;Categories;Areas;Open Access;Open Access Diamond",
                "Scimago",
            )

    # ── PCI ────────────────────────────────────────────────────────────────
    dest = data_extraction / "PCI_friendly.csv.gz"
    if dest.exists():
        log(f"  PCI: already present, skipping")
    else:
        raw = dest.with_suffix("")
        _http_get(URL_PCI, raw, "PCI")
        if raw.exists():
            log("  Compressing PCI ...")
            with open(raw, "rb") as f_in, gzip_module.open(dest, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            raw.unlink()
            log(f"  PCI: compressed to {dest.name}")

    # ── Dataverse APC — optional, skip gracefully if unavailable ──────────
    dest = data_extraction / "APC_dataverse.txt.gz"
    if dest.exists():
        log(f"  Dataverse APC: already present, skipping")
    else:
        raw = _download_dataverse_apc(dest)
        if not raw:
            log("  Dataverse APC unavailable — creating stub. APCs from this source will be skipped.")
            _create_stub_csv_gz(
                dest,
                "Journal\tPublisher\tAPC_EUR\tAPC_year\tAPC_provided\tOA_status",
                "Dataverse",
            )


def _download_dataverse_apc(dest: Path) -> bool:
    """Attempt to download the Dataverse APC dataset. Returns True on success."""
    import gzip as gzip_module
    import shutil
    import json

    log("Downloading Dataverse APC dataset via API ...")
    # Step 1: fetch dataset metadata to find the file ID
    headers = {"User-Agent": "journal-enrichment-agent/1.0"}
    try:
        req = urllib.request.Request(URL_DATAVERSE_API, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            meta = json.loads(resp.read())

        # Find the .txt or .tab file in the latest version
        files = meta.get("data", {}).get("latestVersion", {}).get("files", [])
        file_id = None
        for f in files:
            fname = f.get("dataFile", {}).get("filename", "")
            if fname.endswith(".tab") or "APC" in fname:
                file_id = f.get("dataFile", {}).get("id")
                log(f"  Found Dataverse file: {fname} (id={file_id})")
                break

        if not file_id:
            log("  WARNING: No suitable file found in Dataverse dataset.")
            return False

        dl_url = f"https://dataverse.harvard.edu/api/access/datafile/{file_id}"
        raw = dest.with_suffix("")
        req2 = urllib.request.Request(dl_url, headers=headers)
        with urllib.request.urlopen(req2, timeout=120) as resp2:
            data = resp2.read()
        raw.write_bytes(data)
        log(f"  Dataverse APC: {len(data) // 1024:,} KB saved")

        with open(raw, "rb") as f_in, gzip_module.open(dest, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        raw.unlink()
        log(f"  Dataverse APC: compressed to {dest.name}")
        return True

    except Exception as e:
        log(f"  WARNING: Dataverse download failed: {e}")
        return False


def download_genetics_genomics_csv(wtp_dir: Path, credentials_path: Path | None = None) -> None:
    """Download the Genetics & Genomics sheet to data_extracted/genetics_genomics.csv."""
    data_extracted = wtp_dir / "data_extracted"
    data_extracted.mkdir(parents=True, exist_ok=True)
    dest = data_extracted / "genetics_genomics.csv"

    log("Downloading Genetics & Genomics sheet via Sheets API ...")
    try:
        service = _sheets_client.get_sheets_service(credentials_path=credentials_path, readonly=True)
        rows = _sheets_client.download_tab_as_csv(
            service,
            _sheets_client.SHEET_TAB_NAMES["genetics_genomics"],
            dest,
        )
        log(f"  Genetics & Genomics: {len(rows)} rows downloaded")
    except Exception as exc:
        log(f"ERROR: Could not download Genetics & Genomics sheet: {exc}")
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
    import csv
    with open(path, encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        return [row for row in reader]


def is_empty(val) -> bool:
    """True if value is None, empty string, or whitespace."""
    return val is None or str(val).strip() == ""


def compute_gaps(raw_row: dict, enriched_row: dict | None) -> list[dict]:
    """Return a list of gap dicts for a single journal."""
    gaps = []

    # If no enriched row exists for this journal, skip enrichment analysis
    # (can happen if name changed between raw and processed output)
    row = enriched_row if enriched_row else raw_row

    # Check each field with a known priority
    for field, (priority, weight) in FIELD_PRIORITIES.items():
        current_val = row.get(field, "")
        if is_empty(current_val):
            gaps.append({
                "field": field,
                "current_value": "",
                "gap_type": "fill",
                "priority": priority,
                "priority_weight": weight,
            })

    # Special: detect journals that got NO Scimago enrichment at all
    # These are prime candidates for "alt_name" suggestion (Scimago Journal Title)
    scimago_rank = row.get("Scimago Rank", "")
    scimago_quartile = row.get("Scimago Quartile", "")
    scimago_alt = raw_row.get("Scimago Journal Title", "")

    if is_empty(scimago_rank) and is_empty(scimago_quartile):
        # No Scimago match — suggest finding the alt name
        gaps.append({
            "field": "Scimago Journal Title",
            "current_value": scimago_alt if not is_empty(scimago_alt) else "",
            "gap_type": "alt_name",
            "priority": "high",
            "priority_weight": 8,
            "note": (
                "Journal has no Scimago data. Providing the correct Scimago Journal Title "
                "enables automatic enrichment with Rank, Quartile, H index, and Publisher."
            ),
        })

    return gaps


def build_gap_report(wtp_dir: Path) -> dict:
    """Compare raw Google Sheets data with enriched pipeline output and build gap report."""
    raw_path = wtp_dir / "data_extracted" / "genetics_genomics.csv"
    enriched_path = wtp_dir / "data" / "genetics_genomics.csv"

    if not raw_path.exists():
        log(f"ERROR: Raw CSV not found: {display_path(raw_path)}")
        sys.exit(1)
    if not enriched_path.exists():
        log(f"ERROR: Enriched CSV not found: {display_path(enriched_path)}")
        sys.exit(1)

    raw_rows = load_csv_as_dicts(raw_path)
    enriched_rows = load_csv_as_dicts(enriched_path)

    # Build enriched lookup by normalized journal name
    # We use a simple lowercase strip as a fast key (the pipeline already normalised)
    enriched_by_name: dict[str, dict] = {}
    for row in enriched_rows:
        name = row.get("Journal", "").strip()
        if name:
            enriched_by_name[name.lower()] = row

    journals_with_gaps = []
    total_gaps = 0
    priority_counts: dict[str, int] = {"high": 0, "medium": 0, "low": 0}

    for raw_row in raw_rows:
        journal_name = raw_row.get("Journal", "").strip()
        if not journal_name:
            continue

        enriched_row = enriched_by_name.get(journal_name.lower())
        gaps = compute_gaps(raw_row, enriched_row)

        if not gaps:
            continue

        # Sort gaps by priority weight descending
        gaps.sort(key=lambda g: g["priority_weight"], reverse=True)

        # Remove internal priority_weight before output (human-facing JSON)
        for gap in gaps:
            gap.pop("priority_weight", None)

        total_gaps += len(gaps)
        for gap in gaps:
            priority_counts[gap["priority"]] = priority_counts.get(gap["priority"], 0) + 1

        journals_with_gaps.append({
            "name": journal_name,
            "website": enriched_row.get("Website", "") if enriched_row else raw_row.get("Website", ""),
            "current_publisher": enriched_row.get("Publisher", "") if enriched_row else "",
            "current_business_model": enriched_row.get("Business model", "") if enriched_row else "",
            "gaps": gaps,
        })

    # Sort journals: those with HIGH-priority gaps first
    def journal_sort_key(j):
        has_high = any(g["priority"] == "high" for g in j["gaps"])
        has_medium = any(g["priority"] == "medium" for g in j["gaps"])
        return (0 if has_high else 1 if has_medium else 2, j["name"].lower())

    journals_with_gaps.sort(key=journal_sort_key)

    return {
        "run_date": date.today().isoformat(),
        "wtp_dir": display_path(wtp_dir),
        "tab": "Genetics & Genomics",
        "total_journals": len(raw_rows),
        "journals_with_gaps": len(journals_with_gaps),
        "total_gap_instances": total_gaps,
        "priority_summary": priority_counts,
        "field_gap_counts": _count_field_gaps(journals_with_gaps),
        "journals": journals_with_gaps,
    }


def _count_field_gaps(journals: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for j in journals:
        for gap in j["gaps"]:
            field = gap["field"]
            counts[field] = counts.get(field, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: x[1], reverse=True))


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
        help="Skip downloading the Google Sheet and external sources (use existing files).",
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
        help="Path to service-account JSON key (default: GOOGLE_SERVICE_ACCOUNT_KEY env var or ~/.config/wheretopublish/google_service_account.json)",
    )
    args = parser.parse_args()

    wtp_dir: Path = args.wtp_dir

    if not wtp_dir.exists():
        log(f"ERROR: WTP directory not found: {display_path(wtp_dir)}")
        sys.exit(1)

    log(f"=== WhereToPublish Gap Analysis ===")
    log(f"WTP dir: {display_path(wtp_dir)}")
    log(f"Output : {display_path(args.output)}")

    if not args.skip_download:
        ensure_extraction_data(wtp_dir)
        download_genetics_genomics_csv(wtp_dir, credentials_path=args.credentials)
    else:
        log("Skipping download (--skip-download set)")

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
    log(f"  Total journals      : {report['total_journals']}")
    log(f"  Journals with gaps  : {report['journals_with_gaps']}")
    log(f"  Total gap instances : {report['total_gap_instances']}")
    log(f"  Priority breakdown  : {report['priority_summary']}")
    log(f"  Top missing fields  : {dict(list(report['field_gap_counts'].items())[:5])}")


if __name__ == "__main__":
    main()
