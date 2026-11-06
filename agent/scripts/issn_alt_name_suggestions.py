"""issn_alt_name_suggestions.py — deterministic Alternative journal name generator.

Builds ISSN → journal-title lookups from the WhereToPublish external data files
(Scimago, DOAJ, OpenAPC) in <wtp_dir>/data_extraction/ and resolves
Alternative journal name values for journals whose name is empty but that
already have at least one ISSN.  No LLM is involved.

Run from the journal-metadata-enrichment/ project root:
    python3 agent/scripts/issn_alt_name_suggestions.py
"""

from __future__ import annotations

import argparse
import csv
import gzip
import sys
from pathlib import Path
from typing import Callable, Iterator

from enrichment_common import DEFAULT_WTP_DIR, is_empty, SUGGESTIONS_CSV_PATH, WTP_SCRIPTS_DIR

if str(WTP_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(WTP_SCRIPTS_DIR))

import sheets_client
from libraries import format_issn, norm_name
from suggestions_io import dedupe_suggestions, load_suggestions, write_suggestions


FIELD_NAME = "Alternative journal name"
ISSN_FIELDS = ("e-ISSN", "p-ISSN", "ISSN-L")
PRESENCE_COLUMNS = ("Present in Scimago", "Present in DOAJ", "Present in openAPC")


# ---------------------------------------------------------------------------
# ISSN → title lookup builders (stdlib csv/gzip readers only)
# ---------------------------------------------------------------------------

def _iter_gz_rows(path: Path, delimiter: str = ",") -> Iterator[dict[str, str]]:
    with gzip.open(path, "rt", encoding="utf-8", errors="replace", newline="") as handle:
        yield from csv.DictReader(handle, delimiter=delimiter)


def _add_issn(lookup: dict[str, list[str]], raw_issn: str, title: str) -> None:
    formatted = format_issn(raw_issn)
    if formatted is None or not title:
        return
    lookup.setdefault(formatted, [])
    if title not in lookup[formatted]:
        lookup[formatted].append(title)


def load_scimago_issn_title_lookup(wtp_dir: Path) -> dict[str, list[str]]:
    """Scimago: the 'Issn' column holds a comma-separated list of bare ISSNs."""
    path = wtp_dir / "data_extraction" / "scimagojr.csv.gz"
    lookup: dict[str, list[str]] = {}
    for row in _iter_gz_rows(path, delimiter=";"):
        title = (row.get("Title") or "").strip()
        for raw in (row.get("Issn") or "").split(","):
            _add_issn(lookup, raw.strip(), title)
    return lookup


def load_doaj_issn_title_lookup(wtp_dir: Path) -> dict[str, list[str]]:
    """DOAJ: print ISSN and EISSN are separate columns."""
    path = wtp_dir / "data_extraction" / "DOAJ.csv.gz"
    lookup: dict[str, list[str]] = {}
    for row in _iter_gz_rows(path):
        title = (row.get("Journal title") or "").strip()
        _add_issn(lookup, row.get("Journal ISSN (print version)") or "", title)
        _add_issn(lookup, row.get("Journal EISSN (online version)") or "", title)
    return lookup


def load_openapc_issn_title_lookup(wtp_dir: Path) -> dict[str, list[str]]:
    """OpenAPC: issn, issn_print, issn_electronic and issn_l columns."""
    path = wtp_dir / "data_extraction" / "openapc.csv.gz"
    lookup: dict[str, list[str]] = {}
    for row in _iter_gz_rows(path):
        title = (row.get("journal_full_title") or "").strip()
        for column in ("issn", "issn_print", "issn_electronic", "issn_l"):
            raw = (row.get(column) or "").strip()
            if raw.upper() == "NA":
                continue
            _add_issn(lookup, raw, title)
    return lookup


# ---------------------------------------------------------------------------
# Deterministic suggestion generation
# ---------------------------------------------------------------------------

class SourceConfig:
    def __init__(self, name: str, loader: Callable[[Path], dict[str, list[str]]],
                 url_builder: Callable[[str], str]) -> None:
        self.name = name
        self.loader = loader
        self.url_builder = url_builder


SOURCE_PRIORITY = (
    SourceConfig(
        name="Scimago",
        loader=load_scimago_issn_title_lookup,
        url_builder=lambda issn: f"https://www.scimagojr.com/journalsearch.php?q={issn}&tip=issn",
    ),
    SourceConfig(
        name="DOAJ",
        loader=load_doaj_issn_title_lookup,
        url_builder=lambda issn: f"https://doaj.org/toc/{issn}",
    ),
    SourceConfig(
        name="OpenAPC",
        loader=load_openapc_issn_title_lookup,
        url_builder=lambda issn: "https://treemaps.intact-project.org/apcdata/openapc/",
    ),
)


def load_csv_rows(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def iter_field_csv_paths(wtp_dir: Path) -> Iterator[Path]:
    for slug in sheets_client.SHEET_TAB_NAMES:
        csv_path = wtp_dir / "data_extracted" / f"{slug}.csv"
        if not csv_path.exists():
            raise FileNotFoundError(f"Field CSV not found: {csv_path}")
        yield csv_path


def collect_row_issns(row: dict[str, str]) -> list[str]:
    seen_issns: set[str] = set()
    issns: list[str] = []

    for field in ISSN_FIELDS:
        formatted = format_issn(row.get(field, ""))
        if formatted is None or formatted in seen_issns:
            continue
        seen_issns.add(formatted)
        issns.append(formatted)

    return issns


def needs_alt_name_suggestion(row: dict[str, str]) -> bool:
    if not is_empty(row.get(FIELD_NAME, "")):
        return False
    if not collect_row_issns(row):
        return False
    return any(str(row.get(column, "")).strip() == "No" for column in PRESENCE_COLUMNS)


def resolve_source_match(
    lookup: dict[str, list[str]], issns: list[str]
) -> tuple[str, str] | None:
    matches_by_normalized_title: dict[str, tuple[str, str]] = {}

    for issn in issns:
        for title in lookup.get(issn, []):
            normalized_title = norm_name(title)
            if not normalized_title:
                continue
            if normalized_title not in matches_by_normalized_title:
                matches_by_normalized_title[normalized_title] = (title, issn)

    if not matches_by_normalized_title:
        return None
    if len(matches_by_normalized_title) > 1:
        return None
    return next(iter(matches_by_normalized_title.values()))


def build_suggestion(
    journal_name: str,
    matched_title: str,
    matched_issn: str,
    source: SourceConfig,
) -> dict[str, str]:
    return {
        "journal": journal_name,
        "field": FIELD_NAME,
        "current_value": "",
        "suggested_value": matched_title,
        "confidence": "1.00",
        "source_urls": source.url_builder(matched_issn),
        "reasoning": (
            f"Matched ISSN {matched_issn} in {source.name}. "
            f"{source.name} lists this journal as '{matched_title}', "
            "so the Alternative journal name is filled deterministically from the ISSN match."
        ),
        "suggestion_type": "alt_name",
        "priority": "high",
    }


def generate_suggestions(wtp_dir: Path) -> tuple[list[dict[str, str]], int]:
    lookups = [(source, source.loader(wtp_dir)) for source in SOURCE_PRIORITY]
    candidate_count = 0
    suggestions: list[dict[str, str]] = []

    for csv_path in iter_field_csv_paths(wtp_dir):
        for row in load_csv_rows(csv_path):
            journal_name = str(row.get("Journal", "")).strip()
            if not journal_name or not needs_alt_name_suggestion(row):
                continue

            candidate_count += 1
            issns = collect_row_issns(row)
            for source, lookup in lookups:
                match = resolve_source_match(lookup, issns)
                if match is None:
                    continue

                matched_title, matched_issn = match
                if norm_name(matched_title) == norm_name(journal_name):
                    continue

                suggestions.append(
                    build_suggestion(
                        journal_name=journal_name,
                        matched_title=matched_title,
                        matched_issn=matched_issn,
                        source=source,
                    )
                )
                break

    return suggestions, candidate_count


def append_deterministic_suggestions(output: Path, wtp_dir: Path) -> int:
    """Generate deterministic alt-name rows and merge them into the CSV.

    Returns the number of suggestions written.
    """
    existing_rows = load_suggestions(output)
    generated_rows, _ = generate_suggestions(wtp_dir)
    deduped_rows, _ = dedupe_suggestions(existing_rows + generated_rows)
    write_suggestions(output, deduped_rows)
    return len(generated_rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Generate deterministic Alternative journal name suggestions from ISSN matches "
            "across Scimago, DOAJ, and OpenAPC."
        )
    )
    parser.add_argument(
        "--wtp-dir",
        type=Path,
        default=DEFAULT_WTP_DIR,
        help=f"Path to the WhereToPublish repo (default: {DEFAULT_WTP_DIR})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=SUGGESTIONS_CSV_PATH,
        help=f"Path to Agent_suggestions.csv (default: {SUGGESTIONS_CSV_PATH})",
    )
    args = parser.parse_args()

    existing_rows = load_suggestions(args.output)
    generated_rows, candidate_count = generate_suggestions(args.wtp_dir)
    deduped_rows, dropped_rows = dedupe_suggestions(existing_rows + generated_rows)
    write_suggestions(args.output, deduped_rows)

    print(f"Scanned {candidate_count} candidate journal(s) with ISSN-backed missing-source coverage.")
    print(f"Generated {len(generated_rows)} deterministic Alternative journal name suggestion(s).")
    print(f"Dropped {dropped_rows} duplicate suggestion row(s) while canonicalizing {args.output}.")
    print(f"{args.output} now contains {len(deduped_rows)} canonical suggestion row(s).")


if __name__ == "__main__":
    main()
